"""本地物品键与 ALAS 物品编号的明确映射，不做近似匹配。"""

LOCAL_TO_ITEM_ID = {
    'wheat': 2000,
    'corn': 2001,
    'rice': 2002,
    'chinese_cabbage': 2003,
    'potato': 2005,
    'soybean': 2006,
    'pasture': 2008,
    'coffee_bean': 2009,
    'apple': 2016,
    'citrus': 2017,
    'banana': 2018,
    'mango': 2019,
    'lemon': 2020,
    'avocado': 2021,
    'rubber': 2022,
    'pear': 4005,
    'persimmon': 4007,
    'carrot': 2004,
    'onion': 2007,
    'flax': 2010,
    'strawberry': 2011,
    'cotton': 2012,
    'tea': 2014,
    'lavender': 2015,
    'pineapple': 4021,
    'asparagus': 4019,
    'bass': 5007,
    'yellowfin_tuna': 5107,
    'shell': 5001,
    'shrimp': 5005,
    'crayfish': 5006,
    'crab': 5008,
    'squid': 5101,
    'sea_cucumber': 5108,
    'Copper': 2701,
    'Aluminium': 2702,
    'Iron': 2703,
    'Sulphur': 2704,
    'Silver': 2705,
    'Elegant': 2803,
    'Practical': 2801,
    'Selected': 2802,
    'chicken_feed': 3000,
    'pig_feed': 3001,
    'cattle_feed': 3002,
    'sheep_feed': 3003,
    'wheat_flour': 3004,
    'chicken': 2602,
    'pork': 2600,
    'milk': 2603,
    'cheese': 3006,
    'tofu': 3011,
    'fresh_honey': 2606,
    'roasted_skewer': 3029,
    'chicken_potato': 3030,
    'carrot_omelette': 3033,
    'stir_fried_chicken': 3032,
    'steak_bowl': 3034,
    'crayfish_stir_fry': 3119,
    'carnival': 3109,
    'double_energy': 3110,
    'omurice': 3013,
    'cabbage_tofu': 3014,
    'salad': 3015,
    'tofu_meat': 3012,
    'tofu_combo': 3101,
    'hearty_meal': 3102,
    'fish_chip': 3114,
    'fo_tiao': 3120,
    'onion_fish': 3116,
    'double_bamboo_shoots': 4025,
    'asparagus_shrimp': 4026,
    'amaranth_rice_ball': 4039,
    'tomato_egg': 4040,
    'matsutake_chicken_soup': 4010,
    'persimmon_cake': 4009,
    'spring_flower_tea': 4024,
    'carrot_pear_juice': 4013,
    'chrysanthemum_tea': 4014,
    'watermelon_juice': 4038,
    'cucumber_juice': 4037,
    'pineapple_juice': 4023,
    'apple_juice': 3017,
    'banana_mango': 3018,
    'honey_lemon': 3019,
    'strawberry_lemon': 3020,
    'strawberry_honey': 3022,
    'floral_fruity': 3103,
    'fruit_paradise': 3104,
    'lavender_tea': 3021,
    'sunny_honey': 3105,
    'iced_coffee': 3005,
    'omelette': 3059,
    'latte': 3007,
    'citrus_coffee': 3008,
    'strawberry_milkshake': 3010,
    'morning_light': 3111,
    'wake_up_call': 3112,
    'fruity_fruitier': 3113,
    'apple_pie': 3009,
    'corn_cup': 3023,
    'orange_pie': 3024,
    'banana_crepe': 3026,
    'orchard_duo': 3107,
    'rice_mango': 3025,
    'succulently_sweet': 3106,
    'berry_orange': 3108,
    'strawberry_charlotte': 3028,
    'seafood_rice': 3118,
    'file_cabinet': 3052,
    'filter_element': 3056,
    'iron_nail': 3044,
    'cutlery': 3047,
    'leather': 3036,
    'boot': 3040,
    'peanut_oil': 4012,
    'shepherd_purse': 4027,
    'spring_bouquet': 4028,
    'summer_bouquet': 4042,
    'autumn_bouquet': 4011,
    'jasmine_oil': 4041,
    'egg': 2601,
    'raw_leather': 2604,
    'wool': 2605,
    'tomato': 4033,
    'cucumber': 4035,
}

LOCAL_TO_ITEM_ID.update({
    'autumn_chrysanthemum': 4001, 'reed_flower': 4002, 'peanut': 4003, 'matsutake': 4004,
    'pear_seed': 4006, 'persimmon_seed': 4008,
    'coal': 2700, 'raw_timber': 2800,
    'catfish': 5002, 'koi_carp': 5003, 'common_carp': 5004,
    'mackerel': 5102, 'tuna': 5103, 'salmon': 5104,
    'red_sea_bream': 5105, 'black_porgy': 5106,
    'freshwater_fish_meat': 2521, 'saltwater_fish_meat': 2522,
    'cloth': 3035, 'rope': 3037, 'gloves': 3038, 'aroma_sachet': 3039,
    'wound_dressings': 3041, 'charcoal_brush': 3042, 'cable': 3043,
    'chemicals': 3045, 'gunpowder': 3046, 'paper': 3048, 'notebook': 3049,
    'chair_and_desk': 3050, 'choice_wooden_barrel': 3051,
    'ink_cartridge': 3053, 'clock': 3054, 'battery': 3055,
    'ornamental_painting': 3117, 'lemon_shrimp': 3115,
})

ITEM_ID_TO_LOCAL = {item_id: name for name, item_id in LOCAL_TO_ITEM_ID.items()}


def resolve_item_id(key):
    """识别物品编号、本地键或官方名称；未知物品明确报错。"""
    import re

    from module.island.data import DIC_ISLAND_ITEM

    if isinstance(key, bool):
        raise ValueError('物品编号不能是布尔值')
    if isinstance(key, int):
        item_id = key
    else:
        text = str(key).strip()
        if text in LOCAL_TO_ITEM_ID:
            return LOCAL_TO_ITEM_ID[text]
        match = re.fullmatch(r'(?:.*\()?(\d+)\)?', text)
        if match:
            item_id = int(match.group(1))
        else:
            matches = {item_id for item_id, data in DIC_ISLAND_ITEM.items()
                       if text in data['name'].values()}
            if len(matches) != 1:
                raise ValueError(f'未知或重名的岛屽物品：{key}')
            return matches.pop()
    if item_id not in DIC_ISLAND_ITEM:
        raise ValueError(f'未知岛屽物品编号：{item_id}')
    return item_id
