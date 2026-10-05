"""设备 ID 管理模块。

基于不可逆的硬件指纹哈希生成匿名的设备唯一标识符，用于本地配置区分与遥测统计。
"""
import hashlib
import json
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from module.logger import logger

def _wmic_query(wmic_class: str, field: str) -> str:
    """通过 WMIC 查询 Windows 硬件信息。
    
    Args:
        wmic_class (str): WMI 类名（例如 'baseboard', 'cpu'）。
        field (str): 要查询的字段名。
        
    Returns:
        str: 查询结果字符串，失败返回空字符串。
    """
    try:
        result = subprocess.run(
            ['wmic', wmic_class, 'get', field],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
        )
        lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        if len(lines) >= 2:
            return lines[1]
    except Exception:
        pass
    return ''

def _collect_hardware_fingerprint() -> str:
    """收集本机硬件指纹字符串。

    Returns:
        str: 拼接后的硬件特征指纹。
    """
    parts = []
    
    if platform.system() == 'Windows':
        hw_queries = [
            ('baseboard', 'serialnumber'),
            ('cpu', 'processorid'),
            ('bios', 'serialnumber'),
            ('diskdrive', 'serialnumber'),
        ]
        for cls, field in hw_queries:
            val = _wmic_query(cls, field)
            if val and val.lower() not in ('to be filled by o.e.m.', 'default string', 'none', ''):
                parts.append(f'{cls}.{field}={val}')
    else:
        for mid_path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                mid = Path(mid_path).read_text(encoding='utf-8').strip()
                if mid:
                    parts.append(f'machine-id={mid}')
                    break
            except Exception:
                pass

        if platform.system() == 'Darwin':
            try:
                result = subprocess.run(
                    ['system_profiler', 'SPHardwareDataType'],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.splitlines():
                    if 'Hardware UUID' in line:
                        parts.append(f'hw-uuid={line.split(":")[-1].strip()}')
                        break
            except Exception:
                pass

    # 已完全舍弃 MAC 地址依赖
    parts.append(f'platform={platform.node()}-{platform.machine()}')
    
    return '|'.join(parts)


def generate_device_id() -> str:
    """基于硬件指纹生成唯一设备 ID。

    Returns:
        str: 32 位 SHA-256 哈希设备标识符。
    """
    fingerprint = _collect_hardware_fingerprint()
    # 使用 SHA-256 哈希处理硬件指纹，生成不可逆的匿名设备标识符
    device_id = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:32]
    return device_id


_device_id: Optional[str] = None
_old_device_id: Optional[str] = None # 用于记录迁移前的旧 ID
_refresh_timer: Optional[threading.Timer] = None
_REFRESH_INTERVAL = 300
_init_lock = threading.Lock()


def _device_id_file() -> Path:
    """设备ID 缓存文件路径（项目根 log/device_id.json）。"""
    return Path(__file__).resolve().parents[2] / 'log' / 'device_id.json'


def _read_stored_device_id(device_id_file: Path) -> Optional[str]:
    """读取缓存文件里已登记的设备ID，失败或缺失返回 None。"""
    try:
        with device_id_file.open('r', encoding='utf-8') as f:
            stored_id = json.load(f).get('device_id')
    except Exception:
        return None
    return stored_id or None


def get_device_id() -> str:
    """获取当前设备 ID。

    Returns:
        str: 32 位设备标识符。
    """
    global _device_id
    if _device_id is None:
        with _init_lock:
            if _device_id is None:
                _device_id = _init_device_id()
    return _device_id


def get_old_device_id() -> Optional[str]:
    """获取迁移前的旧 ID。

    Returns:
        str | None: 迁移前的旧设备 ID，若无变更则为 None。
    """
    global _old_device_id
    return _old_device_id


def _verify_device_id_in_background(stored_id: Optional[str], device_id_file: Path) -> None:
    """后台核对硬件指纹，与缓存不一致时改判并触发数据库迁移。

    设备ID 是 opsi_items 等表的归属键（查询都带 device_id 条件），换掉就会读不到
    历史数据，所以这里**只做核对、不擅自改写**：指纹一致时只刷新时间戳，不一致时
    才把缓存值记为旧 ID 供迁移使用。
    """
    global _device_id, _old_device_id
    try:
        generated_id = generate_device_id()
    except Exception as exc:
        logger.warning(f'[设备-ID] 后台指纹核对失败，沿用已登记的设备ID: {exc}')
        _start_refresh_timer(stored_id or '', device_id_file)
        return

    if stored_id and generated_id == stored_id:
        logger.info(f'设备ID 已确认: {generated_id[:8]}...')
        _start_refresh_timer(stored_id, device_id_file)
        return

    if stored_id:
        _old_device_id = stored_id
        logger.info(
            f'设备ID change detected for migration! '
            f'Old: {stored_id[:8]}, New: {generated_id[:8]}'
        )
    _overwrite_device_id(generated_id, device_id_file)
    with _init_lock:
        _device_id = generated_id
    logger.info(f'设备ID initialized: {generated_id[:8]}...')
    _start_refresh_timer(generated_id, device_id_file)


def _init_device_id() -> str:
    """初始化设备 ID 并检查旧 ID 迁移。

    Returns:
        str: 初始化后的设备 ID。
    """
    global _old_device_id
    device_id_file = _device_id_file()

    # 已登记过设备ID：直接沿用，把昂贵的硬件指纹采集（4 次 wmic 子进程，实测
    # 0.5~1.1 s）挪到后台线程。否则这份开销会卡在首个 WebUI 页面渲染里 ——
    # 会话建立的 _block_restricted_device() 会调用本函数，实测让首屏多等 860 ms。
    stored_id = _read_stored_device_id(device_id_file)
    if stored_id:
        threading.Thread(
            target=_verify_device_id_in_background,
            args=(stored_id, device_id_file),
            daemon=True,
            name='device-id-verify',
        ).start()
        return stored_id

    # 没有缓存文件（全新安装、或仅启动了 WebUI 还没跑过 alas）：
    # 此时必须同步生成，否则设备ID 会是空的。
    device_id = generate_device_id()
    # 自动识别变更并暂存旧 ID 用于数据库热迁移（device_id_file 本函数开头已算好）
    if device_id_file.exists():
        try:
            with device_id_file.open('r', encoding='utf-8') as f:
                stored_id = json.load(f).get('device_id')
                if stored_id and stored_id != device_id:
                    _old_device_id = stored_id
                    logger.info(f'检测到设备ID变更并用于迁移，旧ID: {stored_id[:8]}，新ID: {device_id[:8]}')
        except Exception:
            pass

    _overwrite_device_id(device_id, device_id_file)

    _start_refresh_timer(device_id, device_id_file)

    return device_id


def _overwrite_device_id(device_id: str, file_path: Path):
    """将设备 ID 写入本地日志文件。

    Args:
        device_id (str): 32 位设备 ID。
        file_path (Path): 目标 json 文件路径。
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'device_id': device_id,
            '_generated_by': 'hardware_fingerprint_v2_no_mac',
            '_last_refresh': time.strftime('%Y-%m-%d %H:%M:%S'),
            '_warning': 'This file is auto-generated and overwritten every 5 minutes.'
        }
        with file_path.open('w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f'[设备-ID] 覆盖设备ID文件失败: {e}')


def _refresh_callback(device_id: str, file_path: Path):
    """定时器回调：定期刷新设备 ID 文件。

    Args:
        device_id (str): 设备 ID。
        file_path (Path): 目标 json 文件路径。
    """
    _overwrite_device_id(device_id, file_path)
    _start_refresh_timer(device_id, file_path)


def _start_refresh_timer(device_id: str, file_path: Path):
    """启动设备 ID 文件的定期刷新后台定时器。

    Args:
        device_id (str): 设备 ID。
        file_path (Path): 目标 json 文件路径。
    """
    global _refresh_timer
    if _refresh_timer is not None:
        _refresh_timer.cancel()
    _refresh_timer = threading.Timer(_REFRESH_INTERVAL, _refresh_callback, args=(device_id, file_path))
    _refresh_timer.daemon = True
    _refresh_timer.start()
