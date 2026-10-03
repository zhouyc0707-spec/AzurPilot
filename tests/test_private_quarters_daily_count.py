"""私人休息室「每日互动次数」读取的离线回归测试。

背景（2026-09-27 / 09-29 两次实例）：任务在 00:01 先买每周物品，紧接着读左上角的
次数徽章；购买动画/物品提示正好压住徽章，三次重试全读到空 → 0 → 判定
「每日亲密度次数耗尽，退出子任务」，当天互动整天空缺。可 00:43 的截图上是 3/3，
同一天用项目自己的 OCR 读那张图也确认是 3。根因是「徽章没读出来」被当成了「0 次」。

修复后：
1. 次数改在**购买之前**读（`pq_run`）；
2. 校验 `DigitCounter` 的 total 必须为每日精力上限 3：读不到或上限异常就重试；
   连续两次读到有效的 0（徽章显示 0/3）才认定耗尽，读取失败打断连续确认；
   重试后仍未确认（含单次零值）返回 None，调用方不再静默跳过互动。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import module.config.server as server
from module.private_quarters.private_quarters import PrivateQuarters
from module.private_quarters.status import PQStatus


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

    def test_single_valid_zero_among_unreadable_returns_none(self):
        """一次零值出现在读取开头、中间或结尾，都不能确认耗尽。"""
        for zero_index in (0, 1, 3):
            with self.subTest(zero_index=zero_index):
                readings = [(0, False)] * 4
                readings[zero_index] = (0, True)
                stub = self.build(readings)

                self.assertIsNone(stub._pq_get_daily_count(retry=3))

    def test_single_attempt_cannot_confirm_zero(self):
        """禁用重试时只读到一次零值，仍应返回未知。"""
        stub = self.build([(0, True)])

        self.assertIsNone(stub._pq_get_daily_count(retry=0))

    def test_unreadable_breaks_zero_confirmation(self):
        """识别失败隔开的两次零值不能确认耗尽。"""
        stub = self.build([(0, True), (0, False), (0, True), (0, False)])

        self.assertIsNone(stub._pq_get_daily_count(retry=3))

    def test_interrupted_zeros_do_not_hide_remaining_energy(self):
        """重试最后读到 3/3 时，不能被之前不连续的零值提前跳过。"""
        stub = self.build([(0, True), (0, False), (0, True), (3, True)])

        self.assertEqual(stub._pq_get_daily_count(retry=3), 3)

    def test_zero_can_be_confirmed_after_an_invalid_read(self):
        """失败后重新连续读到两次零值，仍能正常确认耗尽。"""
        stub = self.build([(0, True), (0, False), (0, True), (0, True)])

        self.assertEqual(stub._pq_get_daily_count(retry=3), 0)
        self.assertEqual(stub.reads, 4)

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

    def test_pq_run_interacts_after_unconfirmed_zero_reads(self):
        """单次零值或被失败打断的零值，不应让当天互动整天空缺。"""
        cases = [
            [(0, False), (0, True), (0, False), (0, False)],
            [(0, True), (0, False), (0, True), (3, True)],
        ]
        for readings in cases:
            with self.subTest(readings=readings):
                stub = self.build(readings)

                stub.pq_run(buy_roses=True, buy_cake=False, target_interact=True, target_ship='taihou')

                self.assertEqual(stub.interacted, ['taihou'])

    def test_pq_run_interacts_after_raw_ocr_errors(self):
        """通过真实计数解析器复现 031 和一次 0/3 混杂时漏掉互动的问题。"""
        stub = self.build([(0, False)])
        stub.device = SimpleNamespace(image=None)
        stub.status_get_daily_count_detail = PQStatus.status_get_daily_count_detail.__get__(stub)

        with patch('module.ocr.ocr.Ocr.ocr', side_effect=['031', '0/3', '031', '031']) as ocr:
            stub.pq_run(buy_roses=True, buy_cake=False, target_interact=True, target_ship='taihou')

        self.assertEqual(ocr.call_count, 4)
        self.assertEqual(stub.interacted, ['taihou'])


class DailyCountOcrTest(unittest.TestCase):
    def test_energy_badge_requires_the_expected_total(self):
        """每日精力上限固定为 3，斜线缺失或上限误读都应视为未知。"""
        stub = SimpleNamespace(device=SimpleNamespace(image=None))
        cases = [
            ('3/3', (3, True)),
            ('2/3', (2, True)),
            ('0/3', (0, True)),
            ('031', (0, False)),
            ('0/1', (0, False)),
            ('0/31', (0, False)),
        ]
        for text, expected in cases:
            with self.subTest(text=text), patch('module.ocr.ocr.Ocr.ocr', return_value=text):
                self.assertEqual(PQStatus.status_get_daily_count_detail(stub), expected)


if __name__ == '__main__':
    unittest.main()
