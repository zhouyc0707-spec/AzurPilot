"""手工经营菜单的货架容量与订单库存保护。"""

from collections import defaultdict

from module.exception import RequestHumanTakeover
from module.island.island_season import SEASONAL_ITEMS
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.production_recipe_config import (
    get_initial_capacity_from_grade, get_waitress_effect, get_waitress_options,
    normalize_waitress_slots,
)


RESTAURANT_TO_SHOP = {601: 1, 602: 2, 603: 3, 604: 4, 901: 5}
SHOP_TO_RESTAURANT = {shop: restaurant for restaurant, shop in RESTAURANT_TO_SHOP.items()}
SHOP_NAMES = {1: '有鱼餐馆', 2: '白熊饮品', 3: '啾啾简餐', 4: '乌鱼烤肉', 5: '啾咖啡'}
# 此集合必须与 IslandBusiness.shop_products 的真实点击模板保持一致。
BUSINESS_MENU_ITEMS = {
    601: ('double_bamboo_shoots', 'tofu_meat', 'tofu_combo', 'hearty_meal', 'fo_tiao',
          'amaranth_rice_ball', 'matsutake_chicken_soup', 'persimmon_cake',
          'shiitake_chicken_soup', 'green_jujube_porridge'),
    602: ('spring_flower_tea', 'banana_mango', 'strawberry_lemon', 'strawberry_honey', 'floral_fruity',
          'fruit_paradise', 'lavender_tea', 'sunny_honey', 'watermelon_juice',
          'chrysanthemum_tea', 'carrot_pear_juice'),
    603: ('orchard_duo', 'succulently_sweet', 'berry_orange', 'strawberry_charlotte', 'seafood_rice',
          'water_chestnut_cake', 'sugar_tangerine_daifuku'),
    604: ('roasted_skewer', 'stir_fried_chicken', 'steak_bowl', 'crayfish_stir_fry',
          'carnival', 'double_energy', 'lemon_shrimp'),
    901: ('citrus_coffee', 'strawberry_milkshake', 'morning_light', 'wake_up_call',
          'fruity_fruitier', 'cheese'),
}
LOCAL_WAITRESS_NAMES = {
    'ChaoHo': 'Chao_Ho', 'ChangFeng': 'Chang_Feng', 'Eugen': 'Prinz_Eugen',
    'August': 'August_von_Parseval', 'Helena': 'Helena', 'Cheshire': 'Cheshire', 'Belfast': 'Belfast',
}


def _read(config, path, default=None):
    """只读共享配置，不绑定任务、不构造配置实例。"""
    cross_get = getattr(config, 'cross_get', None)
    if callable(cross_get):
        return cross_get(path, default=default)
    group, argument = path.rsplit('.', 2)[-2:]
    return getattr(config, f'{group}_{argument}', default)


def _shop_path(restaurant_id, argument):
    return f'IslandBusiness.IslandBusinessShop{RESTAURANT_TO_SHOP[restaurant_id]}.{argument}'


def get_business_menu_items(restaurant_id, season=None):
    """返回当前季节有真实点击模板的经营商品。"""
    items = BUSINESS_MENU_ITEMS[restaurant_id]
    if season is None:
        return items
    module_key = {601: 'restaurant', 602: 'teahouse', 603: 'juu_eatery'}.get(restaurant_id)
    excluded = {
        item for key, data in SEASONAL_ITEMS.items() if key != season
        for item in data.get(module_key, ())
    }
    return tuple(item for item in items if item not in excluded)


def _season(config):
    return _read(config, 'IslandPlan.IslandPlan.Season', 'spring') or 'spring'


def get_shop_menu(config, restaurant_id, season=None):
    """读取手工 Product1..5 菜单并过滤非当季餐品。"""
    season = season or _season(config)
    allowed = set(get_business_menu_items(restaurant_id, season))
    return {
        item: 1 for index in range(1, 6)
        if (item := _read(config, _shop_path(restaurant_id, f'Product{index}'), 'None')) in allowed
    }


def get_restaurant_settings(config):
    """将本地等级和两个人选适配为官方纯计算器的输入。"""
    business_enabled = _read(config, 'IslandBusiness.Scheduler.Enable', True)
    batch_enabled = _read(config, 'IslandBusiness.IslandBusiness.BatchEnabled', True)
    active_shops = set(SHOP_NAMES)
    if batch_enabled:
        first = _read(config, 'IslandBusiness.IslandBusiness.Batch1Shops', [3, 1, 5])
        second = _read(config, 'IslandBusiness.IslandBusiness.Batch2Shops', [2, 4])
        active_shops = {int(shop) for shop in (*first, *second)}
    settings = {}
    for restaurant_id, shop in RESTAURANT_TO_SHOP.items():
        grade = _read(config, _shop_path(restaurant_id, 'Grade'), 'bronze')
        try:
            get_initial_capacity_from_grade(grade)
        except ValueError as exc:
            raise RequestHumanTakeover(f'{SHOP_NAMES[shop]}的店铺等级无效: {grade}') from exc
        raw_characters = [
            _read(config, _shop_path(restaurant_id, f'Char{index}'), 'None') for index in (1, 2)
        ]
        characters = list(dict.fromkeys(char for char in raw_characters if char and char != 'None'))
        # 本地空配置会使用黄鸡；不能让纯计算器误认为所有商店被停用。
        characters = characters or ['WorkerJuu']
        allowed = set(get_waitress_options(restaurant_id))
        slots = []
        for character in characters:
            candidate = LOCAL_WAITRESS_NAMES.get(character, 'any')
            slots.append(candidate if candidate in allowed else 'any')
        if not business_enabled or shop not in active_shops:
            slots = []
        settings[restaurant_id] = {'grade': grade, 'waitress_slots': normalize_waitress_slots(restaurant_id, slots)}
    return settings


def get_restaurant_capacity(config, restaurant_id):
    """容量来自真实店铺等级与角色加成，与季节替换阈值无关。"""
    settings = get_restaurant_settings(config)[restaurant_id]
    bonus, _ = get_waitress_effect(restaurant_id, settings['waitress_slots'])
    return get_initial_capacity_from_grade(settings['grade']) + bonus


def get_menu_reserve_items(config):
    """每种有效菜品预留完整一货架；同物品跨店累计，数量不按日销量相乘。"""
    reserve = defaultdict(int)
    settings = get_restaurant_settings(config)
    for restaurant_id, data in settings.items():
        if all(slot == 'none' for slot in data['waitress_slots']):
            continue
        bonus, _ = get_waitress_effect(restaurant_id, data['waitress_slots'])
        capacity = get_initial_capacity_from_grade(data['grade']) + bonus
        for item in get_shop_menu(config, restaurant_id):
            reserve[LOCAL_TO_ITEM_ID[item]] += capacity
    return dict(reserve)


def get_order_effective_stock(stock, hard_floor, reserve=0, priority=False):
    """普通订单扣保留线及经营预留；紧急/季节/临到期单由调用方设置优先。"""
    if priority:
        return stock
    return stock - max(hard_floor, 0) - max(reserve, 0)


def menu_reservations_known(config):
    """手工全空会沿用旧经营的全部可见菜品，不能把未知菜单当成零预留。"""
    for restaurant_id, settings in get_restaurant_settings(config).items():
        if all(slot == 'none' for slot in settings['waitress_slots']):
            continue
        if not any(_read(config, _shop_path(restaurant_id, f'Product{index}'), 'None')
                   not in (None, '', 'None') for index in range(1, 6)):
            return False
    return True
