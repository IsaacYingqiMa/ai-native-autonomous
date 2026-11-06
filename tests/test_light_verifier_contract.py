"""Native-output contract and recovery regressions from the 16-spec real session."""
from __future__ import annotations
import copy
import json
import unittest
from unittest.mock import patch
import test_light_workflow as workflow
from aidevflow.storage import GateError, read_json, write_json_atomic
from aidevflow.backends.zscode.review import VERIFIER
from aidevflow.backends.zscode.engine import LightGate


class VerifierContractTests(unittest.TestCase):
    def setUp(self):
        self.f = workflow.LightWorkflowTests();self.f.setUp();self.f.reach_regression()
        self.root, self.gate = self.f.root, self.f.gate

    def tearDown(self): self.f.tearDown()

    def dispatch(self, call):
        prepared = self.gate.verifier_assignment()
        req = {'type': 'tool_call', 'session_id': 'contract-session', 'toolCallId': call, 'input': prepared['task_call']}
        self.gate.observe_verifier(req)
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        refs = [x['path'] for x in manifest['files'] if x['label'] == 'src/sample.py']
        value = {'schema': 'aidevflow/light-verdict-v1', 'run_id': self.gate.status()['run_id'],
            'bundle_sha256': prepared['sha256'], 'decision': 'pass', 'reason': 'Independent approved-requirement review',
            'items': [{'id': ident, 'status': 'passed', 'reason': 'Examined behavior and evidence', 'evidence': refs} for ident in ('S1', 'S2')]}
        return prepared, req, value

    def finish(self, req, value):
        return self.gate.observe_verifier({**req, 'type': 'tool_result', 'details': {'results': [
            {'agent': VERIFIER, 'id': 'verifier-' + req['toolCallId'], 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': value}}]}})

    def test_host_binds_ids_evidence_and_rejects_coordinator_overrides(self):
        public = self.gate.prepare_verifier()
        self.assertNotIn('outputSchema', public['task_call'])
        prepared = self.gate.verifier_assignment()
        schema = prepared['task_call']['outputSchema']
        self.assertEqual(prepared['task_call']['schemaMode'], 'strict')
        self.assertEqual(schema['properties']['items']['maxItems'], 2)
        self.assertEqual(schema['properties']['items']['items']['properties']['id']['enum'], ['S1', 'S2'])
        refs = schema['properties']['items']['items']['properties']['evidence']['items']['enum']
        self.assertTrue(all(x.startswith('objects/') and ' (' not in x for x in refs))
        req = {'type': 'tool_call', 'session_id': 'test', 'toolCallId': 'bare', 'input': public['task_call']}
        with self.assertRaisesRegex(GateError, '宿主绑定'): self.gate.observe_verifier(req)
        bad = copy.deepcopy(prepared['task_call']);bad['outputSchema'] = {'type': 'object'}
        with self.assertRaisesRegex(GateError, '宿主'): self.gate.observe_verifier({**req, 'input': bad})

    def test_extra_summary_and_annotated_paths_are_corrected_without_repackaging(self):
        original = self.gate.prepare_verifier()
        prepared, req, value = self.dispatch('first')
        malformed = copy.deepcopy(value)
        malformed['items'].append({**malformed['items'][0], 'id': 'TDD-EVIDENCE'})
        blocked = self.gate.guard('yield', {'data': malformed}, {'name': VERIFIER})
        self.assertTrue(blocked['block']);self.assertIn('unexpected=TDD-EVIDENCE', blocked['reason'])
        # Simulate an old/native-validator failure escaping its local guard; the Gate remains strict.
        receipt = self.finish(req, malformed)
        self.assertEqual(receipt['status'], 'invalid_output')
        self.assertIn('TDD-EVIDENCE', self.gate.summary()['verification']['error'])
        correction = self.gate.status()['verifier_correction']
        self.assertEqual(read_json(self.root / correction['path'])['previous_output'], malformed)
        same = self.gate.prepare_verifier()
        self.assertEqual(same['path'], original['path']);self.assertEqual(same['sha256'], original['sha256'])
        self.assertEqual(self.gate.status()['verification_attempts'], 1)
        _, req2, corrected = self.dispatch('second')
        annotated = copy.deepcopy(corrected)
        annotated['items'][0]['evidence'][0] += ' (src/sample.py: explanation)'
        block = self.gate.guard('yield', {'data': annotated}, {'name': VERIFIER})
        self.assertTrue(block['block']);self.assertIn('.evidence[0]', block['reason'])
        self.assertFalse(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        self.assertEqual(self.gate.status()['stage'], 'verify')
        self.assertEqual(self.finish(req2, corrected)['status'], 'completed')
        self.assertEqual(self.gate.status()['stage'], 'delivery_ready')
        self.assertEqual(self.gate.status()['verification_attempts'], 0)
        self.f.accept(self.gate.status()['final_candidate'], 'actual test user')
        self.assertEqual(self.gate.archive()['stage'], 'archived')

    def test_format_repair_cannot_erase_failed_findings_or_turn_revise_into_pass(self):
        self.gate.prepare_verifier();_, req, value = self.dispatch('failed')
        approval = self.gate.status()['approval']
        value['decision'] = 'revise';value['reason'] = 'S1 requires a behavior correction'
        value['items'][0]['status'] = 'failed'
        for item in value['items']: item['evidence'] = [x + ' (human explanation)' for x in item['evidence']]
        self.assertEqual(self.finish(req, value)['status'], 'invalid_output')
        prepared, req2, corrected = self.dispatch('correction')
        self.assertEqual(prepared['task_call']['outputSchema']['properties']['decision'], {'const': 'revise'})
        self.assertTrue(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        corrected['decision'] = 'revise'
        self.assertTrue(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        corrected['items'][0]['status'] = 'failed';corrected['reason'] = value['reason']
        self.assertFalse(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        self.finish(req2, corrected)
        self.assertEqual(self.gate.status()['stage'], 'applying')
        self.assertEqual(self.gate.status()['approval'], approval)
        self.assertEqual(self.gate.status()['repair_feedback'], value['reason'])

    def test_failed_summary_cannot_be_dropped_to_obtain_a_pass(self):
        self.gate.prepare_verifier();_, req, value = self.dispatch('summary-failed')
        value['items'].append({**value['items'][0], 'id': 'REQ-COVERAGE', 'status': 'failed',
                               'reason': 'Missing evidence of a business requirement'})
        self.assertEqual(self.finish(req, value)['status'], 'invalid_output')
        _, req2, corrected = self.dispatch('summary-correction')
        self.assertTrue(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        corrected['decision'] = 'revise';corrected['reason'] = value['items'][-1]['reason']
        self.assertFalse(self.gate.guard('yield', {'data': corrected}, {'name': VERIFIER})['block'])
        self.finish(req2, corrected)
        self.assertEqual(self.gate.status()['stage'], 'applying')
        self.assertEqual(self.gate.status()['repair_feedback'], corrected['reason'])

    def test_stale_native_result_cannot_deliver_or_route_a_repair(self):
        self.gate.prepare_verifier();_, req, value = self.dispatch('stale')
        (self.root / 'src/sample.py').write_text('def one(): return 42\ndef two(): return 2\n', encoding='utf-8')
        value['decision'] = 'revise';value['items'][0]['status'] = 'failed'
        self.assertEqual(self.finish(req, value)['status'], 'stale')
        state = self.gate.status()
        self.assertEqual(state['stage'], 'verify')
        self.assertNotIn('verdict', state);self.assertNotIn('repair_feedback', state)

    def test_bad_native_results_remain_bounded_despite_progress_resets(self):
        config = read_json(self.root / '.aidevflow/project.json')
        config['failure_limits'] = {'verification_failures': 6}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        self.gate.prepare_verifier()
        bundle = self.gate.status()['verifier_bundle']
        for n in range(6):
            prepared, req, value = self.dispatch('invalid-' + str(n))
            value['items'][0]['evidence'][0] += ' (invalid annotation)'
            self.assertEqual(self.finish(req, value)['status'], 'invalid_output')
            if n < 5:
                self.assertFalse(self.gate.activity('provider')['stopped'])
                self.assertEqual(self.gate.prepare_verifier()['sha256'], bundle['sha256'])
        state = self.gate.status()
        self.assertEqual(state['stage'], 'needs_attention')
        self.assertEqual(state['attention_stage'], 'verify')
        self.assertEqual(state['verification_attempts'], 6)
        self.assertTrue(self.gate.activity('provider')['stopped'])

    def test_pending_legacy_receipt_can_finish_and_next_dispatch_uses_strict_contract(self):
        self.gate.prepare_verifier();_, req, value = self.dispatch('legacy')
        state, run = self.gate.load()
        path = self.root / state['verifier_invocation'];receipt = read_json(path)
        legacy = {k: v for k, v in req['input'].items() if k not in ('outputSchema', 'schemaMode')}
        from aidevflow.backends.zscode.review import digest
        receipt.pop('output_schema');receipt['input_digest'] = digest(legacy)
        write_json_atomic(path, receipt)
        state['verifier_bundle'].pop('contract_sha256')
        receipt['bundle'] = copy.deepcopy(state['verifier_bundle']);write_json_atomic(path, receipt)
        self.gate.checkpoint(state, run, 'test_legacy_upgrade')
        self.assertIn('legacy bundle', self.gate.verifier_context()['task_call']['task'])
        value['items'][0]['evidence'][0] += ' (legacy annotation)'
        self.assertEqual(self.finish({**req, 'input': legacy}, value)['status'], 'invalid_output')
        assignment = self.gate.verifier_assignment()
        self.assertEqual(assignment['task_call']['schemaMode'], 'strict')
        self.assertIn('FORMAT CORRECTION', assignment['task_call']['task'])

    def test_budget_does_not_reset_for_revision_or_diagnostic_text_changes(self):
        p = self.root / '.aidevflow/project.json';config = read_json(p)
        config['progress_limits'] = {'providers': 3};write_json_atomic(p, config)
        self.assertFalse(self.gate.activity('provider')['stopped'])
        state, run = self.gate.load();state['diagnosis'] = 'Only an explanation changed'
        self.gate.checkpoint(state, run, 'test_no_semantic_progress')
        self.assertFalse(LightGate(self.root).activity('provider')['stopped'])
        stopped = LightGate(self.root).activity('provider')
        self.assertTrue(stopped['stopped']);self.assertIn('providers=3/3', stopped['reason'])
        budget = self.gate.summary()['autonomy_budget']
        self.assertEqual(budget['limits'], {'providers': 3, 'reads': 256, 'seconds': 10800})
        self.assertEqual(budget['triggered'], ['providers'])

    def test_real_role_milestones_reset_budget_and_waiting_time_is_not_active_time(self):
        p = self.root / '.aidevflow/project.json';config = read_json(p)
        config['progress_limits'] = {'providers': 3, 'reads': 5, 'seconds': 10};write_json_atomic(p, config)
        with patch('aidevflow.backends.zscode.progress.time.monotonic', return_value=100) as clock:
            self.gate.activity('provider')
            self.gate.prepare_verifier()
            self.assertEqual(self.gate.budget_status()['used']['providers'], 0)
            clock.return_value = 200
            self.assertFalse(self.gate.activity('provider')['stopped'])
            clock.return_value = 201
            _, req, value = self.dispatch('waiting')
            clock.return_value = 3000
            self.assertFalse(LightGate(self.root).activity('status')['stopped'])
            self.assertEqual(self.gate.budget_status()['used']['active_seconds'], 1)
            malformed = copy.deepcopy(value);malformed['items'].append({**value['items'][0], 'id': 'EXTRA'})
            clock.return_value = 3001
            self.finish(req, malformed)
            clock.return_value = 3002
            self.assertFalse(self.gate.activity('provider')['stopped'])
            self.assertEqual(self.gate.budget_status()['used']['providers'], 2)
            self.gate.prepare_verifier()  # Invalid results and idempotent packaging are not milestones.
            clock.return_value = 3011
            stopped = self.gate.activity('read')
        self.assertTrue(stopped['stopped']);self.assertIn('seconds=11/10', stopped['reason'])
        self.assertEqual(self.gate.status()['attention_stage'], 'verify')
