"""单轮岛屿任务共用的库存账，只接受已确认的观测和实际变化。"""

from numbers import Integral

from module.island.item_ids import resolve_item_id
from module.logger import logger


def _quantity(value):
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f'岛屿库存数量必须是非负整数：{value!r}')
    return int(value)


def _normalize_changes(changes):
    result = {}
    for key, value in changes.items():
        item = resolve_item_id(key)
        result[item] = result.get(item, 0) + _quantity(value)
    return result


class IslandRoundInventory:
    """本轮现货、在制产物与失败记录；结束后整体丢弃，不持久化。

    库存未知用 None 表示。收获后盘点得到现货，派遣的预计产物只能进入
    in_production；购买、兑换和派遣分别在确认成功后记账。
    """

    def __init__(self, stocks=None):
        self.stocks = {}
        self.in_production = {}
        self.blocked_products = set()
        for key, quantity in (stocks or {}).items():
            self.observe(key, quantity)

    def get(self, key):
        """返回已确认的现货数量；未识别或已失效的数量为 None。"""
        return self.stocks.get(resolve_item_id(key))

    def observe(self, key, quantity):
        """用当前页面确认的总量覆盖旧值；未知不能由历史记录补齐。"""
        item = resolve_item_id(key)
        if quantity is None:
            self.stocks.pop(item, None)
        else:
            self.stocks[item] = _quantity(quantity)

    def consume(self, changes):
        """记录已发生的消耗；余额矛盾时标记未知，不能强行归零。"""
        for item, quantity in _normalize_changes(changes).items():
            current = self.stocks.get(item)
            if current is None:
                continue
            if current < quantity:
                self.stocks.pop(item)
                logger.warning(f'[岛屿-本轮库存] {item} 账面 {current} 小于确认消耗 {quantity}，库存待复核')
            else:
                self.stocks[item] = current - quantity

    def add(self, changes):
        """记录已确认到账的增量；原总量未知时仍需页面确认总量。"""
        for item, quantity in _normalize_changes(changes).items():
            current = self.stocks.get(item)
            if current is not None:
                self.stocks[item] = current + quantity

    def production_started(self, product, quantity):
        """登记派遣后的预计产物，不把尚未收获的产物作为可用材料。"""
        item = resolve_item_id(product)
        self.in_production[item] = self.in_production.get(item, 0) + _quantity(quantity)

    def block(self, product):
        """本轮确认失败的产品不因套餐分解再次进入派遣。"""
        self.blocked_products.add(resolve_item_id(product))

    def is_blocked(self, product):
        return resolve_item_id(product) in self.blocked_products
