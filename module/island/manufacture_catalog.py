"""官方工坊配方与本地物品键的完整映射，保留已核实的旧版视觉资源。"""

from module.island.data import DIC_ISLAND_RECIPE, DIC_ISLAND_SLOT
from module.island.island_season import SEASONAL_ITEMS
from module.island.item_ids import ITEM_ID_TO_LOCAL, LOCAL_TO_ITEM_ID
from module.island_manufacture import assets


PLACE_TO_CATEGORY = {703: 'wood_processing', 704: 'industrial_production',
                     705: 'electronic_processing', 706: 'handmade'}


def _seasonal_recipes():
    """用现有季节定义和官方产物编号关联配方，不用菜名近似匹配。"""
    result = {}
    for season, modules in SEASONAL_ITEMS.items():
        for name in modules.get('handmade', []):
            item_id = LOCAL_TO_ITEM_ID.get(name)
            matches = [recipe_id for recipe_id, recipe in DIC_ISLAND_RECIPE.items()
                       if item_id in recipe['commission_product']]
            if len(matches) != 1:
                raise ValueError(f'季节制造配方无法唯一匹配：{name}，候选 {matches}')
            result[matches[0]] = season
    return result


def get_catalog(season=None):
    """返回四类工坊的完整配方；None 包含历季配方，指定季节仅加相应限定品。

    原有常驻、选品与派遣行为由制造调度层融合；没有真实图像的资源字段为 None，
    必须走同帧选品/库存识别，不能套用其他商品模板或把未知库存当作零。
    """
    if season not in (None, 'none', 'spring', 'summer', 'autumn', 'winter'):
        raise ValueError(f'未知制造季节：{season}')
    catalog = {category: [] for category in PLACE_TO_CATEGORY.values()}
    recipe_place = {}
    for slot in DIC_ISLAND_SLOT.values():
        place = slot['place']
        if place not in PLACE_TO_CATEGORY:
            continue
        for recipe_id in slot['formula'] + slot['activity_formula']:
            recipe_place[recipe_id] = place
    seasonal = _seasonal_recipes()
    recipe_place.update({recipe_id: 706 for recipe_id in seasonal})
    for recipe_id, place in sorted(recipe_place.items()):
        recipe_season = seasonal.get(recipe_id)
        if recipe_season is not None and season is not None and recipe_season != season:
            continue
        recipe = DIC_ISLAND_RECIPE[recipe_id]
        if len(recipe['commission_product']) != 1:
            raise ValueError(f'制造配方主产物不唯一：{recipe_id}')
        item_id, count = next(iter(recipe['commission_product'].items()))
        if item_id not in ITEM_ID_TO_LOCAL:
            raise ValueError(f'制造产物缺少本地物品键：{item_id}')
        name = ITEM_ID_TO_LOCAL[item_id]
        stem = 'VITRIOL' if name == 'chemicals' else name.upper()
        template = getattr(assets, f'TEMPLATE_{stem}', None)
        # 文件柜图曾被误用为净水滤芯；真实库存图缺失时改用 item ID 的选品库存证据。
        if name == 'filter_element':
            template = None
        entry = {
            'name': name, 'var_name': name, 'id': recipe_id, 'recipe_id': recipe_id,
            'item_id': item_id, 'yield': count, 'ingredients': dict(recipe['commission_cost']),
            'workload': recipe['workload'], 'production_limit': recipe['production_limit'],
            'place': place, 'season': recipe_season,
            'template': template, 'selection': getattr(assets, f'SELECT_{stem}', None),
            'selection_check': getattr(assets, f'SELECT_{stem}_CHECK', None),
            'post_action': getattr(assets, f'POST_{stem}', None),
        }
        catalog[PLACE_TO_CATEGORY[place]].append(entry)
    return catalog
