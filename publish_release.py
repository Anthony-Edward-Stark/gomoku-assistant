"""Publish a Release only after all native packages have been tested and hashed."""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from build_app import APP_ID, APP_NAME, ENGINE, VERSION


def gh(*args, missing=False):
    result = subprocess.run(['gh', *map(str, args)], capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        if missing and ('HTTP 404' in result.stderr or 'release not found' in result.stderr.lower()):
            return None
        raise RuntimeError(result.stderr)
    return result.stdout


def main():
    repo, commit = os.environ['GITHUB_REPOSITORY'], os.environ['GITHUB_SHA']
    tag = 'v' + VERSION
    xiangqi = ENGINE == 'pikafish'
    suffix = '.dmg' if xiangqi else '.zip'
    folder = Path('release')
    packages = [folder / (APP_ID + '-Windows-x64.zip'),
                *[folder / (APP_ID + '-macOS-' + arch + suffix) for arch in ('AppleSilicon', 'Intel')]]
    digests = {}
    for file in packages:
        with file.open('rb') as stream:
            digests[file.name] = 'sha256:' + hashlib.file_digest(stream, 'sha256').hexdigest()
    ref = gh('api', f'repos/{repo}/git/ref/tags/{tag}', missing=True)
    if ref:
        assert json.loads(ref)['object']['sha'] == commit, 'Tag points to a different commit'
    release = gh('release', 'view', tag, '--repo', repo, '--json', 'databaseId', missing=True)
    if release is None:
        notes = folder / 'notes.md'
        instructions = ('新增 Apple 芯片 Mac 和 Windows x64，继续提供 Intel Mac。\n\n'
                        'Mac 保留 USB 取屏。Windows 解压 ZIP 后启动助手，通过 Edge 或 Chrome 选择已有 iPhone 投屏窗口。\n\n')
        if xiangqi:
            instructions += ('首次使用需按 README 从 Pikafish 2026-09-06 官方发布包取出 `pikafish.nnue`。\n\n')
        instructions += '安装与签名说明见仓库 README。\n\n'
        instructions += '\n'.join(f'- `{name}`: `{digest}`' for name, digest in digests.items()) + '\n'
        notes.write_text(instructions, encoding='utf-8')
        gh('release', 'create', tag, '--repo', repo, '--target', commit, '--draft',
           '--title', f'{APP_NAME} {VERSION}', '--notes-file', notes)
        release = gh('release', 'view', tag, '--repo', repo, '--json', 'databaseId')
    api = f'repos/{repo}/releases/{json.loads(release)["databaseId"]}'
    current = json.loads(gh('api', api))
    if current['draft']:
        assert current['target_commitish'] == commit or ref is not None
        assets = {asset['name']: asset for asset in current['assets']}
        for file in packages:
            asset = assets.get(file.name)
            if asset and asset['state'] == 'starter' and asset['size'] == 0:
                gh('api', '--method', 'DELETE', f'repos/{repo}/releases/assets/{asset["id"]}')
                asset = None
            if asset is None:
                gh('release', 'upload', tag, file, '--repo', repo)
    current = json.loads(gh('api', api))
    actual = {asset['name']: asset['digest'] for asset in current['assets'] if asset['state'] == 'uploaded'}
    assert actual == digests, 'Release asset checksum mismatch'
    if current['draft']:
        gh('release', 'edit', tag, '--repo', repo, '--draft=false', '--latest')
    result = json.loads(gh('api', f'repos/{repo}/releases/tags/{tag}'))
    assert not result['draft'] and not result['prerelease']
    assert json.loads(gh('api', f'repos/{repo}/git/ref/tags/{tag}'))['object']['sha'] == commit
    print(result['html_url'])


if __name__ == '__main__':
    main()
