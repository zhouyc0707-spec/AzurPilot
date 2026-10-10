"""按统计页的物品类别和月份打开本机耄耋相接截图目录。"""
import re
from datetime import datetime
from pathlib import Path

from module.api import background_service
from module.api.protocol import ApiError


def open_folder(configs, instance: str, item: str, month: str) -> dict:
    """打开已存在的月份归档，缺失时回退到已有分类目录，不创建空文件夹。

    Args:
        configs: 实例配置服务；读取配置不会触发游戏任务或保存配置。
        instance: 已有实例名。
        item: 与统计和截图归类共用的高价值物品分类键。
        month: 页面选中的月份，格式为 YYYY-MM。

    Returns:
        dict: 实际路径、请求月份路径及 month/category/missing 打开结果。

    Raises:
        ApiError: 参数、配置、目录边界或系统文件管理器调用失败。
    """
    configs.path(instance)
    from module.statistics.azurstats import AzurStats

    folders = {key: folder for key, folder, _ in AzurStats.MEOW_LOOT_RULES}
    if not isinstance(item, str) or item not in folders:
        raise ApiError('INVALID_PARAMS', '不支持的耄耋收获物品类别')
    if not isinstance(month, str) or not re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month):
        raise ApiError('INVALID_PARAMS', '截图月份格式应为 YYYY-MM')
    try:
        moment = datetime.strptime(month, '%Y-%m')
    except ValueError as error:
        raise ApiError('INVALID_PARAMS', '截图月份无效') from error

    values, _ = configs.read(instance)
    save_folder = values.get('Alas', {}).get('DropRecord', {}).get('SaveFolder', './screenshots')
    if not isinstance(save_folder, str) or not save_folder.strip():
        raise ApiError('CONFIG_INVALID', '掉落截图保存目录无效，请检查系统设置')

    root = Path(save_folder)
    if not root.is_absolute():
        root = Path(configs.root) / root
    screenshot_root = (root / 'opsi_meowfficer_farming').resolve()
    category_path = (screenshot_root / folders[item]).resolve()
    requested_path = (category_path / f'{moment.year % 100}年{moment.month}月').resolve()
    # 只打开归档树内部的目录，分类或月份链接不能转向其他服务端目录。
    if not category_path.is_relative_to(screenshot_root) or not requested_path.is_relative_to(category_path):
        raise ApiError('INVALID_PARAMS', '截图归档目录无效：分类或月份路径超出保存目录')

    if requested_path.is_dir():
        path, scope = requested_path, 'month'
    elif category_path.is_dir():
        path, scope = category_path, 'category'
    else:
        path, scope = None, 'missing'
    if path is not None:
        try:
            background_service.open_local_directory(path)
        except background_service.BackgroundError as error:
            raise ApiError('FOLDER_OPEN_FAILED', str(error), {'path': str(path)}) from error
    return {
        'opened': path is not None,
        'path': str(path) if path is not None else None,
        'requestedPath': str(requested_path),
        'scope': scope,
        'item': item,
        'month': month,
    }
