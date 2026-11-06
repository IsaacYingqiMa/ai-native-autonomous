"""Candidate-bound review routing, preflight, and recoverable repair failures."""
from __future__ import annotations
import copy
import json

from ...locking import writes
from ...storage import GateError, read_json, sha256_file, sha256_bytes, write_json_new
from .review import digest
from .revision import WorkflowError


class RepairLifecycle:
    def baseline_assignment(self):
        context = self.baseline_context()
        if 'task_call' not in context: return context
        state, _ = self.load()
        return {**context, 'task_call': {**context['task_call'],
            'outputSchema': self.baseline_output_schema(state, state['baseline_review']), 'schemaMode': 'strict'}}

    def baseline_output_schema(self, state, review):
        schema = read_json(self.path('.zscode/extensions/aidevflow/baseline.schema.json'))
        manifest = self.check_review_bundle(review)
        props = schema['properties']
        props['run_id']['const'] = state['run_id']
        props['bundle_sha256']['const'] = review['sha256']
        for key in ('acceptance_ids', 'changed_paths'):
            items = manifest[key]
            props[key].update(minItems=len(items), maxItems=len(items), uniqueItems=True)
            if items: props[key]['items'] = {'type': 'string', 'enum': items}
        return schema

    def review_key(self, state, mode, group, files, proposal=None, subject=None):
        definition = None
        if proposal:
            keys = ('id', 'title', 'kind', 'group', 'specs', 'files', 'argv', 'regression', 'checks')
            definition = {'changes': proposal['changes'], 'before': proposal['before'],
                'plan': [{k: t[k] for k in keys if k in t} for t in proposal['plan']],
                'spec': proposal.get('spec'), 'approval': proposal['approval']}
        return digest({'mode': mode, 'group': group,
            'subject': subject or self.baseline_subject(state, {p: sha256_bytes(v) for p, v in files.items()}),
            'proposal': definition})

    def existing_review_key(self, state, review):
        if review.get('key'): return review['key']
        proposal = review.get('proposal')
        files = {p: c.encode() for p, c in (proposal or {}).get('changes', {}).items()}
        return self.review_key(state, review['mode'], review['group'], files, proposal, review['subject'])

    def baseline_approval(self, state):
        review = state.get('baseline_review', {})
        pin = review.get('approval_receipt')
        if pin:
            path = self.path(pin['path'])
            if sha256_file(path) != pin['sha256']: raise GateError('基线批准回执已被修改')
            candidates = [(pin['path'], read_json(path))]
        else:
            candidates = []
            if state.get('baseline_invocation'):
                candidates.append((state['baseline_invocation'], read_json(self.path(state['baseline_invocation']))))
            # Compatibility with real pre-2.4.2 receipts. Never synthesize approval.
            if review.get('decision') == 'approve':
                _, run = self.load()
                candidates += [(p.relative_to(self.root).as_posix(), read_json(p)) for p in (run / 'native-baselines').glob('*.json')]
        for path, receipt in candidates:
            value = receipt.get('value', {})
            if (receipt.get('bundle') == review.get('sha256') and receipt.get('status') == 'completed'
                    and receipt.get('execution_finished') and value.get('decision') == 'approve'
                    and value.get('bundle_sha256') == review.get('sha256') and value.get('run_id') == state['run_id']):
                return {'path': path, 'sha256': sha256_file(self.path(path))}
        return None

    def baseline_route(self, state):
        review = state.get('baseline_review')
        if not review: return {'phase': 'absent', 'next_action': 'prepare_baseline'}
        receipt = read_json(self.path(state['baseline_invocation'])) if state.get('baseline_invocation') else {}
        bound = receipt.get('bundle') == review['sha256']
        if bound and not receipt.get('execution_finished'):
            return {'phase': 'reviewing', 'next_action': 'wait', 'invocation': state['baseline_invocation']}
        if state.get('repair_failure'):
            return {'phase': 'correction_required', 'next_action': 'repair_propose', 'failure': state['repair_failure']}
        task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        if review['mode'] == 'initial':
            current = state['stage'] == 'applying' and task.get('kind') == 'red' and task.get('group') == review['group'] and not task.get('completed')
        else:
            current = state['stage'] == 'repairing'
        if not current: return {'phase': 'consumed', 'next_action': 'status'}
        if self.baseline_approval(state):
            return {'phase': 'approved', 'next_action': 'run' if review['mode'] == 'initial' else 'repair_apply'}
        if bound and receipt.get('status') == 'completed' and receipt.get('value', {}).get('decision') == 'revise':
            return {'phase': 'revise', 'next_action': 'prepare_baseline' if review['mode'] == 'initial' else 'repair_propose'}
        return {'phase': 'review_required', 'next_action': 'baseline_context'}

    def reuse_review(self, state, key):
        review = state.get('baseline_review')
        if review and self.existing_review_key(state, review) == key and not state.get('repair_failure'):
            return self.baseline_context()
        failure = state.get('repair_failure', {})
        if failure.get('candidate_key') == key:
            raise WorkflowError(failure['code'], '候选未变化；按已保存的失败证据修正后重新提案',
                                {**failure, 'repair_replay': True, 'cached': True})
        return None

    def repair_recovery(self, state):
        route = self.baseline_route(state)
        if state['stage'] != 'repairing': return None
        failure = state.get('repair_failure')
        actions = [route['next_action']]
        task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        if failure and task.get('kind') == 'red' and not task.get('completed') and task.get('result', {}).get('status') == 'failed':
            actions.append('retry')
        return {'kind': 'repair_lifecycle', **route, 'allowed_actions': actions,
            'detected_issues': [failure['message']] if failure else [],
            'requires_user_resume': False,
            'proposal_path': f"specs/drafts/{state['run_id']}/repair.json",
            'note': '等待中的真实角色不重派；已批准候选直接应用；失败候选修改草稿后重提。'}

    def record_repair_failure(self, state, run, key, diagnostic, message, *, source):
        old = state.get('repair_failure')
        if old: state.setdefault('repair_failure_history', []).append(old)
        state['repair_failure'] = {**diagnostic, 'candidate_key': key, 'source': source,
            'message': str(message)[:1600], 'code': diagnostic.get('code', 'REPAIR_RED_INVALID')}
        state['stage'] = 'repairing'
        self.checkpoint(state, run, 'repair_' + source + '_failed')

    def preflight_repair(self, state, run, mode, proposal, key):
        trial = copy.deepcopy(state)
        current = trial['tasks'][trial['cursor']] if trial['cursor'] < len(trial['tasks']) else {}
        if current.get('kind') == 'red' and not current.get('completed') and current.get('group') in proposal['affected']:
            trial.setdefault('red_origins', {}).pop(current['group'], None)
            trial['red_snapshot_groups'] = [g for g in trial.get('red_snapshot_groups', []) if g != current['group']]
            self.capture_red_origin(trial, run, current['group'])
        proofs = []
        for group in proposal['affected']:
            red = next(t for t in proposal['plan'] if t.get('group') == group and t['kind'] == 'red')
            accepted = any(t.get('completed') for t in state['tasks'] if t.get('group') == group and t['kind'] == 'red')
            if mode == 'repair' or accepted or set(red['files']).intersection(proposal['changes']):
                proofs.append(self.replay_red(trial, run, group, proposal['changes'], proposal['plan'], candidate_key=key))
        return proofs

    def stage_repair(self, state, run, mode, group, files, proposal):
        key = self.review_key(state, mode, group, files, proposal)
        reused = self.reuse_review(state, key)
        if reused is not None: return reused
        self._review_available(state)
        try:
            proposal['preflight'] = self.preflight_repair(state, run, mode, proposal, key)
        except WorkflowError as exc:
            if not exc.details.get('repair_replay'): raise
            self.record_repair_failure(state, run, key, exc.details, str(exc), source='preflight')
            raise
        if state.get('baseline_review'):
            state.setdefault('baseline_review_history', []).append({k: state['baseline_review'][k]
                for k in ('path', 'sha256', 'decision', 'approval_receipt') if k in state['baseline_review']})
        if state.get('repair_failure'):
            state.setdefault('repair_failure_history', []).append(state.pop('repair_failure'))
        state['baseline_review'] = self.review_bundle(state, run, mode, group, files, proposal)
        state['baseline_review']['key'] = key
        state['baseline_return_stage'] = 'applying'
        state['stage'] = 'repairing'
        self.checkpoint(state, run, 'derived_revision_proposed' if mode == 'derived' else 'baseline_repair_proposed')
        return self.baseline_context()

    @writes
    def migrate_repair_lifecycle(self):
        if not self.active.exists(): return
        state, run = self.load()
        if state['stage'] != 'repairing' or state.get('repair_failure'): return
        self.integrity(state); self.baselines(state)
        self.no_inflight(state)
        if (run / 'baseline-repair-transaction.json').exists(): return
        review = state.get('baseline_review', {})
        for path in reversed(sorted((run / 'events').glob('*.json'))):
            event = read_json(path); old = event['state']
            if old.get('baseline_review', {}).get('sha256') != review.get('sha256'): break
            failure = old.get('operation_failure', {})
            if (event['event'] == 'operation_rejected' and failure.get('action') == 'repair_apply'
                    and str(failure.get('reason', '')).startswith('修订后的测试未在原始实现上形成有效 RED')):
                evidence = {'path': path.relative_to(self.root).as_posix(), 'sha256': sha256_file(path), 'legacy': True}
                self.record_repair_failure(state, run, self.existing_review_key(state, review),
                    {'code': 'REPAIR_RED_INVALID', 'evidence': evidence, 'repair_replay': True}, failure['reason'], source='legacy_apply')
                return
