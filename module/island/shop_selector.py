"""按真实材料缺口采购，几何源自 ALAS 0078abb848de11d4dffb44fc558e82954f7ec682。"""

import re

import module.config.server as server
from module.base.button import Button, ButtonGrid
from module.base.timer import Timer
from module.base.utils import area_offset, crop
from module.exception import GameStuckError
from module.island.assets import ISLAND_SHOP_CONFIRM, ISLAND_SHOP_GET
from module.island.data import DIC_ISLAND_RECIPE, DIC_ISLAND_SHOP, DIC_ISLAND_SHOP_ITEM_TO_RECIPE, DIC_ISLAND_SHOP_RECIPE
from module.island.manufacture_selector import (
    _normalize_name, _recipe_ids_in_same_category, _selected_recipe_row,
    read_selected_recipe_inventory, select_manufacture_recipe,
)
from module.island_material_shop.assets import (
    ALAS_INFO_GOTO_SHOP, ALAS_MATERIAL_SHOP_CHECK, ALAS_SHOP_BUY_AMOUNT,
    ALAS_SHOP_BUY_AMOUNT_MINUS_ONE, ALAS_SHOP_BUY_AMOUNT_MINUS_TEN,
    ALAS_SHOP_BUY_AMOUNT_PLUS_ONE, ALAS_SHOP_BUY_AMOUNT_PLUS_TEN,
    ALAS_SHOP_BUY_CHECK, ALAS_SHOP_BUY_CONFIRM,
)
from module.logger import logger
from module.ocr.ocr import Ocr
from module.ui.navbar import Navbar


SHOP_GRID = ButtonGrid(origin=(224, 184), delta=(163, 226), button_shape=(147, 201),
                       grid_shape=(6, 2), name='MATERIAL_SHOP_GOODS')
SHOP_NAME_AREA = (12, 142, 134, 163)
COIN_AREA = (1030, 18, 1156, 47)


def _integer(value):
    text = str(value).strip()
    return int(text) if re.fullmatch(r'\d+', text) else None


def material_purchase_plan(recipe_id, item_id, target, stock):
    """仅接受配方中的目标材料和岛屿金币报价，不为未知库存或其他货币采购。"""
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise ValueError(f'材料目标必须是正整数：{target}')
    if stock is None or not isinstance(stock, int) or stock < 0:
        return None
    if item_id not in DIC_ISLAND_RECIPE[recipe_id]['commission_cost']:
        return None
    shop_id = DIC_ISLAND_SHOP_ITEM_TO_RECIPE.get(item_id)
    if shop_id is None:
        return None
    recipe = DIC_ISLAND_SHOP_RECIPE[shop_id]
    if set(recipe['resource_consume']) != {1} or set(recipe['items']) != {item_id}:
        return None
    pack_size = recipe['items'][item_id]
    unit_cost = recipe['resource_consume'][1]
    if pack_size <= 0 or unit_cost <= 0:
        return None
    deficit = max(target - stock, 0)
    count = (deficit + pack_size - 1) // pack_size
    return {'shop_recipe_id': shop_id, 'count': count, 'coin_cost': unit_cost * count,
            'item_id': item_id, 'target': target, 'stock': stock}


def _coin_stock(main):
    image = crop(main.device.image, COIN_AREA)
    return _integer(Ocr([], lang='cnocr', letter=(255, 245, 118), threshold=160,
                        alphabet='0123456789', name='MATERIAL_SHOP_COINS').ocr(image, direct_ocr=True))


def _enter_material_shop(main, recipe_id, item_id):
    materials = list(DIC_ISLAND_RECIPE[recipe_id]['commission_cost'])
    layout = {1: (750, 0), 2: (663, 175), 3: (634, 116)}.get(len(materials))
    if layout is None:
        return False
    x = layout[0] + layout[1] * materials.index(item_id)
    button = Button(area=(x, 474, x + 82, 557), color=(), button=(x, 474, x + 82, 557),
                    name=f'MATERIAL_INFO_{item_id}')
    click_timer = Timer(2, count=4)
    recipe_ids = _recipe_ids_in_same_category(recipe_id)
    for _ in main.loop(skip_first=False, timeout=Timer(25)):
        if main.appear(ALAS_MATERIAL_SHOP_CHECK, offset=(0, 20)):
            return True
        if main.appear_then_click(ALAS_INFO_GOTO_SHOP, offset=(20, 20), interval=2):
            continue
        if _selected_recipe_row(main, recipe_id, recipe_ids) is not None and click_timer.reached():
            main.device.click(button)
            click_timer.reset()
    return False


def _ensure_fish_shop_tab(main, shop_recipe_id):
    """官方鱼苗跳转可能保留错误页签，必须正向选择对应淡水/海水/其他分类。"""
    if not 111101 <= shop_recipe_id < 111300:
        return True
    tab = next((index for index, shop in enumerate((10033, 10034, 10035))
                if shop_recipe_id in DIC_ISLAND_SHOP[shop]['goods']), None)
    if tab is None:
        return False
    navbar = Navbar(ButtonGrid(origin=(184, 82), delta=(176, 0), button_shape=(176, 42),
                              grid_shape=(3, 1), name='MATERIAL_SHOP_FISH_TABS'),
                    active_color=(249, 181, 76), inactive_color=(235, 235, 235),
                    active_threshold=30, inactive_threshold=15, active_count=5000, inactive_count=5000)
    click_timer = Timer(2, count=4)
    for _ in main.loop(timeout=Timer(12)):
        if not main.appear(ALAS_MATERIAL_SHOP_CHECK, offset=(0, 20)):
            continue
        if navbar.get_active(main) == tab:
            return True
        if click_timer.reached():
            main.device.click(navbar.grids.buttons[tab])
            click_timer.reset()
    return False


def _find_shop_card(main, shop_recipe_id):
    expected = _normalize_name(DIC_ISLAND_SHOP_RECIPE[shop_recipe_id]['name'][server.server])
    language = server.server if server.server in ('jp', 'tw') else 'cnocr'
    ocr = Ocr([], lang=language, letter=(66, 66, 66), threshold=200, name='MATERIAL_SHOP_SKU')
    names = ocr.ocr([crop(main.device.image, area_offset(SHOP_NAME_AREA, button.area[:2]))
                     for button in SHOP_GRID.buttons], direct_ocr=True)
    if len(names) != len(SHOP_GRID.buttons):
        return None
    matches = [button for name, button in zip(names, SHOP_GRID.buttons) if _normalize_name(name) == expected]
    return matches[0] if len(matches) == 1 else None


def _open_purchase(main, shop_recipe_id):
    clicked = False
    timer = Timer(2, count=4)
    for _ in main.loop(timeout=Timer(15)):
        if clicked and main.appear(ALAS_SHOP_BUY_CHECK, offset=(20, 20)):
            return True
        if main.appear(ALAS_MATERIAL_SHOP_CHECK, offset=(0, 20)) and timer.reached():
            button = _find_shop_card(main, shop_recipe_id)
            if button is None:
                continue
            main.device.click(button)
            clicked = True
            timer.reset()
    return False


def _set_purchase_quantity(main, count):
    ocr = Ocr(ALAS_SHOP_BUY_AMOUNT, lang='cnocr', letter=(57, 58, 60), threshold=160,
              alphabet='0123456789', name='MATERIAL_SHOP_QUANTITY')
    confirmed = 0
    for _ in main.loop(skip_first=False, timeout=Timer(60)):
        if not main.appear(ALAS_SHOP_BUY_CHECK, offset=(20, 20)):
            confirmed = 0
            continue
        current = _integer(ocr.ocr(main.device.image))
        if current is None or current <= 0:
            confirmed = 0
            continue
        if current == count:
            confirmed += 1
            if confirmed == 2:
                return True
            continue
        confirmed = 0
        if count - current >= 10:
            button = ALAS_SHOP_BUY_AMOUNT_PLUS_TEN
        elif current - count >= 10:
            button = ALAS_SHOP_BUY_AMOUNT_MINUS_TEN
        else:
            button = ALAS_SHOP_BUY_AMOUNT_PLUS_ONE if current < count else ALAS_SHOP_BUY_AMOUNT_MINUS_ONE
        main.appear_then_click(button, offset=(20, 20), interval=2)
    return False


def _confirm_purchase(main, plan, coins_before):
    """只确认一次购买；结果不明时触发恢复，禁止盲目再次扣款。"""
    clicked = False
    for _ in main.loop(skip_first=False, timeout=Timer(20)):
        if main.appear(ISLAND_SHOP_GET, offset=(20, 20)):
            if main.appear_then_click(ISLAND_SHOP_CONFIRM, offset=(20, 20), interval=2):
                continue
        if clicked and main.appear(ALAS_MATERIAL_SHOP_CHECK, offset=(0, 20)):
            coins_after = _coin_stock(main)
            if coins_after == coins_before - plan['coin_cost']:
                return True
        if not clicked and main.appear_then_click(ALAS_SHOP_BUY_CONFIRM, offset=(20, 20), interval=2):
            clicked = True
    if clicked:
        main.device.save_screenshot(genre='island_material_purchase_unknown', interval=0)
        raise GameStuckError('岛屿材料购买未确认金币变化，停止重试并保留现场')
    return False


def ensure_selected_recipe_material(main, recipe_id, item_id, target):
    """补齐指定材料的实际缺口，返回选品页实读库存达到目标后才成功。

    Pages:
        in: 已正向确认配方的选品数量页
        out: 同一配方的选品数量页
    """
    inventory = read_selected_recipe_inventory(main, recipe_id)
    if inventory is None or item_id not in inventory:
        return False
    plan = material_purchase_plan(recipe_id, item_id, target, inventory[item_id]['stock'])
    if plan is None:
        return False
    if plan['count'] == 0:
        return True
    logger.info(f"[岛屿] 材料 {item_id} 缺口 {target - plan['stock']}，采购 {plan['count']} 份，金币 {plan['coin_cost']}")
    success = False
    try:
        if not _enter_material_shop(main, recipe_id, item_id):
            return False
        if not _ensure_fish_shop_tab(main, plan['shop_recipe_id']):
            return False
        coins = _coin_stock(main)
        if coins is None or coins < plan['coin_cost']:
            return False
        if not _open_purchase(main, plan['shop_recipe_id']):
            return False
        if not _set_purchase_quantity(main, plan['count']):
            return False
        success = _confirm_purchase(main, plan, coins)
    finally:
        # 已核实的返回原语可处理获得物品弹窗；后续还需重新确认具体目标配方。
        returned = main.back_to_select_product_after_shop()
    if not success or not returned or not select_manufacture_recipe(main, recipe_id, skip_first_screenshot=False):
        return False
    inventory = read_selected_recipe_inventory(main, recipe_id)
    return inventory is not None and inventory[item_id]['stock'] >= target
