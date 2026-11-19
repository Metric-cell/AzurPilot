"""OCR stays local even when an old deployment file still contains RPC settings."""

import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from module.ocr import models, ocr
from module.runtime.setting import State


class LocalOcrTests(unittest.TestCase):
    def test_recognizer_uses_local_model_factory_with_legacy_settings(self):
        old_settings = SimpleNamespace(UseOcrServer=True, OcrClientAddress='example.invalid:22268')
        with patch.object(State, '_deploy_config_', old_settings, create=True):
            reader = ocr.Ocr((0, 0, 1, 1))
            local_model = object()
            with patch.object(models.OCR_MODEL, 'azur_lane', local_model):
                self.assertIs(reader.cnocr, local_model)
        self.assertIs(ocr.OCR_MODEL, models.OCR_MODEL)

    def test_idle_releases_local_cache_even_with_old_rpc_setting(self):
        from module.base import resource

        with patch.object(State, '_deploy_config_', SimpleNamespace(UseOcrServer=True), create=True), \
             patch.object(resource.Resource, 'instances', {}), \
             patch.object(resource, 'del_cached_property'), \
             patch('module.ocr.al_ocr.release_ocr_models', return_value=0) as release:
            resource.release_resources()
        release.assert_called_once_with(names=['azur_lane', 'cn', 'jp', 'tw', 'det'])

    def test_rpc_module_and_standalone_entry_are_absent(self):
        self.assertIsNone(importlib.util.find_spec('module.ocr.rpc'))


if __name__ == '__main__':
    unittest.main()
