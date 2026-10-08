"""补齐本地原料生产目录，物品、配方与耗材均按官方编号关联。"""

from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RECIPE, DIC_ISLAND_SHOP_ITEM_TO_RECIPE
from module.island.item_ids import ITEM_ID_TO_LOCAL


EXTRA_RAW_RECIPE_IDS = {
    'fishery': (201002, 201003, 201004, 201102, 201103, 201104, 201105, 201106),
    'mine': (401001,),
    'forest': (402001,),
    'nursery': (9900017, 9900018),
}


def get_extra_raw_items(category, season=None):
    """返回需通用选品识别的原料；缺少真实旧模板时明确为 None。

    Args:
        category: 本地生产类别 fishery / mine / forest / nursery。
        season: None 含全部季节，其他季节仅在夏季返回番茄和黄瓜。

    Returns:
        list[dict]: 与本地生产条目兼容的元数据。实际库存必须由选品页
        产物计数或本物品真实图标确认，不能将 None 模板识别成零库存。
    """
    if category not in EXTRA_RAW_RECIPE_IDS:
        raise ValueError(f'未知原料类别：{category}')
    if season not in (None, 'none', 'spring', 'summer', 'autumn', 'winter'):
        raise ValueError(f'未知原料季节：{season}')
    if category == 'nursery' and season not in (None, 'summer'):
        return []
    result = []
    for recipe_id in EXTRA_RAW_RECIPE_IDS[category]:
        recipe = DIC_ISLAND_RECIPE[recipe_id]
        if len(recipe['commission_product']) != 1:
            raise ValueError(f'原料配方产物不唯一：{recipe_id}')
        item_id, amount = next(iter(recipe['commission_product'].items()))
        name = ITEM_ID_TO_LOCAL[item_id]
        ingredients = dict(recipe['commission_cost'])
        if len(ingredients) > 1:
            raise ValueError(f'原料配方耗材不唯一：{recipe_id}')
        material_id = next(iter(ingredients), None)
        entry = {
            'name': name, 'cn_name': DIC_ISLAND_ITEM[item_id]['name']['cn'],
            'var_name': name, 'item_id': item_id, 'id': recipe_id, 'recipe_id': recipe_id,
            'yield': amount, 'ingredients': ingredients, 'category': category,
            'template': None, 'selection': None, 'selection_check': None, 'post_action': None,
            'material_item_id': material_id,
            'shop_recipe_id': DIC_ISLAND_SHOP_ITEM_TO_RECIPE.get(material_id),
            'shop': None, 'workload': recipe['workload'],
            'production_limit': recipe['production_limit'],
            'season': 'summer' if category == 'nursery' else None,
        }
        if category == 'fishery':
            entry.update(tab='freshwater' if material_id < 1200 else 'seawater', buy_max=4)
        result.append(entry)
    return result
