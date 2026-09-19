"""
伪造 PIL 模块。

在子进程启动时注入虚拟的 PIL 模块到 sys.modules，避免加载真实的
图像处理库。用于减少进程管理器等非图像处理场景的启动开销。
"""

import os
import sys
from types import ModuleType


def _skip_fake_pil() -> bool:
    """是否跳过假 PIL 注入。

    单元测试进程里后续用例需要真实 PIL（截图解码、统计图表渲染、跨进程截图等），
    而注入发生在导入期，靠个别测试文件自觉调用 ``remove_fake_pil_module()``
    会被 discover 的收集顺序绕过（先收集的用例已经把假 PIL 装进 sys.modules）。
    因此在注入源头判断：跑测试时不注入。生产路径（gui.py 启动 WebUI）不受影响。

    Returns:
        bool: 需要跳过注入时返回 True。
    """
    return 'unittest' in sys.modules or os.environ.get('ALAS_SKIP_FAKE_PIL') == '1'


def import_fake_pil_module():
    if _skip_fake_pil():
        return
    fake_pil_module = ModuleType('PIL')
    fake_pil_module.Image = ModuleType('PIL.Image')
    fake_pil_module.Image.Image = type('MockPILImage', (), dict(__init__=None))
    sys.modules['PIL'] = fake_pil_module
    sys.modules['PIL.Image'] = fake_pil_module.Image


def remove_fake_pil_module():
    sys.modules.pop('PIL', None)
    sys.modules.pop('PIL.Image', None)
