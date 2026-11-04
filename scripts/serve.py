#!/usr/bin/env python3
"""Serve viewer code and an explicit set of separately obtained local assets."""
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
from pathlib import Path
import shutil
import struct
from urllib.parse import unquote, urlsplit
import webbrowser


WEB_ROOT = Path(__file__).resolve().parent.parent / 'web'
ASSET_PATHS = {
    'campus_data.json': 'outputs/campus_student_flow/campus_data.json',
    'campus_agents.json': 'outputs/campus_student_flow/agents.json',
    'library_agents.json': 'outputs/student_flow/agents.json',
    'layout.json': 'outputs/student_flow/layout.json',
    'Campus_Exterior.glb': 'outputs/campus_student_flow/Campus_Exterior.glb',
    'Campus_Vehicles_Animated.glb': 'outputs/campus_student_flow/Campus_Vehicles_Animated.glb',
    'Students_Animated.glb': 'outputs/student_flow/Students_Animated.glb',
    **{f'Library_Floor_{floor}.glb': f'outputs/student_flow/Library_Floor_{floor}.glb'
       for floor in range(1, 6)},
}


def contained_file(root, relative):
    """Resolve a known relative name while rejecting missing files and link escapes."""
    try:
        path = (root / relative).resolve(strict=True)
        path.relative_to(root)
        return path if path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def vector(value, length):
    return isinstance(value, list) and len(value) == length and all(finite_number(n) for n in value)


def reject_constant(value):
    raise ValueError(f'Non-finite JSON number: {value}')


def check_json(name, path):
    with path.open(encoding='utf-8') as handle:
        data = json.load(handle, parse_constant=reject_constant)
    if not isinstance(data, dict):
        raise ValueError('JSON root must be an object')
    if name == 'campus_data.json':
        if not all(isinstance(data.get(key), list) for key in ['buildings', 'roads', 'pois', 'boundary']):
            raise ValueError('campus data requires buildings, roads, pois and boundary arrays')
        if len(data['boundary']) < 3 or not all(vector(point, 2) for point in data['boundary']):
            raise ValueError('campus boundary requires at least three finite 2D points')
    elif name in ['campus_agents.json', 'library_agents.json']:
        if not finite_number(data.get('duration')) or data['duration'] <= 0 or not isinstance(data.get('agents'), list):
            raise ValueError('agent data requires a positive duration and agents array')
        ids = set()
        for agent in data['agents']:
            if not isinstance(agent, dict) or not isinstance(agent.get('id'), str) or not agent['id'] or agent['id'] in ids:
                raise ValueError('agents require unique nonempty string IDs')
            ids.add(agent['id'])
            if not isinstance(agent.get('events'), list) or not agent['events']:
                raise ValueError('each agent requires nonempty events')
            previous = -math.inf
            for event in agent['events']:
                if (not isinstance(event, dict) or not finite_number(event.get('t')) or event['t'] < previous
                        or not vector(event.get('position'), 3) or not isinstance(event.get('state'), str)
                        or not finite_number(event.get('floor'))):
                    raise ValueError('events require ordered times, positions, states and floors')
                previous = event['t']
    elif name == 'layout.json':
        if not all(isinstance(data.get(key), list) for key in ['seats', 'obstacles']):
            raise ValueError('layout requires seats and obstacles arrays')
        if any(not isinstance(seat, dict) or not vector(seat.get('position'), 3) for seat in data['seats']):
            raise ValueError('seats require finite 3D positions')
        if any(not isinstance(item, dict) or not vector(item.get('min'), 3) or not vector(item.get('max'), 3)
               for item in data['obstacles']):
            raise ValueError('obstacles require finite min/max 3D positions')


def check_glb(name, path):
    size = path.stat().st_size
    with path.open('rb') as handle:
        header = handle.read(20)
        if len(header) != 20:
            raise ValueError('truncated GLB header')
        magic, version, declared_size, json_size, chunk_type = struct.unpack('<4sIIII', header)
        if magic != b'glTF' or version != 2 or declared_size != size or chunk_type != 0x4e4f534a:
            raise ValueError('invalid GLB v2 header, size or JSON chunk')
        if json_size % 4 or 20 + json_size > size:
            raise ValueError('invalid GLB JSON chunk length')
        data = json.loads(handle.read(json_size), parse_constant=reject_constant)
        if not isinstance(data, dict) or data.get('asset', {}).get('version') != '2.0':
            raise ValueError('GLB requires glTF asset version 2.0')
        if not isinstance(data.get('nodes'), list) or not isinstance(data.get('scenes'), list) or not data['scenes']:
            raise ValueError('GLB requires node and scene arrays')
        binary_header = handle.read(8)
        if len(binary_header) != 8:
            raise ValueError('GLB requires an embedded binary chunk')
        binary_size, binary_type = struct.unpack('<II', binary_header)
        if binary_type != 0x004e4942 or binary_size % 4 or 28 + json_size + binary_size != size:
            raise ValueError('invalid GLB embedded binary chunk')
        buffers = data.get('buffers')
        if (not isinstance(buffers, list) or len(buffers) != 1 or not isinstance(buffers[0], dict)
                or buffers[0].get('uri') or not isinstance(buffers[0].get('byteLength'), int)
                or not 0 <= buffers[0]['byteLength'] <= binary_size):
            raise ValueError('GLB requires one embedded buffer with valid length')
        views = data.get('bufferViews', [])
        if not isinstance(views, list):
            raise ValueError('GLB bufferViews must be an array')
        for view in views:
            if (not isinstance(view, dict) or view.get('buffer', 0) != 0
                    or not isinstance(view.get('byteOffset', 0), int) or view.get('byteOffset', 0) < 0
                    or not isinstance(view.get('byteLength'), int) or view['byteLength'] < 0
                    or view.get('byteOffset', 0) + view['byteLength'] > buffers[0]['byteLength']):
                raise ValueError('GLB buffer view exceeds its embedded buffer')
        for image in data.get('images', []):
            if not isinstance(image, dict):
                raise ValueError('invalid GLB image')
            if 'bufferView' in image:
                if not isinstance(image['bufferView'], int) or not 0 <= image['bufferView'] < len(views):
                    raise ValueError('invalid GLB embedded image buffer view')
            elif not image.get('uri', '').startswith('data:'):
                raise ValueError('GLB external images are not allowed')
        if name in ['Students_Animated.glb', 'Campus_Vehicles_Animated.glb']:
            animations = data.get('animations', [])
            if not isinstance(animations, list) or not any(isinstance(clip, dict) and clip.get('name') == '_defaultAnim_' for clip in animations):
                raise ValueError('animated GLB requires the _defaultAnim_ clip')


def validate_assets(asset_root):
    """Check exactly the twelve contract paths; never enumerate the private tree."""
    root = Path(asset_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('--asset-root must be a directory')
    failures = []
    for name, relative in ASSET_PATHS.items():
        path = contained_file(root, relative)
        if path is None:
            failures.append(f'{name}: missing, not a regular file, or outside --asset-root')
            continue
        try:
            if name.endswith('.json'):
                check_json(name, path)
            else:
                check_glb(name, path)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error) as error:
            failures.append(f'{name}: {error}')
    if failures:
        raise ValueError('Asset preflight failed:\n' + '\n'.join('- ' + failure for failure in failures))
    return root


class ViewerHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, asset_root, web_root, **kwargs):
        self.asset_root = asset_root
        self.web_root = web_root
        super().__init__(*args, **kwargs)

    def requested_file(self):
        try:
            requested = unquote(urlsplit(self.path).path, errors='strict')
        except (UnicodeError, ValueError):
            return None
        if not requested.startswith('/') or '\\' in requested or '\x00' in requested:
            return None
        if requested == '/':
            return contained_file(self.web_root, 'index.html')
        parts = requested[1:].split('/')
        if any(not part or part.startswith('.') for part in parts):
            return None
        if parts[0] == 'assets':
            if len(parts) != 2 or parts[1] not in ASSET_PATHS:
                return None
            return contained_file(self.asset_root, ASSET_PATHS[parts[1]])
        return contained_file(self.web_root, '/'.join(parts))

    def respond(self, send_body):
        path = self.requested_file()
        if path is None:
            self.send_error(404, 'Not found')
            return
        try:
            handle = path.open('rb')
        except OSError:
            self.send_error(404, 'Not found')
            return
        with handle:
            import os
            size = os.fstat(handle.fileno()).st_size
            content_type = {'.js': 'text/javascript', '.mjs': 'text/javascript',
                            '.glb': 'model/gltf-binary', '.json': 'application/json'}.get(path.suffix)
            if content_type is None:
                content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(size))
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if send_body:
                try:
                    shutil.copyfileobj(handle, self.wfile)
                except (BrokenPipeError, ConnectionResetError):
                    pass

    def do_GET(self):
        self.respond(True)

    def do_HEAD(self):
        self.respond(False)

    def log_message(self, format, *args):
        # Avoid logging arbitrary query strings while serving a private asset tree.
        pass


def create_server(asset_root, port=8768, web_root=WEB_ROOT):
    root = validate_assets(asset_root)
    web = Path(web_root).resolve(strict=True)
    if not web.is_dir() or contained_file(web, 'index.html') is None:
        raise ValueError('web/index.html is missing or outside the web directory')
    handler = partial(ViewerHandler, asset_root=root, web_root=web)
    return ThreadingHTTPServer(('127.0.0.1', port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root', required=True, type=Path,
                        help='Separately obtained original 90_完整工程 directory; only 12 allowlisted files are read')
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--check-only', action='store_true', help='Validate all assets without starting a server')
    parser.add_argument('--open', action='store_true', help='Open the local integrated viewer in a browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    try:
        if args.check_only:
            validate_assets(args.asset_root)
            print('PASS: all 12 allowlisted JSON/GLB assets satisfy the input contract.')
            return 0
        server = create_server(args.asset_root, port=args.port)
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')
    url = f'http://127.0.0.1:{server.server_port}/?count=1000&tour=1'
    print(url, flush=True)
    print('Keep this terminal open. Ctrl+C stops the server.', flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
