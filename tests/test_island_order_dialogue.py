"""脱敏对白边框回归，不读取正文、库存、角色或真实游戏设备。"""

from pathlib import Path
import unittest

import cv2
import numpy as np
from PIL import Image

from module.island.order_dialogue import get_order_dialogue_bounds


FIXTURES = Path(__file__).parent / 'fixtures' / 'island_order_dialogue'
FIXTURE_ORIGIN = (161, 589)
EXPECTED_BOUNDS = (177, 605, 638, 680)
BACKGROUND = (80, 120, 140)


def paste_dialogue(image, origin=FIXTURE_ORIGIN, name='dialogue_edges'):
    """保留脱敏边框整块相对位置，供订单全链路夹具复用。"""
    with Image.open(FIXTURES / f'{name}.png') as source:
        tile = np.asarray(source.convert('RGB'))
    x, y = origin
    image[y:y + tile.shape[0], x:x + tile.shape[1]] = tile


class OrderDialogueTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((720, 1280, 3), BACKGROUND, dtype=np.uint8)

    def test_real_anonymized_frame_and_attached_marker_have_exact_body_bounds(self):
        for name in ('dialogue_edges', 'dialogue_attached_l'):
            with self.subTest(name=name):
                self.image[:] = BACKGROUND
                paste_dialogue(self.image, name=name)
                self.assertEqual(get_order_dialogue_bounds(self.image), EXPECTED_BOUNDS)

    def test_attached_small_l_cannot_raise_body_top_to_connected_component_top(self):
        paste_dialogue(self.image, name='dialogue_attached_l')
        mask = (self.image.min(axis=2) >= 225) & (np.ptp(self.image, axis=2) <= 25)
        _, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        rows = np.where(labels == labels[615, 400])[0]
        self.assertEqual(int(rows.min()), 601)
        self.assertEqual(get_order_dialogue_bounds(self.image), EXPECTED_BOUNDS)

    def test_translation_uses_current_edges_instead_of_fixed_coordinates(self):
        for dx, dy in ((40, -20), (-40, 15), (100, -50)):
            with self.subTest(offset=(dx, dy)):
                self.image[:] = BACKGROUND
                paste_dialogue(self.image, origin=(161 + dx, 589 + dy))
                self.assertEqual(get_order_dialogue_bounds(self.image),
                                 (177 + dx, 605 + dy, 638 + dx, 680 + dy))

    def test_frame_outside_known_bottom_order_area_is_not_assumed_dialogue(self):
        paste_dialogue(self.image, origin=(161, 190))
        self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_each_missing_edge_is_rejected(self):
        missing = (
            (slice(605, 608), slice(210, 604)),
            (slice(677, 680), slice(210, 604)),
            (slice(645, 655), slice(177, 180)),
            (slice(640, 650), slice(635, 638)),
        )
        for area in missing:
            with self.subTest(edge=area):
                self.image[:] = BACKGROUND
                paste_dialogue(self.image)
                self.image[area] = BACKGROUND
                self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_each_corner_requires_round_cutout_instead_of_solid_white(self):
        corners = (
            (slice(605, 623), slice(177, 195)),
            (slice(605, 623), slice(620, 638)),
            (slice(662, 680), slice(177, 195)),
            (slice(662, 680), slice(620, 638)),
        )
        for area in corners:
            with self.subTest(corner=area):
                self.image[:] = BACKGROUND
                paste_dialogue(self.image)
                self.image[area] = 255
                self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_plain_white_rectangle_and_rounded_white_without_gradient_are_rejected(self):
        for width in (447, 461):
            with self.subTest(width=width):
                self.image[:] = BACKGROUND
                self.image[605:680, 177:177 + width] = 255
                self.assertIsNone(get_order_dialogue_bounds(self.image))
        self.image[:] = BACKGROUND
        paste_dialogue(self.image)
        white = (self.image.min(axis=2) >= 225) & (np.ptp(self.image, axis=2) <= 25)
        self.image[white] = 255
        self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_missing_shadow_and_empty_inner_plane_are_rejected(self):
        paste_dialogue(self.image)
        self.image[687:690, 209:606] = self.image[680:683, 209:606]
        self.assertIsNone(get_order_dialogue_bounds(self.image))
        self.image[:] = BACKGROUND
        paste_dialogue(self.image)
        self.image[613:620, 199:616] = BACKGROUND
        self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_no_frame_map_noise_and_horizontal_white_bar_are_rejected(self):
        self.assertIsNone(get_order_dialogue_bounds(self.image))
        rng = np.random.default_rng(0)
        self.image[rng.integers(520, 710, 15000), rng.integers(100, 832, 15000)] = 255
        self.assertIsNone(get_order_dialogue_bounds(self.image))
        self.image[601:607, 182:629] = 255
        self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_rescaled_frame_is_not_a_verified_1280_by_720_asset(self):
        with Image.open(FIXTURES / 'dialogue_edges.png') as source:
            for scale in (0.9, 1.01, 1.1):
                with self.subTest(scale=scale):
                    self.image[:] = BACKGROUND
                    tile = np.asarray(source.resize((round(source.width * scale), round(source.height * scale)),
                                                    Image.Resampling.NEAREST).convert('RGB'))
                    self.image[580:580 + tile.shape[0], 150:150 + tile.shape[1]] = tile
                    self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_two_positive_frames_are_ambiguous(self):
        paste_dialogue(self.image, origin=(161, 609))
        paste_dialogue(self.image, origin=(161, 514))
        self.assertIsNone(get_order_dialogue_bounds(self.image))

    def test_image_remains_unchanged_and_invalid_input_is_rejected(self):
        paste_dialogue(self.image)
        expected = self.image.copy()
        self.assertEqual(get_order_dialogue_bounds(self.image), EXPECTED_BOUNDS)
        np.testing.assert_array_equal(self.image, expected)
        for value in (None, 'image', self.image.astype(float), self.image[:360, :640], self.image[:, :, 0]):
            with self.subTest(input_type=type(value).__name__):
                self.assertIsNone(get_order_dialogue_bounds(value))


if __name__ == '__main__':
    unittest.main()
