"""背景上传与服务端代抓不得绕过 WebUI 的登录校验。"""
import tempfile
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from module.api.app import create_app
from tests.test_api import fixture


class BackgroundAuthTests(unittest.TestCase):
    def test_http_requires_token_from_authenticated_websocket(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(root=fixture(directory), password='test-secret', manage_runtime=False)
            with TestClient(app) as client, patch('module.api.app.proxy_fetch', return_value=(b'image', 'image/png')) as fetch:
                self.assertEqual(401, client.get('/api/v1/background/media?url=https://example.com/a.png').status_code)
                self.assertEqual(401, client.post('/api/v1/background/gallery').status_code)
                fetch.assert_not_called()
                with client.websocket_connect('/api/v1/ws') as ws:
                    ws.receive_json()
                    def request(identifier, method, params):
                        ws.send_json({'v': 1, 'type': 'request', 'id': identifier, 'method': method, 'params': params})
                        return ws.receive_json()
                    self.assertEqual('UNAUTHORIZED', request('1', 'background.access', {})['error']['code'])
                    self.assertTrue(request('2', 'auth.login', {'password': 'test-secret'})['ok'])
                    token = request('3', 'background.access', {})['result']['token']
                response = client.get('/api/v1/background/media', params={'url': 'https://example.com/a.png', 'token': token})
                self.assertEqual(200, response.status_code)
                self.assertEqual(b'image', response.content)
                fetch.assert_called_once_with('https://example.com/a.png')
                with patch('module.api.app.gallery_add_bytes', return_value={'id': 'sample'}) as add:
                    response = client.post('/api/v1/background/gallery', headers={'x-azurpilot-background-token': token}, files={'file': ('test.png', b'image', 'image/png')})
                    self.assertEqual(200, response.status_code)
                    add.assert_called_once()
