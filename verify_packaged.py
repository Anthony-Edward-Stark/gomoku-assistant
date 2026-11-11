"""Smoke-test the frozen runtime, capture lifecycle and native engine on its build host."""
import argparse
import hashlib
import json
import os
import platform
import plistlib
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from build_app import APP_ID, APP_NAME, BACKEND, ENGINE, ROOT, VERSION


def wait_until(get, matches, timeout=20):
    end = time.monotonic() + timeout
    item = None
    while time.monotonic() < end:
        item = get()
        if matches(item):
            return item
        time.sleep(.10)
    raise AssertionError(f'Timed out: {item!r}'[:1800])


def jpeg_board():
    image = np.full((780, 780, 3), (135, 190, 225), dtype=np.uint8)
    for i in range(15):
        coord = 61 + i * 47
        cv2.line(image, (coord, 61), (coord, 719), (55, 65, 80), 2)
        cv2.line(image, (61, coord), (719, coord), (55, 65, 80), 2)
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    return encoded.tobytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='store_true', help='Test the unfrozen dashboard')
    parser.add_argument('--weight', type=Path, default=ROOT / '.build/native-engine/pikafish.nnue')
    args = parser.parse_args()
    windows = os.name == 'nt'
    xiangqi = ENGINE == 'pikafish'
    if args.source:
        import sys
        command = [sys.executable, str(ROOT / 'app.py')]
    else:
        if windows:
            bundle = ROOT / 'dist' / APP_ID
            assert (bundle / 'VERSION.txt').read_text(encoding='utf-8').strip() == VERSION
            executable = bundle / (APP_ID + '.exe')
            internal = bundle / '_internal'
            for file in (executable, internal / 'engine' / (ENGINE + '.exe')):
                data = file.read_bytes()
                offset = int.from_bytes(data[0x3c:0x40], 'little')
                assert data[offset:offset+6] == b'PE\0\0\x64\x86', file
        else:
            bundle = ROOT / 'dist' / (APP_NAME + '.app')
            plist = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
            arch = platform.machine()
            assert plist['AssistantArchitecture'] == arch and plist['CFBundleShortVersionString'] == VERSION
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle], check=True)
            executable = bundle / 'Contents/Resources/backend' / BACKEND
            internal = executable.parent / '_internal'
            for file in (executable, internal / 'bin/phone-capture', internal / 'engine' / ENGINE):
                actual = subprocess.check_output(['/usr/bin/lipo', '-archs', file], text=True).split()
                assert actual == [arch], file
        assert not list(internal.rglob('*.nnue'))
        manifest = json.loads((internal / 'engine/manifest.json').read_text(encoding='utf-8'))
        for filename, digest in manifest['sha256'].items():
            with (internal / 'engine' / filename).open('rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, filename
        assert (internal if windows else bundle / 'Contents/Resources').joinpath('source/LICENSE').is_file()
        command = [str(executable)]
    frame = jpeg_board()
    with tempfile.TemporaryDirectory(prefix='assistant-smoke-') as directory:
        session = Path(directory) / '测试 session'
        session.mkdir()
        ready = Path(directory) / 'ready.json'
        if xiangqi:
            # Put only the separately obtained file in the user-session directory.
            (session / 'pikafish.nnue').write_bytes(args.weight.read_bytes())
        env = dict(os.environ)
        if not args.source:
            env.pop('ASSISTANT_ENGINE_DIR', None)
        process = subprocess.Popen([*command, '--no-open-browser', '--browser-capture', '--port', '0',
                                    '--session', str(session), '--ready-file', str(ready), '--duration', '65'], env=env)
        url = None
        try:
            wait_until(lambda: (ready.is_file(), process.poll()), lambda x: x == (True, None), 25)
            startup = json.loads(ready.read_text(encoding='utf-8'))
            assert startup['capture_pid'] is None
            url = startup['url']

            def call(path, data=None, *, token=None, origin=None, status=200):
                headers = {}
                if data is not None:
                    jpeg = isinstance(data, bytes)
                    data = data if jpeg else json.dumps(data).encode()
                    headers.update({'Content-Type': 'image/jpeg' if jpeg else 'application/json', 'Origin': origin or url})
                if token:
                    headers['X-Capture-Token'] = token
                request = urllib.request.Request(url + path, data=data, headers=headers)
                try:
                    response = urllib.request.urlopen(request, timeout=5)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    assert response.code == status, (path, response.code)
                    return json.load(response) if response.headers.get_content_type() == 'application/json' else response.read()

            state = lambda: call('/api/state')
            assert state()['source'] == 'browser_window'
            assert b'getDisplayMedia' in call('/browser_capture.js')
            call('/api/capture/start', {}, origin='https://example.com', status=403)
            lease = call('/api/capture/start', {})['token']
            call('/api/capture/frame', b'not-a-jpeg', token=lease, status=400)
            call('/api/capture/frame', frame, token=lease)
            wait_until(state, lambda s: s['frame_id'] is not None, 6)
            assert call('/frame.jpg') == frame
            next_lease = call('/api/capture/start', {})['token']
            call('/api/capture/frame', frame, token=lease, status=409)
            assert state()['frame_id'] is None
            if xiangqi:
                from rules import START_BOARD, legal_moves, to_fen
                call('/api/settings', {'budget_ms': 500})
                call('/api/fen', {'fen': to_fen(START_BOARD, 'red')})
                suggested = wait_until(state, lambda s: s['status'] == 'ready', 16)
                assert suggested['suggestion']['move'] in legal_moves(START_BOARD, 'red')
                reset = call('/api/reset-game', {})
                assert reset['source'] == 'browser_window' and reset['suggestion'] is None
                call('/api/capture/frame', frame, token=next_lease)
                wait_until(state, lambda s: s['frame_id'] is not None, 6)
            else:
                call('/api/settings', {'my_color': 1, 'budget_ms': 500})
                def next_frame():
                    call('/api/capture/frame', frame, token=next_lease)
                    return state()
                suggested = wait_until(next_frame, lambda s: s['status'] == 'ready', 16)
                x, y = suggested['suggestion']['move']
                assert 0 <= x < 15 and 0 <= y < 15 and suggested['board'][y][x] == 0
            wait_until(state, lambda s: s['status'] == 'stale' and s['frame_id'] is None and s['suggestion'] is None, 6)
            call('/frame.jpg', status=404)
            call('/api/capture/end', {}, token=next_lease)
            assert state()['status'] == 'waiting_window'
            call('/api/capture/frame', frame, token=next_lease, status=409)
            assert not (session / 'latest.jpg').exists() and not (session / 'latest-state.json').exists()
            call('/api/stop', {})
            assert process.wait(timeout=20) == 0
            print(f'{APP_ID} {platform.machine()}: capture, stale-frame and engine smoke tests passed')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    main()
