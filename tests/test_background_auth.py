"""背景上传沿用 WebSocket 的授权边界。"""
import tempfile
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from module.api.app import create_app
from tests.test_api import fixture


class BackgroundAuthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = fixture(directory.name)
        library = patch('module.api.app.LIBRARY_DIR', self.root / 'background-library')
        library.start()
        self.addCleanup(library.stop)

    def app(self, password='test-secret'):
        return create_app(root=self.root, password=password, manage_runtime=False)

    def token(self, client, password='test-secret', headers=None):
        with client.websocket_connect('/api/v1/ws', headers=headers or {}) as ws:
            session = ws.receive_json()
            if session['data']['authRequired']:
                ws.send_json({'v': 1, 'type': 'request', 'id': 'login', 'method': 'auth.login',
                              'params': {'password': password}})
                self.assertTrue(ws.receive_json()['ok'])
            ws.send_json({'v': 1, 'type': 'request', 'id': 'token', 'method': 'background.access', 'params': {}})
            return ws.receive_json()['result']['token']

    def test_unauthenticated_websocket_cannot_issue_token(self):
        with TestClient(self.app()) as client, client.websocket_connect('/api/v1/ws') as ws:
            self.assertTrue(ws.receive_json()['data']['authRequired'])
            ws.send_json({'v': 1, 'type': 'request', 'id': 'token', 'method': 'background.access', 'params': {}})
            self.assertEqual('UNAUTHORIZED', ws.receive_json()['error']['code'])

    def test_upload_requires_valid_token_and_rejects_query_tokens(self):
        with TestClient(self.app()) as client, patch('module.api.app.gallery_add_bytes', return_value={'id': 'sample'}) as add:
            token = self.token(client)
            self.assertNotEqual('test-secret', token)
            for headers, params in (({}, {}), ({'x-azurpilot-background-token': 'invalid'}, {}),
                                    ({}, {'token': token})):
                response = client.post('/api/v1/background/gallery', headers=headers, params=params,
                                       files={'file': ('test.png', b'image', 'image/png')})
                self.assertEqual(401, response.status_code)
            add.assert_not_called()
            response = client.post('/api/v1/background/gallery', headers={'x-azurpilot-background-token': token},
                                   files={'file': ('test.png', b'image', 'image/png')})
            self.assertEqual(200, response.status_code)
            add.assert_called_once_with(b'image', 'test.png', 'image/png')

    def test_demo_cannot_upload_even_with_token(self):
        with patch.dict('os.environ', {'DEMO': '1'}), TestClient(self.app(password='')) as client:
            token = self.token(client)
            with patch('module.api.app.gallery_add_bytes') as add:
                response = client.post('/api/v1/background/gallery',
                                       headers={'x-azurpilot-background-token': token},
                                       files={'file': ('test.png', b'image', 'image/png')})
                self.assertEqual(403, response.status_code)
                add.assert_not_called()

    def test_new_application_rejects_previous_token(self):
        with TestClient(self.app()) as client:
            old_token = self.token(client)
        with TestClient(self.app()) as client, patch('module.api.app.gallery_add_bytes') as add:
            self.assertNotEqual(old_token, self.token(client))
            response = client.post('/api/v1/background/gallery', headers={'x-azurpilot-background-token': old_token})
            self.assertEqual(401, response.status_code)
            add.assert_not_called()

    def test_passwordless_mode_can_issue_background_token(self):
        with TestClient(self.app(password=''), client=('127.0.0.1', 55555),
                        base_url='http://localhost') as client:
            self.assertTrue(self.token(client, headers={'host': 'localhost'}))

    def test_network_background_methods_and_route_are_absent(self):
        app = self.app()
        self.assertNotIn('background.resolve', app.state.gateway.router.methods)
        self.assertNotIn('background.gallery.add', app.state.gateway.router.methods)
        self.assertFalse(any(getattr(route, 'path', '') == '/api/v1/background/media' for route in app.routes))
        with TestClient(app) as client, client.websocket_connect('/api/v1/ws') as ws:
            self.assertTrue(ws.receive_json()['data']['authRequired'])
            ws.send_json({'v': 1, 'type': 'request', 'id': 'login', 'method': 'auth.login',
                          'params': {'password': 'test-secret'}})
            self.assertTrue(ws.receive_json()['ok'])
            for method in ('background.resolve', 'background.gallery.add'):
                ws.send_json({'v': 1, 'type': 'request', 'id': method, 'method': method, 'params': {}})
                self.assertEqual('METHOD_NOT_FOUND', ws.receive_json()['error']['code'])

    def test_gallery_serves_only_inert_media_with_protective_headers(self):
        library = self.root / 'background-library'
        library.mkdir(exist_ok=True)
        (library / 'legacy.svg').write_text('<svg/>')
        (library / 'index.json').write_text('{}')
        (library / 'wallpaper.png').write_bytes(b'image')
        with TestClient(self.app()) as client:
            self.assertEqual(404, client.get('/background-library/legacy.svg').status_code)
            self.assertEqual(404, client.get('/background-library/index.json').status_code)
            response = client.get('/background-library/wallpaper.png')
            self.assertEqual(200, response.status_code)
            self.assertEqual('image/png', response.headers['content-type'])
            self.assertEqual('nosniff', response.headers['x-content-type-options'])
            self.assertIn('sandbox', response.headers['content-security-policy'])
