"""Actual checks and a full recovery chain; native role receipts are explicit fixtures."""
import copy
import os
import unittest
from unittest.mock import patch

import test_light_workflow as w
from aidevflow.storage import GateError, read_json, write_json_atomic
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.backends.zscode.engine import LightGate


class RepairLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = w.LightWorkflowTests(); self.f.setUp()
        self.gate, self.root = self.f.gate, self.f.root

    def tearDown(self): self.f.tearDown()

    def draft(self, name, value):
        path = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        p = self.root / path; p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str): p.write_text(value, encoding='utf8')
        else: write_json_atomic(p, value)
        return path

    def broken_red(self):
        self.f.begin(); self.f.write_test(1)
        self.original = (self.root / 'tests/test_1.py').read_text('utf8')
        (self.root / 'tests/test_1.py').write_text(self.original.replace('self.assertEqual(one(), 1)',
            "raise RuntimeError('initial broken harness')"), encoding='utf8')
        self.f.run_current()

    def proposal(self, content, name='repair.json', reason='Repair the test harness'):
        return self.draft(name, {'reason': reason, 'group': 'G1',
            'changes': {'tests/test_1.py': self.draft(name + '.py', content)}})

    def dispatch(self, call):
        assignment = self.gate.baseline_assignment()
        req = {'type': 'tool_call', 'session_id': 'lifecycle-fixture', 'toolCallId': call,
               'input': assignment['task_call']}
        self.gate.observe_baseline(req)
        return req

    def invalid(self, req):
        return self.gate.observe_baseline({**req, 'type': 'tool_result', 'details': {'results': [
            {'id': 'fixture-' + req['toolCallId'], 'agent': 'aidevflow-baseline-reviewer', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': {'bad': True}}}]}})

    def test_approved_initial_context_is_idempotent_and_cannot_redispatch(self):
        self.f.begin(); self.f.write_test(1)
        assignment = self.gate.prepare_baseline(); self.f.baseline_review()
        before = self.gate.status()['revision']
        context = self.gate.prepare_baseline()
        self.assertEqual(context['phase'], 'approved'); self.assertEqual(context['next_action'], 'run')
        self.assertNotIn('task_call', context); self.assertEqual(self.gate.status()['revision'], before)
        with self.assertRaises(GateError):
            self.gate.observe_baseline({'type': 'tool_call', 'session_id': 'another', 'toolCallId': 'duplicate', 'input': assignment['task_call']})

    def test_inflight_context_waits_and_replayed_dispatch_is_idempotent(self):
        self.f.begin(); self.f.write_test(1); self.gate.prepare_baseline()
        req = self.dispatch('inflight'); revision = self.gate.status()['revision']
        context = self.gate.baseline_context()
        self.assertEqual(context['phase'], 'reviewing'); self.assertNotIn('task_call', context)
        self.gate.observe_baseline(req)
        self.assertEqual(self.gate.status()['revision'], revision)
        with self.assertRaises(GateError): self.gate.observe_baseline({**req, 'toolCallId': 'second'})

    def test_failed_preflight_has_evidence_and_new_candidate_can_be_submitted(self):
        self.broken_red()
        content = (self.root / 'tests/test_1.py').read_text('utf8') + '# still broken\n'
        bad = self.proposal(content)
        with self.assertRaises(GateError) as caught: model_operation(self.gate, {'action': 'repair_propose', 'path': bad})
        evidence = caught.exception.details['diagnostic']['evidence']
        self.assertEqual(read_json(self.root / evidence['path'])['passed'], False)
        self.assertEqual(self.gate.summary()['recovery']['phase'], 'correction_required')
        before = list((self.root / '.aidevflow/light-runs' / self.gate.status()['run_id'] / 'repair-red-evidence').glob('*.json'))
        with self.assertRaises(GateError): model_operation(self.gate, {'action': 'repair_propose', 'path': bad})
        after = list(before[0].parent.glob('*.json')); self.assertEqual(len(before), len(after))
        good = self.proposal(self.original, name='corrected.json')
        self.assertIn('task_call', model_operation(self.gate, {'action': 'repair_propose', 'path': good}))

    def test_apply_failure_can_be_corrected_and_complete_entire_workflow(self):
        self.broken_red(); approval = self.gate.status()['approval']
        transient = self.original.replace('import unittest', 'import unittest, os').replace(
            'self.assertEqual(one(), 1)', "(_ for _ in ()).throw(RuntimeError('transient apply fault')) if os.getenv('AIDEVFLOW_FIXTURE_REPLAY_FAULT') else self.assertEqual(one(), 1)")
        first = self.proposal(transient)
        self.gate.propose_repair(first); self.f.baseline_review()
        with patch.dict(os.environ, {'AIDEVFLOW_FIXTURE_REPLAY_FAULT': '1'}):
            with self.assertRaises(GateError): model_operation(self.gate, {'action': 'repair_apply'})
        summary = self.gate.summary()
        self.assertEqual(summary['recovery']['phase'], 'correction_required')
        self.assertIn('transient apply fault', summary['failure_summary'])
        self.assertNotIn('task_call', self.gate.baseline_context())
        self.gate.propose_repair(self.proposal(self.original, name='second.json'))
        self.f.baseline_review(); self.gate.apply_repair()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run(); self.f.write_test(2); self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 2\n', encoding='utf8')
        self.gate.run(); self.assertEqual(self.gate.run()['stage'], 'verify')
        self.f.witness(); self.assertEqual(self.gate.status()['stage'], 'delivery_ready')
        self.f.accept(self.gate.status()['final_candidate'], 'fixture actual user')
        self.assertEqual(self.gate.status()['approval'], approval)
        self.assertEqual(self.gate.archive()['stage'], 'archived')

    def test_reason_and_draft_filename_do_not_create_new_review(self):
        self.broken_red(); self.gate.propose_repair(self.proposal(self.original)); self.f.baseline_review()
        before = self.gate.status()
        result = self.gate.propose_repair(self.proposal(self.original, name='renamed.json', reason='Different wording'))
        self.assertEqual(result['phase'], 'approved'); self.assertEqual(result['next_action'], 'repair_apply')
        self.assertEqual(self.gate.status()['revision'], before['revision'])
        self.assertEqual(result['sha256'], before['baseline_review']['sha256'])

    def test_invalid_native_retries_do_not_reset_provider_budget(self):
        self.f.begin(); self.f.write_test(1); self.gate.prepare_baseline()
        config = read_json(self.root / '.aidevflow/project.json'); config['progress_limits'] = {'providers': 3}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        self.assertFalse(self.gate.activity('provider')['stopped'])
        self.invalid(self.dispatch('bad-1'))
        self.assertFalse(LightGate(self.root).activity('provider')['stopped'])
        self.invalid(self.dispatch('bad-2'))
        self.assertTrue(self.gate.activity('provider')['stopped'])

    def test_legacy_failed_apply_is_migrated_without_new_approval(self):
        self.broken_red(); self.gate.propose_repair(self.proposal(self.original)); self.f.baseline_review()
        approval = self.gate.status()['approval']
        state, run = self.gate.load()
        state['operation_failure'] = {'action': 'repair_apply', 'count': 1, 'total': 1,
            'reason': '修订后的测试未在原始实现上形成有效 RED：historical failure'}
        self.gate.checkpoint(state, run, 'operation_rejected')
        state, run = self.gate.load(); state['operation_failure']['action'] = 'repair_propose'
        state['operation_failure']['reason'] = '当前没有可修订的执行基线'
        self.gate.checkpoint(state, run, 'operation_rejected')
        self.gate.migrate_repair_lifecycle()
        self.assertEqual(self.gate.baseline_context()['phase'], 'correction_required')
        self.assertEqual(self.gate.status()['approval'], approval)

    def test_stale_approved_workspace_routes_to_correction_and_can_be_reproposed(self):
        self.broken_red(); proposal = self.proposal(self.original)
        self.gate.propose_repair(proposal); self.f.baseline_review()
        source = self.root / 'src/sample.py'
        source.write_text(source.read_text('utf8') + '# workspace changed\n', encoding='utf8')
        with self.assertRaises(GateError): model_operation(self.gate, {'action': 'repair_apply'})
        self.assertEqual(self.gate.baseline_context()['phase'], 'correction_required')
        self.assertIn('task_call', self.gate.propose_repair(proposal))

    def test_strict_baseline_contract_is_host_bound(self):
        self.f.begin(); self.f.write_test(1)
        public = self.gate.prepare_baseline()
        self.assertNotIn('outputSchema', public['task_call'])
        prepared = self.gate.baseline_assignment()
        schema = prepared['task_call']['outputSchema']
        self.assertEqual(prepared['task_call']['schemaMode'], 'strict')
        self.assertEqual(schema['properties']['bundle_sha256']['const'], prepared['sha256'])
        forged = copy.deepcopy(prepared['task_call']); forged['outputSchema'] = {'type': 'object'}
        with self.assertRaises(GateError):
            self.gate.observe_baseline({'type': 'tool_call', 'session_id': 'forged', 'toolCallId': 'forged', 'input': forged})
        self.f.baseline_review()
        self.assertEqual(self.gate.baseline_context()['phase'], 'approved')

    def test_unaccepted_failed_repair_has_a_controlled_preparation_retry(self):
        self.broken_red()
        proposal = self.proposal((self.root / 'tests/test_1.py').read_text('utf8') + '# invalid\n')
        with self.assertRaises(GateError): self.gate.propose_repair(proposal)
        self.assertIn('retry', self.gate.summary()['recovery']['allowed_actions'])
        self.gate.retry('Correct the unaccepted RED harness')
        self.assertEqual(self.gate.status()['stage'], 'applying')
        self.assertFalse(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        (self.root / 'tests/test_1.py').write_text(self.original, encoding='utf8')
        self.assertEqual(self.f.run_current()['current_task']['kind'], 'green')

    def test_new_review_candidates_without_task_progress_cannot_refresh_budget_forever(self):
        self.f.begin(); self.f.write_test(1)
        self.gate.prepare_baseline(); self.f.baseline_review()
        config = read_json(self.root / '.aidevflow/project.json'); config['progress_limits'] = {'providers': 3}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        for n in range(3):
            if n:
                path = self.root / 'tests/test_1.py'
                path.write_text(path.read_text('utf8') + f'# another reviewed candidate {n}\n', encoding='utf8')
                self.gate.prepare_baseline(); self.f.baseline_review()
            result = self.gate.activity('provider')
            self.assertEqual(result['stopped'], n == 2)

    def test_unexecuted_current_red_preflight_keeps_prior_shared_implementation(self):
        self.f.begin(); self.f.write_test(1); self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run(); self.f.write_test(2)
        path = self.draft('g2.py', (self.root / 'tests/test_2.py').read_text('utf8') + '# clarified\n')
        spec = self.draft('spec.json', {'content': w.SPEC.replace('## 知识缺口', '说明：保持原业务约定。\n## 知识缺口'), 'acceptance_map': w.MAP})
        proposal = self.draft('derived.json', {'reason': 'Clarify next RED before its first execution', 'group': 'G2',
            'changes': {'tests/test_2.py': path}, 'spec_path': spec})
        self.gate.propose_repair(proposal)
        proofs = self.gate.status()['baseline_review']['proposal']['preflight']
        g2 = next(read_json(self.root / p['path']) for p in proofs if read_json(self.root / p['path'])['group'] == 'G2')
        self.assertEqual(g2['counts']['failed'], 1)
        self.assertEqual(g2['counts']['passed'], None)  # unittest reports only failures; previous test must not fail.
        self.assertIn('Ran 2 tests', g2['output'])
        self.assertNotIn('FAIL: test_value (test_1.', g2['output'])

    def test_migration_rejects_tampered_documents_without_rewriting_them(self):
        self.broken_red(); self.gate.propose_repair(self.proposal(self.original)); self.f.baseline_review()
        state, run = self.gate.load()
        state['operation_failure'] = {'action': 'repair_apply', 'count': 1, 'total': 1,
            'reason': '修订后的测试未在原始实现上形成有效 RED：legacy failure'}
        self.gate.checkpoint(state, run, 'operation_rejected')
        path = self.root / state['directory'] / 'plan.md'
        content = path.read_text('utf8') + '\nmanual out-of-band change\n'; path.write_text(content, encoding='utf8')
        revision = self.gate.load()[0]['revision']
        with self.assertRaises(GateError): self.gate.migrate_repair_lifecycle()
        self.assertEqual(path.read_text('utf8'), content)
        self.assertEqual(self.gate.load()[0]['revision'], revision)

    def test_preparation_retry_after_prior_group_guides_only_current_unfrozen_tests(self):
        self.f.begin(); self.f.write_test(1); self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run(); previous = copy.deepcopy(self.gate.status()['baselines']['G1'])
        self.f.write_test(2); path = self.root / 'tests/test_2.py'; original = path.read_text('utf8')
        path.write_text(original.replace('self.assertEqual(two(), 2)', "raise RuntimeError('broken second harness')"), encoding='utf8')
        self.f.run_current()
        proposal = self.draft('g2-repair.json', {'reason': 'Repair second harness', 'group': 'G2',
            'changes': {'tests/test_2.py': self.draft('g2-bad.py', path.read_text('utf8') + '# still broken\n')}})
        with self.assertRaises(GateError): self.gate.propose_repair(proposal)
        self.gate.retry('Correct current unaccepted tests, keep the completed group frozen')
        recovery = self.gate.summary()['recovery']
        self.assertEqual(recovery['kind'], 'edit_unfrozen_tests')
        self.assertEqual(recovery['unfrozen_files'], ['tests/test_2.py'])
        self.assertEqual(self.gate.status()['baselines']['G1'], previous)
        path.write_text(original, encoding='utf8')
        self.assertEqual(self.f.run_current()['current_task']['kind'], 'green')


if __name__ == '__main__': unittest.main()
