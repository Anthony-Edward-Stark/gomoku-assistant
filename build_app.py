"""Build the desktop assistant with its native engine and bundled Python runtime."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_ID = 'GomokuAssistant'
APP_NAME = '五子棋助手'
BUNDLE_ID = 'local.gomoku.assistant'
BACKEND = 'gomoku-backend'
ENGINE = 'pbrain-rapfi'
VERSION = '1.2.0'
DEFAULT_SETTINGS = {'rule': 'freestyle', 'size': 15, 'budget_ms': 2000, 'my_color': 2, 'paused': False}
EXTRA_SOURCE = ('verify_strength.py',)


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def checksum(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def copy_source(resources):
    source = resources / 'source'
    source.mkdir()
    for name in ('LICENSE', 'README.md', 'app.py', 'vision.py', 'engine.py', 'index.html',
                 'browser_capture.py', 'browser_capture.js', 'requirements.txt', 'capture.swift',
                 'build_app.py', 'prepare_engine.py', 'pack_release.py', 'verify.py', 'verify_packaged.py',
                 'run.sh', *EXTRA_SOURCE):
        shutil.copy2(ROOT / name, source / name)
    shutil.copytree(ROOT / 'macos', source / 'macos')
    licenses = resources / 'licenses'
    licenses.mkdir()
    python_license = next(p for p in (Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt',
                                      Path(sys.base_prefix) / 'LICENSE.txt') if p.is_file())
    shutil.copy2(python_license, licenses / 'Python.txt')
    pyinstaller = importlib.metadata.distribution('pyinstaller')
    license_file = next(p for p in pyinstaller.files if p.as_posix().endswith('licenses/COPYING.txt'))
    shutil.copy2(pyinstaller.locate_file(license_file), licenses / 'PyInstaller.txt')
    shutil.copy2(ROOT / 'README.md', resources / '说明.md')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-dir', type=Path, default=ROOT / '.build/app')
    parser.add_argument('--engine-dir', type=Path, default=ROOT / '.build/native-engine')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--settings', type=Path)
    args = parser.parse_args()
    windows = os.name == 'nt'
    arch = platform.machine().lower()
    if not (windows and arch == 'amd64' or sys.platform == 'darwin' and arch in {'x86_64', 'arm64'}):
        raise SystemExit('Use a native macOS Intel/arm64 or Windows x64 build host')
    app = (args.output or ROOT / 'dist' / (APP_ID if windows else APP_NAME + '.app')).resolve()
    build = args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=True)
    engine = args.engine_dir.resolve()
    if engine == ROOT / '.build/native-engine' and not engine.exists() and arch == 'x86_64':
        engine = ROOT / 'engine'
    manifest = json.loads((engine / 'manifest.json').read_text(encoding='utf-8'))
    binary = ENGINE + ('.exe' if windows else '')
    if binary not in manifest['sha256'] or any(n.lower().endswith('.nnue') for n in manifest['sha256']):
        raise ValueError('Expected a native engine manifest without private or separately licensed NNUE files')
    if not windows and arch not in subprocess.check_output(['/usr/bin/lipo', '-archs', engine / binary], text=True).split():
        raise ValueError('Engine architecture mismatch; run prepare_engine.py first')
    for name, expected in manifest['sha256'].items():
        if checksum(engine / name) != expected:
            raise ValueError(f'Engine file checksum mismatch: {name}')
    engine_files = tuple(dict.fromkeys((*manifest['sha256'], 'manifest.json',
                        *(name for name in ('AUTHORS', 'NETWORKS.txt') if (engine / name).is_file()))))
    exe_name = APP_ID if windows else BACKEND
    extras = ['--windowed'] if windows else ['--target-architecture', arch]
    if not windows:
        capture = build / 'phone-capture'
        run('/usr/bin/swiftc', '-O', '-target', f'{arch}-apple-macosx14.0', ROOT / 'capture.swift', '-o', capture)
        extras += ['--add-binary', f'{capture}:bin']
    data = [arg for name in engine_files for arg in ('--add-data', f'{engine / name}:engine')]
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--noupx',
        '--name', exe_name, '--distpath', build / 'dist', '--workpath', build / 'pyi', '--specpath', build,
        '--paths', ROOT, '--add-data', f'{ROOT / "index.html"}:.', '--add-data', f'{ROOT / "browser_capture.js"}:.',
        '--copy-metadata', 'numpy', '--copy-metadata', 'opencv-python-headless', *extras, *data, ROOT / 'app.py')
    if app.exists():
        expected_file = app / ('_internal/source/build_app.py' if windows else 'Contents/Info.plist')
        if not expected_file.is_file() or not windows and plistlib.loads(expected_file.read_bytes()).get('CFBundleIdentifier') != BUNDLE_ID:
            raise RuntimeError(f'Refusing to replace an unrelated output: {app}')
        shutil.rmtree(app)
    if windows:
        shutil.copytree(build / 'dist' / exe_name, app)
        resources = app / '_internal'
        copy_source(resources)
        shutil.copy2(ROOT / 'README.md', app / 'README.md')
        (app / 'VERSION.txt').write_text(VERSION + '\n', encoding='utf-8')
    else:
        contents = app / 'Contents'
        resources = contents / 'Resources'
        executable = contents / 'MacOS' / APP_ID
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        shutil.copytree(build / 'dist' / exe_name, resources / 'backend', symlinks=True)
        run('/usr/bin/swiftc', '-O', '-target', f'{arch}-apple-macosx14.0', '-framework', 'AppKit',
            '-framework', 'WebKit', ROOT / 'macos/Launcher.swift', '-o', executable)
        run('/usr/bin/swiftc', '-O', ROOT / 'macos/Icon.swift', '-o', build / 'make-icon')
        run(build / 'make-icon', build / 'Assistant.iconset')
        run('/usr/bin/iconutil', '-c', 'icns', build / 'Assistant.iconset', '-o', resources / 'Assistant.icns')
        settings = dict(DEFAULT_SETTINGS)
        if args.settings:
            saved = json.loads(args.settings.read_text(encoding='utf-8'))
            settings.update({key: saved[key] for key in settings.keys() - {'paused'} if key in saved})
        (resources / 'default-settings.json').write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')
        copy_source(resources)
        (contents / 'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': APP_ID, 'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': APP_NAME,
            'CFBundleDisplayName': APP_NAME, 'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': VERSION,
            'CFBundleVersion': '3', 'CFBundleIconFile': 'Assistant.icns', 'LSMinimumSystemVersion': '14.0',
            'AssistantArchitecture': arch, 'NSHighResolutionCapable': True,
            'NSCameraUsageDescription': f'读取 USB 连接的 iPhone 屏幕，用于{APP_NAME}识别棋盘。',
            'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
        }))
        (contents / 'PkgInfo').write_bytes(b'APPL????')
        run('/usr/bin/codesign', '--force', '--deep', '--sign', '-', '--timestamp=none', app)
        bundled = resources / 'backend/_internal/engine'
        manifest['pre_packaging_sha256'] = dict(manifest['sha256'])
        manifest['sha256'] = {name: checksum(bundled / name) for name in manifest['sha256']}
        manifest['packaging'] = {'target': f'macOS {arch}', 'signature': 'ad-hoc'}
        (bundled / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        run('/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', app)
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
    print(app)


if __name__ == '__main__':
    main()
