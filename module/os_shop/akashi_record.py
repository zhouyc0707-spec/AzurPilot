"""购买前确认整店货架，独立记录明石猫王，不改变购买过滤。"""
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from module.base.utils import color_similar, crop, load_image
from module.logger import logger
from module.statistics.akashi_shop_records import AkashiShopRecords


@lru_cache(maxsize=32)
def _empty_slot_template(server, slot):
    """无商品的货架背景与售罄物品框不同，只匹配已校准的服务器及槽位。"""
    path = Path(__file__).resolve().parents[2] / 'assets' / 'shop' / 'akashi_empty' / f'{server}_{slot}.png'
    return load_image(str(path)) if path.is_file() else None


def observation(runner, items, issues=None):
    """正向确认每格是已识别商品或空模板，返回在售箱数与整店签名。"""
    def reject(reason):
        if issues is not None:
            issues.append(reason)
        return None

    grid = runner.os_akashi_shop_items
    image = runner.device.image
    if image.shape != (720, 1280, 3):
        return reject('截图尺寸不是1280×720')
    by_area = {tuple(item.area): item for item in items}
    if len(by_area) != len(items):
        return reject('商品格位置重复')
    boxes = {'20': 0, '50': 0, '100': 0}
    signature = []
    for slot, button in enumerate(grid.grids.buttons, start=1):
        item = by_area.get(tuple(button.area))
        if item is None:
            # 原识别器会丢弃无货币或无价格的格，缺失本身不能证明已经售罄。
            raw = grid.item_class(image, button)
            color = cv2.mean(crop(raw.image, grid.template_area))[:3]
            empty_score = max((
                cv2.minMaxLoc(cv2.matchTemplate(raw.image, template, cv2.TM_CCOEFF_NORMED))[1]
                for name, template in grid.templates.items()
                if name.startswith('Empty') and color_similar(color, grid.colors[name], threshold=30)
                and np.std(template) > 1
            ), default=0)
            background = _empty_slot_template(runner.config.SERVER, slot)
            background_score = 0
            if (background is not None and background.shape == raw.image.shape and np.std(background) > 1
                    and color_similar(cv2.mean(raw.image)[:3], cv2.mean(background)[:3], threshold=30)):
                background_score = cv2.minMaxLoc(
                    cv2.matchTemplate(raw.image, background, cv2.TM_CCOEFF_NORMED)
                )[1]
            if max(empty_score, background_score) < .95:
                return reject(f'第{slot}格未确认商品或空位（物品空框{empty_score:.2f}，货架背景{background_score:.2f}）')
            signature.append((tuple(button.area), 'Empty'))
            continue
        if (not item.is_known_item() or type(item.amount) is not int or item.amount <= 0
                or item.cost not in ('YellowCoins', 'PurpleCoins', 'EnergyStorage') or item.price <= 0):
            return reject(f'第{slot}格商品、数量、货币或价格不完整')
        match = re.fullmatch(r'ActionPoint(20|50|100)', item.name)
        if match:
            if not getattr(item, 'amount_valid', False):
                return reject(f'第{slot}格行动力数量未通过校验')
            boxes[match[1]] += item.amount
        signature.append((tuple(item.area), item.name, item.amount, item.cost, item.price))
    total = sum(int(base) * count for base, count in boxes.items())
    return total, boxes, tuple(signature)


def observe_record(runner, items, can_recheck):
    """可能刷新纪录时请求一次独立新帧；完成或放弃后不影响正常购买。"""
    session = getattr(runner, '_akashi_shop_session', None)
    instance = getattr(getattr(runner, 'config', None), 'config_name', None)
    if (not isinstance(session, dict) or not isinstance(instance, str) or not instance
            or session.get('record_checked') or session.get('purchase_attempted')
            or runner.config.SHOP_EXTRACT_TEMPLATE):
        return False
    try:
        issues = []
        current = observation(runner, items, issues=issues)
        if current is not None:
            total, boxes, signature = current
            store = AkashiShopRecords()
            monthly = store.records(instance, runner.config.SERVER)['monthly']
            if total <= 0 or (monthly and total < monthly['totalAp']):
                session['record_checked'] = True
                return False
            if session.get('record_candidate') == signature:
                # 两次名称、数量和货架布局一致，保存当前未经购买的完整原帧。
                if store.record(instance, runner.config.SERVER, total, boxes, runner.device.image.copy()):
                    logger.info(f'[明石猫王] 在售行动力{total}点，已更新本月纪录及完整商店截图')
                session['record_checked'] = True
                return False
            session['record_candidate'] = signature
        else:
            session.pop('record_candidate', None)
        if can_recheck:
            # 清掉本次商品缓存，下一帧必须独立进行OCR，不能复用旧数值凑双帧。
            session.pop('inventory', None)
            return True
        session['record_checked'] = True
        reason = '；'.join(issues) if issues else '两次货架识别结果不一致'
        logger.warning(f'[明石猫王] 购买前货架未能完整稳定确认，本店不刷新纪录：{reason}')
    except Exception:
        session['record_checked'] = True
        logger.exception('[明石猫王] 纪录保存失败，继续原商店购买流程')
    return False
