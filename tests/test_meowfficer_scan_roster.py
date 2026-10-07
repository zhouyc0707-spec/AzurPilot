"""猫窝拥有数与等级排序的离线回归，使用匿名截图和 OCR 输出夹具。"""

import unittest
from unittest.mock import patch

import numpy as np

from module.exception import GameStuckError, GameTooManyClickError, RequestHumanTakeover
from module.meowfficer.scan_roster import (COUNT_AREA, SORT_AREA, STATIC_ATTRIBUTE_AREAS,
                                          lock_independent_sort, read_roster_count,
                                          read_static_attributes)


BOX = [[20, 25], [120, 25], [120, 100], [20, 100]]
LEFT_BOX = [[55, 40], [115, 40], [115, 110], [55, 110]]
RIGHT_BOX = [[120, 42], [185, 42], [185, 112], [120, 112]]


def _result(text, confidence=0.99, box=BOX):
    return [(text, box, confidence)]


def _frame():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for area, color in ((COUNT_AREA, (41, 93, 167)), (SORT_AREA, (62, 143, 227))):
        x0, y0, x1, y1 = area
        image[y0:y1, x0:x1] = color
    return image


class _AreaOCR:
    """根据实际输入的裁剪尺寸识别字段，双变体分别提供可控输出。"""

    def __init__(self, count=None, sort=None):
        self.count = [_result('350/350')] * 2 if count is None else list(count)
        self.sort = [_result('等级')] * 2 if sort is None else list(sort)
        self.calls = {'count': 0, 'sort': 0}
        self.inputs = {'count': [], 'sort': []}

    def det(self, image):
        if image.shape == (150, 384, 3):
            field = 'count'
        elif image.shape == (162, 309, 3):
            field = 'sort'
        else:
            raise AssertionError(f'裁剪或补边范围错误：{image.shape}')
        outputs = getattr(self, field)
        index = self.calls[field]
        self.calls[field] += 1
        self.inputs[field].append(image.copy())
        value = outputs[min(index, len(outputs) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


class RosterCountTests(unittest.TestCase):

    def setUp(self):
        guard = patch('module.config.server.server', 'cn')
        guard.start()
        self.addCleanup(guard.stop)

    def test_confirmed_count_uses_both_variants_and_rgb_to_bgr(self):
        ocr = _AreaOCR()
        self.assertEqual(read_roster_count(_frame(), ocr), 350)
        self.assertEqual(ocr.calls['count'], 2)
        np.testing.assert_array_equal(ocr.inputs['count'][0][30, 30], (167, 93, 41))

    def test_zero_and_reasonable_capacity_are_exact_counts(self):
        for text, expected in (('0/350', 0), ('1/350', 1), ('350/350', 350),
                               ('9999/10000', 9999), ('10000/10000', 10000),
                               (' 350 / 350 ', 350)):
            with self.subTest(text=text):
                self.assertEqual(read_roster_count(_frame(), _AreaOCR(count=[_result(text)] * 2)),
                                 expected)

    def test_owned_or_capacity_disagreement_returns_unknown(self):
        for second in ('349/350', '350/500'):
            with self.subTest(second=second):
                ocr = _AreaOCR(count=[_result('350/350'), _result(second)])
                self.assertIsNone(read_roster_count(_frame(), ocr))

    def test_invalid_count_and_impossible_capacity_are_rejected(self):
        for text in ('350', '350|350', '350／350', '拥有350/350', '350/350容量',
                     '-1/350', '351/350', '10000/10001', '100000/100000', '', 'O/350'):
            with self.subTest(text=text):
                self.assertIsNone(read_roster_count(_frame(), _AreaOCR(count=[_result(text)] * 2)))

    def test_blank_missing_or_multiple_count_lines_are_rejected(self):
        for result in ([], None, _result('350') + _result('/350'),
                       _result('350/350') + _result('猫窝容量')):
            with self.subTest(result=result):
                self.assertIsNone(read_roster_count(_frame(), _AreaOCR(count=[result] * 2)))

    def test_finite_high_confidence_is_required_for_each_count_variant(self):
        for score in (0.89, float('nan'), float('inf'), -float('inf'), None, '未知'):
            for index in (0, 1):
                with self.subTest(score=score, variant=index):
                    results = [_result('350/350'), _result('350/350')]
                    results[index] = _result('350/350', score)
                    self.assertIsNone(read_roster_count(_frame(), _AreaOCR(count=results)))
        self.assertEqual(read_roster_count(_frame(), _AreaOCR(count=[_result('350/350', 0.9)] * 2)),
                         350)

    def test_dict_result_is_accepted_only_with_explicit_confidence(self):
        good = [{'text': '350/350', 'score': 0.99}]
        self.assertEqual(read_roster_count(_frame(), _AreaOCR(count=[good] * 2)), 350)
        for bad in ([{'text': '350/350'}], ['350/350'], [('350/350', BOX)],
                    [(350, BOX, 0.99)]):
            with self.subTest(result=bad):
                self.assertIsNone(read_roster_count(_frame(), _AreaOCR(count=[bad] * 2)))

    def test_ordinary_model_error_is_unknown_but_flow_errors_propagate(self):
        self.assertIsNone(read_roster_count(_frame(), _AreaOCR(count=[RuntimeError('模型故障')])))
        for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error_type):
                error = error_type('流程中断')
                with self.assertRaises(error_type) as raised:
                    read_roster_count(_frame(), _AreaOCR(count=[error]))
                self.assertIs(raised.exception, error)


class LockIndependentSortTests(unittest.TestCase):

    def setUp(self):
        guard = patch('module.config.server.server', 'cn')
        guard.start()
        self.addCleanup(guard.stop)

    def test_exact_level_sort_requires_both_variants_and_bgr_crop(self):
        ocr = _AreaOCR()
        self.assertTrue(lock_independent_sort(_frame(), ocr))
        self.assertEqual(ocr.calls['sort'], 2)
        np.testing.assert_array_equal(ocr.inputs['sort'][0][30, 30], (227, 143, 62))

    def test_two_exact_characters_in_the_same_line_confirm_level_sort(self):
        split = _result('等', box=LEFT_BOX) + _result('级', box=RIGHT_BOX)
        self.assertTrue(lock_independent_sort(_frame(), _AreaOCR(sort=[split] * 2)))
        self.assertTrue(lock_independent_sort(_frame(), _AreaOCR(sort=[_result('等级'), split])))
        self.assertTrue(lock_independent_sort(_frame(), _AreaOCR(sort=[list(reversed(split))] * 2)))

    def test_lock_sort_or_partial_labels_do_not_imply_level_sort(self):
        for text in ('锁定', '稀有度', '等', '级', '等级降序', '级等', '等级锁定', ''):
            with self.subTest(text=text):
                self.assertFalse(lock_independent_sort(_frame(), _AreaOCR(sort=[_result(text)] * 2)))

    def test_variant_disagreement_and_extra_text_are_rejected(self):
        for output in (_result('锁定'), [], _result('等级') + _result('等')):
            with self.subTest(output=output):
                self.assertFalse(lock_independent_sort(
                    _frame(), _AreaOCR(sort=[_result('等级'), output])))

    def test_split_characters_need_valid_horizontal_geometry(self):
        invalid = [
            _result('等', box=None) + _result('级', box=RIGHT_BOX),
            _result('等', box=[[float('nan'), 0]] * 4) + _result('级', box=RIGHT_BOX),
            _result('等', box=RIGHT_BOX) + _result('级', box=LEFT_BOX),
            _result('等', box=LEFT_BOX) + _result('级', box=[[120, 150], [185, 150], [185, 220], [120, 220]]),
            _result('等', box=LEFT_BOX) + _result('级', box=[[220, 40], [285, 40], [285, 110], [220, 110]]),
            _result('等', box=LEFT_BOX) + _result('级', box=LEFT_BOX),
        ]
        for output in invalid:
            with self.subTest(output=output):
                self.assertFalse(lock_independent_sort(_frame(), _AreaOCR(sort=[output] * 2)))

    def test_confidence_rules_apply_to_each_split_character(self):
        for confidence in (0.89, float('nan'), float('inf'), None):
            with self.subTest(confidence=confidence):
                output = _result('等', box=LEFT_BOX) + _result('级', confidence, RIGHT_BOX)
                self.assertFalse(lock_independent_sort(_frame(), _AreaOCR(sort=[output] * 2)))

    def test_ordinary_model_error_is_false_but_flow_errors_propagate(self):
        self.assertFalse(lock_independent_sort(_frame(), _AreaOCR(sort=[RuntimeError('模型故障')])))
        for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error_type):
                error = error_type('流程中断')
                with self.assertRaises(error_type) as raised:
                    lock_independent_sort(_frame(), _AreaOCR(sort=[error]))
                self.assertIs(raised.exception, error)


class RosterImageGuardTests(unittest.TestCase):

    def test_other_servers_and_invalid_images_never_call_ocr(self):
        for server in ('cn', 'en', 'jp', 'tw'):
            images = (None, np.zeros((720, 1280), dtype=np.uint8),
                      np.zeros((1080, 1920, 3), dtype=np.uint8),
                      np.zeros((720, 1280, 3), dtype=np.float32))
            if server != 'cn':
                images += (_frame(),)
            for image in images:
                with self.subTest(server=server, shape=getattr(image, 'shape', None)), \
                        patch('module.config.server.server', server):
                    ocr = _AreaOCR()
                    self.assertIsNone(read_roster_count(image, ocr))
                    self.assertFalse(lock_independent_sort(image, ocr))
                    self.assertIsNone(read_static_attributes(image, ocr))
                    self.assertEqual(ocr.calls, {'count': 0, 'sort': 0})


class _AttributeOCR:
    """按真实数字区域宽度区分三项属性，不依赖调用顺序辨认字段。"""

    def __init__(self, override=None):
        self.outputs = {
            'logistics': [_result('131')] * 2,
            'command': [_result('217')] * 2,
            'tactics': [_result('171')] * 2,
        }
        self.outputs.update(override or {})
        self.calls = {name: 0 for name in self.outputs}
        self.inputs = {name: [] for name in self.outputs}

    def det(self, image):
        sizes = {(141, 177, 3): 'logistics', (141, 174, 3): 'command', (141, 204, 3): 'tactics'}
        if image.shape not in sizes:
            raise AssertionError(f'静态属性裁剪范围错误：{image.shape}')
        field = sizes[image.shape]
        outputs = self.outputs[field]
        index = self.calls[field]
        self.calls[field] += 1
        self.inputs[field].append(image.copy())
        value = outputs[min(index, len(outputs) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


def _attribute_frame():
    image = _frame()
    for area, color in zip(STATIC_ATTRIBUTE_AREAS, ((45, 91, 153), (63, 123, 211), (75, 161, 239))):
        x0, y0, x1, y1 = area
        image[y0:y1, x0:x1] = color
    return image


class StaticAttributeTests(unittest.TestCase):

    def setUp(self):
        guard = patch('module.config.server.server', 'cn')
        guard.start()
        self.addCleanup(guard.stop)

    def test_three_fields_are_in_order_and_each_uses_both_bgr_variants(self):
        ocr = _AttributeOCR()
        self.assertEqual(read_static_attributes(_attribute_frame(), ocr), (131, 217, 171))
        self.assertEqual(ocr.calls, {'logistics': 2, 'command': 2, 'tactics': 2})
        for field, color in (('logistics', (153, 91, 45)), ('command', (211, 123, 63)),
                             ('tactics', (239, 161, 75))):
            np.testing.assert_array_equal(ocr.inputs[field][0][30, 30], color)

    def test_difference_in_any_field_makes_the_whole_identity_unknown(self):
        for field in ('logistics', 'command', 'tactics'):
            with self.subTest(field=field):
                ocr = _AttributeOCR({field: [_result('131'), _result('132')]})
                self.assertIsNone(read_static_attributes(_attribute_frame(), ocr))

    def test_missing_multiple_or_non_numeric_field_is_not_guessed(self):
        for field in ('logistics', 'command', 'tactics'):
            for result in ([], None, _result('后勤131'), _result('1O1'), _result('-1'),
                           _result('10001'), _result('31') + _result('1')):
                with self.subTest(field=field, result=result):
                    self.assertIsNone(read_static_attributes(
                        _attribute_frame(), _AttributeOCR({field: [result] * 2})))

    def test_low_or_non_finite_confidence_in_any_field_is_rejected(self):
        for field in ('logistics', 'command', 'tactics'):
            for confidence in (0.89, float('nan'), float('inf'), None):
                with self.subTest(field=field, confidence=confidence):
                    self.assertIsNone(read_static_attributes(_attribute_frame(), _AttributeOCR({
                        field: [_result('131'), _result('131', confidence)]})))

    def test_zero_attributes_are_preserved(self):
        ocr = _AttributeOCR({name: [_result('0')] * 2 for name in ('logistics', 'command', 'tactics')})
        self.assertEqual(read_static_attributes(_attribute_frame(), ocr), (0, 0, 0))

    def test_model_error_is_unknown_and_control_errors_still_propagate(self):
        self.assertIsNone(read_static_attributes(_attribute_frame(), _AttributeOCR({
            'command': [RuntimeError('模型故障')]})))
        for error_type in (GameStuckError, GameTooManyClickError, RequestHumanTakeover):
            with self.subTest(error=error_type):
                error = error_type('流程中断')
                with self.assertRaises(error_type) as raised:
                    read_static_attributes(_attribute_frame(), _AttributeOCR({'command': [error]}))
                self.assertIs(raised.exception, error)


if __name__ == '__main__':
    unittest.main()
