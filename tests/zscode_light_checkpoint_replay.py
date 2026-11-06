"""Prepare a real checkpoint's reviewed correction in an isolated copy.

No model/approval simulation, no writes to the input project. This proves upgrade
and proposal preparation, not independent verification or product delivery.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.installer import install
from aidevflow.storage import sha256_file, write_json_atomic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args(); project = args.project.resolve()
    if not (project / '.git').is_dir(): raise RuntimeError('A standalone Git repository is required')
    original, run = LightGate(project).load()
    if original['stage'] != 'needs_attention' or original['baselines']:
        raise RuntimeError('This probe targets the reported unaccepted-RED checkpoint only')
    before = {str(p): sha256_file(p) for p in run.rglob('*') if p.is_file() and 'upgrades' not in p.parts}
    base = SOURCE / '.test-tmp'; base.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(dir=base, prefix='checkpoint-replay-'))
    shutil.copytree(project, target, dirs_exist_ok=True, symlinks=True,
        ignore=shutil.ignore_patterns('node_modules', '__pycache__', 'upgrades'))
    result = install(SOURCE, target, upgrade_active=True); gate = LightGate(target)
    assert gate.load()[0] == original
    gate.resume('isolated recovery episode; original user authorized framework repair')
    state = gate.status(); draft = f"specs/drafts/{state['run_id']}"
    proposal = {'reason': 'Reconcile corrected spec with registered plan; preserve all approved acceptance',
        'group': state['current_task']['group'], 'changes': {}, 'spec_path': draft + '/spec.json'}
    write_json_atomic(target / draft / 'replay-repair.json', proposal)
    assignment = gate.propose_repair(draft + '/replay-repair.json')
    assert gate.status()['stage'] == 'repairing'
    assert gate.status()['approval'] == original['approval']
    assert LightGate(project).load()[0] == original
    assert all(sha256_file(Path(p)) == sha for p, sha in before.items())
    report = {'project_copy': str(target), 'original_revision': original['revision'],
        'original_unchanged': True, 'approval_preserved': True, 'upgrade_backup': result['upgrade_backup'],
        'stage': gate.status()['stage'], 'native_assignment_prepared': bool(assignment['task_call']),
        'native_review_executed': False, 'product_delivery_passed': False}
    write_json_atomic(target / '.aidevflow/checkpoint-replay-result.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
