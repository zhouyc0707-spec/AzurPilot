"""生产计划的通用原料派遣：复用本地角色与岗位确认，不猜未知库存。"""

import math

from module.base.timer import Timer
from module.exception import GameStuckError
from module.island.assets import ISLAND_POST_SELECT, ISLAND_SELECT_CHARACTER_CHECK, ISLAND_SELECT_PRODUCT_CHECK
from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import ITEM_ID_TO_LOCAL, LOCAL_TO_ITEM_ID
from module.island.island_shop_base import IslandShopBase
from module.island_manufacture.assets import ALAS_RECIPE_CHECK
from module.island.manufacture_selector import (
    read_selected_recipe_inventory, read_selected_recipe_quantity, select_manufacture_recipe, set_manufacture_quantity,
)
from module.island.planner_report import invalidate_planner_stocks, record_planner_dispatch
from module.island.production_planner import (
    IslandPlanningError, load_planner_targets, load_production_protection, planner_idle_products, read_config,
)
from module.logger import logger


def recipe_for_local_name(name):
    item = LOCAL_TO_ITEM_ID.get(name)
    recipes = [recipe for recipe, data in DIC_ISLAND_RECIPE.items() if item in data['commission_product']]
    if len(recipes) != 1:
        raise ValueError(f'物品 {name} 无法唯一对应生产配方：{recipes}')
    return recipes[0]


def recipe_runtime_terms(config, recipe_id):
    """复用已扫描科技的牧场倍率与副产物；原料、产出保持同一倍率。"""
    from module.island.planner_utils import load_technology_status
    recipe = DIC_ISLAND_RECIPE[recipe_id]
    costs, outputs = dict(recipe['commission_cost']), dict(recipe['commission_product'])
    upgrades = {
        101013: (410301, 410302, 410303, 410304, 410305),
        101015: (420302, 420303, 420304),
        101016: (430302, 430303, 430304),
        101018: (440302, 440303, 440304),
    }
    if recipe_id in upgrades:
        status = load_technology_status(read_config(config, 'TechnologyStatus', ''))
        multiplier = 1 + sum(status.get(key, False) for key in upgrades[recipe_id])
        costs = {item: count * multiplier for item, count in costs.items()}
        outputs = {item: count * multiplier for item, count in outputs.items()}
        if status.get(400001, False):
            for item, count in recipe['second_product_display'].items():
                outputs[item] = outputs.get(item, 0) + count * multiplier
    return costs, outputs


class PlannedProductionMixin:
    """为农场、矿林与渔场提供同一派遣闭环，关闭规划时继续原有流程。"""

    POST_PRODUCE_LIMIT = 12
    read_food_dispatch_preview = IslandShopBase.read_food_dispatch_preview
    confirm_food_dispatch = IslandShopBase.confirm_food_dispatch
    finish_food_dispatch = IslandShopBase.finish_food_dispatch
    _sync_planned_food_materials = IslandShopBase._sync_planned_food_materials

    def deduct_materials(self, product, number):
        # 原料现货在选品页读到后只更新本轮账；不建立跨任务库存缓存。
        for item, data in getattr(self, '_planner_material_inventory', {}).items():
            if data['cost'] > 0:
                data['stock'] = max(data['stock'] - number * data['cost'], 0)

    def _planned_dispatch_stage(self):
        for _ in self.loop(timeout=Timer(25), skip_first=False):
            if self.appear(ISLAND_SELECT_PRODUCT_CHECK, offset=1) or self.appear(ALAS_RECIPE_CHECK, offset=(20, 20)):
                return 'products'
            if self.appear(ISLAND_SELECT_CHARACTER_CHECK, offset=1):
                return 'characters'
            if self.appear_then_click(ISLAND_POST_SELECT, offset=1, interval=2):
                continue
            if self.ui_additional():
                continue
        raise GameStuckError('进入原料派遣页超时')

    def _planned_open_product_page(self, post_id, characters=None, product=None):
        self.post_close()
        if not self.post_open(self.posts[post_id]['button']):
            raise GameStuckError(f'{post_id} 无法打开岗位')
        stage = self._planned_dispatch_stage()
        if stage == 'characters':
            if getattr(self, 'special_character', False) and product is not None:
                selected = self.select_special_character(product)
            else:
                selected = self.select_character(character_list=characters)
            if not selected:
                self.back_to_postmanage_from_dispatch()
                return False
            if not self.confirm_selected_character(f'{post_id}计划派遣'):
                self.back_to_postmanage_from_dispatch()
                return False
            if self._planned_dispatch_stage() != 'products':
                raise GameStuckError(f'{post_id} 选人后未进入选品页')
        return True

    def _planned_dispatch_recipe(self, post_id, name, target, time_var, characters=None,
                                 in_production=0, filler=False, extra_stocks=None):
        recipe_id = recipe_for_local_name(name)
        recipe = DIC_ISLAND_RECIPE[recipe_id]
        item_id = LOCAL_TO_ITEM_ID[name]
        if not self._planned_open_product_page(post_id, characters, name):
            return 0
        if not select_manufacture_recipe(self, recipe_id):
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'原料配方无法确认：{name}')
        inventory = read_selected_recipe_inventory(self, recipe_id)
        if inventory is None:
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'原料当前库存无法可靠读取：{name}')
        costs, outputs = recipe_runtime_terms(self.config, recipe_id)
        observed_count = read_selected_recipe_quantity(self, recipe_id) if recipe_id in (101013, 101015, 101016, 101018) else None
        for material, data in inventory.items():
            if data['cost'] > 0:
                data['cost'] = costs[material]
                if recipe_id in (101013, 101015, 101016, 101018) and (
                        observed_count is None or data['display_required'] != observed_count * data['cost']):
                    raise GameStuckError(f'实际原料数与已扫描科技倍率不符：{name}')
        stock = inventory[item_id]['stock']
        self._planner_actual_stocks[item_id] = stock
        produced = self._planner_dispatched.get(item_id, 0)
        yield_amount = outputs[item_id]
        if filler:
            count = min(recipe['production_limit'], max(1, int(4 * 36000 // recipe['workload'])))
        else:
            count = math.ceil(max(target - stock - in_production - produced, 0) / yield_amount)
            targets = load_planner_targets(self.config)
            for extra, amount in outputs.items():
                if extra == item_id or targets.get(extra, 0) <= 0:
                    continue
                if extra not in (extra_stocks or {}):
                    self.back_to_postmanage_from_dispatch()
                    raise IslandPlanningError(f'{ITEM_ID_TO_LOCAL.get(extra, extra)} 副产物现货未能可靠观测，保留需求等待复检')
                extra_deficit = targets[extra] - extra_stocks[extra] - self._planner_dispatched.get(extra, 0)
                count = max(count, math.ceil(max(extra_deficit, 0) / amount))
            count = min(recipe['production_limit'], count)
        if count <= 0:
            self.back_to_postmanage_from_dispatch()
            return 0
        for material, data in list(inventory.items()):
            if data['cost'] <= 0:
                continue
            if material < 2000 or material in (4006, 4008, 4020, 4022, 4034, 4036):
                if data['stock'] < count * data['cost']:
                    from module.island.shop_selector import ensure_selected_recipe_material
                    if not ensure_selected_recipe_material(self, recipe_id, material, count * data['cost']):
                        self.back_to_postmanage_from_dispatch()
                        return 0
                    inventory = read_selected_recipe_inventory(self, recipe_id)
                    if inventory is None:
                        raise GameStuckError(f'采购耗材后库存无法确认：{name}')
        for material, data in inventory.items():
            if data['cost'] > 0:
                protected = self._planner_protection.get(ITEM_ID_TO_LOCAL.get(material), 0)
                count = min(count, max(data['stock'] - protected, 0) // data['cost'])
        if count <= 0:
            self.back_to_postmanage_from_dispatch()
            return 0
        if not set_manufacture_quantity(self, count):
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'原料生产次数无法确认：{name}')
        self._planner_material_inventory = inventory
        preview, confirmed_at = self.confirm_food_dispatch(count, f'{name}计划派遣')
        actual = self.finish_food_dispatch(post_id, name, time_var, count, preview, confirmed_at)
        units = actual * yield_amount
        for output, quantity in outputs.items():
            self._planner_dispatched[output] = self._planner_dispatched.get(output, 0) + actual * quantity
        if actual > 0:
            record_planner_dispatch(self.config, {output: actual * quantity for output, quantity in outputs.items()},
                                    '原料派遣确认')
            invalidate_planner_stocks(self.config, costs, '原料派遣用料后')
        self.posts[post_id]['crop'] = name
        self.posts[post_id]['state'] = 'working'
        self.posts[post_id]['runs'] = actual
        logger.info(f'[岛屿-生产规划] {name} 实际下单 {actual} 轮，预计产出 {units}')
        return units

    def _planned_dispatch_category(self, idle_posts, items, inventory=None, characters=None):
        targets = load_planner_targets(self.config)
        names = [item['name'] for item in items]
        needed = [name for name in names if targets.get(LOCAL_TO_ITEM_ID.get(name), 0) > 0]
        self._planner_actual_stocks = getattr(self, '_planner_actual_stocks', {})
        self._planner_dispatched = getattr(self, '_planner_dispatched', {})
        self._planner_protection = load_production_protection(self.config)
        for post in idle_posts:
            # 每个岗位后重排积累比例；目标已满的商品仍可按 ALAS 余岗顺序积累。
            live_inventory = dict(inventory or {})
            for item, stock in self._planner_actual_stocks.items():
                if item in ITEM_ID_TO_LOCAL:
                    live_inventory[ITEM_ID_TO_LOCAL[item]] = stock + self._planner_dispatched.get(item, 0)
            fillers = planner_idle_products(self.config, names, live_inventory) or []
            candidates = [(name, False) for name in needed] + [(name, True) for name in fillers]
            for name, filler in candidates:
                worker = characters(name) if callable(characters) else characters
                count = self._planned_dispatch_recipe(
                    post['post_id'], name, targets.get(LOCAL_TO_ITEM_ID[name], 0),
                    post['time_var_name'], worker,
                    in_production=getattr(self, '_planner_in_production', {}).get(LOCAL_TO_ITEM_ID[name], 0),
                    filler=filler)
                if count > 0:
                    break
