"""用真实材料标签验证完整定位与彩色耗料；CI 不加载 OCR 模型。"""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from module.island.island import Island
from module.island.material_counter import material_counter_images, read_material_counter_text


FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'island_material_counter'
CASES = (
    ('wheat_0_9.png', (754, 540, 796, 558), '0/9', (0, 9)),
    ('pig_62_2_6.png', (731, 540, 812, 558), '62/(2+6)', (62, 8)),
    ('pig_62_10_30.png', (742, 540, 841, 558), '62/(10+30)', (62, 40)),
)


def load_fixture(name):
    with Image.open(FIXTURE_DIR / name) as image:
        return np.asarray(image.convert('RGB'))


class MaterialCounterImageTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('module.island.material_counter.logger'))
        self.enterContext(patch('module.island.island.logger'))

    def test_fixtures_preserve_only_the_anonymous_counter_region(self):
        for name, *_ in CASES:
            with self.subTest(name=name):
                image = load_fixture(name)
                self.assertEqual(image.shape, (720, 1280, 3))
                outside = image.copy()
                outside[530:575, 690:890] = 0
                self.assertFalse(outside.any())
                self.assertTrue(image[530:575, 690:890].any())

    def test_real_labels_preserve_full_width_and_white_or_orange_digits(self):
        for name, (left, top, right, bottom), *_ in CASES:
            with self.subTest(name=name):
                image = load_fixture(name)
                candidates = material_counter_images(image)
                self.assertEqual(len(candidates), 2)
                label = image[top:bottom, left:right].astype(int)
                orange = ((label[:, :, 0] > 180) & (label[:, :, 1] > 80)
                          & (label[:, :, 1] < 220) & (label[:, :, 2] < 120))
                if name.startswith('pig_'):
                    # 现场的橙色 +6、+30 也属于总耗料，不能仅提取白色部分。
                    self.assertGreater(np.count_nonzero(orange), 20)
                for binary in candidates:
                    self.assertEqual(binary.shape, (bottom - top + 4, right - left + 4))
                    self.assertTrue((binary[:2] == 255).all())
                    self.assertTrue((binary[-2:] == 255).all())
                    self.assertTrue((binary[:, :2] == 255).all())
                    self.assertTrue((binary[:, -2:] == 255).all())
                    body = binary[2:-2, 2:-2]
                    self.assertGreater(np.count_nonzero(body[3:-3, 5:15] == 0), 10)
                    self.assertGreater(np.count_nonzero(body[3:-3, -15:-5] == 0), 5)
                    self.assertTrue((body[orange] == 0).all())

    def test_horizontal_animation_keeps_the_same_complete_counter(self):
        for name, *_ in CASES:
            image = load_fixture(name)
            expected = material_counter_images(image)
            for shift in (-20, 20):
                with self.subTest(name=name, shift=shift):
                    actual = material_counter_images(np.roll(image, shift, axis=1))
                    self.assertEqual(len(actual), 2)
                    for before, after in zip(expected, actual):
                        np.testing.assert_array_equal(before, after)

    def test_absent_truncated_or_multiple_labels_are_not_read(self):
        blank = np.zeros((720, 1280, 3), dtype=np.uint8)
        background = blank.copy()
        background[530:575, 690:890] = 200
        multiple = background.copy()
        wheat = load_fixture('wheat_0_9.png')[540:558, 754:796]
        multiple[540:558, 729:771] = wheat
        multiple[540:558, 779:821] = wheat
        truncated = background.copy()
        # 标签左侧贴住搜索边界，不能确认首位是否已被截掉。
        pig = load_fixture('pig_62_10_30.png')[540:558, 742:841]
        truncated[540:558, 690:789] = pig
        for label, image in (('empty', blank), ('no_label', background),
                             ('multiple', multiple), ('truncated', truncated)):
            with self.subTest(label=label), patch(
                    'module.island.material_counter._MaterialCounterOcr') as reader:
                self.assertEqual(material_counter_images(image), [])
                self.assertEqual(read_material_counter_text(image), '')
                reader.assert_not_called()

    def test_both_thresholds_must_agree_on_the_complete_text(self):
        image = load_fixture('pig_62_2_6.png')
        for texts in (['62/(2+6)', '62/(2)'], ['62/(2+6)'], [],
                      ['62/(2+6)', '62/(2+6)', '62/(2+6)']):
            with self.subTest(texts=texts), patch(
                    'module.island.material_counter._MaterialCounterOcr') as reader:
                reader.return_value.ocr.return_value = texts
                self.assertEqual(read_material_counter_text(image), '')

    def test_agreed_text_still_passes_strict_parser_before_becoming_stock(self):
        island = Island.__new__(Island)
        cases = list(CASES) + [
            ('pig_62_2_6.png', None, '62/(2+)', None),
            ('wheat_0_9.png', None, '69', None),
        ]
        for name, _, text, parsed in cases:
            with self.subTest(text=text), patch(
                    'module.island.material_counter._MaterialCounterOcr') as reader:
                island.device = SimpleNamespace(image=load_fixture(name))
                reader.return_value.ocr.return_value = [text, text]
                stock, counter = island.ocr_select_product_material_detail(expected_quantity=9)
                self.assertEqual((stock, counter), (parsed[0] if parsed else None, text))
                self.assertEqual(Island.parse_select_product_material_counter(counter), parsed)
                args, kwargs = reader.return_value.ocr.call_args
                self.assertEqual(len(args[0]), 2)
                self.assertEqual(kwargs, {'direct_ocr': True})


if __name__ == '__main__':
    unittest.main()
