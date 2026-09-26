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
    # 打上标记：remove_fake_pil_module 据此只摘自己注入的假模块，不动真 PIL
    fake_pil_module.__azurpilot_fake_pil__ = True
    fake_pil_module.Image.__azurpilot_fake_pil__ = True
    sys.modules['PIL'] = fake_pil_module
    sys.modules['PIL.Image'] = fake_pil_module.Image


def _is_fake(module) -> bool:
    """判断 sys.modules 里的条目是不是本模块注入的假 PIL。"""
    return bool(getattr(module, '__azurpilot_fake_pil__', False))


def remove_fake_pil_module():
    """移除假 PIL 注入；**只摘自己注入的**。

    原先无差别 ``sys.modules.pop('PIL')`` 会把真 PIL 一起摘掉：进程里其他地方
    （例如 ``module.base.utils``）已经持有真的 ``PIL.Image`` 引用，父包被摘走后它
    再懒加载图片插件就失败，于是**完好的 PNG 也会抛 UnidentifiedImageError**。
    2026-09-26 排查：全量测试跑过 test_commission_income_record 之后，岛屿/科研
    那批图片用例集体失败，单独运行却全过，就是这里造成的。
    """
    for name in ('PIL', 'PIL.Image'):
        module = sys.modules.get(name)
        if module is not None and _is_fake(module):
            sys.modules.pop(name, None)
