"""明石猫王截图只读接口，不连接设备或启动游戏任务。"""
import base64

from module.config.deep import deep_get
from module.config.server import to_server
from module.logger import logger
from module.statistics.akashi_shop_records import AkashiShopRecords


def report(configs, instance, scope):
    """读取当前实例服务器的本月或历史纪录，按需返回受控截图。"""
    configs.path(instance)
    values, _ = configs.read(instance)
    server = to_server(deep_get(values, 'Alas.Emulator.PackageName', 'cn'))
    store = AkashiShopRecords(configs.directory)
    records = store.records(instance, server)
    selected = records['monthly' if scope == 'monthly' else 'history']
    result = {'instance': instance, 'scope': scope, 'month': records['month'], 'record': None}
    if selected is not None:
        record = {key: value for key, value in selected.items() if key != 'screenshot'}
        try:
            content = store.image(selected)
            record['image'] = 'data:image/png;base64,' + base64.b64encode(content).decode('ascii')
        except (OSError, ValueError):
            logger.warning('[明石猫王] 截图不可读取，保留纪录数值：%s/%s', instance, scope)
            record['image'] = None
            record['imageMissing'] = True
        result['record'] = record
    return result
