"""大世界明石商店模块。

管理大世界（Operation Siren）明石（Akashi）商店的购买逻辑。
提供各服务器（CN/EN/JP/TW）的物品网格配置差异、商品识别、
购买决策以及购买流程的状态循环控制。
"""
from types import SimpleNamespace
from typing import List
from module.base.button import Button, ButtonGrid
from module.base.decorator import Config, cached_property
from module.logger import logger
from module.os_handler.map_event import MapEventHandler
from module.os_handler.os_status import OSStatus
from module.os_shop.selector import Selector
from module.os_shop.ui import OSShopUI
from module.os_shop.item import OSShopItem as Item, OSShopItemGrid as ItemGrid
from module.os_shop.assets import PORT_SUPPLY_CHECK


class AkashiShop(OSStatus, OSShopUI, Selector, MapEventHandler):
    """大世界明石商店处理器。"""

    @staticmethod
    def _akashi_inventory_snapshot(grid, image):
        """仅记录商品识别区域，逐像素校验本次进店的缓存是否仍有效。"""
        areas = [grid.template_area, grid.amount_area, grid.cost_area,
                 grid.price_area, grid.tag_area, grid.counter_area]
        areas.extend(area for _, area in grid.amount_area_rules)
        buttons = grid.grids.buttons
        layout = (image.shape, tuple((button.area, button.button) for button in buttons),
                  tuple(areas))
        signatures = {}
        for button in buttons:
            x, y, right, bottom = button.area
            regions = [image[y:bottom, x:right]]
            regions.extend(image[y + top:y + end, x + left:x + last]
                           for left, top, last, end in areas)
            signatures[button.area] = tuple((region.shape, region.tobytes()) for region in regions)
        return layout, signatures

    def _akashi_predict_inventory(self):
        """复用当前截图中未改变的格，仅重识别发生变化或尚未识别的格。"""
        grid = self.os_akashi_shop_items
        session = getattr(self, '_akashi_shop_session', None)
        if not isinstance(session, dict) or self.config.SHOP_EXTRACT_TEMPLATE:
            if isinstance(session, dict):
                session.pop('inventory', None)
            grid.predict(self.device.image)
            return grid.items
        if not self.appear(PORT_SUPPLY_CHECK, offset=(20, 20)):
            session.pop('inventory', None)
            grid.items = []
            return []

        layout, signatures = self._akashi_inventory_snapshot(grid, self.device.image)
        cache = session.get('inventory')
        if cache is None or cache['layout'] != layout:
            grid.predict(self.device.image)
            items = {item.area: item for item in grid.items}
        else:
            items = {}
            changed = []
            for button in grid.grids.buttons:
                previous = cache['items'].get(button.area)
                unchanged = signatures[button.area] == cache['signatures'].get(button.area)
                # 未知、空格和识别失败的格仍重识别，避免复用短暂加载或价格误读。
                if unchanged and previous is not None and previous.is_known_item():
                    previous.image_raw = self.device.image
                    items[button.area] = previous
                else:
                    changed.append(button)
            if changed:
                # 原识别器仅依赖 buttons 加载物品；保留其模板和计数器，结束后还原网格。
                original_grids = grid.grids
                try:
                    grid.grids = SimpleNamespace(buttons=changed)
                    grid.predict(self.device.image)
                    items.update((item.area, item) for item in grid.items)
                finally:
                    grid.grids = original_grids
            logger.attr('明石商品复用', len(items) - len(grid.items) if changed else len(items))

        grid.items = [items[button.area] for button in grid.grids.buttons if button.area in items]
        session['inventory'] = {'layout': layout, 'signatures': signatures, 'items': items}
        return grid.items

    @cached_property
    @Config.when(SERVER='tw')
    def os_akashi_shop_items(self) -> ItemGrid:
        """获取明石商店物品网格（台服）。

        Returns:
            ItemGrid: 台服明石商店的物品网格配置。
        """
        shop_grid = ButtonGrid(
            origin=(233, 224), delta=(193, 228), button_shape=(98, 98), grid_shape=(4, 2), name='SHOP_GRID')
        shop_items = ItemGrid(
            shop_grid, templates={}, amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186), price_area=(52, 132, 132, 165)
        )
        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    @cached_property
    @Config.when(SERVER='en')
    def os_akashi_shop_items(self) -> ItemGrid:
        """获取明石商店物品网格（国际服）。

        Returns:
            ItemGrid: 国际服明石商店的物品网格配置。
        """
        shop_grid = ButtonGrid(
            origin=(231, 222), delta=(190, 224), button_shape=(98, 98), grid_shape=(4, 2), name='SHOP_GRID')
        shop_items = ItemGrid(
            shop_grid, templates={}, amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186), price_area=(52, 132, 132, 165)
        )
        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    @cached_property
    @Config.when(SERVER=None)
    def os_akashi_shop_items(self) -> ItemGrid:
        """获取明石商店物品网格（默认/国服）。

        Returns:
            ItemGrid: 默认明石商店的物品网格配置。
        """
        shop_grid = ButtonGrid(
            origin=(233, 224), delta=(193.2, 228), button_shape=(98, 98), grid_shape=(4, 2), name='SHOP_GRID')
        shop_items = ItemGrid(
            shop_grid, templates={}, amount_area=(60, 74, 96, 95),
            counter_area=(85, 170, 134, 186), price_area=(52, 132, 132, 165)
        )
        shop_items.load_template_folder('./assets/shop/os')
        shop_items.load_cost_template_folder('./assets/shop/os_cost')
        return shop_items

    def os_shop_get_items_in_akashi(self) -> List[Item]:
        """识别明石商店中的所有物品。

        通过模板匹配识别当前屏幕上的商店物品，记录每行物品信息。

        Returns:
            list[Item]: 识别到的物品列表，无物品时返回空列表。
        """
        if self.config.SHOP_EXTRACT_TEMPLATE:
            self.os_akashi_shop_items.extract_template(self.device.image, './assets/shop/os')
        self._akashi_predict_inventory()

        items = self.os_akashi_shop_items.items
        if len(items):
            min_row = self.os_akashi_shop_items.grids[0, 0].area[1]
            row = [str(item) for item in items if item.button[1] == min_row]
            logger.info(f'明石商店第 1 行: {row}')
            row = [str(item) for item in items if item.button[1] != min_row]
            logger.info(f'明石商店第 2 行: {row}')
            return items
        else:
            logger.info('未找到明石商店物品')
            return []

    def os_shop_get_item_to_buy_in_akashi(self) -> Item:
        """获取明石商店中待购买的物品。

        获取金币信息后识别商店物品，处理商店加载延迟的情况，
        应用过滤器筛选可购买物品。

        Returns:
            Item: 待购买的物品，无可购买物品时返回 None。
        """
        self.os_shop_get_coins()
        # 初次识别加最多两次重拍；最后一次成功结果也必须进入正常过滤。
        for attempt in range(3):
            if not self.appear(PORT_SUPPLY_CHECK, offset=(20, 20)):
                return None
            items = self.os_shop_get_items_in_akashi()
            if items and all(item.is_known_item() for item in items):
                from module.os_shop.akashi_record import observe_record
                if observe_record(self, items, can_recheck=attempt < 2):
                    self.device.screenshot()
                    continue
                session = getattr(self, '_akashi_shop_session', None)
                if isinstance(session, dict):
                    session['record_checked'] = True
                items = self.items_filter_in_akashi_shop(items)
                return items.pop() if items else None
            if attempt < 2:
                logger.warning('明石商店为空或存在未知物品，正在重新截图确认')
                self.device.screenshot()

        return None
