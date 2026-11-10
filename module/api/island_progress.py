"""季节生产目标的只读库存快照；不扫描游戏、不修改生产配置。"""

from module.config.deep import deep_get
from module.config.server import to_server
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island.seasonal_task_targets import imported_item_catalog, parse_imported_targets
from module.island.seasonal_targets import SEASONAL_PRODUCTION_ITEMS


def season_progress(configs, instance):
    """把本地库存编号转换为页面目标编号，未知数量保持空值。"""
    from module.island.seasonal_progress import load_progress

    data, _ = configs.read(instance)
    season = deep_get(data, 'IslandPlan.IslandPlan.Season', default='spring')
    result = {'season': season, 'updatedAt': None, 'status': 'missing', 'items': []}
    if season not in SEASONAL_PRODUCTION_ITEMS:
        return result
    catalog = imported_item_catalog()
    names = {name for names in SEASONAL_PRODUCTION_ITEMS[season].values() for name in names}
    selected = {item for item, (name, _) in catalog.items() if name in names}
    imported = deep_get(data, f'IslandSeasonalProduction.IslandSeason{season.capitalize()}.ImportedTargets', '{}')
    selected.update(parse_imported_targets(imported, season=season))
    server = to_server(deep_get(data, 'Alas.Emulator.PackageName', 'auto'))
    snapshot = load_progress(instance, season, server=server, root=configs.root)
    if snapshot:
        result.update(updatedAt=snapshot['updated_at'], status=snapshot['status'])
    records = snapshot['items'] if snapshot else {}
    for item in sorted(selected):
        name, _ = catalog[item]
        record = records.get(str(LOCAL_TO_ITEM_ID[name]), {})
        result['items'].append({
            'itemId': item, 'key': name,
            'stock': record.get('stock'), 'inProduction': record.get('in_production'),
        })
    return result
