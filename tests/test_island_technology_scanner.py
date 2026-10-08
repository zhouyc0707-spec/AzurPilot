"""科技扫描的离线资源定位、覆盖完整性与失败边界回归。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from module.base.utils import load_image, rgb2luma
from module.exception import GameStuckError
from module.island.technology_scanner import IslandTechnologyScanner, extract_flowchart
from module.ui.page import Page, page_island, page_island_phone, page_island_technology


class TechnologyScannerTest(unittest.TestCase):
    def setUp(self):
        self.scanner = object.__new__(IslandTechnologyScanner)
        self.scanner.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                                              screenshot=Mock())

    def test_official_chart_windows_locate_exactly_for_each_tab(self):
        for tab in range(2, 7):
            chart = load_image(f'./assets/island/technology/technology_chart_{tab}.png')
            if chart.ndim == 3:
                chart = rgb2luma(chart)
            for position in (0, 600, chart.shape[1] - 1113):
                with self.subTest(tab=tab, position=position):
                    view = chart[:, position:position + 1113]
                    with patch('module.island.technology_scanner.extract_flowchart', return_value=view):
                        self.assertEqual(self.scanner.get_technology_view_position(tab), position)

    def test_unrelated_flowchart_cannot_fabricate_a_view_position(self):
        unrelated = np.random.default_rng(20261008).integers(0, 256, (720, 1113), dtype=np.uint8)
        with patch('module.island.technology_scanner.extract_flowchart', return_value=unrelated):
            with self.assertRaises(GameStuckError):
                self.scanner.get_technology_view_position(2)

    def test_extraction_rejects_invalid_screen_dimensions(self):
        with self.assertRaises(GameStuckError):
            extract_flowchart(np.zeros((1080, 1920, 3), dtype=np.uint8))

    def test_constant_frames_cannot_pass_normalized_template_matching(self):
        for value in (0, 255):
            with self.subTest(value=value):
                with patch('module.island.technology_scanner.extract_flowchart',
                           return_value=np.full((720, 1113), value, dtype=np.uint8)):
                    with self.assertRaises(GameStuckError):
                        self.scanner.get_technology_view_position(2)

    def test_visible_inactive_nodes_are_observed_but_hidden_nodes_are_absent(self):
        self.scanner.device.image.fill(50)
        self.scanner.device.image[80:132, 190:410] = 255
        positions = {1: (133, 106), 2: (433, 106), 3: (1200, 106), 4: (1000, 646)}
        observations = {}
        self.scanner._scan_visible_technology(positions, 0, observations)
        self.assertEqual(observations, {1: True, 2: False})

    def test_reset_stops_at_left_edge_without_an_extra_swipe(self):
        self.scanner.get_technology_view_position = Mock(side_effect=[1200, 600, 0])
        self.scanner._island_technology_swipe = Mock()
        self.assertTrue(self.scanner.technology_reset_view(tab=2))
        self.assertEqual(self.scanner._island_technology_swipe.call_count, 2)

    def test_reset_failure_is_bounded_and_explicit(self):
        self.scanner.get_technology_view_position = Mock(return_value=100)
        self.scanner._island_technology_swipe = Mock()
        with self.assertRaises(GameStuckError):
            self.scanner.technology_reset_view(tab=2)
        self.assertEqual(self.scanner._island_technology_swipe.call_count, 12)

    def test_partial_scan_does_not_return_unobserved_nodes_as_false(self):
        self.scanner.island_technology_side_navbar_ensure = Mock()
        self.scanner.technology_reset_view = Mock()
        self.scanner.get_technology_view_position = Mock(return_value=0)
        self.scanner._scan_visible_technology = Mock()
        self.scanner._island_technology_swipe = Mock()
        self.scanner.loop = lambda **_kwargs: iter(range(25))
        with self.assertRaises(GameStuckError):
            self.scanner.scan_all()
        self.assertEqual(self.scanner._island_technology_swipe.call_count, 3)

    def test_all_five_tabs_must_be_observed_before_return(self):
        self.scanner.island_technology_side_navbar_ensure = Mock()
        self.scanner.technology_reset_view = Mock()
        self.scanner.get_technology_view_position = Mock(return_value=0)
        self.scanner._island_technology_swipe = Mock()
        self.scanner.loop = lambda **_kwargs: iter(range(25))

        def observe(positions, _position, observations):
            observations.update({index: index % 2 == 0 for index in positions})

        self.scanner._scan_visible_technology = observe
        result = self.scanner.scan_all()
        self.assertEqual(len(result), 164)
        self.assertEqual(self.scanner.island_technology_side_navbar_ensure.call_count, 5)
        self.scanner._island_technology_swipe.assert_not_called()

    def test_mobile_phone_navigation_uses_the_verified_main_page_entry(self):
        Page.init_connection(page_island_technology)
        try:
            self.assertIs(page_island_phone.parent, page_island)
            self.assertIs(page_island.parent, page_island_technology)
            self.assertEqual(page_island.links[page_island_technology].name, 'ALAS_ISLAND_GOTO_TECHNOLOGY')
            self.assertIn(page_island, page_island_technology.links)
        finally:
            Page.clear_connection()


if __name__ == '__main__':
    unittest.main()
