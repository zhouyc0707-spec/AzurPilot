"""按生产缺口有限兑换鱼肉，确认预览和到账后才继续生产。"""

import math
import re
import unicodedata
from collections import defaultdict

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import crop
from module.exception import GameStuckError
from module.island.data import DIC_ISLAND_EXCHANGE_RECIPE, DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE
from module.island.order_stock import get_menu_reserve_items
from module.island.planner_utils import (
    get_current_season_remaining_days, get_stuck_season_order_requirements, load_item_mapping,
    normalize_item_keys, normalize_item_needs, resolve_stuck_season_order_id,
)
from module.island.production_planner import load_planner_targets, read_config
from module.island.planner_report import invalidate_planner_stocks, record_planner_stocks
from module.island.assets import GET_ITEMS_ISLAND
from module.island_exchange.assets import ALAS_EXCHANGE_CONFIRM
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.page import page_island_exchange_shop


FISH_MEAT_IDS = (2521, 2522)
FISH_CARD_AREA = (175, 80, 927, 620)
AMOUNT_AREA = (957, 517, 1185, 580)
MAX_BATCH_FISH = 5
MAX_EXCHANGE_BATCHES = 20
EXCHANGE_GROUP_LABELS = {2521: ('淡水鱼', '淡水魚'), 2522: ('海水鱼', '海水魚')}
EXCHANGE_MAIN_LABELS = ('鱼肉加工', '魚肉加工', 'Fish processing')


def _normalize_text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f'鱼库存保护数量无效：{value}')
    return math.ceil(value)


def get_fish_protection(config):
    """原料保底、直接任务和经营下一架原料不参与鱼肉兑换。"""
    floor = normalize_item_keys(load_item_mapping(read_config(config, 'HardFloorItems', '{}')))
    task = normalize_item_needs(load_item_mapping(read_config(config, 'TaskTarget', '{}')),
                                default_period=get_current_season_remaining_days())
    cross_get = getattr(config, 'cross_get', None)
    stuck_id = cross_get('IslandDailyOrder.IslandDailyOrder.StuckSeasonOrderId', 0) if callable(cross_get) else 0
    stuck_id = resolve_stuck_season_order_id(stuck_id)
    pending = get_stuck_season_order_requirements(stuck_id)
    reserve = get_menu_reserve_items(config)
    # 当前食谱不直接消耗可兑换鱼；按编号保留此链路，避免以后新增食谱漏保护。
    ingredients = defaultdict(int)
    for item_id, count in reserve.items():
        recipes = [recipe for recipe in DIC_ISLAND_RECIPE.values() if item_id in recipe['commission_product']]
        if len(recipes) != 1:
            raise ValueError(f'菜单物品原料配方不唯一：{item_id}')
        recipe = recipes[0]
        amount = recipe['commission_product'][item_id]
        for source, cost in recipe['commission_cost'].items():
            ingredients[source] += math.ceil(count * cost / amount)
    fish_ids = {source for recipe in DIC_ISLAND_EXCHANGE_RECIPE.values() for source in recipe['resource_consume']}
    return {fish: _number(floor.get(fish, 0)) + _number(reserve.get(fish, 0))
            + _number(ingredients.get(fish, 0)) + _number(task.get(fish, {}).get('total_need_count', 0))
            + _number(pending.get(fish, 0))
            for fish in fish_ids}


def plan_fish_exchange(meat_id, deficit, stock, protected=None, max_fish=MAX_BATCH_FISH):
    """在剩余库存内选择有限鱼；优先少超额，再少占订单价值。

    一个整鱼无法拆分，允许达到目标时超出不足一条鱼的产量；不足则仅兑换
    当前可用的有限批次。不会为无缺口的鱼肉选鱼，也不使用鱼苗或石油物品。
    """
    if meat_id not in FISH_MEAT_IDS or not isinstance(max_fish, int) or not 1 <= max_fish <= MAX_BATCH_FISH:
        raise ValueError('鱼肉编号或兑换批次上限无效')
    deficit = _number(deficit)
    if not deficit:
        return {}, 0
    protected = protected or {}
    units = []
    for recipe_id, recipe in sorted(DIC_ISLAND_EXCHANGE_RECIPE.items()):
        if meat_id not in recipe['items']:
            continue
        fish_id, cost = next(iter(recipe['resource_consume'].items()))
        if cost != 1:
            raise ValueError(f'鱼肉兑换耗材单位已变化：{recipe_id}')
        available = max(_number(stock.get(fish_id, 0)) - _number(protected.get(fish_id, 0)), 0)
        units.extend([(fish_id, recipe['items'][meat_id], DIC_ISLAND_ITEM[fish_id]['order_price'])]
                     * min(available, max_fish))
    # 至多十种鱼各五条，完整枚举产量状态；每个鱼单位只允许使用一次。
    states = {(0, 0): (0, ())}
    for fish_id, yield_amount, value in units:
        for (count, amount), (total_value, selected) in list(states.items()):
            if count == max_fish:
                continue
            key = count + 1, amount + yield_amount
            candidate = total_value + value, selected + (fish_id,)
            if key not in states or candidate < states[key]:
                states[key] = candidate
    candidates = [(amount, count, value, selected) for (count, amount), (value, selected) in states.items() if count]
    if not candidates:
        return {}, 0
    complete = [candidate for candidate in candidates if candidate[0] >= deficit]
    best = min(complete, key=lambda row: (row[0], row[2], row[1], row[3])) if complete else min(
        candidates, key=lambda row: (-row[0], row[2], row[1], row[3]))
    selected = defaultdict(int)
    for fish in best[3]:
        selected[fish] += 1
    return dict(selected), best[0]


def parse_exchange_amounts(detections):
    """识别右侧现货与带 + 的新增预览；不把未知文本转换成数字零。"""
    tokens = [_normalize_text(text) for text, _, score in detections if score >= 0.90]
    combined = [re.fullmatch(r'(\d+)\+(\d+)', token) for token in tokens]
    combined = [match for match in combined if match]
    if len(combined) == 1 and len(tokens) == 1:
        return int(combined[0][1]), int(combined[0][2])
    current = [int(token) for token in tokens if re.fullmatch(r'\d+', token)]
    added = [int(token[1:]) for token in tokens if re.fullmatch(r'\+\d+', token)]
    if len(current) != 1 or len(added) > 1 or len(tokens) != len(current) + len(added):
        return None
    return current[0], added[0] if added else 0


def parse_exchange_cards(detections, meat_id, language='cn'):
    """只接受唯一全名和同卡右下数量；数量框有歧义时不给出可兑换量。"""
    fish_ids = {source for recipe in DIC_ISLAND_EXCHANGE_RECIPE.values() if meat_id in recipe['items']
                for source in recipe['resource_consume']}
    names = {_normalize_text(DIC_ISLAND_ITEM[fish]['name'][language]): fish for fish in fish_ids}
    rows, counts = [], []
    for text, box, score in detections:
        if score < 0.90:
            continue
        value = _normalize_text(text)
        points = [(float(x), float(y)) for x, y in box]
        area = min(x for x, _ in points), min(y for _, y in points), max(x for x, _ in points), max(y for _, y in points)
        if value in names:
            rows.append((names[value], area))
        elif re.fullmatch(r'\d+', value):
            counts.append((int(value), area))
    associations = []
    for fish, area in rows:
        if sum(item == fish for item, _ in rows) != 1:
            continue
        center = (area[0] + area[2]) / 2
        nearby = [(index, count) for index, (count, rect) in enumerate(counts)
                  if center + 8 <= (rect[0] + rect[2]) / 2 <= center + 72
                  and area[1] - 60 <= (rect[1] + rect[3]) / 2 <= area[1] - 3]
        if len(nearby) != 1:
            continue
        index, count = nearby[0]
        associations.append((fish, area, index, count))
    result = {}
    for fish, area, index, count in associations:
        if sum(other_index == index for _, _, other_index, _ in associations) != 1:
            continue
        # 只点击名称条，避开选中后出现的减号与数量输入框。
        result[fish] = {'stock': count, 'button': area}
    return result


class FishExchangeSession:
    """借用当前任务的设备与导航，不初始化账号或额外调度器。"""

    def __init__(self, main):
        self.main = main
        self.ocr = Ocr([], lang='cnocr', name='FISH_EXCHANGE_TEXT')

    def _detect(self, area):
        return self.ocr.cnocr.det(crop(self.main.device.image, area))

    def _unknown(self, reason):
        self.main.device.save_screenshot(genre='island_fish_exchange_unknown', interval=0)
        raise GameStuckError(f'鱼肉兑换未得到正向确认：{reason}')

    def _panel(self, meat_id):
        if not self.main.appear(ALAS_EXCHANGE_CONFIRM, offset=(20, 20)):
            return False
        expected = _normalize_text(DIC_ISLAND_ITEM[meat_id]['name'][server.server])
        names = self._detect((927, 80, 1250, 500))
        return any(score >= 0.90 and _normalize_text(text) == expected for text, _, score in names)

    def _amounts(self):
        return parse_exchange_amounts(self._detect(AMOUNT_AREA))

    def _click_side_label(self, labels):
        detections = self._detect((0, 70, 168, 695))
        names = {_normalize_text(label) for label in labels}
        matches = [box for text, box, score in detections if score >= 0.90 and _normalize_text(text) in names]
        if len(matches) != 1:
            return False
        box = matches[0]
        area = (min(x for x, _ in box), min(y for _, y in box) + 70,
                max(x for x, _ in box), max(y for _, y in box) + 70)
        self.main.device.click(Button(area=area, color=(), button=area, name='FISH_EXCHANGE_SIDE'))
        return True

    def _goto_group(self, meat_id):
        """切换另一组再返回，游戏 FlushGroup 会清空选择，不使用全选按钮。"""
        self.main.ui_goto(page_island_exchange_shop, get_ship=False)
        other = 2522 if meat_id == 2521 else 2521
        phase = 0
        click_timer = Timer(2, count=4)
        for _ in self.main.loop(skip_first=False, timeout=Timer(25)):
            target = other if phase == 0 else meat_id
            if self._panel(target):
                if phase == 0:
                    phase = 1
                    click_timer.clear()
                    continue
                amounts = self._amounts()
                if amounts is not None and amounts[1] == 0:
                    return amounts[0]
            if click_timer.reached():
                clicked = self._click_side_label(EXCHANGE_GROUP_LABELS[target])
                if not clicked:
                    clicked = self._click_side_label(EXCHANGE_MAIN_LABELS)
                if clicked:
                    click_timer.reset()
        self._unknown('未能进入已清空选择的鱼肉加工分组')

    def _cards(self, meat_id):
        cards = parse_exchange_cards(self._detect(FISH_CARD_AREA), meat_id, server.server)
        for info in cards.values():
            x1, y1, x2, y2 = info['button']
            info['button'] = (x1 + FISH_CARD_AREA[0], y1 + FISH_CARD_AREA[1],
                              x2 + FISH_CARD_AREA[0], y2 + FISH_CARD_AREA[1])
        return cards

    def _handle_exchange_popup(self):
        """只接受游戏鱼肉加工专用确认文案，其他确认弹窗不点击。"""
        expected = {'是否确认进行加工?', '是否確認進行加工?'}
        texts = self._detect((250, 200, 1050, 510))
        if any(score >= 0.90 and _normalize_text(text) in expected for text, _, score in texts):
            return self.main.handle_popup_confirm('ISLAND_FISH_EXCHANGE')
        return False

    def _exchange_batch(self, meat_id, current, cards, selection, produced):
        pending = None
        selected = 0
        remaining = dict(selection)
        clicked_confirm = False
        popup_confirmed = False
        action_timeout = Timer(8).start()
        for _ in self.main.loop(skip_first=False, timeout=Timer(45)):
            if clicked_confirm:
                if self._handle_exchange_popup():
                    popup_confirmed = True
                    action_timeout.reset()
                    continue
                if self.main.appear_then_click(GET_ITEMS_ISLAND, offset=(20, 20), interval=2):
                    continue
            if self._panel(meat_id):
                amounts = self._amounts()
                if amounts is None:
                    continue
                stock, preview = amounts
                if clicked_confirm:
                    if popup_confirmed and stock == current + produced and preview == 0:
                        return stock
                elif stock != current:
                    self._unknown('选鱼期间现货发生变化')
                elif pending is not None:
                    if preview == selected + pending:
                        selected = preview
                        pending = None
                        action_timeout.reset()
                    elif preview != selected:
                        self._unknown('单卡增量与兑换配方不一致')
                elif preview != selected:
                    self._unknown('选择预览与本轮选择不一致')
                elif selected == produced and not any(remaining.values()):
                    self.main.device.click(ALAS_EXCHANGE_CONFIRM)
                    clicked_confirm = True
                    action_timeout.reset()
                    continue
                else:
                    fish = next(item for item, count in remaining.items() if count)
                    self.main.device.click(Button(area=cards[fish]['button'], color=(), button=cards[fish]['button'],
                                                  name=f'FISH_EXCHANGE_CARD_{fish}'))
                    remaining[fish] -= 1
                    pending = next(recipe['items'][meat_id] for recipe in DIC_ISLAND_EXCHANGE_RECIPE.values()
                                   if fish in recipe['resource_consume'])
                    action_timeout.reset()
                    continue
            if action_timeout.reached():
                self._unknown('选择、确认或到账超时；未继续补点')
        self._unknown('兑换批次超时')

    def run(self, targets, protected):
        changed = False
        for meat_id in FISH_MEAT_IDS:
            target = targets.get(meat_id, 0)
            if target <= 0:
                continue
            for _ in range(MAX_EXCHANGE_BATCHES):
                current = self._goto_group(meat_id)
                if current >= target:
                    break
                cards = self._cards(meat_id)
                if not cards:
                    self.main.device.save_screenshot(genre='island_fish_exchange_unknown', interval=0)
                    logger.warning('[岛屿-鱼肉兑换] 没有完整识别到可兑换鱼卡，保留库存等待下轮')
                    break
                self.main.device.screenshot()
                confirmed = self._cards(meat_id)
                if not self._panel(meat_id) or self._amounts() != (current, 0) or {
                    fish: info['stock'] for fish, info in cards.items()
                } != {fish: info['stock'] for fish, info in confirmed.items()}:
                    self._unknown('相邻截图中的鱼库存或加工页状态不一致')
                cards = confirmed
                selection, produced = plan_fish_exchange(meat_id, target - current,
                                                        {fish: info['stock'] for fish, info in cards.items()}, protected)
                if not selection:
                    logger.info(f'[岛屿-鱼肉兑换] {meat_id} 缺{target-current}，可用鱼不足，保留最低库存')
                    break
                logger.attr('FishExchange', {'item': meat_id, 'stock': current, 'target': target,
                                            'fish': selection, 'output': produced})
                after = self._exchange_batch(meat_id, current, cards, selection, produced)
                config = getattr(self.main, 'config', None)
                invalidate_planner_stocks(config, [meat_id, *selection], '鱼肉兑换后')
                record_planner_stocks(config, {meat_id: after}, '鱼肉兑换到账页')
                changed = True
                if after >= target:
                    break
            else:
                logger.warning('[岛屿-鱼肉兑换] 已达本轮有限批次上限，剩余需求留待下轮')
        return changed


def ensure_fish_meat_targets(main):
    """仅在有效规划要求鱼肉时兑换；返回 True 表示真实库存已经变化。

    Pages:
        in: 可导航的岛屿页面。
        out: page_island_exchange_shop。调用者随后按原有流程进入仓库/岗位。
    """
    targets = load_planner_targets(main.config)
    if not any(targets.get(item, 0) > 0 for item in FISH_MEAT_IDS):
        return False
    if server.server not in ('cn', 'tw'):
        logger.warning('[岛屿-鱼肉兑换] 当前语言缺少已核实的逐鱼选择识别，保留库存')
        return False
    return FishExchangeSession(main).run(targets, get_fish_protection(main.config))
