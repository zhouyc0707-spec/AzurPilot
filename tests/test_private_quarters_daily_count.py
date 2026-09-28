"""私人休息室「每日互动次数」读取的离线回归测试。

背景（2026-09-27 / 09-29 两次实例）：任务在 00:01 先买每周物品，紧接着读左上角的
次数徽章；购买动画/物品提示正好压住徽章，三次重试全读到空 → 0 → 判定
「每日亲密度次数耗尽，退出子任务」，当天互动整天空缺。可 00:43 的截图上是 3/3，
同一天用项目自己的 OCR 读那张图也确认是 3。根因是「徽章没读出来」被当成了「0 次」。

修复后：
1. 次数改在**购买之前**读（`pq_run`）；
2. 用 `DigitCounter` 的 total 判断徽章是否真被识别：读不到就重试、不当作 0；
   连续两次读到有效的 0（徽章显示 0/3）才认定耗尽；始终读不到则返回 None，
   调用方不再静默跳过互动。
"""
import unittest

import module.config.server as server
from module.private_quarters.private_quarters import PrivateQuarters


class StubPrivateQuarters:
    """绑定 PrivateQuarters 上被测试的真实方法。"""

    _pq_get_daily_count = PrivateQuarters._pq_get_daily_count
    pq_run = PrivateQuarters.pq_run

    def __init__(self, readings, max_loop=50):
        # readings: 依次返回的 (count, recognized)；用完后重复最后一项
        self.readings = list(readings)
        self.max_loop = max_loop
        self.reads = 0
        self.interacted = []
        self.not_supported_filter = {server.server: ()}
        self.shop_filter = ''
        self.log_lines = []

    # —— 被测方法用到的接口 ——
    def status_get_daily_count_detail(self):
        index = min(self.reads, len(self.readings) - 1)
        self.reads += 1
        return self.readings[index]

    def loop(self, skip_first=True, timeout=None):
        # 有界：等待循环不该真的等 1.5 秒
        for _ in range(3):
            yield None

    def shop_strategy_enabled(self):
        return False

    def pq_execute_interact(self, target_ship):
        self.interacted.append(target_ship)


class DailyCountTest(unittest.TestCase):
    def build(self, readings, **kwargs):
        return StubPrivateQuarters(readings, **kwargs)

    def test_unreadable_badge_is_retried_then_used(self):
        """徽章先被动画遮住、随后可读：应重试并拿到真实次数（修复前会返回 0）。"""
        stub = self.build([(0, False), (0, False), (3, True)])

        self.assertEqual(stub._pq_get_daily_count(retry=3), 3)

    def test_confirmed_zero_needs_two_valid_reads(self):
        """两次读到有效的 0/3 才算耗尽。"""
        stub = self.build([(0, True), (0, True)])

        self.assertEqual(stub._pq_get_daily_count(retry=3), 0)

    def test_single_valid_zero_then_nonzero_returns_nonzero(self):
        """先读到 0/3、再读到 3/3：以非零为准，不算耗尽。"""
        stub = self.build([(0, True), (3, True)])

        self.assertEqual(stub._pq_get_daily_count(retry=3), 3)

    def test_never_readable_returns_none(self):
        """一直读不到徽章：返回 None（未知），而不是 0。"""
        stub = self.build([(0, False)])

        self.assertIsNone(stub._pq_get_daily_count(retry=3))

    def test_pq_run_interacts_when_count_unknown(self):
        """次数未知时不再跳过互动（真没次数时互动流程自己会退出）。"""
        stub = self.build([(0, False)])

        stub.pq_run(buy_roses=True, buy_cake=False, target_interact=True, target_ship='taihou')

        self.assertEqual(stub.interacted, ['taihou'])

    def test_pq_run_skips_when_exhausted(self):
        """确认耗尽（有效的 0）时仍应跳过互动。"""
        stub = self.build([(0, True)])

        stub.pq_run(buy_roses=True, buy_cake=False, target_interact=True, target_ship='taihou')

        self.assertEqual(stub.interacted, [])


if __name__ == '__main__':
    unittest.main()
