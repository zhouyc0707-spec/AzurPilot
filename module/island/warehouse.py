"""岛屿仓库 OCR 模块。

提供岛屿仓库物品数量的 OCR 识别功能，基于网格布局遍历仓库槽位。
通过模板匹配定位目标物品，再对数量区域进行数字 OCR 读取，返回库存数量。
"""
from module.ocr.ocr import *
from module.base.button import *
from module.ui.ui import *
from module.exception import GameStuckError
from module.ui.assets import ISLAND_WAREHOUSE_CHECK


class WarehouseQuantityDigit(Digit):
    """记录各数量框的有效性，区分数字 0 与空文本的兜底 0。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.region_validity = []

    def after_process(self, result):
        quantity = super().after_process(result)
        self.region_validity.append(self.last_valid)
        return quantity

    def ocr(self, image, direct_ocr=False):
        self.region_validity = []
        return super().ocr(image, direct_ocr=direct_ocr)


class WarehouseOCR:
    """岛屿仓库物品 OCR 识别基类。

    基于网格布局遍历仓库槽位，通过模板匹配定位目标物品，
    并提取对应数字区域进行 OCR 识别，获取物品库存数量。

    Attributes:
        warehouse_grid (ButtonGrid): 仓库槽位网格布局 (6x2)。
        number_area_relative (tuple[int, int, int, int]): 槽位相对于左上角的数字区域坐标偏移。
    """

    def __init__(self, *args, **kwargs):
        """初始化仓库网格和数字区域相对坐标。

        Args:
            *args: 可变位置参数。
            **kwargs: 可变关键字参数。
        """
        self.warehouse_grid = ButtonGrid(
            origin=(301, 150),
            delta=(142, 167),
            button_shape=(104, 110),
            grid_shape=(6, 2),
            name="WAREHOUSE_GRID"
        )
        self.number_area_relative = (45, 90, 99, 110)

    def ocr_item_quantity(self, screenshot, template):
        """在仓库网格中通过模板匹配定位物品并识别其库存数量。

        Args:
            screenshot (np.ndarray): 游戏画面截图。
            template (Template): 目标物品的图标模板。

        Returns:
            int: 物品库存数量，未找到返回 0。
        """
        for _, _, button in self.warehouse_grid.generate():
            cell_image = crop(screenshot, button.area)
            if template.match(cell_image, similarity=0.85):
                number_area = self._get_number_area(button)
                ocr_button = Button(
                    area=number_area,
                    color=(),
                    button=number_area,
                    name="ITEM_NUMBER"
                )
                ocr_instance = Digit(ocr_button,letter = (255, 255, 255), threshold = 200,
                alphabet = '0123456789')
                return ocr_instance.ocr(screenshot)
        return 0

    def ocr_item_quantities(self, screenshot, item_templates):
        """从已确认的同一张仓库截图批量读取物品数量。

        数量框和物品匹配规则沿用单项识别。只复用本次调用中的格子裁图与
        数量定位，不保留截图、库存或跨任务缓存；批量 OCR 异常时逐项降级。

        Args:
            screenshot (np.ndarray): 当前 1280×720 仓库截图。
            item_templates (dict[str, Template]): 物品名称到仓库图标模板的映射。

        Returns:
            dict[str, int]: 当前截图中的库存，未找到的物品仍为 0。

        Raises:
            GameStuckError: 当前画面不是完整仓库页，禁止从其他页面读库存。
        """
        if not item_templates:
            return {}
        if (
            getattr(screenshot, 'shape', None) != (720, 1280, 3)
            or not ISLAND_WAREHOUSE_CHECK.match_template_color(
                screenshot, offset=(30, 30), similarity=0.85, threshold=30
            )
        ):
            raise GameStuckError('仓库页面未确认，停止库存识别')

        cells = [
            (button, crop(screenshot, button.area))
            for _, _, button in self.warehouse_grid.generate()
        ]
        results = dict.fromkeys(item_templates, 0)
        matched = {}
        for name, template in item_templates.items():
            for button, cell_image in cells:
                if template.match(cell_image, similarity=0.85):
                    matched[name] = self._get_number_area(button)
                    break
        if not matched:
            return results

        # 同格可能对应多个兼容名称；合并数量框，避免重复 OCR。
        areas = list(dict.fromkeys(matched.values()))
        buttons = [Button(area=area, color=(), button=area, name='ITEM_NUMBER') for area in areas]
        ocr = WarehouseQuantityDigit(buttons, letter=(255, 255, 255), threshold=200,
                                     alphabet='0123456789', name='WAREHOUSE_ITEM_NUMBERS')
        try:
            quantities = ocr.ocr(screenshot)
            quantities = quantities if isinstance(quantities, list) else [quantities]
            if len(quantities) != len(areas) or any(
                not isinstance(quantity, (int, np.integer))
                or isinstance(quantity, (bool, np.bool_)) or quantity < 0
                for quantity in quantities
            ):
                raise ValueError('仓库批量 OCR 返回数量与区域不一致')
        except (OSError, RuntimeError, ValueError, TypeError, IndexError) as exc:
            logger.warning(f'[岛屿] 仓库批量识别不可用，改用单项识别: {type(exc).__name__}')
            for name in matched:
                results[name] = self.ocr_item_quantity(screenshot, item_templates[name])
            return results

        quantity_by_area = dict(zip(areas, map(int, quantities)))
        validity = ocr.region_validity
        if len(validity) != len(areas):
            validity = [False] * len(areas)
        invalid_areas = {area for area, valid in zip(areas, validity) if not valid}
        results.update({name: quantity_by_area[area] for name, area in matched.items()})
        for name, area in matched.items():
            if area in invalid_areas:
                results[name] = self.ocr_item_quantity(screenshot, item_templates[name])
        return results

    def _get_number_area(self, button):
        """计算指定仓库槽位中物品数量的绝对屏幕区域。

        Args:
            button (Button): 仓库槽位的按钮对象。

        Returns:
            tuple[int, int, int, int]: 物品数量的绝对坐标区域 (x1, y1, x2, y2)。
        """
        x1 = button.area[0] + self.number_area_relative[0]
        y1 = button.area[1] + self.number_area_relative[1]
        x2 = button.area[0] + self.number_area_relative[2]
        y2 = button.area[1] + self.number_area_relative[3]
        return (x1, y1, x2, y2)
