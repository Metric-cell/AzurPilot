import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.device.device import Device
from module.daemon.benchmark import Benchmark
from module.exception import GamePageUnknownError
from module.ui.page import page_main
from module.ui.ui import UI


class TestSafeDeviceNavigation(unittest.TestCase):
    def test_for_existing_device_disables_runtime_side_effects(self):
        config = Mock()

        with patch.object(Device, '__init__', return_value=None) as init:
            Device.for_existing_device(config)

        init.assert_called_once_with(
            config=config,
            auto_start_emulator=False,
            initialize_runtime=False,
        )

    def test_ui_goto_main_forwards_recover_unknown(self):
        ui = object.__new__(UI)

        with patch.object(UI, 'ui_ensure', return_value=True) as ensure:
            self.assertTrue(ui.ui_goto_main(recover_unknown=False))

        ensure.assert_called_once_with(destination=page_main, recover_unknown=False)

    def test_unknown_page_without_recovery_raises(self):
        ui = object.__new__(UI)
        ui.device = Mock()
        ui.device.has_cached_image = True

        with patch('module.ui.ui.Timer') as timer:
            timer.return_value.start.return_value = timer.return_value
            timer.return_value.reached.return_value = True

            with self.assertRaises(GamePageUnknownError):
                ui.ui_get_current_page(recover_unknown=False)


class TestScreenshotBenchmarkCompatibility(unittest.TestCase):
    def test_android_13_does_not_benchmark_droidcast(self):
        benchmark = object.__new__(Benchmark)
        benchmark.__dict__['device'] = SimpleNamespace(
            sdk_ver=33,
            is_chinac_phone_cloud=False,
            nemu_ipc_available=lambda: False,
            ldopengl_available=lambda: False,
        )
        benchmark.benchmark = Mock(return_value=('ADB', None))

        self.assertEqual('ADB', benchmark.run_simple_screenshot_benchmark())

        screenshot_methods, click_methods = benchmark.benchmark.call_args.args
        self.assertNotIn('DroidCast', screenshot_methods)
        self.assertNotIn('DroidCast_raw', screenshot_methods)
        self.assertEqual((), click_methods)


if __name__ == '__main__':
    unittest.main()
