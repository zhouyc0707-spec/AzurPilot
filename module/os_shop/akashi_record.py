"""购买前确认整店货架，独立记录明石猫王，不改变购买过滤。"""
import re

import cv2
import numpy as np

from module.base.utils import color_similar, crop
from module.logger import logger
from module.statistics.akashi_shop_records import AkashiShopRecords


def observation(runner, items):
    """正向确认每格是已识别商品或空模板，返回在售箱数与整店签名。"""
    grid = runner.os_akashi_shop_items
    image = runner.device.image
    if image.shape != (720, 1280, 3):
        return None
    by_area = {tuple(item.area): item for item in items}
    if len(by_area) != len(items):
        return None
    boxes = {'20': 0, '50': 0, '100': 0}
    signature = []
    for button in grid.grids.buttons:
        item = by_area.get(tuple(button.area))
        if item is None:
            # 原识别器会丢弃无货币或无价格的格，缺失本身不能证明已经售罄。
            raw = grid.item_class(image, button)
            color = cv2.mean(crop(raw.image, grid.template_area))[:3]
            empty = any(
                name.startswith('Empty') and color_similar(color, grid.colors[name], threshold=30)
                and np.std(template) > 1
                and cv2.minMaxLoc(cv2.matchTemplate(raw.image, template, cv2.TM_CCOEFF_NORMED))[1] >= .95
                for name, template in grid.templates.items()
            )
            if not empty:
                return None
            signature.append((tuple(button.area), 'Empty'))
            continue
        if (not item.is_known_item() or type(item.amount) is not int or item.amount <= 0
                or item.cost not in ('YellowCoins', 'PurpleCoins', 'EnergyStorage') or item.price <= 0):
            return None
        match = re.fullmatch(r'ActionPoint(20|50|100)', item.name)
        if match:
            if not getattr(item, 'amount_valid', False):
                return None
            boxes[match[1]] += item.amount
        signature.append((tuple(item.area), item.name, item.amount, item.cost, item.price))
    total = sum(int(base) * count for base, count in boxes.items())
    return total, boxes, tuple(signature)


def observe_record(runner, items, can_recheck):
    """可能刷新纪录时请求一次独立新帧；完成或放弃后不影响正常购买。"""
    session = getattr(runner, '_akashi_shop_session', None)
    instance = getattr(getattr(runner, 'config', None), 'config_name', None)
    if (not isinstance(session, dict) or not isinstance(instance, str) or not instance
            or session.get('record_checked') or session.get('purchase_attempted')
            or runner.config.SHOP_EXTRACT_TEMPLATE):
        return False
    try:
        current = observation(runner, items)
        if current is not None:
            total, boxes, signature = current
            store = AkashiShopRecords()
            monthly = store.records(instance, runner.config.SERVER)['monthly']
            if total <= 0 or (monthly and total < monthly['totalAp']):
                session['record_checked'] = True
                return False
            if session.get('record_candidate') == signature:
                # 两次名称、数量和货架布局一致，保存当前未经购买的完整原帧。
                store.record(instance, runner.config.SERVER, total, boxes, runner.device.image.copy())
                session['record_checked'] = True
                return False
            session['record_candidate'] = signature
        else:
            session.pop('record_candidate', None)
        if can_recheck:
            # 清掉本次商品缓存，下一帧必须独立进行OCR，不能复用旧数值凑双帧。
            session.pop('inventory', None)
            return True
        session['record_checked'] = True
        logger.warning('[明石猫王] 购买前货架未能完整稳定确认，本店不刷新纪录')
    except Exception:
        session['record_checked'] = True
        logger.exception('[明石猫王] 纪录保存失败，继续原商店购买流程')
    return False
