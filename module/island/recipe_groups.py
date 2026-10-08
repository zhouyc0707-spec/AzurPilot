"""补齐固定 ALAS 数据只列当季岗位配方的缺口，统一历季配方场所。

场所依据本地已核实的 SEASONAL_ITEMS 与各模块岗位归属；物品使用官方
commission_product 编号精确关联，不凭名称相似度猜分类，也不等同科技解锁。
"""

from module.island.data import DIC_ISLAND_RECIPE
from module.island.island_season import SEASONAL_ITEMS
from module.island.item_ids import LOCAL_TO_ITEM_ID


GROUP_TO_PLACE = {
    'field': 101, 'mine': 401, 'wood': 402, 'orchard': 501, 'nursery': 502,
    'fishery': 201, 'ranch_chicken': 102, 'ranch_pig': 102, 'ranch_cow': 102, 'ranch_sheep': 102,
    'koi': 601, 'bear': 602, 'eatery': 603, 'grill': 604, 'cafe': 901,
    'manufacturing_lumber': 703, 'manufacturing_machinery': 704,
    'manufacturing_electronic': 705, 'manufacturing_crafts': 706,
}
SEASONAL_MODULE_TO_GROUP = {
    'restaurant': 'koi', 'teahouse': 'bear', 'orchard': 'orchard',
    'nursery': 'nursery', 'handmade': 'manufacturing_crafts',
}


def _seasonal_groups():
    groups = {}
    for modules in SEASONAL_ITEMS.values():
        for module, names in modules.items():
            group = SEASONAL_MODULE_TO_GROUP[module]
            for name in names:
                item = LOCAL_TO_ITEM_ID[name]
                matches = [recipe for recipe, data in DIC_ISLAND_RECIPE.items() if item in data['commission_product']]
                if len(matches) != 1:
                    raise ValueError(f'季节物品 {name} 无法唯一关联官方配方：{matches}')
                if matches[0] in groups and groups[matches[0]] != group:
                    raise ValueError(f'季节配方 {matches[0]} 场所归属冲突')
                groups[matches[0]] = group
    return groups


SEASONAL_RECIPE_GROUPS = _seasonal_groups()
