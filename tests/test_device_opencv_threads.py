"""设备开始连接前限制图像并行开销，不访问模拟器或用户配置。"""

import unittest
from unittest.mock import Mock, call, patch

from module.device.device import Device


class ConnectionReached(Exception):
    """用连接前的测试断点阻止真实设备初始化。"""


class TestDeviceOpenCvThreads(unittest.TestCase):
    def test_limit_is_applied_before_connection_for_both_entry_modes(self):
        for initialize_runtime in (True, False):
            with self.subTest(initialize_runtime=initialize_runtime):
                calls = Mock()
                with (
                    patch('module.device.device.cv2.getNumThreads', return_value=20),
                    patch('module.device.device.cv2.setNumThreads', calls.set_threads),
                    patch('module.device.device.logger'),
                    patch('module.device.connection.Connection.__init__',
                          side_effect=ConnectionReached) as connect,
                ):
                    calls.attach_mock(connect, 'connect')
                    device = Device.__new__(Device)
                    with self.assertRaises(ConnectionReached):
                        Device.__init__(device, 'test-instance', initialize_runtime=initialize_runtime)
                self.assertEqual(calls.mock_calls, [call.set_threads(4), call.connect('test-instance')])

    def test_existing_lower_limit_and_disabled_parallelism_are_preserved(self):
        for threads in (0, 1, 2, 3, 4):
            with self.subTest(threads=threads):
                with (
                    patch('module.device.device.cv2.getNumThreads', return_value=threads),
                    patch('module.device.device.cv2.setNumThreads') as set_threads,
                    patch('module.device.connection.Connection.__init__', side_effect=ConnectionReached),
                ):
                    with self.assertRaises(ConnectionReached):
                        Device.__init__(Device.__new__(Device), 'test-instance')
                    set_threads.assert_not_called()


if __name__ == '__main__':
    unittest.main()
