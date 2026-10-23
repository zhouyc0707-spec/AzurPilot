"""按实例保存明石商店在售行动力的月度最高纪录及完整现场。"""

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

import numpy as np
from PIL import Image

from module.config.time_source import now as current_time
from module.config.utils import SERVER_TO_TIMEZONE
from module.logger import logger
from module.persistence.database import get_database, register_instance
from module.persistence.values import read_value, write_value


FIELD = 'akashi_shop_champion'
_SCREENSHOT = re.compile(r'[0-9a-f]{64}/\d{4}-(?:0[1-9]|1[0-2])/[0-9a-f]{32}\.png')


def _server_timezone(server):
    """使用游戏服务器既有的固定时区。"""
    if server not in SERVER_TO_TIMEZONE:
        raise ValueError('不支持的游戏服务器')
    return timezone(SERVER_TO_TIMEZONE[server])


def current_server_month(server):
    """返回校准时间在游戏服务器所处的自然月。"""
    return current_time(_server_timezone(server)).strftime('%Y-%m')


def _observed_time(server, observed_at):
    stamp = observed_at if observed_at is not None else current_time(_server_timezone(server))
    if not isinstance(stamp, datetime):
        raise TypeError('商店观测时间必须是 datetime')
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=_server_timezone(server))
    return stamp.astimezone(_server_timezone(server))


def _counts(total_ap, boxes):
    """箱子总量须与商品明细完全一致，禁止不完整结果夺取纪录。"""
    if type(total_ap) is not int or total_ap <= 0:
        raise ValueError('行动力总量必须是正整数')
    if type(boxes) is not dict or set(boxes) != {'20', '50', '100'}:
        raise ValueError('行动力箱明细必须包含 20、50、100 三种规格')
    if any(type(count) is not int or count < 0 for count in boxes.values()):
        raise ValueError('行动力箱数量必须是非负整数')
    if sum(int(base) * count for base, count in boxes.items()) != total_ap:
        raise ValueError('行动力总量与箱子明细不一致')
    return dict(boxes)


def _record_key(record):
    stamp = datetime.fromisoformat(record['time'])
    if stamp.tzinfo is None:
        raise ValueError('商店纪录时间缺少时区')
    return record['totalAp'], stamp


class AkashiShopRecords:
    """复用总库扩展字段，仅事务读写本功能的月度元数据。

    Args:
        config_directory: 总库所在的配置目录，默认沿用当前业务上下文。
        screenshot_root: 本功能截图根目录，默认位于安装目录 screenshots 下。
    """

    def __init__(self, config_directory=None, screenshot_root=None):
        self.store = get_database(config_directory)
        self.screenshot_root = Path(
            screenshot_root or self.store.directory.parent / 'screenshots' / 'akashi_shop_records'
        ).absolute()

    @staticmethod
    def _read_field(connection, row):
        if row['mode'] != 'value':
            raise ValueError('商店纪录扩展字段格式无效')
        result = read_value(connection, row['value_set_id'])
        if type(result) is not dict:
            raise ValueError('商店纪录不是服务器映射')
        return result

    @staticmethod
    def _validate_record(record, server, month):
        if type(record) is not dict or record.get('server') != server or record.get('month') != month:
            raise ValueError('商店纪录所属范围不一致')
        _counts(record['totalAp'], record['boxes'])
        _record_key(record)
        if datetime.fromisoformat(record['time']).astimezone(_server_timezone(server)).strftime('%Y-%m') != month:
            raise ValueError('商店纪录时间与月份不一致')
        screenshot = record.get('screenshot')
        if not isinstance(screenshot, str) or not _SCREENSHOT.fullmatch(screenshot):
            raise ValueError('商店截图路径无效')
        if PurePosixPath(screenshot).parts[1] != month:
            raise ValueError('商店截图月份不一致')
        return record

    def records(self, instance, server):
        """读取本月及历月最高纪录；同分选择最新观测，不读取统计明细。"""
        month = current_server_month(server)
        monthly = history = None
        with self.store.transaction(write=False) as connection:
            rows = connection.execute(
                'SELECT month,mode,value_set_id FROM cl1_compat_fields WHERE instance=? AND name=? ORDER BY month',
                (instance, FIELD),
            )
            for row in rows:
                record = self._read_field(connection, row).get(server)
                if record is None:
                    continue
                self._validate_record(record, server, row['month'])
                if row['month'] == month:
                    monthly = record
                if history is None or _record_key(record) >= _record_key(history):
                    history = record
        return {'month': month, 'monthly': monthly, 'history': history}

    def _path(self, relative):
        """只接受受控根内的本功能 UUID 图片，拒绝穿越或链接到根外。"""
        if not isinstance(relative, str) or not _SCREENSHOT.fullmatch(relative):
            raise ValueError('商店截图路径无效')
        root = self.screenshot_root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError('商店截图超出保存目录')
        return path

    def _write_image(self, relative, image):
        """先原子保存原始完整画面，数据库提交后才发布图片引用。"""
        if isinstance(image, Image.Image):
            if image.mode != 'RGB' or image.size != (1280, 720):
                raise ValueError('商店截图必须是 1280×720 RGB 图像')
            picture = image
        elif isinstance(image, np.ndarray) and image.shape == (720, 1280, 3) and image.dtype == np.uint8:
            picture = Image.fromarray(image)
        else:
            raise ValueError('商店截图必须是 1280×720 RGB 图像')
        path = self._path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 创建父目录后重新检查，避免已存在的目录链接将输出导向根外。
        path = self._path(relative)
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('xb') as file:
                picture.save(file, format='PNG')
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _cleanup_replaced_image(self, relative):
        """提交后仅回收不再被任一冠军引用的旧图；清理失败不影响新纪录。"""
        try:
            # 与纪录写入共用总库写锁，避免检查引用与删除之间另一个进程发布引用。
            with self.store.transaction() as connection:
                rows = connection.execute('SELECT mode,value_set_id FROM cl1_compat_fields WHERE name=?', (FIELD,))
                for row in rows:
                    data = self._read_field(connection, row)
                    if any(isinstance(record, dict) and record.get('screenshot') == relative for record in data.values()):
                        return
                self._path(relative).unlink(missing_ok=True)
        except Exception as error:
            logger.warning('[明石商店] 无法清理已替换的猫王截图：%s', type(error).__name__)

    def record(self, instance, server, total_ap, boxes, image, observed_at=None):
        """达到或超过月纪录时保存现场；并发写入始终以总量和观测时间比较。

        原有月度统计完全保留；图片或事务失败传递给调用方，不覆盖旧纪录。
        历史纪录从该实例、该服务器的月冠军计算，不依赖另一个可失配指针。
        """
        stamp = _observed_time(server, observed_at)
        boxes = _counts(total_ap, boxes)
        month = stamp.strftime('%Y-%m')
        relative = f'{hashlib.sha256(instance.encode("utf-8")).hexdigest()}/{month}/{uuid4().hex}.png'
        record = {'totalAp': total_ap, 'boxes': boxes, 'time': stamp.isoformat(),
                  'month': month, 'server': server, 'screenshot': relative}
        published = False
        try:
            with self.store.transaction() as connection:
                row = connection.execute(
                    'SELECT mode,value_set_id FROM cl1_compat_fields WHERE instance=? AND month=? AND name=?',
                    (instance, month, FIELD),
                ).fetchone()
                data = self._read_field(connection, row) if row is not None else {}
                previous = data.get(server)
                if previous is not None:
                    self._validate_record(previous, server, month)
                    if _record_key(record) < _record_key(previous):
                        return False
                self._write_image(relative, image)
                register_instance(connection, instance)
                connection.execute('INSERT OR IGNORE INTO cl1_months(instance,month) VALUES(?,?)', (instance, month))
                data[server] = record
                value_id = write_value(connection, instance, data, month=month)
                connection.execute('''INSERT INTO cl1_compat_fields(instance,month,name,mode,value_set_id)
                    VALUES(?,?,?,'value',?) ON CONFLICT(instance,month,name)
                    DO UPDATE SET mode='value',value_set_id=excluded.value_set_id''', (instance, month, FIELD, value_id))
                if row is not None:
                    connection.execute('DELETE FROM typed_value_sets WHERE id=?', (row['value_set_id'],))
            published = True
        finally:
            if not published:
                self._path(relative).unlink(missing_ok=True)
        if previous is not None:
            self._cleanup_replaced_image(previous['screenshot'])
        return True

    def image(self, record):
        """读取受控范围内的 PNG；不存在或被修改时显式报错。"""
        self._validate_record(record, record['server'], record['month'])
        data = self._path(record['screenshot']).read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('商店截图不是 PNG')
        return data
