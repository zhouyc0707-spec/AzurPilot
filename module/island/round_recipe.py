"""从生产选品页读取本轮成品与材料库存，不购买或提交生产。"""

from collections import Counter

from module.base.timer import Timer
from module.base.utils import area_offset, crop
from module.island.data import DIC_ISLAND_RECIPE
from module.island.item_ids import resolve_item_id
from module.exception import GameStuckError
from module.logger import logger
from module.ocr.ocr import Ocr


def recipe_for_product(product):
    """取得产物唯一对应的生产配方，拒绝猜测未知或歧义配方。"""
    item = resolve_item_id(product)
    recipes = [recipe for recipe, data in DIC_ISLAND_RECIPE.items()
               if item in data['commission_product']]
    if len(recipes) != 1:
        raise ValueError(f'物品 {product} 无法唯一对应生产配方：{recipes}')
    return recipes[0]


def _read_recipe_stock_page(main, recipe_ids):
    """同帧识别完整卡片名称，并批量读取各卡片现货数字。"""
    from module.island.manufacture_selector import (
        RECIPE_PRODUCT_STOCK_AREA, _parse_product_stock, _read_recipe_rows,
    )

    rows = _read_recipe_rows(main, recipe_ids)
    counts = Counter(recipe for recipe, _ in rows if recipe is not None)
    matched = [(recipe, button) for recipe, button in rows if counts[recipe] == 1]
    if not matched:
        return rows, {}
    images = [crop(main.device.image, area_offset(RECIPE_PRODUCT_STOCK_AREA, button.area[:2]))
              for _, button in matched]
    texts = Ocr([], lang='cnocr', letter=(80, 80, 80), threshold=160,
                name='ROUND_RECIPE_PRODUCT_STOCKS').ocr(images, direct_ocr=True)
    if not isinstance(texts, list) or len(texts) != len(matched):
        return rows, {}
    stocks = {}
    for (recipe, _), text in zip(matched, texts):
        stock = _parse_product_stock(text)
        if stock is not None:
            stocks[recipe] = stock
    return rows, stocks


def scan_recipe_product_stocks(main, products, *, max_swipes=16):
    """在已进入的选品页批量读现货，重复页不累加，缺项不当零。

    参数接受本地产品名或物品编号。可靠结果以物品编号返回，并同步已有
    island_round 库存账；未读到的项不修改原账，在制数量也不混入现货。
    连续两帧确认同一视窗，先有限归顶，再向下遍历；不点击配方或详情。

    Pages:
        in: 已选角色、数量控件已就绪的生产选品页。
        out: 同一生产选品页，未购买、未派遣。
    """
    from module.island.manufacture_selector import (
        ALAS_RECIPE_AMOUNT_MAX, ALAS_RECIPE_CHECK, RECIPE_AMOUNT_MAX_OFFSET,
        _recipe_ids_in_same_category,
    )

    if isinstance(max_swipes, bool) or not isinstance(max_swipes, int) or max_swipes < 0:
        raise ValueError('选品页滑动上限必须为非负整数')
    requested = {resolve_item_id(product) for product in products}
    if not requested:
        return {}
    recipes = {recipe_for_product(item): item for item in requested}
    recipe_ids = set()
    for recipe in recipes:
        recipe_ids.update(_recipe_ids_in_same_category(recipe))

    observed = {}
    conflicts = set()
    last_frame = None
    before_swipe = None
    direction = 1
    swipes = 0
    for _ in main.loop(skip_first=False, timeout=Timer(60)):
        if (not main.appear(ALAS_RECIPE_CHECK, offset=(20, 20))
                or not main.appear(ALAS_RECIPE_AMOUNT_MAX, offset=RECIPE_AMOUNT_MAX_OFFSET)):
            last_frame = None
            continue
        rows, stocks = _read_recipe_stock_page(main, recipe_ids)
        if not any(recipe is not None for recipe, _ in rows):
            last_frame = None
            continue
        signature = tuple((recipe, tuple(button.area), stocks.get(recipe)) for recipe, button in rows)
        if signature != last_frame:
            last_frame = signature
            continue

        for recipe, stock in stocks.items():
            item = recipes.get(recipe)
            if item is None or item in conflicts:
                continue
            if item in observed and observed[item] != stock:
                # 同次只读扫描不应改变现货；矛盾读数不能覆盖先前值或互相相加。
                observed.pop(item)
                conflicts.add(item)
                continue
            observed[item] = stock
        if len(observed) == len(requested):
            break

        if signature == before_swipe or swipes >= max_swipes:
            if direction < 0:
                break
            direction = -1
            swipes = 0
            before_swipe = None
            last_frame = None
            continue
        before_swipe = signature
        name = 'ROUND_RECIPE_STOCKS_TOP' if direction > 0 else 'ROUND_RECIPE_STOCKS_NEXT'
        main.device.swipe_vector(vector=(0, direction * 300), box=(300, 142, 350, 602), name=name)
        main.device.click_record_remove(name)
        swipes += 1
        last_frame = None

    ledger = getattr(main, 'island_round', None)
    if ledger is not None:
        for item, stock in observed.items():
            ledger.observe(item, stock)
    missing = requested - observed.keys()
    if missing:
        logger.warning(f'[岛屿-选品库存] 未取得明确成品库存：{sorted(missing)}')
    return observed


def scan_post_product_stocks(main, button, products, characters):
    """借已确认空岗扫描同产线成品，再正向返回管理页；不提交派遣。"""
    products = tuple(products)
    if not products:
        return {}
    if not _open_probe_product_page(main, button, products[0], characters):
        return {}
    observed = scan_recipe_product_stocks(main, products)
    _close_probe_product_page(main)
    return observed


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
            # 准备期的空岗可能已被先执行的农田任务派出，不能沿用旧快照借忙岗。
            current_posts = getattr(main, 'posts', {})
            post = next((entry for entry in farm_posts['farm']
                         if current_posts.get(entry.get('post_id'), {}).get('state') == 'idle'
                         and current_posts[entry['post_id']].get('round_enabled', True)), None)
            if post is not None:
                for product in ('wheat', 'corn', 'pasture'):
                    if resolve_item_id(product) in missing:
                        yield main, post['button'], product, main.worker_filters['farm']

        states = getattr(main, '_round_ranch_states', {})
        ranch = (('egg', 3000, 'ChickenFilter'), ('pork', 3001, 'PigFilter'),
                 ('milk', 3002, 'RancherFilter'), ('wool', 3003, 'WoolWorkerFilter'))
        for index, (product, feed, worker) in enumerate(ranch, 1):
            post_id = f'ISLAND_RANCH_POST{index}'
            if ((feed in missing or resolve_item_id(product) in missing)
                    and states.get(post_id) == 'idle' and main.is_ranch_post_enabled(post_id)):
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
    from module.island.production_recipe import ProductionRecipeMixin

    main.goto_postmanage()
    main.post_manage_mode(POST_MANAGE_PRODUCTION)
    main.post_close()
    # 管理页会保留上一场所的滚动位置，原料任务也没有 post_open 的补滑兜底。
    main.device.screenshot()
    if not main.appear(button, offset=300):
        if not main.post_manage_swipe_to_top() or not main.post_manage_swipe_until_appear(button, min_swipes=0):
            logger.warning(f'[岛屿-选品库存] 未定位到可读取的岗位：{button}')
            return False
    if not main.post_open(button):
        return False
    stage = ProductionRecipeMixin._dispatch_stage(main)
    if stage == 'characters':
        if getattr(main, 'special_character', False):
            selected = main.select_special_character(product)
        else:
            selected = main.select_character(character_list=characters)
        if not selected or not main.confirm_selected_character('本轮库存只读复核'):
            _close_probe_product_page(main)
            return False
        if ProductionRecipeMixin._dispatch_stage(main) != 'products':
            _close_probe_product_page(main)
            return False
    elif stage != 'products':
        _close_probe_product_page(main)
        return False
    return True


def probe_round_missing_stocks(runners, ledger, item_ids):
    """牧场实际决策时按需补读磨坊及副产缺项，允许确认换季后的真实零库存。

    仅读取本轮当前仍空闲且启用的岗位；无空岗、可用角色或可靠配方时保留未知。
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
