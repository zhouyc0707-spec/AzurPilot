"""岛屿制造工坊模块。

继承 IslandShopBase，实现制造工坊的产品配置与岗位管理。
关闭规划时使用原手工目录；有效规划通过完整配方目录与真实名称确认执行制造链。
保留原角色选择、荠菜按钮、岗位及时间记录，临时工坊只在季节委托成品入库后关闭。
"""
from module.island.island import *
from module.island_manufacture.assets import *
from module.island.island_shop_base import IslandShopBase
from module.island.planner_report import invalidate_planner_stocks, record_planner_dispatch
from module.island.assets import *
from module.ui.page import *
from datetime import timedelta

from module.config.time_source import now as current_time
from module.logger import logger
from module.base.button import Button


# 固定位置按钮 — 在产品选择界面不滑动时，荠菜的固定位置
FIXED_SELECT_SHEPHERD_PURSE = Button(
    area=(), color=(), button=(224, 151, 274, 209),
    file={'cn': '', 'en': '', 'jp': '', 'tw': ''}
)


# 季节限定手工产品配置（按 SEASONAL_ITEMS['handmade'] 的键引用）
# 此表仅限定关闭规划时的原手工生产目录。规划使用 manufacture_catalog 完整目录，
# 包括茉莉精油、秋季花束等已解锁且在活动期限内的配方。
SEASONAL_HANDMADE_ITEMS = {
    'shepherd_purse': {
        'name': 'shepherd_purse', 'template': TEMPLATE_SHEPHERD_PURSE,
        'var_name': 'shepherd_purse', 'selection': FIXED_SELECT_SHEPHERD_PURSE,
        'selection_check': FIXED_SELECT_SHEPHERD_PURSE, 'post_action': POST_SHEPHERD_PURSE,
    },
    'summer_bouquet': {
        'name': 'summer_bouquet', 'template': TEMPLATE_SUMMER_BOUQUET,
        'var_name': 'summer_bouquet', 'selection': SELECT_SUMMER_BOUQUET,
        'selection_check': SELECT_SUMMER_BOUQUET_CHECK, 'post_action': POST_SUMMER_BOUQUET,
    },
}


class IslandManufacture(IslandShopBase):
    """岛屿制造工坊自动化管理器。

    继承 IslandShopBase，管理木料加工、电子加工、工业生产和手工制作四大区域的自动化排产。

    Attributes:
        shop_type (str): 店铺类型标识。
        time_prefix (str): 岗位完成时间前缀。
        post_manage_swipe_count (int): 岗位管理界面滑动次数。
        filter_asset (str): 仓库筛选分类。
        manufacture (dict): 制造业各分类的产品配置。
        post_buttons (dict): 岗位按钮映射。
        shop_items (list): 展平的所有产品配置列表。
        unavailable_products (set): 当前批次已确认材料不足的产品集合。
    """
    POST_PRODUCE_LIMIT = 12

    def __init__(self, *args, **kwargs):
        # 先初始化基类
        IslandShopBase.__init__(self, *args, **kwargs)

        # 设置店铺类型
        self.shop_type = "manufacture"
        self.time_prefix = "time_manufacture"

        # 设置滑动配置（岗位管理界面需要两次滑动）
        self.post_manage_swipe_count = 2

        # === 初始化全局季节配置 ===
        self._init_season_config()

        # 设置筛选资产
        self.filter_asset = 'factory'

        # 制造业产品配置
        self.manufacture = {
            'wood_processing': {
                'items': [
                    {'name': 'file_cabinet', 'template': TEMPLATE_FILE_CABINET,
                     'var_name': 'file_cabinet', 'selection': SELECT_FILE_CABINET,
                     'selection_check': SELECT_FILE_CABINET_CHECK, 'post_action': POST_FILE_CABINET},
                ]
            },
            #TEMPLATE_FILTER_ELEMENT 未添加
            'electronic_processing': {
                'items': [
                    {'name': 'filter_element', 'template': TEMPLATE_FILE_CABINET,
                     'var_name': 'filter_element', 'selection': SELECT_FILTER_ELEMENT,
                     'selection_check': SELECT_FILTER_ELEMENT_CHECK, 'post_action': POST_FILTER_ELEMENT},
                ]
            },
            'industrial_production': {
                'items': [
                    {'name': 'iron_nail', 'template': TEMPLATE_IRON_NAIL,
                     'var_name': 'iron_nail', 'selection': SELECT_IRON_NAIL,
                     'selection_check': SELECT_IRON_NAIL_CHECK, 'post_action': POST_IRON_NAIL},
                    {'name': 'cutlery', 'template': TEMPLATE_CUTLERY,
                     'var_name': 'cutlery', 'selection': SELECT_CUTLERY,
                     'selection_check': SELECT_CUTLERY_CHECK, 'post_action': POST_CUTLERY},
                ]
            },
            'handmade': {
                'items': [
                    {'name': 'leather', 'template': TEMPLATE_LEATHER,
                     'var_name': 'leather', 'selection': SELECT_LEATHER,
                     'selection_check': SELECT_LEATHER_CHECK, 'post_action': POST_LEATHER},
                    {'name': 'boot', 'template': TEMPLATE_BOOT,
                     'var_name': 'boot', 'selection': SELECT_BOOT,
                     'selection_check': SELECT_BOOT_CHECK, 'post_action': POST_BOOT},
                    {'name': 'peanut_oil', 'template': TEMPLATE_PEANUT_OIL,
                     'var_name': 'peanut_oil', 'selection': SELECT_PEANUT_OIL,
                     'selection_check': SELECT_PEANUT_OIL_CHECK, 'post_action': POST_PEANUT_OIL},
                ]
            }
        }
        # 季节限定：手工类产品（荠菜干、秋季花束等，按当前季节配置）
        if hasattr(self, 'season_config') and self.season_config.is_seasonal_enabled:
            for item_name in (self.season_config.get_seasonal_items('handmade') or []):
                item_config = SEASONAL_HANDMADE_ITEMS.get(item_name)
                if not item_config:
                    continue
                if any(it['name'] == item_name for it in self.manufacture['handmade']['items']):
                    # 花生油等基础产品已常驻，无需重复添加
                    continue
                self.manufacture['handmade']['items'].append(item_config.copy())
                logger.info(f"[岛屿-制造业] 季节限定：{self._item_cn(item_name)} 已添加到手工产品列表")

        # 根据配置初始化岗位按钮
        self.post_buttons = self._init_post_buttons()

        # 将所有产品展平到一个列表中，供基类使用
        self.shop_items = []
        for category in self.manufacture.values():
            self.shop_items.extend(category['items'])

        # 初始化需求列表（制造业不需要外部配置的需求）
        self.post_products = []

        # 设置配置（使用4个参数，删除任务相关配置）
        self.setup_config(
            config_meal_prefix="IslandManufacture_Meal",
            config_number_prefix="IslandManufacture_MealNumber",
            config_away_cook="IslandManufactureNextTask_AwayCook",
            config_post_number="IslandManufacture_PostNumber"
        )

        # 初始化店铺
        self.initialize_shop()

        # 本批生产已确认材料不足的物品（同一批内后续岗位直接跳过）
        self.unavailable_products = set()
        self._legacy_manufacture = self.manufacture
        # 错用文件柜模板会把未知滤芯库存当成另一商品，必须留给选品页实读。
        for item in self._legacy_manufacture['electronic_processing']['items']:
            if item['name'] == 'filter_element':
                item['template'] = None
        self._unknown_working_categories = set()
        self._manufacture_scheduled = {}

    @staticmethod
    def _post_category(post_id):
        if 'WOOD_PROCESSING' in post_id:
            return 'wood_processing'
        if 'ELECTRONIC_PROCESSING' in post_id:
            return 'electronic_processing'
        if 'INDUSTRIAL' in post_id:
            return 'industrial_production'
        return 'handmade'

    def _use_planned_catalog(self, enabled):
        """计划使用完整配方；关闭后恢复原有固定生产目录和角色逻辑。"""
        if enabled:
            from module.island.manufacture_catalog import get_catalog
            from module.island.production_planner import get_planned_recipe_items
            groups = {'wood_processing': 'manufacturing_lumber', 'industrial_production': 'manufacturing_machinery',
                      'electronic_processing': 'manufacturing_electronic', 'handmade': 'manufacturing_crafts'}
            self.manufacture = {category: {'items': get_planned_recipe_items(self.config, groups[category], items)}
                                for category, items in get_catalog().items()}
            # 花生油常驻等本地原件不删除；游戏当前不可见时选品会明确失败。
            for category, data in self._legacy_manufacture.items():
                existing = {item['name'] for item in self.manufacture[category]['items']}
                for item in data['items']:
                    if item['name'] not in existing:
                        self.manufacture[category]['items'].append(item)
        else:
            self.manufacture = self._legacy_manufacture
        self.shop_items = [item for category in self.manufacture.values() for item in category['items']]
        self.name_to_config = {item['name']: item for item in self.shop_items}

    def post_product_check(self):
        """没有工作详情模板的产品保持未知，不能冒用其他产物或把在制品当零。"""
        self._last_manufacture_product = None
        for item in self.shop_items:
            if item.get('post_action') is not None and self.appear(item['post_action']):
                self._last_manufacture_product = item['name']
                return item['name']
        return None

    def post_check(self, post_id, time_var_name):
        self._last_manufacture_product = None
        super().post_check(post_id, time_var_name)
        if self.posts[post_id]['status'] == 'working' and getattr(self, '_last_manufacture_product', None) is None:
            category = self._post_category(post_id)
            self._unknown_working_categories.add(category)
            logger.info(f'[岛屿-制造业] {category} 存在未知在制品，收取前暂停该类别追加派遣')

    def get_warehouse_counts(self):
        """真实旧模板先读；缺图产品保持未知，选品时按物品编号读取现货。"""
        self.warehouse_filter(self.filter_asset)
        image = self.device.screenshot()
        templates = {item['name']: item['template'] for item in self.shop_items if item.get('template') is not None}
        self.warehouse_counts = self.ocr_item_quantities(image, templates) if templates else {}
        return self.warehouse_counts

    def _prepare_planned_recipe_page(self, post_id):
        """选择岗位和原有角色，正向确认选品页后交给独立识别阶段。"""
        from module.island.planned_dispatch import PlannedProductionMixin
        return PlannedProductionMixin._planned_open_product_page(self, post_id)

    def _planned_dispatch_stage(self):
        from module.island.planned_dispatch import PlannedProductionMixin
        return PlannedProductionMixin._planned_dispatch_stage(self)

    def _observe_final_manufacture_stock(self):
        """关闭临时工坊前，逐个成品读真实现货；不因缺中间品先派出多余任务。"""
        from module.island.manufacture_selector import read_selected_recipe_inventory, select_manufacture_recipe
        from module.island.production_planner import read_config
        from module.island.item_ids import ITEM_ID_TO_LOCAL
        import json
        if not read_config(self.config, 'AutoManufactureActive', False):
            return
        if any(post['status'] == 'working' for post in self.posts.values()):
            return
        finals = json.loads(read_config(self.config, 'OrderManufactureFinalTargets', '{}'))
        for category, data in self.manufacture.items():
            idle = self.get_idle_posts_by_category(category)
            if not idle:
                continue
            for item in data['items']:
                if str(item.get('item_id')) not in finals or not item.get('recipe_id'):
                    continue
                if not self._prepare_planned_recipe_page(idle[0]):
                    continue
                selected = select_manufacture_recipe(self, item['recipe_id'])
                inventory = read_selected_recipe_inventory(self, item['recipe_id']) if selected else None
                self.back_to_postmanage_from_dispatch()
                if inventory is None:
                    raise GameStuckError(f'关闭临时工坊前成品现货无法确认：{item["name"]}')
                for item_id, observation in inventory.items():
                    if item_id in ITEM_ID_TO_LOCAL:
                        self.warehouse_counts[ITEM_ID_TO_LOCAL[item_id]] = observation['stock']

    def _dispatch_planned_manufacture(self, post_id, item, target):
        """同帧实读库存与原料，按产出率与可用材料选批次，确认成功后才记账。"""
        from module.island.item_ids import ITEM_ID_TO_LOCAL
        from module.island.manufacture_selector import (
            read_selected_recipe_inventory, select_manufacture_recipe, set_manufacture_quantity,
        )
        if not self._prepare_planned_recipe_page(post_id):
            return 0
        if not select_manufacture_recipe(self, item['recipe_id']):
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'制造配方无法识别：{item["name"]}')
        inventory = read_selected_recipe_inventory(self, item['recipe_id'])
        if inventory is None:
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'制造配方库存或原料无法可靠读取：{item["name"]}')
        for item_id, data in inventory.items():
            if item_id in ITEM_ID_TO_LOCAL:
                self.warehouse_counts[ITEM_ID_TO_LOCAL[item_id]] = data['stock']
        name = item['name']
        total = (inventory[item['item_id']]['stock'] + self.post_check_meal.get(name, 0)
                 + self._manufacture_scheduled.get(name, 0))
        missing = max(target - total, 0)
        batches = min((missing + item['yield'] - 1) // item['yield'], item['production_limit'])
        for item_id, data in inventory.items():
            if data['cost'] <= 0:
                continue
            material = ITEM_ID_TO_LOCAL.get(item_id)
            protected = self._planner_protection.get(material, 0)
            batches = min(batches, max(data['stock'] - protected, 0) // data['cost'])
        if batches <= 0:
            self.back_to_postmanage_from_dispatch()
            return 0
        if not set_manufacture_quantity(self, batches):
            self.back_to_postmanage_from_dispatch()
            raise GameStuckError(f'制造次数无法正向确认：{name}')
        preview, confirmed_at = self.confirm_food_dispatch(batches, f'{self._item_cn(name)}制造派遣')
        actual_batches = self.finish_food_dispatch(
            post_id, name, self._post_time_vars[post_id], batches, preview, confirmed_at)
        quantity = actual_batches * item['yield']
        self._manufacture_scheduled[name] = self._manufacture_scheduled.get(name, 0) + quantity
        if actual_batches > 0:
            record_planner_dispatch(self.config, {item['item_id']: quantity}, '工坊派遣确认')
            invalidate_planner_stocks(self.config, item['ingredients'], '工坊派遣用料后')
        for material_id, cost in item['ingredients'].items():
            material = ITEM_ID_TO_LOCAL.get(material_id)
            if material in self.warehouse_counts:
                self.warehouse_counts[material] = max(0, self.warehouse_counts[material] - actual_batches * cost)
        return quantity

    def schedule_planned_manufacture(self):
        from module.island.production_planner import load_planner_targets, planner_idle_products, read_config
        targets = load_planner_targets(self.config)
        temporary = read_config(self.config, 'AutoManufactureActive', False)
        for category, data in self.manufacture.items():
            if category in self._unknown_working_categories:
                continue
            idle = self.get_idle_posts_by_category(category)
            for post_id in idle:
                products = [(item, False) for item in data['items']
                            if item.get('recipe_id') and targets.get(item['item_id'], 0) > 0]
                if not temporary:
                    current = {name: stock + self._manufacture_scheduled.get(name, 0)
                               for name, stock in self.warehouse_counts.items()}
                    filler = planner_idle_products(self.config, [item['name'] for item in data['items']], current)
                    products += [(item, True) for name in (filler or []) for item in data['items']
                                 if item['name'] == name and item.get('recipe_id')]
                for item, filler in products:
                    target = targets.get(item['item_id'], 0)
                    if filler:
                        target = (self.warehouse_counts.get(item['name'], 0) + self._manufacture_scheduled.get(item['name'], 0)
                                  + self.post_check_meal.get(item['name'], 0) + item['yield'] * item['production_limit'])
                    if self._dispatch_planned_manufacture(post_id, item, target) > 0:
                        break

    def _finish_temporary_manufacture(self):
        from module.island.item_ids import LOCAL_TO_ITEM_ID
        from module.island.production_planner import finish_auto_manufacture
        stocks = {LOCAL_TO_ITEM_ID[name]: count for name, count in self.warehouse_counts.items()
                  if name in LOCAL_TO_ITEM_ID}
        return finish_auto_manufacture(
            self.config, stocks, working=any(post['status'] == 'working' for post in self.posts.values()))

    def _init_post_buttons(self):
        """根据配置初始化启用的制造业岗位按钮。

        Returns:
            dict[str, Button]: 岗位标识到按钮资源的映射字典。
        """
        post_buttons = {}
        if self.config.WoodProcessing_Positions >= 1:
            post_buttons['ISLAND_WOOD_PROCESSING_POST1'] = ISLAND_WOOD_PROCESSING_POST1
        if self.config.WoodProcessing_Positions >= 2:
            post_buttons['ISLAND_WOOD_PROCESSING_POST2'] = ISLAND_WOOD_PROCESSING_POST2

        if self.config.ElectronicProcessing_Positions >= 1:
            post_buttons['ISLAND_ELECTRONIC_PROCESSING_POST1'] = ISLAND_ELECTRONIC_PROCESSING_POST1
        if self.config.ElectronicProcessing_Positions >= 2:
            post_buttons['ISLAND_ELECTRONIC_PROCESSING_POST2'] = ISLAND_ELECTRONIC_PROCESSING_POST2

        if self.config.Industrial_Positions >= 1:
            post_buttons['ISLAND_INDUSTRIAL_POST1'] = ISLAND_INDUSTRIAL_POST1
        if self.config.Industrial_Positions >= 2:
            post_buttons['ISLAND_INDUSTRIAL_POST2'] = ISLAND_INDUSTRIAL_POST2

        if self.config.Handmade_Positions >= 1:
            post_buttons['ISLAND_HANDMADE_POST1'] = ISLAND_HANDMADE_POST1
        if self.config.Handmade_Positions >= 2:
            post_buttons['ISLAND_HANDMADE_POST2'] = ISLAND_HANDMADE_POST2

        return post_buttons

    def get_idle_posts_by_category(self, category):
        """获取指定制造类别的空闲岗位 ID 列表。

        Args:
            category (str): 制造类别（'wood_processing'、'electronic_processing'、'industrial_production'、'handmade'）。

        Returns:
            list[str]: 属于该类别的空闲岗位 ID 列表。
        """
        category_posts = []
        if category == 'wood_processing':
            category_posts = ['ISLAND_WOOD_PROCESSING_POST1', 'ISLAND_WOOD_PROCESSING_POST2']
        elif category == 'electronic_processing':
            category_posts = ['ISLAND_ELECTRONIC_PROCESSING_POST1', 'ISLAND_ELECTRONIC_PROCESSING_POST2']
        elif category == 'industrial_production':
            category_posts = ['ISLAND_INDUSTRIAL_POST1', 'ISLAND_INDUSTRIAL_POST2']
        elif category == 'handmade':
            category_posts = ['ISLAND_HANDMADE_POST1', 'ISLAND_HANDMADE_POST2']

        # 只返回实际存在的空闲岗位
        return [post_id for post_id in category_posts
                if post_id in self.posts and self.posts[post_id]['status'] == 'idle']

    def select_product(self, product_selection, product_selection_check):
        """选择制造产品。

        荠菜使用固定坐标点击；其他产品调用父类模板匹配与滑动搜索逻辑。

        Args:
            product_selection (Button): 产品选择按钮或坐标。
            product_selection_check (Button): 产品选择确认检测按钮。

        Returns:
            bool: 是否成功选中目标产品。
        """
        # 荠菜 → 直接点击固定位置
        if product_selection == FIXED_SELECT_SHEPHERD_PURSE:
            self.device.click(FIXED_SELECT_SHEPHERD_PURSE)
            self.device.sleep(0.5)
            return True

        # 其他产品使用父类逻辑（模板匹配 + 向下滑动查找）
        return super().select_product(product_selection, product_selection_check)

    def select_product_with_material_check(self, post_id, product_list):
        """选择产品并检查材料是否充足。

        按候选列表顺序尝试进入岗位选择；若材料不足则记忆并退出重置滑动，尝试下一个产品。

        Args:
            post_id (str): 目标岗位标识。
            product_list (list[dict]): 待尝试的产品配置列表。

        Returns:
            dict | None: 成功安排生产的产品配置，全部失败则返回 None。
        """
        post_button = self.posts[post_id]['button']

        for product_info in product_list:
            product_name = product_info['name']
            selection = product_info['selection']
            selection_check = product_info['selection_check']

            # 同一批内前面岗位已确认材料不足的物品，直接跳过
            if product_name in self.unavailable_products:
                logger.info(f"[岛屿-制造业] {self._item_cn(product_name)} 本批已确认材料不足，直接跳过")
                continue

            # 每个产品：进入岗位搜索并选择；找不到则退出岗位重进重试
            for attempt in range(self.PRODUCT_SELECT_RETRY_LIMIT):
                # 打开岗位（首次或重进）
                self.post_close()
                self.post_open(post_button)
                self.device.sleep(0.5)

                entered_product_page = False
                while True:
                    self.device.screenshot()
                    if self.appear_then_click(ISLAND_POST_SELECT, offset=1):
                        self.device.sleep(0.5)
                        continue
                    if self.appear(ISLAND_SELECT_CHARACTER_CHECK, offset=1):
                        if self.select_character():
                            if not self.confirm_selected_character(f"{post_id}制造派遣"):
                                self.back_to_postmanage_from_dispatch()
                                return None
                        else:
                            logger.warning(f"[岛屿-制造业] {post_id}制造派遣无可用角色")
                            self.back_to_postmanage_from_dispatch()
                            return None
                        continue
                    if self.appear(ISLAND_SELECT_PRODUCT_CHECK, offset=1):
                        entered_product_page = True
                        logger.info(f"[岛屿-制造业] 尝试选择产品: {self._item_cn(product_name)}")
                        selected = self.select_product(selection, selection_check)
                        self.device.sleep(0.5)
                        break

                if not entered_product_page:
                    return None

                if not selected:
                    # 搜索失败：退出岗位重新进入（重置列表滚动进度），重试同一产品
                    logger.warning(
                        f"[岛屿-制造业] 未能识别到产品选择项: {self._item_cn(product_name)}，"
                        f"退出岗位重进重试 ({attempt + 1}/{self.PRODUCT_SELECT_RETRY_LIMIT})"
                    )
                    self.device.click(SELECT_UI_BACK)
                    self.device.sleep(0.3)
                    self.wait_until_appear(ISLAND_POSTMANAGE_CHECK)
                    self.device.sleep(0.5)
                    self.post_close()
                    for _ in range(self.post_manage_swipe_count):
                        self.post_manage_up_swipe(450)
                    continue  # 下一轮重进重试同一产品

                # 检查确认按钮状态
                image = self.device.screenshot()
                color = get_color(image, (493, 597, 621, 643))

                # 如果确认按钮是灰色（153, 156, 156），表示材料不足
                if color_similar(color, (153, 156, 156), 80):
                    # 记忆本批材料不足的物品，后续岗位不再重复尝试
                    self.unavailable_products.add(product_name)
                    logger.info(f"[岛屿-制造业] 材料不足，跳过产品: {self._item_cn(product_name)}")
                    # 退出岗位，下一个产品重新进入时列表滚动进度已重置
                    self.device.click(SELECT_UI_BACK)
                    self.device.sleep(0.3)
                    self.wait_until_appear(ISLAND_POSTMANAGE_CHECK)
                    self.device.sleep(0.5)
                    self.post_close()
                    for _ in range(self.post_manage_swipe_count):
                        self.post_manage_up_swipe(450)
                    break  # 跳出重试循环 -> 下一个产品

                # 材料充足，派遣生产
                self.appear_then_click(POST_MAX)
                self.device.click(POST_ADD_ORDER)
                logger.info(f"[岛屿-制造业] 选择产品成功: {self._item_cn(product_name)}")
                self.wait_until_appear(ISLAND_POSTMANAGE_CHECK)
                self.device.sleep(0.5)
                self.post_close()
                for _ in range(self.post_manage_swipe_count):
                    self.post_manage_up_swipe(450)

                # 获取生产时间和数量
                self.post_open(post_button)
                image = self.device.screenshot()
                ocr_post_number = Digit(OCR_POST_NUMBER, letter=(57, 58, 60), threshold=100,
                                        alphabet='0123456789')
                actual_number = ocr_post_number.ocr(image)
                time_work = Duration(ISLAND_WORKING_TIME)
                time_value = time_work.ocr(self.device.image)
                finish_time = current_time() + time_value

                # 设置时间变量
                import re
                match = re.search(r'POST(\d+)', post_id)
                if match:
                    post_num = match.group(1)
                    time_var_name = f'{self.time_prefix}{post_num}'
                    setattr(self, time_var_name, finish_time)

                self.posts[post_id]['status'] = 'working'
                logger.info(f"[岛屿-制造业] 已安排生产：{self._item_cn(product_name)} x{actual_number}")
                self.post_close()
                return product_info

        # 所有产品都无法选择或材料不足
        logger.info("[岛屿-制造业] 所有产品都无法选择或材料不足")
        return None

    def schedule_manufacture(self):
        """安排制造业四大门类的生产排期。"""
        self.schedule_wood_processing()

        self.schedule_electronic_processing()

        self.schedule_industrial_production()

        self.schedule_handmade()

    def schedule_wood_processing(self):
        """安排木料加工生产（生产文件柜）。"""
        idle_posts = self.get_idle_posts_by_category('wood_processing')
        if not idle_posts:
            return
        # 木料加工只生产file_cabinet
        product_list = self.manufacture['wood_processing']['items']
        for post_id in idle_posts:
            self.select_product_with_material_check(post_id, product_list)

    def schedule_electronic_processing(self):
        """安排电子加工生产（生产滤芯）。"""
        idle_posts = self.get_idle_posts_by_category('electronic_processing')
        if not idle_posts:
            return
        # 木料加工只生产file_cabinet
        product_list = self.manufacture['electronic_processing']['items']
        for post_id in idle_posts:
            self.select_product_with_material_check(post_id, product_list)

    def schedule_industrial_production(self):
        """安排工业生产（根据铁钉库存切换铁钉或餐具生产）。"""
        idle_posts = self.get_idle_posts_by_category('industrial_production')
        if not idle_posts:
            return
        # 检查库存iron_nail
        iron_nail_stock = self.warehouse_counts.get('iron_nail', 0)
        # 根据规则选择产品
        if iron_nail_stock >= 20:
            product_list = [item for item in self.manufacture['industrial_production']['items']
                            if item['name'] == 'cutlery']
        else:
            product_list = [item for item in self.manufacture['industrial_production']['items']
                            if item['name'] == 'iron_nail']

        for post_id in idle_posts:
            self.select_product_with_material_check(post_id, product_list)

    def schedule_handmade(self):
        """安排手工制品生产（优先季节限定品、皮靴与皮革）。"""
        idle_posts = self.get_idle_posts_by_category('handmade')
        if not idle_posts:
            return
        # 检查库存leather
        leather_stock = self.warehouse_counts.get('leather', 0)
        # 构建产品选择列表（按优先级）
        product_list = []
        # 优先生产当前季节的限定手工品（荠菜干、夏季花束等）
        seasonal_names = []
        if hasattr(self, 'season_config') and self.season_config.is_seasonal_enabled:
            seasonal_names = self.season_config.get_seasonal_items('handmade') or []
        for item in self.manufacture['handmade']['items']:
            if item['name'] in seasonal_names:
                product_list.append(item)

        # 如果leather库存>=10，则生产boot
        if leather_stock >= 10:
            boot_item = [item for item in self.manufacture['handmade']['items']
                         if item['name'] == 'boot'][0]
            product_list.append(boot_item)

        # 最后生产leather
        leather_item = [item for item in self.manufacture['handmade']['items']
                        if item['name'] == 'leather'][0]
        product_list.append(leather_item)

        for post_id in idle_posts:
            self.select_product_with_material_check(post_id, product_list)

    def run(self):
        """运行制造业工坊自动化主流程。

        巡检各岗位状态、收取成品、读取原料库存并为各类别空闲岗位分配生产，
        最后计算并推迟调度时间。

        Raises:
            GameBugError: 遇到游戏内部错误需要重启时抛出。
        """
        self.island_error = False
        from module.island.production_planner import load_production_protection, refresh_production_plan
        planned = refresh_production_plan(self.config, self.device)
        self._use_planned_catalog(planned)
        self._planner_protection = load_production_protection(self.config)
        self._unknown_working_categories.clear()
        self._manufacture_scheduled.clear()
        self.post_check_meal.clear()
        self._post_time_vars = {}
        # 每批生产开始时清空“材料不足”记忆，避免跨批沿用旧库存状态
        self.unavailable_products = set()

        # 第一步：检查岗位状态
        self.goto_postmanage()
        self.post_manage_mode(POST_MANAGE_PRODUCTION)
        self.post_close()

        # 滑动以看到岗位
        for _ in range(self.post_manage_swipe_count):
            self.post_manage_up_swipe(450)

        # 检查岗位状态
        time_vars = []
        post_index = 1

        # 按顺序检查所有岗位
        for post_id in self.post_buttons.keys():
            time_var_name = f'{self.time_prefix}{post_index}'
            time_vars.append(time_var_name)
            setattr(self, time_var_name, None)
            self._post_time_vars[post_id] = time_var_name
            self.post_check(post_id, time_var_name)
            post_index += 1


        # 判断是否有需要安排的任务
        idle_posts = self.get_idle_posts()
        if idle_posts:
            self.get_warehouse_counts()
            # 如果有空闲岗位，重新进入岗位管理界面安排生产
            logger.info(f"[岛屿-制造业] 有 {len(idle_posts)} 个空闲岗位，开始安排生产")

            # 重新进入岗位管理界面
            self.goto_postmanage()
            self.post_manage_mode(POST_MANAGE_PRODUCTION)
            self.post_close()

            # 滑动以看到岗位
            for _ in range(self.post_manage_swipe_count):
                self.post_manage_up_swipe(450)

            # 安排生产
            if planned:
                self._observe_final_manufacture_stock()
                if self._finish_temporary_manufacture():
                    return
                self.schedule_planned_manufacture()
            else:
                self.schedule_manufacture()
        else:
            logger.info("[岛屿-制造业] 没有空闲岗位，跳过生产安排")

        if self._finish_temporary_manufacture():
            return

        # 设置任务延迟
        finish_times = []
        for var in time_vars:
            time_value = getattr(self, var)
            if time_value is not None:
                finish_times.append(time_value)
        hours_later = current_time() + timedelta(hours=6)
        finish_times.append(hours_later)
        finish_times.sort()
        self.config.task_delay(target=finish_times)

        if self.island_error:
            from module.exception import GameBugError
            raise GameBugError("检测到岛屿ERROR1，需要重启")

    # 以下方法重写以适配基类
    def process_meal_requirements(self, source_products):
        """处理套餐需求。

        制造业无需拆解套餐，直接透传输入需求。

        Args:
            source_products (dict[str, int]): 输入需求。

        Returns:
            dict[str, int]: 原始需求。
        """
        return source_products

    def schedule_production(self):
        """安排生产排期，调用制造业专属调度方法。"""
        self.schedule_manufacture()

    def process_away_cook(self):
        """处理常驻餐品模式，制造业无需常驻配置。"""
        # 制造业有自己的生产规则，不依赖常驻餐品
        self.to_post_products = {}
        logger.info("[岛屿-制造业] 制造业使用内置生产规则，不设置常驻餐品")

    def get_max_producible(self, product, requested_quantity, skip_zero_materials=False):
        """获取产品最大可生产数量。

        制造业生产数量由派遣界面材料检测决定。

        Args:
            product (str): 产品名称。
            requested_quantity (int): 请求数量。
            skip_zero_materials (bool, optional): 是否跳过零材料检查。

        Returns:
            int: 允许生产的数量。
        """
        return requested_quantity

    def check_special_materials(self, product, batch_size):
        """检查特殊材料限制，制造业无特殊材料直接返回原批次数。

        Args:
            product (str): 产品名称。
            batch_size (int): 计划批次数。

        Returns:
            int: 允许生产的批次数。
        """
        return batch_size

    def apply_special_material_constraints(self, requirements):
        """应用特殊材料约束，制造业无特殊限制直接返回。

        Args:
            requirements (dict[str, int]): 原始需求映射。

        Returns:
            dict[str, int]: 调整后需求映射。
        """
        return requirements

    def test(self):
        """测试制造业配置状态。"""
        if self.config.Industrial_Positions > 1:
            logger.info(2)


if __name__ == "__main__":
    az = IslandManufacture('alas', task='Alas')
    az.device.screenshot()
    az.run()
