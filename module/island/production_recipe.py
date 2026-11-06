"""手工生产复用的配方查找与选人、选品页面导航。"""

from module.base.timer import Timer
from module.exception import GameStuckError
from module.island.assets import ISLAND_POST_SELECT, ISLAND_SELECT_CHARACTER_CHECK, ISLAND_SELECT_PRODUCT_CHECK
from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import LOCAL_TO_ITEM_ID
from module.island_manufacture.assets import ALAS_RECIPE_CHECK


def recipe_for_local_name(name):
    item = LOCAL_TO_ITEM_ID.get(name)
    recipes = [recipe for recipe, data in DIC_ISLAND_RECIPE.items() if item in data['commission_product']]
    if len(recipes) != 1:
        raise ValueError(f'物品 {name} 无法唯一对应生产配方：{recipes}')
    return recipes[0]


class ProductionRecipeMixin:
    """确认岗位选人和选品状态，不计算或接管生产目标。"""

    def _dispatch_stage(self):
        for _ in self.loop(timeout=Timer(25), skip_first=False):
            if self.appear(ISLAND_SELECT_PRODUCT_CHECK, offset=1) or self.appear(ALAS_RECIPE_CHECK, offset=(20, 20)):
                return 'products'
            if self.appear(ISLAND_SELECT_CHARACTER_CHECK, offset=1):
                return 'characters'
            if self.appear_then_click(ISLAND_POST_SELECT, offset=1, interval=2):
                continue
            if self.ui_additional():
                continue
        raise GameStuckError('进入原料派遣页超时')

    def _open_product_page(self, post_id, characters=None, product=None):
        self.post_close()
        if not self.post_open(self.posts[post_id]['button']):
            raise GameStuckError(f'{post_id} 无法打开岗位')
        stage = self._dispatch_stage()
        if stage == 'characters':
            if getattr(self, 'special_character', False) and product is not None:
                selected = self.select_special_character(product)
            else:
                selected = self.select_character(character_list=characters)
            if not selected:
                self.back_to_postmanage_from_dispatch()
                return False
            if not self.confirm_selected_character(f'{post_id}生产派遣'):
                self.back_to_postmanage_from_dispatch()
                return False
            if self._dispatch_stage() != 'products':
                raise GameStuckError(f'{post_id} 选人后未进入选品页')
        return True
