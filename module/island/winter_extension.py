"""手工执行、库存识别与订单使用的冬季兼容数据。

独立规划器使用上游官方数据，通过产品名称写回配置，不应用本地旧编号。
此兼容表只作用于 module.island.data，避免改变既有仓库识别和配方映射。
"""

WINTER_ITEMS = {
    1023: {
        'name': {'cn': '砂糖橘树种', 'en': 'Sugar Tangerine Tree Seeds', 'jp': '砂糖橘の木の種', 'tw': '砂糖橘樹種'},
        'pt_num': 1, 'manage_influence': 0, 'order_price': 0,
    },
    1024: {
        'name': {'cn': '青枣树种', 'en': 'Green Jujube Tree Seeds', 'jp': '青棗の木の種', 'tw': '青棗樹種'},
        'pt_num': 1, 'manage_influence': 0, 'order_price': 0,
    },
    2023: {
        'name': {'cn': '砂糖橘', 'en': 'Sugar Tangerine', 'jp': '砂糖橘', 'tw': '砂糖橘'},
        'pt_num': 12, 'manage_influence': 0, 'order_price': 160,
    },
    2024: {
        'name': {'cn': '青枣', 'en': 'Green Jujube', 'jp': '青棗', 'tw': '青棗'},
        'pt_num': 15, 'manage_influence': 0, 'order_price': 180,
    },
    4043: {
        'name': {'cn': '冬菇', 'en': 'Shiitake', 'jp': '椎茸', 'tw': '冬菇'},
        'pt_num': 8, 'manage_influence': 0, 'order_price': 50,
    },
    4044: {
        'name': {'cn': '马蹄', 'en': 'Water Chestnut', 'jp': 'クワイ', 'tw': '荸薺'},
        'pt_num': 6, 'manage_influence': 0, 'order_price': 40,
    },
    4045: {
        'name': {'cn': '梅花', 'en': 'Plum Blossom', 'jp': '梅の花', 'tw': '梅花'},
        'pt_num': 10, 'manage_influence': 0, 'order_price': 65,
    },
    4046: {
        # 与上游冬季花束中「梅花 1 + 水仙花 4」唯一对应；保留手工执行旧编号。
        'name': {'cn': '水仙花', 'en': 'Daffodils', 'jp': 'スイセン', 'tw': '水仙花'},
        'pt_num': 10, 'manage_influence': 0, 'order_price': 65,
    },
    4047: {
        'name': {'cn': '梅花香包', 'en': 'Plum Blossom Sachet', 'jp': '梅の花の香袋', 'tw': '梅花香包'},
        'pt_num': 160, 'manage_influence': 280, 'order_price': 1200,
    },
    4048: {
        'name': {'cn': '冬季花束', 'en': 'Winter Bouquet', 'jp': '冬のブーケ', 'tw': '冬季花束'},
        'pt_num': 180, 'manage_influence': 280, 'order_price': 1350,
    },
    4049: {
        'name': {'cn': '冬菇炖鸡', 'en': 'Shiitake Chicken Soup', 'jp': '椎茸と鶏のスープ', 'tw': '冬菇燉雞'},
        'pt_num': 150, 'manage_influence': 280, 'order_price': 980,
    },
    4050: {
        'name': {'cn': '青枣粥', 'en': 'Green Jujube Porridge', 'jp': '青棗粥', 'tw': '青棗粥'},
        'pt_num': 120, 'manage_influence': 270, 'order_price': 860,
    },
    4051: {
        'name': {'cn': '马蹄糕', 'en': 'Water Chestnut Cake', 'jp': 'クワイの蒸し糕', 'tw': '馬蹄糕'},
        'pt_num': 90, 'manage_influence': 260, 'order_price': 700,
    },
    4052: {
        'name': {'cn': '砂糖橘大福', 'en': 'Sugar Tangerine Daifuku', 'jp': '砂糖橘大福', 'tw': '砂糖橘大福'},
        'pt_num': 110, 'manage_influence': 270, 'order_price': 820,
    },
}


def _recipe(name, workload, commission_cost, commission_product):
    return {
        'name': name,
        'workload': workload,
        'commission_cost': commission_cost,
        'production_limit': 5,
        'commission_product': commission_product,
        'second_product_display': {},
    }


WINTER_RECIPES = {
    501008: _recipe(
        {'cn': '砂糖橘', 'en': 'Sugar Tangerine', 'jp': '砂糖橘', 'tw': '砂糖橘'},
        45000, {1023: 4}, {2023: 8},
    ),
    501009: _recipe(
        {'cn': '青枣', 'en': 'Green Jujube', 'jp': '青棗', 'tw': '青棗'},
        54000, {1024: 4}, {2024: 12},
    ),
    9900025: _recipe(
        {'cn': '冬菇炖鸡', 'en': 'Shiitake Chicken Soup', 'jp': '椎茸と鶏のスープ', 'tw': '冬菇燉雞'},
        18000, {2602: 1, 4043: 2}, {4049: 1},
    ),
    9900026: _recipe(
        {'cn': '青枣粥', 'en': 'Green Jujube Porridge', 'jp': '青棗粥', 'tw': '青棗粥'},
        18000, {2024: 2, 2002: 5}, {4050: 1},
    ),
    9900027: _recipe(
        {'cn': '马蹄糕', 'en': 'Water Chestnut Cake', 'jp': 'クワイの蒸し糕', 'tw': '馬蹄糕'},
        12000, {4044: 2}, {4051: 1},
    ),
    9900028: _recipe(
        {'cn': '砂糖橘大福', 'en': 'Sugar Tangerine Daifuku', 'jp': '砂糖橘大福', 'tw': '砂糖橘大福'},
        12000, {2023: 2, 3004: 3}, {4052: 1},
    ),
    9900029: _recipe(
        {'cn': '梅花香包', 'en': 'Plum Blossom Sachet', 'jp': '梅の花の香袋', 'tw': '梅花香包'},
        36000, {4045: 3}, {4047: 1},
    ),
    9900030: _recipe(
        {'cn': '冬季花束', 'en': 'Winter Bouquet', 'jp': '冬のブーケ', 'tw': '冬季花束'},
        18000, {4045: 1, 4046: 4}, {4048: 1},
    ),
}

WINTER_RESTAURANT_RECIPES = {
    601: {4049: 9900025, 4050: 9900026},
    603: {4051: 9900027, 4052: 9900028},
}

# 来源同独立规划器 data.py（ALAS 9485310）；订单编号及活动日期保持官方值，
# request 物品按完整中文名称映射到本地旧编号，避免把官方青枣 4049 当作冬菇炖鸡。
WINTER_ORDER_ACTIVITY = {
    990028: {
        'type': 5002,
        'start_time': {'cn': '2026-11-05 00:00:00', 'en': '2026-11-05 00:00:00',
                       'jp': '2026-11-05 00:00:00', 'tw': ''},
        'end_time': {'cn': '2027-02-01 12:00:00', 'en': '2027-02-01 23:59:59',
                     'jp': '2027-02-01 16:00:00', 'tw': ''},
        'config_data': list(range(100061, 100076)),
    },
}

WINTER_SEASON_ORDERS = {
    100061: {'activity_id': 990028, 'next_order': 100062, 'season_pt_num': 1000, 'request': {4045: 4}, 'award': {0: 4000}},
    100062: {'activity_id': 990028, 'next_order': 100063, 'season_pt_num': 1000, 'request': {4046: 4}, 'award': {0: 4000}},
    100063: {'activity_id': 990028, 'next_order': 100064, 'season_pt_num': 1000, 'request': {4043: 4}, 'award': {0: 4000}},
    100064: {'activity_id': 990028, 'next_order': 100065, 'season_pt_num': 1000, 'request': {4044: 4}, 'award': {0: 4000}},
    100065: {'activity_id': 990028, 'next_order': 100066, 'season_pt_num': 1000, 'request': {2023: 10}, 'award': {0: 4000}},
    100066: {'activity_id': 990028, 'next_order': 100067, 'season_pt_num': 1000, 'request': {2024: 10}, 'award': {0: 4000}},
    100067: {'activity_id': 990028, 'next_order': 100068, 'season_pt_num': 2500, 'request': {4049: 5}, 'award': {0: 10000}},
    100068: {'activity_id': 990028, 'next_order': 100069, 'season_pt_num': 2500, 'request': {4050: 5}, 'award': {0: 10000}},
    100069: {'activity_id': 990028, 'next_order': 100070, 'season_pt_num': 2500, 'request': {4051: 5}, 'award': {0: 10000}},
    100070: {'activity_id': 990028, 'next_order': 100071, 'season_pt_num': 2500, 'request': {4052: 5}, 'award': {0: 10000}},
    100071: {'activity_id': 990028, 'next_order': 100072, 'season_pt_num': 2500, 'request': {4047: 5}, 'award': {0: 10000}},
    100072: {'activity_id': 990028, 'next_order': 100073, 'season_pt_num': 2500, 'request': {4048: 5}, 'award': {0: 10000}},
    100073: {'activity_id': 990028, 'next_order': 100074, 'season_pt_num': 8000, 'request': {4050: 5, 4051: 5}, 'award': {0: 32000}},
    100074: {'activity_id': 990028, 'next_order': 100075, 'season_pt_num': 8000, 'request': {4048: 5, 4052: 5}, 'award': {0: 32000}},
    100075: {'activity_id': 990028, 'next_order': 0, 'season_pt_num': 8000, 'request': {4047: 5, 4050: 5}, 'award': {0: 32000}},
}


def apply_winter_extension(data):
    """把冬季物品、配方、餐馆菜单和季节订单补入手工执行数据。

    Args:
        data (module): 已导入的 ``module.island.data`` 模块。

    Returns:
        module: 幂等更新后的原模块。
    """
    data.DIC_ISLAND_ITEM.update(WINTER_ITEMS)
    data.DIC_ISLAND_RECIPE.update(WINTER_RECIPES)
    data.DIC_ISLAND_ACTIVITY.update(WINTER_ORDER_ACTIVITY)
    data.DIC_ISLAND_SEASON_ORDER.update(WINTER_SEASON_ORDERS)
    for restaurant_id, menu in WINTER_RESTAURANT_RECIPES.items():
        data.DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.setdefault(restaurant_id, {}).update(menu)
    return data
