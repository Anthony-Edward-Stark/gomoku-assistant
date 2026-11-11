"""Prepare a native engine from the pinned sources for building and testing."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def run(*args):
    subprocess.run(list(map(str, args)), check=True)


def network_archive(settings, cache, override):
    archive = override or cache / 'Pikafish.2026-09-06.7z'
    if override is None and not archive.exists():
        pending = archive.with_suffix('.part')
        with urllib.request.urlopen(settings['download'], timeout=45) as response, pending.open('wb') as file:
            shutil.copyfileobj(response, file)
        if digest(pending) != settings['archive_sha256']:
            raise ValueError('Pikafish release archive checksum mismatch')
        pending.replace(archive)
    if digest(archive) != settings['archive_sha256']:
        raise ValueError('Pikafish release archive checksum mismatch')
    return archive.resolve()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / '.build/native-engine')
    parser.add_argument('--network-archive', type=Path)
    args = parser.parse_args()
    native = args.output.resolve()
    native.mkdir(parents=True, exist_ok=True)
    cache = native.parent / 'upstream'
    cache.mkdir(exist_ok=True)
    host = platform.machine().lower()
    mac = sys.platform == 'darwin'
    win = os.name == 'nt'
    if not (mac and host in {'x86_64', 'arm64'} or win and host == 'amd64'):
        raise SystemExit('Supported native build hosts: Intel/Apple silicon macOS or Windows x64')
    original = ROOT / 'engine'
    manifest = json.loads((original / 'manifest.json').read_text(encoding='utf-8'))
    pika = manifest.get('engine') == 'Pikafish'
    engine = 'pikafish' if pika else 'pbrain-rapfi'
    source = 'pikafish-source.tar.gz' if pika else 'rapfi-source.tar.gz'
    checks = dict(manifest['sha256'])
    for name, expected in checks.items():
        path = original / name
        if digest(path) != expected:
            raise ValueError(f'Pinned input checksum mismatch: {name}')
        if name != engine:
            shutil.copy2(path, native / name)
    for name in ('AUTHORS', 'NETWORKS.txt'):
        if name not in checks and (original / name).is_file():
            shutil.copy2(original / name, native / name)
            checks[name] = digest(native / name)
    if pika:
        settings = manifest['network']
        archive = network_archive(settings, cache, args.network_archive)
        if win:
            sevenzip = shutil.which('7z') or Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe'
            run(sevenzip, 'e', '-y', f'-o{cache}', archive, 'pikafish.nnue', 'Pikafish-Windows-x86-64-universal.exe')
        else:
            run('/usr/bin/tar', '-xf', archive, '-C', cache, 'pikafish.nnue')
        weight = cache / 'pikafish.nnue'
        if digest(weight) != settings['file_sha256']:
            raise ValueError('Pikafish NNUE checksum mismatch')
        # Used by tests and local source runs. The public build only packages the manifest files.
        shutil.copy2(weight, native / weight.name)
    binary = engine + ('.exe' if win else '')
    if mac and host == 'x86_64':
        built = original / engine
        build_note = manifest.get('build') or manifest.get('cmake_options')
    elif pika and win:
        built = cache / 'Pikafish-Windows-x86-64-universal.exe'
        build_note = 'Official Pikafish Windows executable from the checksum-pinned release archive'
    else:
        tree = cache / ('pikafish-src' if pika else 'rapfi-src')
        tree.mkdir(exist_ok=True)
        with tarfile.open(original / source) as archive:
            archive.extractall(tree, filter='data')
        if pika:
            src = next(tree.glob('*/src'))
            shutil.copy2(native / 'pikafish.nnue', src / 'pikafish.nnue')
            run('make', '-C', src, '-j', '3', 'build', 'ARCH=apple-silicon', 'COMP=clang')
            built = src / engine
            build_note = 'make -C src -j 3 build ARCH=apple-silicon COMP=clang'
        else:
            src = tree / 'Rapfi'
            build = cache / 'rapfi-build'
            opts = ['-DCMAKE_BUILD_TYPE=Release', '-DNO_COMMAND_MODULES=ON', '-DENABLE_LTO=OFF',
                    '-DUSE_AVX2=OFF', '-DUSE_AVX512=OFF', '-DUSE_BMI2=OFF', '-DUSE_VNNI=OFF',
                    '-DUSE_SSE=ON' if win else '-DUSE_SSE=OFF',
                    '-DUSE_NEON=OFF' if win else '-DUSE_NEON=ON', '-DUSE_NEON_DOTPROD=OFF']
            if win:
                opts += ['-G', 'Visual Studio 17 2022', '-A', 'x64', '-T', 'ClangCL']
            else:
                opts += ['-DCMAKE_OSX_ARCHITECTURES=arm64', '-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0']
            run('cmake', '-S', src, '-B', build, *opts)
            run('cmake', '--build', build, '--config', 'Release', '--parallel', '3')
            built = build / 'Release' / binary if win else build / binary
            build_note = opts
    shutil.copy2(built, native / binary)
    checks.pop(engine)
    checks[binary] = digest(native / binary)
    manifest.update(sha256=checks, target=f'{sys.platform} {host}', native_build=build_note)
    (native / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Prepared {binary} in {native}')


if __name__ == '__main__':
    main()
