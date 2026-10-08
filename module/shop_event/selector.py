"""
活动商店商品过滤选择器。

通过正则表达式定义商品分类过滤规则，支持按大类（装备/舰船/PT 等）、
子类（SSR/SR 等）和层级（S1-S9/T1-T6）三级筛选。
提供 Filter 实例用于匹配商品名称，按优先级选择购买商品。
本地兼容保留数量后缀，例如 Cube:5 表示该项最多再购买 5 次。

Pages: in: EVENT_SHOP
"""

import re

from module.base.filter import Filter

FILTER_REGEX = re.compile(
    '^(ship|equip|pt|gachaticket'
    '|meta|skinbox'
    '|array|chip|cat|pr|dr'
    '|augment'
    '|cube|medal|expbook'
    '|box|plate|coin|oil|food'
    ')'

    '(ur|ssr'
    '|core|change|enhance'
    '|general|gun|torpedo|antiair|plane)?'

    '(s[1-9]|t[1-6])?$'
)
FILTER_ATTR = ('group', 'sub_genre', 'tier')
FILTER = Filter(FILTER_REGEX, FILTER_ATTR)


def parse_filter_amount(filter_string):
    """解析数量上限；零或负数保留为零上限，不能变成无限购买。"""
    return {token['key']: max(token['amount'], 0)
            for token in parse_filter_tokens(filter_string)
            if token['key'] and token['amount'] is not None}


def strip_filter_amount(filter_string):
    """只在匹配商品时去掉合法整数后缀，不改写用户配置。"""
    return ' > '.join(token['name'] for token in parse_filter_tokens(filter_string))


def parse_filter_tokens(filter_string):
    """将配置拆成有序项目，保留原名称、数量与规范化匹配键。"""
    out = []
    for part in str(filter_string).split('>'):
        raw = part.strip()
        if not raw:
            continue
        token = {'raw': raw, 'name': raw, 'amount': None, 'key': None}
        if ':' in raw:
            name, amount = raw.rsplit(':', 1)
            try:
                amount = int(amount.strip())
            except ValueError:
                pass
            else:
                token['name'] = name.strip()
                token['amount'] = amount
                result = FILTER_REGEX.fullmatch(name.replace(' ', '').lower())
                if result is not None:
                    token['key'] = ''.join(value or '' for value in result.groups())
        out.append(token)
    return out


def rebuild_filter_tokens(tokens):
    """重组过滤器，移除已消耗的数量项目，保留不限量项目和顺序。"""
    parts = []
    for token in tokens:
        name = token.get('name', '').strip()
        amount = token.get('amount')
        if not name:
            continue
        if amount is None:
            parts.append(name)
        elif amount > 0:
            parts.append(f'{name}:{amount}')
    return ' > '.join(parts)


EVENT_SHOP_PRESET_FILTER = {
    'all': """
        EquipUR > EquipSSR > Cube > GachaTicket
        > Array > Chip > CatT3 
        > Meta > SkinBox
        > Oil > Coin > Medal > ExpBookT1 > FoodT1
        > DR > PR
        > AugmentCore > AugmentEnhanceT2 > AugmentChangeT2 > AugmentChangeT1
        > CatT2 > CatT1 > PlateGeneralT3 > PlateT3 > BoxT4
        > ShipSSR
    """,
}
