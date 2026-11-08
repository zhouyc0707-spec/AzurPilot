"""当前季节的追加生产目标；只读取配置，不改写原有生产顺序。"""


SEASONAL_PRODUCTION_ITEMS = {
    'spring': {
        'restaurant': ('double_bamboo_shoots', 'asparagus_shrimp'),
        'teahouse': ('spring_flower_tea', 'pineapple_juice'),
        'nursery': ('asparagus', 'pineapple'),
        'handmade': ('shepherd_purse', 'spring_bouquet'),
    },
    'summer': {
        'restaurant': ('amaranth_rice_ball', 'tomato_egg'),
        'teahouse': ('watermelon_juice', 'cucumber_juice'),
        'nursery': ('tomato', 'cucumber'),
        'handmade': ('jasmine_oil', 'summer_bouquet'),
    },
    'autumn': {
        'restaurant': ('persimmon_cake', 'matsutake_chicken_soup'),
        'teahouse': ('carrot_pear_juice', 'chrysanthemum_tea'),
        'orchard': ('pear', 'persimmon'),
        'handmade': ('peanut_oil', 'autumn_bouquet'),
    },
    'winter': {
        'restaurant': ('shiitake_chicken_soup', 'green_jujube_porridge'),
        'juu_eatery': ('water_chestnut_cake', 'sugar_tangerine_daifuku'),
        'orchard': ('sugar_tangerine', 'green_jujube'),
        'handmade': ('plum_blossom_sachet', 'winter_bouquet'),
    },
}


def seasonal_targets(config, category):
    """按配置页顺序返回当前季节、当前生产区域的正整数库存目标。

    目标为 0、未填写或无效时，不追加该物品；原有手工目标保持不变。
    未知季节不回退到其他季节，避免在换季或配置异常时误派遣。
    本函数不读取游戏状态，也不导入设备、资源或日志模块。

    Args:
        config: 提供 ``cross_get(path)`` 的配置对象。
        category (str): 店铺或种植区域键，如 ``restaurant``、``orchard``。

    Returns:
        dict[str, int]: 保持当前季节物品顺序的追加目标。
    """
    season = config.cross_get('IslandPlan.IslandPlan.Season', default='spring')
    if not isinstance(season, str) or season not in SEASONAL_PRODUCTION_ITEMS:
        return {}
    names = SEASONAL_PRODUCTION_ITEMS[season].get(category, ())
    group = f'IslandSeason{season.capitalize()}'
    result = {}
    for name in names:
        value = config.cross_get(f'IslandSeasonalProduction.{group}.{name}')
        # 配置导入可能保留文本数字；小数、布尔值、负数均不能变成生产需求。
        if isinstance(value, str):
            value = value.strip()
            if not value or not value.isascii() or not value.isdecimal():
                continue
            try:
                value = int(value)
            except ValueError:
                continue
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            result[name] = value
    from module.island.seasonal_task_targets import imported_targets_for_category

    for name, count in imported_targets_for_category(config, season, category).items():
        result[name] = max(result.get(name, 0), count)
    return result
