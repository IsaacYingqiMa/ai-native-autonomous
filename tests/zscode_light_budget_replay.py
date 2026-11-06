"""Replay the real stopped run in a copy; never approve, dispatch, or alter the source project."""
from __future__ import annotations
import argparse
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.storage import GateError, read_json, sha256_file, write_json_atomic
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.installer import install


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True, type=Path)
    args = parser.parse_args()
    original = args.project.resolve()
    state, original_run = LightGate(original).load()
    if state['stage'] != 'needs_attention' or state['tasks'][state['cursor']]['id'] != '2.1':
        raise GateError('This case replay requires the stopped metrics 2.1 checkpoint')
    run_id = state['run_id']
    draft = f'specs/drafts/{run_id}'
    watched = [original_run / 'state.json', original_run / 'events' / f"{state['revision']:08d}.json",
               original / '.aidevflow/project.json', original / draft / 'repair.json',
               original / draft / 'proposed-plan.json', original / 'src/collector.mjs']
    watched += [original / path for files in state['baselines'].values() for path in files]
    hashes = {str(p): sha256_file(p) for p in watched}
    base = SOURCE / '.test-tmp'; base.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='budget64-replay-', dir=base))
    shutil.copytree(original, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('node_modules', '__pycache__', '*.pyc', 'upgrades', 'sessions', 'logs'))
    installed = install(SOURCE, target, upgrade_active=True, extend_budgets=True)
    gate = LightGate(target)
    upgraded = gate.status()
    assert upgraded['revision'] == state['revision'] and upgraded['approval'] == state['approval']
    assert upgraded['baselines'] == state['baselines'] and upgraded['tasks'] == state['tasks']
    gate.resume('isolated replay operator; not business approval')
    errors = []
    for _ in range(2):
        try:
            model_operation(gate, {'action': 'repair_propose', 'path': draft + '/repair.json'})
        except GateError as exc:
            assert exc.code == 'PLAN_CONFLICT' and not exc.details['stopped']
            errors.append(exc.details['diagnostic'])
        else:
            raise AssertionError('Original stale plan unexpectedly accepted')
    plan = copy.deepcopy(errors[-1]['current_plan'])
    task = next(t for t in plan['tasks'] if t['id'] == '2.2')
    assert task['files'] == ['src/metrics.mjs']
    task['files'].append('src/collector.mjs')
    write_json_atomic(target / draft / 'minimal-plan.json', plan)
    write_json_atomic(target / draft / 'minimal-repair.json', {'reason': 'Expose record timestamps for approved metrics',
        'group': 'metrics', 'changes': {}, 'plan_path': draft + '/minimal-plan.json'})
    assignment = model_operation(gate, {'action': 'repair_propose', 'path': draft + '/minimal-repair.json'})
    final = gate.status()
    assert final['stage'] == 'repairing' and final['operation_failure']['count'] == 0
    assert final['operation_failure']['total'] == 2 and assignment['task_call']['agent'] == 'aidevflow-baseline-reviewer'
    assert final['approval'] == state['approval'] and final['baselines'] == state['baselines']
    assert all(sha256_file(Path(path)) == sha for path, sha in hashes.items())
    assert LightGate(original).load()[0]['revision'] == state['revision']
    result = {'original_project': str(original), 'original_revision': state['revision'], 'copy': str(target),
        'method_version': read_json(target / '.aidevflow/project.json')['method_version'],
        'progress_limits': installed['progress_limits'], 'failure_limits': installed['failure_limits'],
        'original_conflicts': errors[0]['conflicts'], 'corrected_proposal_stage': final['stage'],
        'failure_streak_after_acceptance': final['operation_failure']['count'],
        'segment_failures_retained_until_apply': final['operation_failure']['total'],
        'native_review_assignment_ready': True, 'native_review_executed': False,
        'original_unchanged': True}
    write_json_atomic(base / 'budget64-replay-result.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
