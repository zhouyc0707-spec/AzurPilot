"""
私人休息室状态 OCR 识别。

提供私人宿舍商店的货币余额和互动次数的 OCR 读取能力，
包括金币、钻石和每日互动剩余次数。
通过服务器分支适配不同区域的 OCR 参数（字体颜色差异）。

Pages: in: PRIVATE_QUARTERS_SHOP, PRIVATE_QUARTERS_MAIN
"""
import module.config.server as server
from module.ocr.ocr import Digit, DigitCounter
from module.private_quarters.assets import *
from module.shop.shop_status import ShopStatus

if server.server in ['cn', 'jp', 'tw']:
    OCR_DAILY_COUNT = DigitCounter(PRIVATE_QUARTERS_DAILY_COUNT, letter=(218, 219, 221))
else:
    OCR_DAILY_COUNT = DigitCounter(PRIVATE_QUARTERS_DAILY_COUNT, letter=(255, 247, 247))

if server.server != 'jp':
    OCR_SHOP_GOLD_COINS = Digit(PRIVATE_QUARTERS_SHOP_GOLD_COINS, letter=(239, 239, 239), name='OCR_SHOP_GOLD_COINS')
else:
    OCR_SHOP_GOLD_COINS = Digit(PRIVATE_QUARTERS_SHOP_GOLD_COINS, letter=(201, 201, 201), name='OCR_SHOP_GOLD_COINS')

if server.server != 'jp':
    OCR_SHOP_GEMS = Digit(PRIVATE_QUARTERS_SHOP_GEMS, letter=(255, 243, 82), name='OCR_SHOP_GEMS')
else:
    OCR_SHOP_GEMS = Digit(PRIVATE_QUARTERS_SHOP_GEMS, letter=(190, 180, 82), name='OCR_SHOP_GEMS')

OCR_SHOP_PRICE = Digit([], letter=(64, 72, 77), name='OCR_SHOP_PRICE')


class PQStatus(ShopStatus):
    """私人休息室货币与互动次数状态识别器。"""

    def status_get_gold_coins(self):
        """OCR 识别商店金币数量。

        Returns:
            int: 金币数量。

        Pages:
            in: 私人宿舍商店页
        """
        amount = OCR_SHOP_GOLD_COINS.ocr(self.device.image)
        return amount

    def status_get_gems(self):
        """OCR 识别商店钻石数量。

        Returns:
            int: 钻石数量。

        Pages:
            in: 私人宿舍商店页
        """
        amount = OCR_SHOP_GEMS.ocr(self.device.image)
        return amount

    def status_get_daily_count(self):
        """OCR 识别每日互动剩余次数。

        Returns:
            int: 剩余互动次数。

        Pages:
            in: 私人宿舍主页
        """
        count, _ = self.status_get_daily_count_detail()
        return count

    def status_get_daily_count_detail(self):
        """OCR 识别每日互动剩余次数，并告知徽章是否真的被识别到。

        `DigitCounter.ocr()` 返回 `(current, remain, total)`：徽章正常显示时
        `total` 是上限（如 3），而**完全没读到文字**时三项都是 0。因此可以用
        `total > 0` 区分「徽章显示 0（真的用完）」与「徽章没读出来（被动画遮挡、
        页面还没就绪）」—— 后者当成 0 会让整天的互动被静默跳过
        （2026-09-27/29 实例：徽章实际是 3/3 却读到 0）。

        Returns:
            tuple[int, bool]: (剩余互动次数, 徽章是否被识别到)。

        Pages:
            in: 私人宿舍主页
        """
        count, remain, total = OCR_DAILY_COUNT.ocr(self.device.image)
        return count, total > 0
