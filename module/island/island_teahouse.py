"""岛屿茶馆模块。

继承 IslandShopBase，配置茶馆的商品列表与岗位参数。
包含迎春花茶等固定位置饮品定义，支持季节性菜品、时长 OCR 与重试滑动机制。
"""
from module.island_teahouse.assets import *
from module.island.island_shop_base import IslandShopBase
from module.island.assets import *
from module.ui.page import *

from module.logger import logger
from module.base.button import Button
from module.exception import GameStuckError


# 固定位置按钮 — 迎春花茶/西瓜汁使用槽位1固定坐标，不检测图标颜色，不向下滑动
FIXED_SELECT_SPRING_FLOWER_TEA = Button(
    area=(), color=(), button=(212, 143, 292, 211),
    file={'cn': '', 'en': '', 'jp': '', 'tw': ''}
)

# 秋季高优先级饮品（菊花茶，槽位2）的固定位置——固定第二格（y300 行），
# 与槽位1（胡萝卜秋梨汁，y143 行）不在同一格
FIXED_SELECT_CHRYSANTHEMUM_TEA = Button(
    area=(), color=(), button=(212, 300, 292, 360),
    file={'cn': '', 'en': '', 'jp': '', 'tw': ''}
)


# 季节限定饮品配置（高优先级饮品走固定坐标选择：春/夏为槽位1迎春花茶/西瓜汁，
# 秋季为槽位2菊花茶；其余槽位走常规选品流程）
SEASONAL_DRINK_CONFIG = {
    'spring_flower_tea': {
        'name': 'spring_flower_tea', 'cn_name': '迎春花茶',
        'template': TEMPLATE_WINTER_JASMINE_TEA, 'post_action': POST_WINTER_JASMINE_TEA,
        'selection': FIXED_SELECT_SPRING_FLOWER_TEA, 'selection_check': FIXED_SELECT_SPRING_FLOWER_TEA,
    },
    'pineapple_juice': {
        'name': 'pineapple_juice', 'cn_name': '鲜榨菠萝汁',
        'template': None, 'post_action': None,
        'selection': None, 'selection_check': None, 'generic_recipe': True,
    },
    'carrot_pear_juice': {
        'name': 'carrot_pear_juice', 'cn_name': '胡萝卜秋梨汁',
        'template': TEMPLATE_CARROT_PEAR_JUICE, 'post_action': POST_CARROT_PEAR_JUICE,
        'selection': SELECT_CARROT_PEAR_JUICE, 'selection_check': SELECT_CARROT_PEAR_JUICE_CHECK,
    },
    'chrysanthemum_tea': {
        'name': 'chrysanthemum_tea', 'cn_name': '菊花茶',
        'template': TEMPLATE_CHRYSANTHEMUM_TEA, 'post_action': POST_CHRYSANTHEMUM_TEA,
        'selection': SELECT_CHRYSANTHEMUM_TEA, 'selection_check': SELECT_CHRYSANTHEMUM_TEA_CHECK,
    },
    'watermelon_juice': {
        'name': 'watermelon_juice', 'cn_name': '西瓜汁',
        'template': TEMPLATE_WATERMELON_JUICE, 'post_action': POST_WATERMELON_JUICE,
        'selection': SELECT_WATERMELON_JUICE, 'selection_check': SELECT_WATERMELON_JUICE_CHECK,
    },
    'cucumber_juice': {
        'name': 'cucumber_juice', 'cn_name': '黄瓜汁',
        'template': TEMPLATE_CUCUMBER_JUICE, 'post_action': POST_CUCUMBER_JUICE,
        'selection': SELECT_CUCUMBER_JUICE, 'selection_check': SELECT_CUCUMBER_JUICE_CHECK,
    },
}


class IslandTeahouse(IslandShopBase):
    """岛屿白熊饮品（茶馆）自动化管理器。

    继承 IslandShopBase，管理白熊饮品店的饮品制作、季节限定饮品切换、
    固定坐标选品以及蜂蜜特殊原材料库存约束。

    Attributes:
        shop_type (str): 店铺类型标识 ('teahouse')。
        time_prefix (str): 记录岗位完成时间的变量名前缀 ('time_tea')。
        chef_config (str): 厨师选择配置字符串。
        seasonal_high_priority_drink (dict | None): 当前季节高优先级限定饮品配置。
        fresh_honey (int): 仓库中识别到的新鲜蜂蜜库存。
        shop_items (list): 茶馆商品配置列表。
        meal_compositions (dict): 套餐组成与所需单品配置。
        post_buttons (dict): 岗位按钮映射字典。
        filter_asset (str): 仓库筛选分类标识 ('teahouse')。
    """
    # 季节菜品只在优先阶段生产，余岗仅安排用户配置的常驻餐品。
    FILL_SPECIAL_FOOD = False

    def __init__(self, config, device=None, task=None):
        super().__init__(config=config, device=device, task=task)

        # 设置店铺类型
        self.shop_type = "teahouse"
        self.time_prefix = "time_tea"
        self.chef_config = self.config.IslandTeahouse_ChefFilter
        self.post_open_retry_swipe = True

        # === 初始化全局季节配置 ===
        self._init_season_config()
        old_seasonal_enabled = getattr(self.config, 'IslandTeahouse_Seasonal', False)

        # === 根据季节确定高优先级限定饮品 ===
        self.seasonal_high_priority_drink = None  # 对标迎春花茶/西瓜汁
        seasonal_items = self.season_config.get_seasonal_items('teahouse') if hasattr(self, 'season_config') else []

        if old_seasonal_enabled:
            # 仅当「迎春花茶」开关开启时，才设置高优先级季节饮品
            # 位置1饮品（对标迎春花茶）：高优先级，固定坐标点
            if 'spring_flower_tea' in seasonal_items:
                self.seasonal_high_priority_drink = {
                    'name': 'spring_flower_tea', 'cn_name': '迎春花茶',
                    'template': TEMPLATE_WINTER_JASMINE_TEA, 'post_action': POST_WINTER_JASMINE_TEA,
                    'selection': FIXED_SELECT_SPRING_FLOWER_TEA, 'selection_check': FIXED_SELECT_SPRING_FLOWER_TEA,
                }
            elif 'watermelon_juice' in seasonal_items:
                self.seasonal_high_priority_drink = {
                    'name': 'watermelon_juice', 'cn_name': '西瓜汁',
                    'template': TEMPLATE_WATERMELON_JUICE, 'post_action': POST_WATERMELON_JUICE,
                    'selection': FIXED_SELECT_SPRING_FLOWER_TEA, 'selection_check': FIXED_SELECT_SPRING_FLOWER_TEA,
                }
            elif 'chrysanthemum_tea' in seasonal_items:
                self.seasonal_high_priority_drink = {
                    'name': 'chrysanthemum_tea', 'cn_name': '菊花茶',
                    'template': TEMPLATE_CHRYSANTHEMUM_TEA, 'post_action': POST_CHRYSANTHEMUM_TEA,
                    'selection': FIXED_SELECT_CHRYSANTHEMUM_TEA,
                    'selection_check': FIXED_SELECT_CHRYSANTHEMUM_TEA,
                }

            if self.seasonal_high_priority_drink:
                self.special_food = self.seasonal_high_priority_drink['name']
                logger.info(f"[岛屿-白熊饮品] 季节高优先级饮品: {self.seasonal_high_priority_drink['cn_name']}")
            else:
                self.special_food = 'spring_flower_tea'
        else:
            logger.info("[岛屿-白熊饮品] 季节饮品优先生产已关闭，手工目标按当前季节处理")

        # 设置商品列表
        self.shop_items = []
        # ---- 季节饮品 ----
        if old_seasonal_enabled:
            for item_name in seasonal_items:
                drink = SEASONAL_DRINK_CONFIG.get(item_name)
                if not drink:
                    continue
                item = drink.copy()
                if (self.seasonal_high_priority_drink
                        and item_name == self.seasonal_high_priority_drink['name']):
                    # 高优先级季节饮品使用固定坐标选择（迎春花茶/西瓜汁/菊花茶）
                    item['selection'] = self.seasonal_high_priority_drink['selection']
                    item['selection_check'] = self.seasonal_high_priority_drink['selection_check']
                self.shop_items.append(item)
        # ---- 常规菜品 ----
        self.shop_items.extend([
            {'name': 'apple_juice', 'template': TEMPLATE_APPLE_JUICE, 'var_name': 'apple_juice',
             'selection': SELECT_APPLE_JUICE, 'selection_check': SELECT_APPLE_JUICE_CHECK,
             'post_action': POST_APPLE_JUICE},
            {'name': 'banana_mango', 'template': TEMPLATE_BANANA_MANGO, 'var_name': 'banana_mango',
             'selection': SELECT_BANANA_MANGO, 'selection_check': SELECT_BANANA_MANGO_CHECK,
             'post_action': POST_BANANA_MANGO},
            {'name': 'honey_lemon', 'template': TEMPLATE_HONEY_LEMON, 'var_name': 'honey_lemon',
             'selection': SELECT_HONEY_LEMON, 'selection_check': SELECT_HONEY_LEMON_CHECK,
             'post_action': POST_HONEY_LEMON},
            {'name': 'strawberry_lemon', 'template': TEMPLATE_STRAWBERRY_LEMON, 'var_name': 'strawberry_lemon',
             'selection': SELECT_STRAWBERRY_LEMON, 'selection_check': SELECT_STRAWBERRY_LEMON_CHECK,
             'post_action': POST_STRAWBERRY_LEMON},
            {'name': 'strawberry_honey', 'template': TEMPLATE_STRAWBERRY_HONEY, 'var_name': 'strawberry_honey',
             'selection': SELECT_STRAWBERRY_HONEY, 'selection_check': SELECT_STRAWBERRY_HONEY_CHECK,
             'post_action': POST_STRAWBERRY_HONEY},
            {'name': 'floral_fruity', 'template': TEMPLATE_FLORAL_FRUITY, 'var_name': 'floral_fruity',
             'selection': SELECT_FLORAL_FRUITY, 'selection_check': SELECT_FLORAL_FRUITY_CHECK,
             'post_action': POST_FLORAL_FRUITY},
            {'name': 'fruit_paradise', 'template': TEMPLATE_FRUIT_PARADISE, 'var_name': 'fruit_paradise',
             'selection': SELECT_FRUIT_PARADISE, 'selection_check': SELECT_FRUIT_PARADISE_CHECK,
             'post_action': POST_FRUIT_PARADISE},
            {'name': 'lavender_tea', 'template': TEMPLATE_LAVENDER_TEA, 'var_name': 'lavender_tea',
             'selection': SELECT_LAVENDER_TEA, 'selection_check': SELECT_LAVENDER_TEA_CHECK,
             'post_action': POST_LAVENDER_TEA},
            {'name': 'sunny_honey', 'template': TEMPLATE_SUNNY_HONEY, 'var_name': 'sunny_honey',
             'selection': SELECT_SUNNY_HONEY, 'selection_check': SELECT_SUNNY_HONEY_CHECK,
             'post_action': POST_SUNNY_HONEY},
        ])
        # 设置套餐组成
        self.meal_compositions = {
            'floral_fruity': {
                'required': ['lavender_tea', 'apple_juice'],
                'quantity_per': 1
            },
            'fruit_paradise': {
                'required': ['banana_mango', 'strawberry_honey'],
                'quantity_per': 1
            },
            'sunny_honey': {
                'required': ['strawberry_lemon', 'honey_lemon'],
                'quantity_per': 1
            }
        }

        # 设置岗位按钮
        self.post_buttons = {
            'ISLAND_TEAHOUSE_POST1': ISLAND_TEAHOUSE_POST1,
            'ISLAND_TEAHOUSE_POST2': ISLAND_TEAHOUSE_POST2
        }

        # 设置筛选资产
        self.filter_asset = 'teahouse'

        # 设置配置前缀（更新为4个参数，删除任务相关配置）
        self.setup_config(
            config_meal_prefix="IslandTeahouse_Meal",
            config_number_prefix="IslandTeahouse_MealNumber",
            config_away_cook="IslandTeahouseNextTask_AwayCook",
            config_post_number="IslandTeahouse_PostNumber"
        )

        # 仅在本轮替换跨季目标；无对应槽位时跳过，保留用户保存的配置。
        self._auto_switch_seasonal_meals()

        # 特殊材料：蜂蜜（仅用于库存检查和限制，不再有强制消耗任务）
        self.fresh_honey = 0
        self.initialize_shop()

    def _auto_switch_seasonal_meals(self):
        """复用当前季节同槽映射，只注册本轮手工目标需要的普通选品资源。"""
        if not hasattr(self, 'season_config') or not self.season_config:
            return

        registered_names = {item['name'] for item in self.shop_items}
        seasonal_targets = self.seasonal_stock_targets()
        # 父类要求替代品已有资源。固定位置专用饮品仍仅由优先开关注册。
        for item_name in self.season_config.get_seasonal_items('teahouse'):
            drink = SEASONAL_DRINK_CONFIG.get(item_name)
            if (item_name not in registered_names and drink
                    and (getattr(drink['selection'], 'area', ()) or item_name in seasonal_targets)):
                item = drink.copy()
                if not getattr(item['selection'], 'area', ()):
                    # 新目标不依赖未核实固定位置，复用完整配方名与选中蓝框确认。
                    item.update(selection=None, selection_check=None, generic_recipe=True)
                self.shop_items.append(item)

        self._normalize_seasonal_post_products('teahouse')

        # 库存需求按商品目录生成，未用于手工目标的候选不能额外请求季节原料。
        required_names = registered_names | {name for name, _ in self.post_products} | set(seasonal_targets)
        self.shop_items = [item for item in self.shop_items if item['name'] in required_names]

    def get_warehouse_counts(self):
        """获取仓库饮品及特殊材料（新鲜蜂蜜）的库存数量。

        Returns:
            dict[str, int]: 各饮品及蜂蜜的仓库库存映射字典。
        """
        # 先调用父类方法获取基础库存
        super().get_warehouse_counts()
        if getattr(self, 'island_round', None) is not None:
            return self.warehouse_counts

        # 额外获取蜂蜜数量（用于库存限制）
        self.warehouse_filter('basic','other_from')
        image = self.device.screenshot()
        self.fresh_honey = self.ocr_item_quantity(image, TEMPLATE_FRESH_HONEY)
        logger.info(f"[岛屿-白熊饮品] 蜂蜜数量: {self.fresh_honey}")

        # 将蜂蜜库存存入warehouse_counts，便于统一处理
        self.warehouse_counts['fresh_honey'] = self.fresh_honey

        return self.warehouse_counts

    def check_special_materials(self, product, batch_size):
        """检查特殊材料（蜂蜜）对饮品制作批次的限制。

        蜂蜜只用于制作蜂蜜柠檬水（每 1 个）与草莓蜂蜜冰沙（每 4 个），
        不直接参与阳光蜜水等套餐；套餐原料由父类按套餐组成检查。

        Args:
            product (str): 目标饮品名称。
            batch_size (int): 计划制作批次数。

        Returns:
            int: 考虑蜂蜜库存后允许的最大批次数。
        """
        if batch_size <= 0:
            return 0

        # 蜂蜜柠檬水需要 1 个蜂蜜
        if product == 'honey_lemon':
            max_by_honey = min(batch_size, self.fresh_honey)
            return max_by_honey

        # 草莓蜂蜜冰沙需要 4 个蜂蜜
        if product == 'strawberry_honey':
            max_by_honey = min(batch_size, self.fresh_honey // 4)
            return max_by_honey

        return batch_size

    def _select_round_product(self, product):
        """共享轮仍按原季节优先饮品的固定位置选品。"""
        seasonal = self.seasonal_high_priority_drink
        if seasonal and product == seasonal['name']:
            self.device.click(seasonal['selection'])
            return True, True
        return super()._select_round_product(product)

    def post_produce(self, post_id, product, number, time_var_name, product2=None):
        """安排茶馆岗位生产指定饮品。

        季节高优先级饮品（迎春花茶/西瓜汁等）采用固定坐标点击，其他饮品走基类通用流程。

        Args:
            post_id (str): 岗位标识。
            product (str): 目标饮品名称。
            number (int): 计划生产数量。
            time_var_name (str): 存储完成时间的属性名。
            product2 (str, optional): 备选饮品名称。

        Returns:
            int: 实际安排生产的数量，原料不足返回 0。

        Raises:
            GameStuckError: 派遣流程超时或界面卡死时抛出。
        """
        if getattr(self, 'island_round', None) is not None:
            return super().post_produce(post_id, product, number, time_var_name, product2)
        seasonal_drink_name = self.seasonal_high_priority_drink['name'] if self.seasonal_high_priority_drink else ''
        if product == seasonal_drink_name:
            number = min(number, self.get_product_production_limit(product))
            post_button = self.posts[post_id]['button']
            self.post_close()
            self.post_open(post_button)
            self.device.sleep(0.5)
            # 进入商品选择界面（处理选人 + 选商品）
            for _ in self.loop(timeout=120, skip_first=False):
                if self.appear(ISLAND_SELECT_CHARACTER_CHECK, offset=1):
                    # 选择厨师
                    if self.select_character(character_list=self.chef_config):
                        if not self.confirm_selected_character(f"{product}生产派遣"):
                            self.back_to_postmanage_from_dispatch()
                            return 0
                    else:
                        logger.warning(f"[岛屿-白熊饮品] {self._item_cn(product)}生产派遣无可用角色: {self.chef_config}")
                        self.back_to_postmanage_from_dispatch()
                        return 0
                    continue
                if self.appear(ISLAND_SELECT_PRODUCT_CHECK, offset=1):
                    # 在商品列表界面，点击固定位置，不检测图标
                    self.device.click(self.seasonal_high_priority_drink['selection'])
                    break
                # 点击进入选择
                if self.appear_then_click(ISLAND_POST_SELECT, offset=1):
                    continue
            else:
                raise GameStuckError(f"{self._item_cn(product)}生产派遣流程超时")
            # 检查材料并下单
            if self.produce_check():
                logger.warning(f"[岛屿-白熊饮品] 原料不足，无法生产 {self._item_cn(product)}")
                self.device.click(ISLAND_BACK)
                self.device.sleep(0.5)
                return 0
            else:
                self.post_add_one(number - 1)
                number = self._limit_food_batch(product, number)
                if number <= 0:
                    self.back_to_postmanage_from_dispatch()
                    return 0
                preview, confirmed_at = self.confirm_food_dispatch(
                    number, f'{self._item_cn(product)}生产派遣')
            return self.finish_food_dispatch(
                post_id, product, time_var_name, number, preview, confirmed_at)

        return super().post_produce(post_id, product, number, time_var_name, product2)

    def get_priority_production(self):
        """返回季节高优先级饮品的排产计划。

        Returns:
            dict[str, int]: 优先排产饮品名称到数量的映射。
        """
        if not self.seasonal_high_priority_drink:
            return {}
        name = self.seasonal_high_priority_drink['name']
        return {name: self.get_product_production_limit(name)}

    def deduct_materials(self, product, number):
        """扣除制作饮品消耗的原材料（包括蜂蜜和套餐原材料）。

        Args:
            product (str): 制作的饮品名称。
            number (int): 制作批次数量。
        """
        # 先调用父类方法扣除套餐原材料
        super().deduct_materials(product, number)

        # 蜂蜜柠檬水需要 1 个蜂蜜
        if product == 'honey_lemon':
            deducted = min(number, self.fresh_honey)
            self.fresh_honey = max(0, self.fresh_honey - number)
            if 'fresh_honey' in self.warehouse_counts:
                self.warehouse_counts['fresh_honey'] = self.fresh_honey
            logger.info(f"[岛屿-白熊饮品] 扣除蜂蜜：{self._item_cn('fresh_honey')} -{deducted} (用于制作{self._item_cn('honey_lemon')})")

        # 草莓蜂蜜冰沙需要 4 个蜂蜜
        if product == 'strawberry_honey':
            deducted = min(number * 4, self.fresh_honey)
            self.fresh_honey = max(0, self.fresh_honey - number * 4)
            if 'fresh_honey' in self.warehouse_counts:
                self.warehouse_counts['fresh_honey'] = self.fresh_honey
            logger.info(f"[岛屿-白熊饮品] 扣除蜂蜜：{self._item_cn('fresh_honey')} -{deducted} (用于制作{self._item_cn('strawberry_honey')})")

    def apply_special_material_constraints(self, requirements):
        """根据蜂蜜库存按优先级调整蜂蜜柠檬水与草莓蜂蜜冰沙的需求计划。

        蜂蜜只用于蜂蜜柠檬水（每 1 个）与草莓蜂蜜冰沙（每 4 个）；
        两者按顺序共享蜂蜜：先满足蜂蜜柠檬水，剩余蜂蜜再分配给草莓蜂蜜冰沙。
        阳光蜜水的原料（草莓蜜沁 + 蜂蜜柠檬水）由基类套餐需求分解处理。

        Args:
            requirements (dict[str, int]): 各饮品的原始需求映射。

        Returns:
            dict[str, int]: 调整后的饮品需求映射。
        """
        result = requirements.copy()
        if getattr(self, 'island_round', None) is not None:
            return result
        remaining_honey = self.fresh_honey

        # 处理蜂蜜柠檬水的需求（每 1 个蜂蜜）
        if 'honey_lemon' in result and result['honey_lemon'] > 0:
            honey_lemon_needed = result['honey_lemon']
            max_honey_lemon = min(honey_lemon_needed, remaining_honey)
            if max_honey_lemon < honey_lemon_needed:
                logger.info(f"[岛屿-白熊饮品] 蜂蜜不足：{self._item_cn('honey_lemon')}需求从{honey_lemon_needed}调整为{max_honey_lemon}")
            result['honey_lemon'] = max_honey_lemon
            remaining_honey -= max_honey_lemon

        # 处理草莓蜂蜜冰沙的需求（每 4 个蜂蜜）
        if 'strawberry_honey' in result and result['strawberry_honey'] > 0:
            strawberry_honey_needed = result['strawberry_honey']
            max_strawberry_honey = min(strawberry_honey_needed, remaining_honey // 4)
            if max_strawberry_honey < strawberry_honey_needed:
                logger.info(f"[岛屿-白熊饮品] 蜂蜜不足：{self._item_cn('strawberry_honey')}需求从{strawberry_honey_needed}调整为{max_strawberry_honey}")
            result['strawberry_honey'] = max_strawberry_honey

        return result


if __name__ == "__main__":
    az = IslandTeahouse('alas', task='Alas')
    az.device.screenshot()
    az.run()
