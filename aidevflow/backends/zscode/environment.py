"""Bounded local environment facts. This never installs packages or contacts a service."""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path


def inspect(root: Path, versions=True):
    def version(argv):
        if not versions or not argv: return None
        try:
            p = subprocess.run(argv, cwd=root, capture_output=True, encoding='utf8', errors='replace',
                               timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return p.stdout.strip()[:160] if p.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired): return None
    node = shutil.which('node')
    npm = Path(node).parent / 'node_modules/npm/bin/npm-cli.js' if node else None
    git = shutil.which('git')
    package = root / 'package.json'
    manager = 'npm' if package.exists() else None
    for name, tool in [('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lock', 'bun')]:
        if (root / name).exists(): manager = tool
    return {'python': {'executable': sys.executable, 'version': sys.version.split()[0]},
            'node': {'executable': node, 'version': version([node, '--version']) if node else None},
            'npm': {'cli': str(npm) if npm and npm.is_file() else None,
                    'version': version([node, str(npm), '--version']) if npm and npm.is_file() else None},
            'git': {'executable': git, 'version': version([git, '--version']) if git else None},
            'project': {'package_json': package.is_file(), 'package_manager': manager,
                        'node_modules': (root / 'node_modules').is_dir()},
            'network_used': False, 'next_action': '使用返回的路径；缺少环境时报具体缺项，不遍历磁盘猜测路径'}
