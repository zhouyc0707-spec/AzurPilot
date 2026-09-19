"""兼容转发层：本模块已迁移到 :mod:`module.runtime.setting`。

旧 WebUI（PyWebIO）仍按 ``module.webui.setting`` 导入，这里把属性访问转发到
新位置，保证新旧两套界面共用同一份运行时状态（State、进程管理器等），
避免出现两份互不可见的单例。新代码请直接引用 :mod:`module.runtime.setting`。
"""

import importlib as _importlib

_target = _importlib.import_module("module.runtime.setting")

# 把公开符号注入本模块命名空间，兼容 `from module.webui.setting import X`
for _symbol in dir(_target):
    if not _symbol.startswith("__"):
        globals().setdefault(_symbol, getattr(_target, _symbol))
del _symbol


def __getattr__(name):
    """未显式注入的属性（含私有名）一律转发到新模块。"""
    return getattr(_target, name)
