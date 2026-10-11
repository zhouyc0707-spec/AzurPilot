"""新猫领取 OCR 回归：公开猫名/成长框局部实拍，不连接设备或真实配置。"""

from pathlib import Path
import unittest
from unittest.mock import Mock

import numpy as np
from PIL import Image

from module.meowfficer.collect_ocr import (
    CAT_AREA, SLOT_LEFTS, _selected_slot, _visible_slots,
    recognize_collection_cat, recognize_collection_talent,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'meowfficer_collect'
BOX = [[0, 0], [40, 0], [40, 12], [0, 12]]


def ocr_result(text, confidence=0.99):
    return [(text, BOX, confidence)]


def frame(name, cat=False):
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)
    crop = np.array(Image.open(FIXTURES / f'{name}.png').convert('RGB'))
    x0, y0 = CAT_AREA[:2] if cat else (452, 300)
    image[y0:y0 + crop.shape[0], x0:x0 + crop.shape[1]] = crop
    return image


def copy_border(image, source, target):
    """只复制四边标记，保留目标列自己的图标与标题。"""
    x, target_x = SLOT_LEFTS[source], SLOT_LEFTS[target]
    for y0, y1, dx0, dx1 in ((303, 315, 5, 76), (383, 398, 5, 76),
                             (315, 384, -4, 5), (315, 384, 76, 85)):
        image[y0:y1, target_x + dx0:target_x + dx1] = image[y0:y1, x + dx0:x + dx1].copy()


class CollectionCatOcrTests(unittest.TestCase):

    def test_clear_name_crop_accepts_exact_high_confidence_consensus(self):
        for label, name in [('lind', '林德喵'), ('pound', '庞德喵'), ('murray', '莫里喵')]:
            with self.subTest(cat=name):
                ocr = Mock(det=Mock(return_value=ocr_result(name)))
                self.assertEqual(recognize_collection_cat(frame(label + '_name', True), ocr), name)
                self.assertEqual(ocr.det.call_count, 3)

    def test_shifted_name_uses_confirmed_offset(self):
        image = frame('lind_name', True)
        x0, y0, x1, y1 = CAT_AREA
        expected = image[y0:y1, x0:x1].copy()
        image[y0:y1, x0:x1] = 255
        image[y0 - 20:y1 - 20, x0 - 40:x1 - 40] = expected
        shifted_crops = []
        ocr = Mock(det=Mock(side_effect=lambda crop: shifted_crops.append(crop) or ocr_result('林德喵')))
        self.assertEqual(recognize_collection_cat(image, ocr, shifted=True), '林德喵')
        reference = Mock(det=Mock(return_value=ocr_result('林德喵')))
        recognize_collection_cat(frame('lind_name', True), reference)
        np.testing.assert_array_equal(shifted_crops[0], reference.det.call_args_list[0].args[0])

    def test_conflicting_known_names_are_not_selected_by_majority(self):
        ocr = Mock(det=Mock(side_effect=[ocr_result('林德喵'), ocr_result('庞德喵'), ocr_result('林德喵')]))
        self.assertIsNone(recognize_collection_cat(frame('lind_name', True), ocr))

    def test_unknown_partial_low_confidence_and_single_hit_names_are_rejected(self):
        for responses in ([ocr_result('林德'), ocr_result('林德'), ocr_result('林德')],
                          [ocr_result('林德喵', .5)] * 3,
                          [ocr_result('林德喵'), [], []],
                          [[('林德喵', BOX, float('nan'))]] * 3,
                          [['林德喵']] * 3):
            with self.subTest(responses=responses):
                ocr = Mock(det=Mock(side_effect=responses))
                self.assertIsNone(recognize_collection_cat(frame('lind_name', True), ocr))

    def test_extra_text_is_not_treated_as_exact_known_name(self):
        ocr = Mock(det=Mock(return_value=ocr_result('自定义林德喵')))
        self.assertIsNone(recognize_collection_cat(frame('lind_name', True), ocr))


class CollectionTalentOcrTests(unittest.TestCase):

    def test_real_selected_frames_cover_current_i_ii_and_special(self):
        cases = [('lind_battleship_hp_i', 0, '轮机手·战列', 1),
                 ('lind_cruiser_command_i', 0, '新晋指挥官·巡洋', 1),
                 ('lind_main_gun_ii', 1, '熟练炮手·主力', 2),
                 ('pound_main_aa_i', 0, '对空炮手·主力', 1),
                 ('pound_front_sonar_i', 0, '声纳兵·先锋', 1),
                 ('pound_main_gun_ii', 1, '熟练炮手·主力', 2),
                 ('murray_main_sonar_i', 0, '声纳兵·主力', 1),
                 ('murray_carrier_command_i', 0, '新晋指挥官·空母', 1),
                 ('william_cruiser_command_i', 0, '新晋指挥官·巡洋', 1),
                 ('william_special_exp', 1, '被期待的新星', 1),
                 ('patrick_special_exp', 1, '最佳玩伴', 1),
                 ('patrick_cruiser_torpedo_i', 0, '新人雷击士·巡洋', 1),
                 ('rupp_destroyer_gun_ii', 1, '熟练炮手·驱逐', 2),
                 ('old_william_front_aa_ii', 1, '熟练对空炮手·先锋', 2)]
        for label, selected, name, level in cases:
            with self.subTest(fixture=label):
                image = frame(label)
                self.assertEqual(_selected_slot(image), selected)
                ocr = Mock(det=Mock(return_value=ocr_result(name)))
                talents = recognize_collection_talent(image, ocr)
                self.assertEqual([(t.name, t.level) for t in talents], [(name, level)])
                self.assertEqual(talents[0].raw, name)
                self.assertFalse(talents[0].inferred)

    def test_selected_i_does_not_accept_future_iii_title(self):
        ocr = Mock(det=Mock(return_value=ocr_result('轮机长·战列')))
        self.assertEqual(recognize_collection_talent(frame('lind_battleship_hp_i'), ocr), [])

    def test_selected_iii_accepts_only_iii_title(self):
        image = frame('lind_battleship_hp_i')
        copy_border(image, 0, 2)
        x = SLOT_LEFTS[0]
        image[303:315, x + 5:x + 76] = 225
        self.assertEqual(_selected_slot(image), 2)
        ocr = Mock(det=Mock(return_value=ocr_result('轮机长·战列')))
        self.assertEqual(recognize_collection_talent(image, ocr)[0].level, 3)
        ocr.det.return_value = ocr_result('轮机手·战列')
        self.assertEqual(recognize_collection_talent(image, ocr), [])

    def test_two_selected_boxes_do_not_choose_the_first(self):
        image = frame('lind_battleship_hp_i')
        copy_border(image, 0, 1)
        ocr = Mock(det=Mock(return_value=ocr_result('轮机手·战列')))
        self.assertEqual(recognize_collection_talent(image, ocr), [])
        ocr.det.assert_not_called()

    def test_only_three_sides_or_no_selected_box_are_rejected_before_ocr(self):
        for image in (frame('lind_battleship_hp_i'), np.full((720, 1280, 3), 255, np.uint8)):
            image[303:315, 481:552] = 225
            ocr = Mock(det=Mock(return_value=ocr_result('轮机手·战列')))
            self.assertEqual(recognize_collection_talent(image, ocr), [])
            ocr.det.assert_not_called()

    def test_incomplete_icon_layout_is_not_guessed_as_normal_or_special(self):
        image = frame('lind_battleship_hp_i')
        image[318:383, 729:800] = 225
        self.assertEqual(_visible_slots(image), (True, True, False))
        ocr = Mock(det=Mock(return_value=ocr_result('轮机手·战列')))
        self.assertEqual(recognize_collection_talent(image, ocr), [])
        ocr.det.assert_not_called()

    def test_special_layout_does_not_accept_normal_level_ii(self):
        ocr = Mock(det=Mock(return_value=ocr_result('熟练炮手·主力')))
        self.assertEqual(recognize_collection_talent(frame('patrick_special_exp'), ocr), [])

    def test_low_confidence_single_hit_and_unknown_title_do_not_unlock(self):
        for responses in ([ocr_result('轮机手·战列', .5)] * 5,
                          [ocr_result('轮机手·战列'), [], [], [], []],
                          [ocr_result('轮机手·战列额外')] * 5):
            with self.subTest(responses=responses):
                ocr = Mock(det=Mock(side_effect=responses))
                self.assertEqual(recognize_collection_talent(frame('lind_battleship_hp_i'), ocr), [])

    def test_exact_known_titles_disagreeing_between_variants_are_rejected(self):
        responses = [ocr_result('轮机手·战列'), ocr_result('轮机手·驱逐')]
        responses += [ocr_result('轮机手·战列')] * 3
        ocr = Mock(det=Mock(side_effect=responses))
        self.assertEqual(recognize_collection_talent(frame('lind_battleship_hp_i'), ocr), [])

    def test_multiple_known_texts_in_one_title_are_rejected(self):
        results = ocr_result('轮机手·战列') + ocr_result('轮机手·驱逐')
        ocr = Mock(det=Mock(return_value=results))
        self.assertEqual(recognize_collection_talent(frame('lind_battleship_hp_i'), ocr), [])

    def test_invalid_frames_are_rejected_without_ocr(self):
        for image in (None, '截图', np.zeros((720, 1280), np.uint8),
                      np.zeros((720, 1280, 3), np.float32), np.zeros((1440, 2560, 3), np.uint8)):
            with self.subTest(shape=getattr(image, 'shape', None)):
                ocr = Mock()
                self.assertIsNone(recognize_collection_cat(image, ocr))
                self.assertEqual(recognize_collection_talent(image, ocr), [])
                ocr.det.assert_not_called()


if __name__ == '__main__':
    unittest.main()
