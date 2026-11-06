"""Replay a genuinely approved pending baseline repair in a disposable local copy.

Copies the selected project and its existing local dependencies. Never starts a
model, installs dependencies, simulates approval, or changes the input project.
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.installer import install
from aidevflow.storage import read_json, sha256_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args();project = args.project.resolve()
    original, original_run = LightGate(project).load()
    assert original['stage'] == 'repairing' and original['baseline_review']['decision'] == 'approve'
    receipt = read_json(project / original['baseline_invocation'])
    assert receipt['status'] == 'completed' and receipt['value']['decision'] == 'approve'
    assert receipt['bundle'] == original['baseline_review']['sha256']
    assert (project / '.git').is_dir(), 'This local replay requires a standalone Git repository'
    before = {rel: sha256_file(project / rel) for files in original['baselines'].values() for rel in files}
    before.update({rel: sha256_file(project / rel) for rel in LightGate(project).candidate_files() if (project / rel).is_file()})
    package = read_json(project / 'package.json') if (project / 'package.json').is_file() else {}
    if package.get('dependencies') or package.get('devDependencies'):
        assert (project / 'node_modules').is_dir(), 'Existing local dependencies are required; this replay never installs'
    base = SOURCE / '.test-tmp';base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix='checkpoint-replay-', ignore_cleanup_errors=True) as directory:
        target = Path(directory).resolve();target.relative_to(base.resolve())
        assert not target.is_relative_to(project)
        shutil.copytree(project, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules', '__pycache__', 'dist'))
        if (project / 'node_modules').is_dir(): shutil.copytree(project / 'node_modules', target / 'node_modules')
        assert LightGate(project).load()[0] == original, 'Input run changed while copying; retry from a stopped session'
        print(json.dumps({'phase': 'checkpoint_copied', 'revision': original['revision'], 'native_approval': 'real persisted receipt', 'network_used': False}), flush=True)
        install(SOURCE, target, upgrade_active=True)
        config = read_json(target / '.aidevflow/project.json')
        frame = {'protocol': 1, 'runtime': 'zscode', 'id': 'replay', 'action': 'tool', 'payload': {'action': 'repair_apply'}}
        p = subprocess.run([config['python'], '-B', str(target / 'scripts/aidevflow_bridge.py'), str(target)],
            cwd=target, input=json.dumps(frame) + '\n', capture_output=True, encoding='utf8', timeout=360,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert p.returncode == 0, p.stderr
        response = json.loads(p.stdout)
        assert response['ok'], response
        after, _ = LightGate(target).load()
        assert after['stage'] == 'applying' and after['approval'] == original['approval']
        assert after['baseline_version'] == original.get('baseline_version', 0) + 1
        assert after['tasks'][original['cursor']]['completed']
        assert LightGate(project).load()[0] == original
        assert all(sha256_file(project / rel) == h for rel, h in before.items())
        print(json.dumps({'passed': True, 'input_project_unchanged': True, 'approved_requirements_unchanged': True,
            'next_task': after['tasks'][after['cursor']]['id'], 'stage': after['stage'],
            'native_approval': 'real persisted receipt, not simulated', 'network_used': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
