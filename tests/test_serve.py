"""HTTP boundary tests use synthetic assets, never real campus data or models."""
import http.client
import importlib.util
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'serve.py'
spec = importlib.util.spec_from_file_location('viewer_server', SCRIPT)
serve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serve)

# This is the published input contract, intentionally independent of the
# server's implementation map: wrong aliases or an expanded allowlist must fail.
FIXTURE_PATHS = {
    'campus_data.json': 'outputs/campus_student_flow/campus_data.json',
    'campus_agents.json': 'outputs/campus_student_flow/agents.json',
    'library_agents.json': 'outputs/student_flow/agents.json',
    'layout.json': 'outputs/student_flow/layout.json',
    'Campus_Exterior.glb': 'outputs/campus_student_flow/Campus_Exterior.glb',
    'Campus_Vehicles_Animated.glb': 'outputs/campus_student_flow/Campus_Vehicles_Animated.glb',
    'Students_Animated.glb': 'outputs/student_flow/Students_Animated.glb',
    'Library_Floor_1.glb': 'outputs/student_flow/Library_Floor_1.glb',
    'Library_Floor_2.glb': 'outputs/student_flow/Library_Floor_2.glb',
    'Library_Floor_3.glb': 'outputs/student_flow/Library_Floor_3.glb',
    'Library_Floor_4.glb': 'outputs/student_flow/Library_Floor_4.glb',
    'Library_Floor_5.glb': 'outputs/student_flow/Library_Floor_5.glb',
}


def synthetic_glb(animated=False):
    document = {'asset': {'version': '2.0'}, 'nodes': [], 'scenes': [{'nodes': []}],
                'scene': 0, 'buffers': [{'byteLength': 0}]}
    if animated:
        document['animations'] = [{'name': '_defaultAnim_', 'channels': [], 'samplers': []}]
    body = json.dumps(document).encode()
    body += b' ' * (-len(body) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(body))
            + struct.pack('<II', len(body), 0x4e4f534a) + body
            + struct.pack('<II', 0, 0x004e4942))


class ServeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.assets = self.base / 'separate-assets'
        self.web = self.base / 'web'
        self.web.mkdir()
        (self.web / 'index.html').write_text('<h1>synthetic viewer</h1>')
        (self.web / 'viewer.js').write_text('export const marker = "viewer-code";')
        (self.web / 'nested').mkdir()
        (self.web / 'nested' / 'module.mjs').write_text('export default 7;')
        for name, relative in FIXTURE_PATHS.items():
            path = self.assets / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'campus_data.json':
                data = {'buildings': [], 'roads': [], 'pois': [],
                        'boundary': [[0, 0], [10, 0], [0, 10]], 'marker': 'known-test-asset'}
            elif name.endswith('_agents.json'):
                data = {'duration': 10, 'agents': [{'id': name + '-test-agent', 'events': [
                    {'t': 0, 'position': [0, 0, 0], 'floor': 0, 'state': 'walking'},
                    {'t': 10, 'position': [1, 0, 0], 'floor': 0, 'state': 'walking'}]}]}
            elif name == 'layout.json':
                data = {'seats': [], 'obstacles': []}
            else:
                path.write_bytes(synthetic_glb(name in ['Students_Animated.glb', 'Campus_Vehicles_Animated.glb']))
                continue
            path.write_text(json.dumps(data))
        self.secret = self.base / 'not-public.txt'
        self.secret.write_text('synthetic-private-marker')
        (self.assets / 'unlisted.txt').write_text('synthetic-unlisted-marker')
        self.server = None
        self.thread = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
        self.temporary.cleanup()

    def start(self):
        self.server = serve.create_server(self.assets, port=0, web_root=self.web)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, path, method='GET'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_known_json_and_model_are_read_from_the_separate_root(self):
        self.start()
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        status, headers, body = self.request('/assets/campus_data.json')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['marker'], 'known-test-asset')
        self.assertEqual(headers['Content-Type'], 'application/json')
        for name in FIXTURE_PATHS:
            with self.subTest(asset=name):
                status, headers, body = self.request('/assets/' + name)
                self.assertEqual(status, 200)
                self.assertEqual(body, (self.assets / FIXTURE_PATHS[name]).read_bytes())
                self.assertEqual(headers['Content-Type'], 'model/gltf-binary' if name.endswith('.glb') else 'application/json')

    def test_web_only_and_query_string_entrypoint(self):
        self.start()
        status, _, body = self.request('/?count=1000&tour=1')
        self.assertEqual(status, 200)
        self.assertIn(b'synthetic viewer', body)
        status, headers, body = self.request('/nested/module.mjs')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'text/javascript')
        self.assertEqual(body, b'export default 7;')

    def test_head_returns_the_real_length_without_body(self):
        self.start()
        status, headers, body = self.request('/assets/campus_data.json', 'HEAD')
        self.assertEqual(status, 200)
        self.assertEqual(int(headers['Content-Length']), (self.assets / FIXTURE_PATHS['campus_data.json']).stat().st_size)
        self.assertEqual(body, b'')
        status, _, body = self.request('/assets/not-known.json', 'HEAD')
        self.assertEqual(status, 404)
        self.assertEqual(body, b'')

    def test_unknown_assets_and_directories_never_list_private_files(self):
        self.start()
        for path in ['/assets/unlisted.txt', '/assets/', '/assets', '/assets/outputs/student_flow/agents.json', '/nested/']:
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'synthetic-unlisted-marker', body)
                self.assertNotIn(b'synthetic-private-marker', body)

    def test_raw_and_encoded_traversal_do_not_escape_web_or_assets(self):
        self.start()
        for path in ['/../not-public.txt', '/%2e%2e/not-public.txt', '/nested/../../not-public.txt',
                     '/assets/../unlisted.txt', '/assets/%2e%2e/unlisted.txt',
                     '/assets/campus_data.json%2f..%2funlisted.txt', '/%2e%2e%5cnot-public.txt',
                     '/.hidden', '/%00']:
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'synthetic-private-marker', body)

    def test_web_symlinks_cannot_escape_its_root(self):
        (self.web / 'leak.txt').symlink_to(self.secret)
        (self.web / 'linked-directory').symlink_to(self.base, target_is_directory=True)
        self.start()
        for path in ['/leak.txt', '/linked-directory/not-public.txt']:
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b'synthetic-private-marker', body)

    def test_asset_symlink_escape_is_rejected_at_startup_and_after_start(self):
        asset = self.assets / FIXTURE_PATHS['Campus_Exterior.glb']
        backup = asset.read_bytes()
        asset.unlink()
        asset.symlink_to(self.secret)
        with self.assertRaisesRegex(ValueError, 'Campus_Exterior.glb'):
            serve.create_server(self.assets, port=0, web_root=self.web)
        asset.unlink()
        asset.write_bytes(backup)
        self.start()
        asset.unlink()
        asset.symlink_to(self.secret)
        status, _, body = self.request('/assets/Campus_Exterior.glb')
        self.assertEqual(status, 404)
        self.assertNotIn(b'synthetic-private-marker', body)

    def test_missing_file_refuses_the_actual_cli_before_listening(self):
        (self.assets / FIXTURE_PATHS['Library_Floor_5.glb']).unlink()
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        result = subprocess.run([sys.executable, str(SCRIPT), '--asset-root', str(self.assets), '--port', str(port)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Library_Floor_5.glb', result.stderr)
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_invalid_json_and_glb_are_rejected_before_serving(self):
        json_asset = self.assets / FIXTURE_PATHS['layout.json']
        json_asset.write_text('{"seats":"wrong-type","obstacles":[]}')
        model_asset = self.assets / FIXTURE_PATHS['Students_Animated.glb']
        model_asset.write_bytes(b'not-a-model')
        with self.assertRaises(ValueError) as caught:
            serve.create_server(self.assets, port=0, web_root=self.web)
        self.assertIn('layout.json', str(caught.exception))
        self.assertIn('Students_Animated.glb', str(caught.exception))

    def test_check_only_validates_without_starting_server_or_writing_assets(self):
        before = {str(p.relative_to(self.assets)): p.read_bytes() for p in self.assets.rglob('*') if p.is_file()}
        result = subprocess.run([sys.executable, str(SCRIPT), '--asset-root', str(self.assets), '--check-only'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('12 allowlisted', result.stdout)
        after = {str(p.relative_to(self.assets)): p.read_bytes() for p in self.assets.rglob('*') if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
