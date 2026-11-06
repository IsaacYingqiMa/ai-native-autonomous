"""Upgrade a historical, completed-role server checkpoint in a copy; no new native verdicts."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.storage import GateError, read_json, sha256_file, write_json_atomic
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.installer import install
from aidevflow.backends.zscode.bridge import model_operation


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args(); original = args.project.resolve()
    run_id = read_json(original / '.aidevflow/light-active.json')['run_id']
    run = original / '.aidevflow/light-runs' / run_id
    events = [(p, read_json(p)) for p in sorted((run / 'events').glob('*.json'))]
    selected = None
    for path, event in events:
        state = event['state']; review = state.get('baseline_review', {})
        if (event['event'] == 'baseline_review_finished' and state['stage'] == 'repairing'
                and review.get('mode') == 'repair' and review.get('group') == 'server'):
            receipt = read_json(original / state['baseline_invocation'])
            if receipt.get('status') == 'completed' and receipt.get('value', {}).get('decision') == 'approve':
                selected = (path, state)
    if selected is None: raise GateError('No historical server repair checkpoint with a real completed approval')
    source_event, state = selected
    prefix = [(p, e) for p, e in events if p.name <= source_event.name]
    allowed_receipts = {e['state'].get('baseline_invocation', '').rsplit('/', 1)[-1] for _, e in prefix}
    watched = [original / '.aidevflow/project.json', original / 'src/collector.mjs']
    watched += [original / p for files in state['baselines'].values() for p in files]
    hashes = {str(p): sha256_file(p) for p in watched}
    base = SOURCE / '.test-tmp'; base.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='lifecycle-server-', dir=base))
    def ignored(directory, names):
        skip = set(shutil.ignore_patterns('node_modules', '__pycache__', '*.pyc', 'upgrades', 'sessions', 'logs')(directory, names))
        if Path(directory) == original / '.aidevflow':
            skip.update(n for n in names if n in ('coordinator.lock', 'gate-write.lock', 'agent-observer.lock',
                                                  'method-operation.lock', 'event-log.lock'))
        if Path(directory) == run / 'events': skip.update(n for n in names if n > source_event.name)
        if Path(directory) == run / 'native-baselines': skip.update(n for n in names if n not in allowed_receipts)
        return skip
    shutil.copytree(original, target, dirs_exist_ok=True, ignore=ignored)
    copied_run = target / run.relative_to(original)
    write_json_atomic(copied_run / 'state.json', state)  # Projection of an exact, immutable historical event.
    LightGate(target).project(state)
    upgrade = install(SOURCE, target, upgrade_active=True, extend_budgets=True)
    gate = LightGate(target); migrated = gate.status(); context = gate.baseline_context()
    assert context['phase'] == 'correction_required' and 'task_call' not in context
    assert migrated['approval'] == state['approval'] and migrated['baselines'] == state['baselines']
    assert migrated['tasks'] == state['tasks']
    proposal = f'specs/drafts/{run_id}/repair.json'
    assignment = model_operation(gate, {'action': 'repair_propose', 'path': proposal})
    assert assignment['phase'] == 'review_required' and 'task_call' in assignment
    final = gate.status(); proofs = final['baseline_review']['proposal']['preflight']
    assert proofs and all(read_json(target / p['path'])['passed'] for p in proofs)
    assert final['baseline_review']['sha256'] != state['baseline_review']['sha256']
    assert all(sha256_file(Path(p)) == sha for p, sha in hashes.items())
    result = {'original_project': str(original), 'source_event': str(source_event),
        'historical_revision': state['revision'], 'copy': str(target),
        'method_version': read_json(target / '.aidevflow/project.json')['method_version'],
        'migrated_phase': context['phase'], 'new_candidate_phase': assignment['phase'],
        'preflight': [{'exit_code': p['exit_code'], 'counts': read_json(target / p['path'])['counts']} for p in proofs],
        'new_review_bundle': final['baseline_review']['sha256'],
        'native_review_executed': False, 'approval_preserved': final['approval'] == state['approval'],
        'baselines_preserved': final['baselines'] == state['baselines'], 'watched_original_files_unchanged': True,
        'upgrade_backup': upgrade['upgrade_backup']}
    write_json_atomic(base / 'lifecycle-server-result.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
