"""指挥喵评分报告读取与管理模块。

将「指挥喵评分」任务产出的 JSON 报告交付给前端渲染，支持只读查询与清理。
报告由 `module/meowfficer/score_task.py` 生成于仓库根目录的 `log/meowfficer_score.json`，
按机器共享一份。
"""

import json
from pathlib import Path

from module.api.protocol import ApiError

REPORT_NAME = 'meowfficer_score.json'
LOCK_STATUSES = {'changed', 'unchanged', 'skipped', 'unconfirmed'}


def _lock_actions(data, limit):
    """只返回最新的有效审计条目，剔除额外字段及不能确认含义的状态。"""
    if not isinstance(data, list):
        raise ApiError('INTERNAL', '评分报告的锁定处理记录结构不正确')
    actions = []
    for action in data:
        if not isinstance(action, dict) or not isinstance(action.get('name'), str) \
                or not isinstance(action.get('reason'), str) \
                or not isinstance(action.get('status'), str) \
                or action.get('status') not in LOCK_STATUSES \
                or any(key not in action or type(action[key]) not in (bool, type(None))
                       for key in ('before', 'after', 'target')):
            continue
        actions.append({key: action[key] for key in ('name', 'before', 'after', 'target', 'status', 'reason')})
    return actions[-limit:]


def report_path(root: Path) -> Path:
    """获取指挥喵评分报告文件的完整路径。

    Args:
        root (Path): 仓库根目录。

    Returns:
        Path: 报告文件路径（log/meowfficer_score.json）。
    """
    return root / 'log' / REPORT_NAME


def report(configs, instance, limit=100):
    """读取指挥喵评分报告内容。

    Args:
        configs: 配置管理服务实例，用于实例白名单校验与仓库根定位。
        instance (str): 实例名称，仅用于校验与回显（报告本身按机器共享）。
        limit (int, optional): 最多返回的猫咪记录数量（取最新的若干只）。默认为 100。

    Returns:
        dict: 包含 instance, generatedAt, count, cats，以及新报告可选 lockActions 的字典。

    Raises:
        ApiError: 报告不存在或内容损坏时抛出业务错误。
    """
    configs.path(instance)
    path = report_path(configs.root)
    if not path.is_file():
        raise ApiError('NOT_FOUND', '评分报告尚未生成，请先在「工具 → 指挥喵评分」运行一次任务')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ApiError('INTERNAL', f'评分报告无法解析：{exc}') from exc
    if not isinstance(data, dict) or not isinstance(data.get('cats'), list):
        raise ApiError('INTERNAL', '评分报告结构不正确')
    count_limit = max(1, min(500, int(limit)))
    cats = [cat for cat in data['cats'] if isinstance(cat, dict)][-count_limit:]
    result = {'instance': instance, 'generatedAt': str(data.get('generatedAt', '')),
              'count': len(cats), 'cats': cats}
    if 'scannedCount' in data:
        scanned_count = data['scannedCount']
        if type(scanned_count) is not int or scanned_count < 0:
            raise ApiError('INTERNAL', '评分报告的已读取数量不正确')
        result['scannedCount'] = scanned_count
    # 老报告不增加字段；同名猫保持各自的一条记录，不按名字合并。
    if 'lockActions' in data:
        result['lockActions'] = _lock_actions(data['lockActions'], count_limit)
    return result


def clear(configs, instance):
    """清空指挥喵评分报告文件。

    联动删除 json / md / html 三份生成产物，面板回到未生成状态。

    Args:
        configs: 配置管理服务实例，用于实例白名单校验与仓库根定位。
        instance (str): 实例名称，仅用于校验。

    Returns:
        dict: 包含 cleared 状态与已删除文件名列表 removed 的字典。

    Raises:
        ApiError: 文件被占用或删除失败时抛出。
    """
    configs.path(instance)
    base = report_path(configs.root)
    removed = []
    for path in (base, base.with_suffix('.md'), base.with_suffix('.html')):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ApiError('INTERNAL', f'评分报告删除失败：{exc}') from exc
        removed.append(path.name)
    return {'cleared': bool(removed), 'removed': removed}
