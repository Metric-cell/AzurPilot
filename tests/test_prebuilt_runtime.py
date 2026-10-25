"""桌面组件模式的版本一致性和无 Git/npm/uv 启动约定。"""
import json
import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.frontend import ensure_frontend, source_fingerprint
from module.runtime.prebuilt import validate_runtime


class PrebuiltRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / '.azurpilot').mkdir()
        self.manifest = {'schema': 1, 'revision': 'a' * 40, 'python': platform.python_version()}
        self.receipt = {'manifest': self.manifest, 'components': dict.fromkeys(('source', 'resources', 'frontend', 'python', 'dependencies', 'adb'), {})}
        (self.root / 'BUILD_MANIFEST').write_text(json.dumps({'azurpilot_commit': 'a' * 40, 'python': platform.python_version()}))
        self.save()
        self.environment = patch.dict(os.environ, {'AZURPILOT_PREBUILT': '1'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def save(self):
        (self.root / '.azurpilot/installed.json').write_text(json.dumps(self.receipt))

    def test_matching_runtime_passes_and_incomplete_transaction_fails(self):
        validate_runtime(self.root)
        transaction = self.root / '.azurpilot/transaction'
        transaction.mkdir()
        (transaction / 'journal.json').write_text('[]')
        with self.assertRaises(RuntimeError):
            validate_runtime(self.root)

    def test_python_mismatch_and_missing_component_fail(self):
        self.manifest['python'] = '3.14.999'
        self.save()
        with self.assertRaises(RuntimeError):
            validate_runtime(self.root)
        self.manifest['python'] = platform.python_version()
        self.receipt['components'].pop('dependencies')
        self.save()
        with self.assertRaises(RuntimeError):
            validate_runtime(self.root)

    def test_frontend_missing_never_runs_npm_and_valid_build_needs_no_node(self):
        frontend = self.root / 'frontend'
        frontend.mkdir()
        with patch('deploy.frontend.subprocess.run') as run:
            with self.assertRaisesRegex(RuntimeError, '桌面启动器'):
                ensure_frontend(self.root)
            (frontend / 'dist').mkdir()
            (frontend / 'dist/index.html').write_text('built')
            (frontend / 'dist/.source-fingerprint').write_text(source_fingerprint(frontend))
            ensure_frontend(self.root)
            run.assert_not_called()

    def test_supervisor_validates_components_instead_of_starting_uv(self):
        from gui import _prepare_dependency_sync_before_webui_start
        with patch('module.runtime.prebuilt.validate_runtime') as validate, patch('gui.clear_dependency_sync_pending') as clear, patch('gui._start_dependency_sync_service_with_retry') as sync:
            self.assertEqual(_prepare_dependency_sync_before_webui_start(None, None, None, force=True), (True, None, None, None))
            validate.assert_called_once()
            clear.assert_called_once()
            sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
