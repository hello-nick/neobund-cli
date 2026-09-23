#!/usr/bin/env python3
"""Install the self-contained skill and a user-local CLI launcher."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path,
                        default=Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))/'skills')
    parser.add_argument('--bin-dir', type=Path, default=Path.home()/'.local/bin')
    args = parser.parse_args()
    target = args.skill_root.expanduser().resolve()/'neobund-publish'
    launcher = args.bin_dir.expanduser().resolve()/'neobund'
    if target.exists() or target.is_symlink() or launcher.exists() or launcher.is_symlink():
        parser.error('目标已存在，未覆盖。请指定其他目录或先检查已有安装。')
    content = '#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(target/'scripts/neobund.py')) + ' "$@"\n'
    import tempfile
    copied = False
    created_launcher = False
    try:
        launcher.parent.mkdir(parents=True, exist_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.neobund-install-', dir=target.parent) as staging:
            staged = Path(staging)/'neobund-publish'
            shutil.copytree(Path(__file__).resolve().parent/'skill/neobund-publish', staged,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            # Create destination exclusively, so a concurrent installation is not overwritten.
            target.mkdir()
            copied = True
            for child in staged.iterdir():
                shutil.move(str(child), target)
        with launcher.open('x', encoding='utf-8') as f:
            created_launcher = True
            f.write(content)
        launcher.chmod(0o755)
    except OSError as e:
        if created_launcher:
            launcher.unlink(missing_ok=True)
        if copied:
            shutil.rmtree(target)
        parser.error(f'安装失败，已回滚本次创建的文件：{e}')
    print(json.dumps({'skill': str(target), 'cli': str(launcher)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
