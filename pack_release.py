"""Archive the validated native desktop build with a platform-specific filename."""
import os
import platform
import shutil
from pathlib import Path
from build_app import APP_ID, APP_NAME, ENGINE, ROOT, run


def main():
    folder = ROOT / 'release'
    folder.mkdir(exist_ok=True)
    if os.name == 'nt':
        bundle = ROOT / 'dist' / APP_ID
        archive = shutil.make_archive(str(folder / (APP_ID + '-Windows-x64')), 'zip', bundle.parent, bundle.name)
        print(archive)
        return
    bundle = ROOT / 'dist' / (APP_NAME + '.app')
    label = 'AppleSilicon' if platform.machine() == 'arm64' else 'Intel'
    target = folder / (APP_ID + '-macOS-' + label)
    if ENGINE == 'pikafish':
        import sys
        run(sys.executable, ROOT / 'build_dmg.py', '--app', bundle, '--output', target.with_suffix('.dmg'))
    else:
        run('/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', bundle, target.with_suffix('.zip'))


if __name__ == '__main__':
    main()
