"""本地背景图库存取与文件类型检查。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from module.api import background_service as service


class TestGallery(unittest.TestCase):
    """图库存取：索引读写、删除、路径穿越防护。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(service, 'LIBRARY_DIR', self.dir),
            mock.patch.object(service, 'INDEX_FILE', self.dir / 'index.json'),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_upload_accepts_only_inert_media_types(self):
        entry = service.gallery_add_bytes(b'image', 'sample.png', 'image/png')
        self.assertTrue((self.dir / entry['id']).exists())
        for filename, content_type in (('image.svg', 'image/svg+xml'),
                                       ('image.png', 'image/svg+xml'),
                                       ('image.svg', 'image/png')):
            with self.subTest(filename=filename, content_type=content_type):
                with self.assertRaises(service.BackgroundError):
                    service.gallery_add_bytes(b'image', filename, content_type)

    def test_remove_drops_file_and_index(self):
        (self.dir / 'a.jpg').write_bytes(b'x')
        (self.dir / 'index.json').write_text(json.dumps([{'id': 'a.jpg', 'name': 'a', 'size': 1, 'added': 0, 'kind': 'image'}]), encoding='utf-8')
        self.assertTrue(service.gallery_remove('a.jpg'))
        self.assertFalse((self.dir / 'a.jpg').exists())
        self.assertEqual(service.gallery_list(), [])
        self.assertFalse(service.gallery_remove('a.jpg'))

    def test_list_drops_missing_files(self):
        (self.dir / 'index.json').write_text(json.dumps([
            {'id': 'gone.jpg', 'name': 'gone'}, {'id': 'here.jpg', 'name': 'here'},
        ]), encoding='utf-8')
        (self.dir / 'here.jpg').write_bytes(b'x')
        self.assertEqual([item['id'] for item in service.gallery_list()], ['here.jpg'])

    def test_path_guard_rejects_traversal(self):
        for identifier in ('../config/alas.json', '..\secret', '.hidden', '', 'a/b.jpg'):
            self.assertIsNone(service.gallery_path(identifier))
        (self.dir / 'ok.jpg').write_bytes(b'x')
        self.assertIsNotNone(service.gallery_path('ok.jpg'))

    def test_corrupt_index_reads_as_empty(self):
        (self.dir / 'index.json').write_text('{不是 JSON', encoding='utf-8')
        self.assertEqual(service.gallery_list(), [])


if __name__ == '__main__':
    unittest.main()
