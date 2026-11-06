"""在已选配方页补读本轮未知库存，不购买或提交生产。"""

from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import resolve_item_id
from module.exception import GameStuckError


def recipe_for_product(product):
    """取得产物唯一对应的生产配方，拒绝猜测未知或歧义配方。"""
    item = resolve_item_id(product)
    recipes = [recipe for recipe, data in DIC_ISLAND_RECIPE.items()
               if item in data['commission_product']]
    if len(recipes) != 1:
        raise ValueError(f'物品 {product} 无法唯一对应生产配方：{recipes}')
    return recipes[0]


def observe_selected_recipe(main, product):
    """确认当前选中配方后，把产物和全部材料真实库存写入本轮账。

    仓库没有物品卡片时保留未知；选品页能显示真实的零库存，因此在
    换季清空后仍允许只读复核，再由调用方判断补料、数量或跳过。

    Returns:
        bool: True 表示完整读到本配方库存，包括真实零值；False 不改账。

    Pages:
        in: 目标产品已选中的岗位配方页。
        out: 同一岗位配方页，未购买、未派遣。
    """
    ledger = getattr(main, 'island_round', None)
    if ledger is None:
        return False
    from module.island.manufacture_selector import read_selected_recipe_inventory

    recipe_id = recipe_for_product(product)
    inventory = read_selected_recipe_inventory(main, recipe_id)
    if inventory is None:
        return False
    for item, data in inventory.items():
        ledger.observe(item, data['stock'])
    return True


def _mill_probe_candidates(runners, missing):
    """只借已经确认空闲的岗位，覆盖磨坊及牧场判断所需缺项。"""
    for main in runners:
        farm_posts = getattr(main, '_round_idle_posts', None)
        if isinstance(farm_posts, dict) and farm_posts.get('farm'):
            post = farm_posts['farm'][0]
            for product in ('wheat', 'corn', 'pasture'):
                if resolve_item_id(product) in missing:
                    yield main, post['button'], product, main.worker_filters['farm']

        states = getattr(main, '_round_ranch_states', {})
        ranch = (('egg', 3000, 'ChickenFilter'), ('pork', 3001, 'PigFilter'),
                 ('milk', 3002, 'RancherFilter'), ('wool', 3003, 'WoolWorkerFilter'))
        for index, (product, feed, worker) in enumerate(ranch, 1):
            post_id = f'ISLAND_RANCH_POST{index}'
            if (feed in missing or resolve_item_id(product) in missing) and states.get(post_id) == 'idle':
                yield main, main.posts_ranch[post_id], product, getattr(main.config, f'IslandRancher_{worker}')

        food_materials = missing & {3004, 2602}
        if not food_materials or not hasattr(main, 'chef_config'):
            continue
        posts = [post for post in getattr(main, 'posts', {}).values()
                 if post.get('status') == 'idle' and post.get('round_enabled', True)]
        if not posts:
            continue
        # 用当前店铺已配置目录中的配方，避免跨产线或猜测尚未解锁的商品。
        for product in getattr(main, 'name_to_config', {}):
            recipe = DIC_ISLAND_RECIPE[recipe_for_product(product)]
            covered = food_materials & set(recipe['commission_cost'])
            if covered:
                yield main, posts[0]['button'], product, main.chef_config
                food_materials.difference_update(covered)
                if not food_materials:
                    break


def _close_probe_product_page(main):
    if not main.back_to_postmanage_from_dispatch():
        raise GameStuckError('本轮库存只读复核后未能返回岗位管理页')


def _open_probe_product_page(main, button, product, characters):
    """进入空岗的选品页，只选角色而不提交生产。"""
    from module.island.assets import POST_MANAGE_PRODUCTION
    from module.island.planned_dispatch import PlannedProductionMixin

    main.goto_postmanage()
    main.post_manage_mode(POST_MANAGE_PRODUCTION)
    main.post_close()
    if not main.post_open(button):
        return False
    stage = PlannedProductionMixin._planned_dispatch_stage(main)
    if stage == 'characters':
        if getattr(main, 'special_character', False):
            selected = main.select_special_character(product)
        else:
            selected = main.select_character(character_list=characters)
        if not selected or not main.confirm_selected_character('本轮库存只读复核'):
            _close_probe_product_page(main)
            return False
        if PlannedProductionMixin._planned_dispatch_stage(main) != 'products':
            _close_probe_product_page(main)
            return False
    return True


def probe_round_missing_stocks(runners, ledger, item_ids):
    """集中盘点后补读磨坊及牧场缺项，避免换季零库存被长期当作未知跳过。

    仅读取本轮已准备好的空岗；没有空岗、可用角色或可靠配方时保留未知。
    未点击购买或生产确认，因此选人不会占用岗位，也不会消耗材料。

    Pages:
        in: 可导航的岛屿页面。
        out: 最后读取的岗位管理页；没有候选时保持原页面。
    """
    from module.island.manufacture_selector import select_manufacture_recipe
    from module.logger import logger

    requested = {resolve_item_id(item) for item in item_ids}
    missing = {item for item in requested if ledger.get(item) is None}
    for main, button, product, characters in _mill_probe_candidates(runners, missing):
        recipe = DIC_ISLAND_RECIPE[recipe_for_product(product)]
        covered = set(recipe['commission_cost']) | set(recipe['commission_product'])
        if not any(ledger.get(item) is None for item in covered & missing):
            continue
        if not _open_probe_product_page(main, button, product, characters):
            continue
        if select_manufacture_recipe(main, recipe_for_product(product)):
            observe_selected_recipe(main, product)
        _close_probe_product_page(main)
        if all(ledger.get(item) is not None for item in missing):
            break
    unresolved = {item for item in requested if ledger.get(item) is None}
    if unresolved:
        logger.warning(f'[岛屿-本轮库存] 磨坊及牧场所需库存尚未可靠读到：{sorted(unresolved)}')
    return unresolved
