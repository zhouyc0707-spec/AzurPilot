"""保留手工生产和订单识别使用的冬季物品编号。"""

from module.island import data as _data
from module.island.winter_extension import apply_winter_extension as _apply_winter_extension


_apply_winter_extension(_data)
