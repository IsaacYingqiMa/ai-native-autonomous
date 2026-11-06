"""Offline regressions for frozen test repair, native feedback and bounded continuation."""
from __future__ import annotations
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import test_light_workflow as workflow
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.backends.zscode.installer import install
from aidevflow.backends.zscode.environment import inspect
from aidevflow.backends.zscode.progress import test_counts, target_assertion_failure
from aidevflow.locking import observer_lock
from aidevflow.storage import GateError, read_json, write_json_atomic, sha256_file, replace_file_atomic


class LightRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = workflow.LightWorkflowTests();self.f.setUp()
        self.root, self.gate = self.f.root, self.f.gate

    def tearDown(self): self.f.tearDown()

    def draft(self, name, value):
        rel = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        p = self.root / rel;p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf8')
        return rel

    def failed_legacy_baseline(self):
        # Simulate an already-frozen pre-upgrade erroneous assertion, not a live model review.
        self.f.begin();self.f.write_test(1)
        p = self.root / 'tests/test_1.py'
        p.write_text(p.read_text(encoding='utf8').replace('one(), 1', 'one(), 2'), encoding='utf8')
        self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run()
        self.assertEqual(self.gate.status()['current_task']['result']['status'], 'failed')
        old = self.gate.status()
        fixed = self.draft('fixed.py', p.read_text(encoding='utf8').replace('one(), 2', 'one(), 1'))
        proposal = self.draft('repair.json', {'reason': 'The frozen assertion contradicts AC-1: one must return 1',
                        'group': 'G1', 'changes': {'tests/test_1.py': fixed}})
        return old, proposal

    def test_reviewed_repair_replays_original_red_then_finishes_two_review_workflow(self):
        old, proposal = self.failed_legacy_baseline()
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        self.assertFalse(self.gate.guard('write', {'path': proposal})['block'])
        model_operation(self.gate, {'action': 'repair_propose', 'path': proposal})
        with self.assertRaises(GateError): self.gate.apply_repair()
        self.f.baseline_review()
        s = model_operation(self.gate, {'action': 'repair_apply'})
        current = self.gate.status()
        self.assertEqual(s['stage'], 'applying');self.assertEqual(current['baseline_version'], 1)
        self.assertEqual(current['approval'], old['approval']);self.assertEqual(current['requirements'], old['requirements'])
        self.assertEqual(sha256_file(self.root / old['tasks'][0]['result']['path']), old['tasks'][0]['result']['sha256'])
        proof = read_json(self.root / current['tasks'][0]['result']['path'])
        self.assertEqual(proof['source'], 'isolated_original_implementation');self.assertNotEqual(proof['exit_code'], 0)
        self.assertEqual(self.gate.run()['cursor'], 2)
        self.f.write_test(2);self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 2\n', encoding='utf8')
        self.gate.run();self.gate.run();self.f.witness()
        s = self.gate.status()
        bundle = read_json(self.root / s['verifier_bundle']['path'] / 'BUNDLE.json')
        self.assertIn('baseline-history.json', [x['label'] for x in bundle['files']])
        self.f.accept(s['final_candidate'], 'actual user')
        self.assertEqual(self.gate.archive()['stage'], 'archived')

    def test_repair_transaction_recovers_file_write_interruption(self):
        old, proposal = self.failed_legacy_baseline();self.gate.propose_repair(proposal);self.f.baseline_review()
        with patch('aidevflow.backends.zscode.repair.replace_file_atomic', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError): self.gate.apply_repair()
        s = LightGate(self.root).apply_repair()
        self.assertEqual(s['stage'], 'applying');self.assertEqual(self.gate.status()['baseline_version'], 1)
        self.assertEqual(self.gate.status()['approval'], old['approval'])

    def test_failed_checkpoint_exposes_actionable_repair_template_without_history_scanning(self):
        self.f.tasks[0]['files'].append('scripts/run-tests.mjs')
        (self.root / 'scripts/run-tests.mjs').write_text('// fixture runner\n', encoding='utf8')
        old, _ = self.failed_legacy_baseline()
        status = model_operation(self.gate, {'action': 'status'})
        recovery = status['recovery']
        self.assertEqual(status['current_task']['group'], 'G1')
        self.assertEqual(recovery['proposal_template']['group'], 'G1')
        self.assertEqual(set(recovery['proposal_template']['changes']), {'tests/test_1.py'})
        self.assertIn('AssertionError', status['failure_summary'])
        self.assertTrue((self.root / recovery['schema_path']).is_file())
        self.assertEqual(self.gate.status()['revision'], old['revision'])
        for rel in ('.aidevflow/runtime/aidevflow/backends/zscode/repair.py',
                    f".aidevflow/light-runs/{old['run_id']}/events/00000001.json",
                    f".aidevflow/light-runs/{old['run_id']}/progress.json"):
            result = self.gate.guard('read', {'path': rel})
            self.assertTrue(result['block']);self.assertIn('status.recovery', result['reason'])
        self.assertFalse(self.gate.guard('read', {'path': old['current_task']['result']['path']})['block'])
        self.assertFalse(self.gate.guard('read', {'path': 'tests/test_1.py'})['block'])

    def test_upgrade_preserves_a_frozen_gitignore_and_prior_red_evidence(self):
        p = self.root / '.gitignore'
        p.write_text(p.read_text(encoding='utf8').replace('.zscode/logs/\n', ''), encoding='utf8', newline='\n')
        self.f.tasks[0]['files'].append('.gitignore')
        old, _ = self.failed_legacy_baseline()
        ignore = (self.root / '.gitignore').read_bytes()
        result = install(workflow.SOURCE, self.root, upgrade_active=True)
        self.assertEqual((self.root / '.gitignore').read_bytes(), ignore)
        self.assertEqual(self.gate.status(), old)
        self.gate.baselines(self.gate.status())
        self.assertIn(old['run_id'], result['upgrade_backup'])

    def test_repair_cannot_remove_cases_and_stale_approved_proposal_cannot_apply(self):
        _, proposal = self.failed_legacy_baseline()
        value = read_json(self.root / proposal)
        self.draft('empty.py', '# no meaningful test\n')
        value['changes']['tests/test_1.py'] = self.draft('empty.py', '# no meaningful test\n')
        with self.assertRaisesRegex(GateError, '删除'): self.gate.propose_repair(self.draft('bad.json', value))
        self.gate.propose_repair(proposal);self.f.baseline_review()
        p = self.root / 'src/sample.py';p.write_text(p.read_text(encoding='utf8') + '# changed after review\n', encoding='utf8')
        with self.assertRaisesRegex(GateError, '过期'): self.gate.apply_repair()

    def test_later_test_failures_cannot_substitute_for_the_repaired_groups_red(self):
        self.f.reach_regression()
        (self.root / 'src/sample.py').write_text('def one(): return 0\ndef two(): return 2\n', encoding='utf8')
        self.gate.run()
        old = self.gate.status()['baselines']
        content = (self.root / 'tests/test_1.py').read_text(encoding='utf8').replace('one(), 1', 'one(), 0')
        fixed = self.draft('weak.py', content)
        proposal = self.draft('repair.json', {'reason': 'Deliberately invalid fixture: prove another group cannot supply RED',
            'group': 'G1', 'changes': {'tests/test_1.py': fixed}})
        # Mechanical preflight now rejects before an AI can approve an ineffective RED.
        with self.assertRaisesRegex(GateError, '有效 RED'): self.gate.propose_repair(proposal)
        self.assertEqual(self.gate.status()['baselines'], old)

    def test_unaccepted_red_repair_replays_current_pre_green_workspace_after_previous_group(self):
        self.f.begin();self.f.write_test(1);self.f.run_current()
        source = self.root / 'src/sample.py'
        source.write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run()
        self.f.write_test(2)
        test = self.root / 'tests/test_2.py'
        original = test.read_text(encoding='utf8')
        test.write_text(original.replace('self.assertEqual(two(), 2)', "raise RuntimeError('fixture setup error')"), encoding='utf8')
        self.f.run_current()
        self.assertEqual(self.gate.status()['cursor'], 2)
        self.assertNotIn('G2', self.gate.status().get('red_snapshot_groups', []))
        fixed = self.draft('fixed-red.py', original)
        proposal = self.draft('repair.json', {'reason': 'Replace the broken preparation with the original target assertion',
            'group': 'G2', 'changes': {'tests/test_2.py': fixed}})
        approval = self.gate.status()['approval']
        self.gate.propose_repair(proposal);self.f.baseline_review()
        with self.assertRaisesRegex(GateError, 'repair_apply'): self.gate.retry('rewriting a reason does not change phase')
        s = self.gate.apply_repair()
        self.assertEqual(s['current_task']['id'], '2.2')
        state = self.gate.status()
        self.assertTrue(state['tasks'][2]['completed']);self.assertEqual(state['approval'], approval)
        self.assertEqual(source.read_text(encoding='utf8'), 'def one(): return 1\ndef two(): return 0\n')
        self.assertIn('G2', state['red_snapshot_groups'])
        source.write_text('def one(): return 1\ndef two(): return 2\n', encoding='utf8')
        self.gate.run();self.assertEqual(self.gate.run()['stage'], 'verify')

    def test_testing_library_missing_required_ui_is_a_target_assertion(self):
        output = '\x1b[31mTestingLibraryElementError\x1b[39m: Unable to find a label with the text of: 搜索任务\n Tests 7 failed (7)'
        self.assertTrue(target_assertion_failure(output))
        self.assertFalse(target_assertion_failure('ReferenceError: React is not defined\nTests 7 failed (7)'))

    def test_wrong_module_path_is_rejected_before_freezing_and_review_cannot_be_self_signed(self):
        self.f.begin();self.f.write_test(1)
        p = self.root / 'tests/test_1.py';p.write_text(p.read_text(encoding='utf8').replace('src.sample', 'src.wrong'), encoding='utf8')
        with self.assertRaisesRegex(GateError, '计划不一致'): self.gate.prepare_baseline()
        self.assertFalse(self.gate.status()['baselines'])
        self.f.write_test(1)
        with self.assertRaisesRegex(GateError, '原生基线审查'): self.gate.run()
        prepared = self.gate.prepare_baseline()
        with self.assertRaisesRegex(GateError, '锚点'):
            self.gate.observe_baseline({'type': 'tool_result', 'session_id': 'fake', 'toolCallId': 'fake', 'input': prepared['task_call']})
        role = {'name': 'aidevflow-baseline-reviewer'}
        self.assertFalse(self.gate.guard('read', {'path': prepared['path'] + '/BUNDLE.json'}, role)['block'])
        self.assertTrue(self.gate.guard('read', {'path': 'src/sample.py'}, role)['block'])
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'}, role)['block'])

    def test_inconsistent_spec_path_can_be_fixed_before_plan_is_frozen(self):
        self.f.entry()
        self.gate.spec(workflow.SPEC.replace('## 已剔除', '实现：src/incorrect.ts\n\n## 已剔除'), workflow.MAP)
        with self.assertRaisesRegex(GateError, '规格路径与计划不一致'): self.gate.plan(self.f.tasks)
        self.assertEqual(self.gate.status()['stage'], 'plan')
        self.gate.spec(workflow.SPEC, workflow.MAP)
        bad = copy.deepcopy(self.f.tasks);bad[1]['files'] = ['src/../.aidevflow/project.json']
        with self.assertRaises(GateError): self.gate.plan(bad)
        self.assertEqual(self.gate.plan(self.f.tasks)['stage'], 'applying')

    def test_read_loop_budget_survives_worker_recreation_and_status_revisions(self):
        self.f.entry()
        config = self.root / '.aidevflow/project.json';value = read_json(config)
        value['progress_limits'] = {'reads': 3, 'providers': 10, 'seconds': 900};write_json_atomic(config, value)
        self.assertFalse(self.gate.activity('read')['stopped'])
        self.gate.operation_failure('diagnostic', 'one failure creates a revision but no semantic progress')
        self.assertFalse(LightGate(self.root).activity('grep')['stopped'])
        stopped = LightGate(self.root).activity('glob')
        self.assertTrue(stopped['stopped']);self.assertIn('语义进展', stopped['reason'])
        self.assertTrue(self.gate.activity('provider')['stopped'])
        self.assertEqual(self.gate.status()['stage'], 'needs_attention')
        self.gate.resume('actual user');self.assertFalse(self.gate.activity('read')['stopped'])

    def test_progress_counts_test_cases_instead_of_vitest_files(self):
        self.assertEqual(test_counts('Test Files  1 failed (1)\n     Tests  5 failed | 4 passed (9)\n'), {'failed': 5, 'passed': 4})
        self.assertEqual(test_counts('Test Files 1 passed (1)\n Tests 9 passed (9)\n'), {'failed': None, 'passed': 9})

    def test_native_business_answers_are_recorded_once_and_timeout_is_not_a_decision(self):
        self.gate.create('questions-case', 'return integers')
        value = copy.deepcopy(workflow.REQUIREMENTS)
        value['intent']['open_questions'] = [{'id': 'Q1', 'question': 'Should results persist?', 'blocking': True,
            'options': [{'id': 'yes', 'label': 'Yes', 'impact': 'Store locally'}, {'id': 'no', 'label': 'No', 'impact': 'Session only'}], 'recommended_option': 'yes', 'recommendation_reason': 'Convenience'}]
        self.f.submit_requirements(value)
        with patch('aidevflow.backends.zscode.progress.time.monotonic', return_value=100): self.gate.activity('provider')
        event = {'type': 'tool_call', 'session_id': 'native', 'toolCallId': 'ask1',
                 'input': {'questions': [{'id': 'Q1', 'question': 'fake abandon prompt', 'options': []}]}}
        result = self.gate.record_questions(event)
        self.assertEqual(result['input']['questions'][0]['question'], 'Should results persist?')
        response = {**event, 'type': 'tool_result', 'content': [{'type': 'text', 'text': 'Yes'}], 'details': {'timedOut': True}}
        self.assertFalse(self.gate.record_questions(response)['recorded'])
        response['details'] = {'selectedOptions': ['Yes']}
        recovered = LightGate(self.root).recover_questions('native', [{'message': {**response, 'role': 'toolResult', 'toolName': 'ask'}}])
        self.assertTrue(recovered['recovered'])
        self.gate.record_questions(response)
        self.assertEqual(len(self.gate.status()['feedback']), 1)
        with patch('aidevflow.backends.zscode.progress.time.monotonic', return_value=2000):
            self.assertFalse(self.gate.activity('provider')['stopped'])
        bad = {**event, 'toolCallId': 'ask2', 'input': {'questions': [{'id': 'abandon', 'question': 'Restart?'}]}}
        with self.assertRaises(GateError): self.gate.record_questions(bad)

    def test_prepare_task_separates_dependencies_and_existing_checkpoint_can_upgrade(self):
        self.f.entry();self.gate.spec(workflow.SPEC, workflow.MAP)
        tasks = [{'id': '0.1', 'title': 'prepare local test tooling', 'kind': 'prepare', 'specs': ['S1'],
                  'files': ['test-support.txt'], 'argv': [sys.executable, '-c', 'print("ready")']}, *self.f.tasks]
        self.gate.plan(tasks)
        (self.root / 'test-support.txt').write_text('ready', encoding='utf8')
        self.assertEqual(self.gate.run()['cursor'], 1)
        old = self.gate.status()
        with observer_lock(self.gate.control, timeout=0, name='coordinator.lock'):
            with self.assertRaises(TimeoutError): install(workflow.SOURCE, self.root, upgrade_active=True)
        result = install(workflow.SOURCE, self.root, upgrade_active=True)
        self.assertTrue((self.root / result['upgrade_backup'] / 'CHECKPOINT.json').is_file())
        self.assertEqual(self.gate.status(), old)
        with patch('aidevflow.backends.zscode.environment.subprocess.run', side_effect=AssertionError('no command should execute')):
            facts = inspect(self.root, versions=False)
        self.assertFalse(facts['network_used']);self.assertTrue(facts['node']['executable'])

