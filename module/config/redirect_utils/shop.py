"""移除旧商店脚本时暂停对应任务，保留活动商店数量配置。"""

from copy import deepcopy


SHOP_TASKS = ('EventShop', 'ShopFrequent', 'ShopOnce', 'PrivateQuarters', 'OpsiShop', 'OpsiVoucher')


def migrate_shop_options(old, new):
    """转换旧商店配置并返回需要用户复核的任务及原因。

    不修改输入，不执行脚本，也不将原脚本内容写入日志。

    Args:
        old (dict): 迁移前的原始配置。
        new (dict): 已补全默认值的目标配置。

    Returns:
        tuple[dict, list[tuple[str, str]]]: 新配置，以及需复核的任务和原因。
    """
    migrated = deepcopy(new)
    warnings = []
    for task in SHOP_TASKS:
        previous = old.get(task, {})
        if not isinstance(previous, dict):
            continue
        advanced = previous.get('ShopAdvanced', {})
        current = migrated.get(task)
        if not isinstance(current, dict):
            continue
        current.pop('ShopAdvanced', None)
        reasons = []
        if isinstance(advanced, dict) and advanced.get('Mode') == 'advanced':
            reasons.append('商店脚本策略已移除')
        # 本地活动商店仍支持数量上限；读取和迁移不能删除后缀或改变任务开关。
        if reasons:
            current.setdefault('Scheduler', {})['Enable'] = False
            warnings.append((task, '；'.join(reasons)))
    return migrated, warnings
