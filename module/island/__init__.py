"""让本地生产链与上游独立工具使用同一份冬季增量数据。"""

from module.island import data as _data
from module.island_planner.winter_extension import apply_winter_extension as _apply_winter_extension


_apply_winter_extension(_data)
