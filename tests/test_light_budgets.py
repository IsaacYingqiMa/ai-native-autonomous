"""Budget policy: real local RED/GREEN, explicitly simulated native reviews."""
import copy
import unittest

import test_light_workflow as w
from aidevflow.storage import GateError, read_json, write_json_atomic
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.installer import install
from aidevflow.backends.zscode.progress import test_counts


class LightBudgetTests(unittest.TestCase):
    def setUp(self):
        self.f = w.LightWorkflowTests(); self.f.setUp()
        self.gate, self.root = self.f.gate, self.f.root

    def tearDown(self): self.f.tearDown()

    def config(self, **updates):
        path = self.root / '.aidevflow/project.json'
        value = read_json(path); value.update(updates); write_json_atomic(path, value)

    def draft(self, name, value):
        rel = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        write_json_atomic(self.root / rel, value)
        return rel

    def test_generous_defaults_and_visible_failure_budgets(self):
        self.f.begin()
        self.assertEqual(self.gate.progress_limits(), {'providers': 160, 'reads': 256, 'seconds': 10800})
        self.assertEqual(self.gate.failure_limits(), {'operation_warning': 3, 'operation_repeats': 12,
            'operation_total': 30, 'execution_failures': 20, 'baseline_reviews': 16,
            'verification_failures': 16, 'repair_rounds': 20})
        self.assertEqual(self.gate.summary()['autonomy_budget']['failure_limits'], self.gate.failure_limits())

    def test_old_rejections_do_not_survive_a_real_red_green_group(self):
        self.f.begin()
        for _ in range(2): self.gate.operation_failure('repair_propose', 'same conflict')
        self.f.write_test(1); self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run()
        self.assertNotIn('operation_failure', self.gate.status())
        self.assertTrue(self.gate.status()['operation_failure_history'])
        result = self.gate.operation_failure('repair_propose', 'same conflict')
        self.assertEqual(result['attempts'], 1); self.assertEqual(result['total'], 1)
        self.assertFalse(result['stopped']); self.assertEqual(self.gate.status()['current_task']['id'], '2.1')

    def test_accepted_proposal_resolves_streak_but_not_segment_total(self):
        self.f.begin()
        for _ in range(2): self.gate.operation_failure('repair_propose', 'old draft')
        tasks = copy.deepcopy(self.f.tasks); tasks[1]['title'] += ' clarified'
        path = self.draft('repair.json', {'reason': 'Clarify implementation', 'group': 'G1', 'changes': {},
            'plan_path': self.draft('plan.json', {'tasks': tasks})})
        before = self.gate.status()['approval']
        model_operation(self.gate, {'action': 'repair_propose', 'path': path})
        failure = self.gate.status()['operation_failure']
        self.assertEqual(failure['count'], 0); self.assertEqual(failure['total'], 2)
        self.f.baseline_review(); self.gate.apply_repair()
        self.assertNotIn('operation_failure', self.gate.status())
        self.assertEqual(self.gate.status()['approval'], before)

    def test_third_failure_enters_correction_without_stopping(self):
        self.f.begin()
        for _ in range(3): result = self.gate.operation_failure('repair_propose', 'inspect current plan')
        self.assertFalse(result['stopped'])
        self.assertTrue(result['correction']['required'])
        self.assertEqual(result['limits']['operation_repeats'], 12)
        self.assertIn('operation_correction', self.gate.summary())
        self.assertEqual(self.gate.status()['stage'], 'applying')

    def test_status_worker_recreation_and_text_changes_do_not_reset_total(self):
        self.f.begin()
        self.config(failure_limits={'operation_warning': 2, 'operation_repeats': 4, 'operation_total': 5})
        for n in range(5):
            gate = LightGate(self.root)
            state, run = gate.load(); state['diagnosis'] = str(n); gate.checkpoint(state, run, 'test_diagnostic')
            result = gate.operation_failure('different-' + str(n), 'new wording')
            model_operation(gate, {'action': 'status'})
            self.assertEqual(result['total'], n + 1)
            self.assertEqual(result['stopped'], n == 4)

    def test_same_rejection_stops_at_configured_boundary(self):
        self.f.begin()
        self.config(failure_limits={'operation_warning': 2, 'operation_repeats': 4, 'operation_total': 8})
        for n in range(4):
            result = self.gate.operation_failure('repair_propose', 'unchanged')
            self.assertEqual(result['stopped'], n == 3)
        self.assertIn('4/4', self.gate.status()['attention_reason'])

    def test_invalid_configuration_cannot_disable_limits(self):
        self.f.begin()
        for value in ({'unknown': 1}, {'execution_failures': True}, {'repair_rounds': 0},
                      {'operation_warning': 12, 'operation_repeats': 12},
                      {'operation_repeats': 31, 'operation_total': 30}):
            with self.subTest(value=value):
                self.config(failure_limits=value)
                with self.assertRaises(GateError): self.gate.failure_limits()
        self.config(failure_limits={})
        for value in ({'seconds': -1}, {'reads': True}, {'unknown': 3}):
            self.config(progress_limits=value)
            with self.assertRaises(GateError): self.gate.progress_limits()

    def test_execution_failure_limit_is_configurable_and_red_failure_remains_real(self):
        self.f.begin(); self.config(failure_limits={'execution_failures': 2})
        self.f.write_test(1)
        test = self.root / 'tests/test_1.py'
        test.write_text(test.read_text('utf8').replace('self.assertEqual(one(), 1)', "raise RuntimeError('broken setup')"), encoding='utf8')
        for n in range(2):
            result = self.f.run_current()
            self.assertEqual(result['stage'], 'needs_attention' if n == 1 else 'applying')
        self.assertIn('2/2', result['attention_reason'])
        self.assertFalse(result['tasks'][0].get('completed'))

    def test_baseline_review_budget_can_be_set_independently(self):
        self.f.begin(); self.config(failure_limits={'baseline_reviews': 2}); self.f.write_test(1)
        for n in range(2):
            if n:
                path = self.root / 'tests/test_1.py'
                path.write_text(path.read_text('utf8').replace('self.assertEqual(one(), 1)',
                    'self.assertEqual(one(), 1); self.assertIsInstance(one(), int)'), encoding='utf8')
            self.gate.prepare_baseline(); self.f.baseline_review('revise')
            self.assertEqual(self.gate.status()['stage'], 'needs_attention' if n == 1 else 'applying')
        self.assertIn('2/2', self.gate.status()['attention_reason'])

    def test_repair_round_budget_preserves_editable_recovery(self):
        self.f.begin(); self.config(failure_limits={'repair_rounds': 2})
        state, run = self.gate.load()
        for n in range(3): self.gate.route_repair(state, 'real behavior failure')
        self.gate.checkpoint(state, run, 'test_repair_limit')
        self.assertEqual(self.gate.status()['stage'], 'needs_attention')
        self.gate.resume('actual operator')
        self.assertEqual(self.gate.status()['repair_rounds'], 0)
        self.assertEqual(self.gate.status()['current_task']['kind'], 'green')

    def test_upgrade_preserves_custom_limits_and_explicit_extension_only_raises_them(self):
        self.f.begin(); before = self.gate.status()
        self.config(progress_limits={'providers': 200, 'reads': 96, 'seconds': 1800},
                    failure_limits={'execution_failures': 25})
        install(w.SOURCE, self.root, upgrade_active=True)
        self.assertEqual(self.gate.progress_limits()['seconds'], 1800)
        result = install(w.SOURCE, self.root, upgrade_active=True, extend_budgets=True)
        self.assertTrue(result['upgrade_backup'])
        self.assertEqual(self.gate.progress_limits(), {'providers': 200, 'reads': 256, 'seconds': 10800})
        self.assertEqual(self.gate.failure_limits()['execution_failures'], 25)
        self.assertEqual(self.gate.status()['revision'], before['revision'])
        self.assertEqual(self.gate.status()['approval'], before['approval'])

    def test_plan_conflict_names_fields_and_supplies_current_authoritative_definition(self):
        self.f.begin(); before = self.gate.status()
        tasks = copy.deepcopy(self.f.tasks)
        for task in tasks[:2]: task['specs'] = ['S1', 'S2']
        path = self.draft('repair.json', {'reason': 'Only extend implementation files', 'group': 'G2', 'changes': {},
            'plan_path': self.draft('stale-plan.json', {'tasks': tasks})})
        with self.assertRaises(GateError) as caught:
            model_operation(self.gate, {'action': 'repair_propose', 'path': path})
        self.assertEqual(caught.exception.code, 'PLAN_CONFLICT')
        diagnostic = caught.exception.details['diagnostic']
        self.assertEqual([(x['task_id'], x['field']) for x in diagnostic['conflicts']], [('1.1', 'specs'), ('1.2', 'specs')])
        self.assertEqual(diagnostic['current_plan']['tasks'][0]['specs'], ['S1'])
        self.assertEqual(self.gate.status()['tasks'], before['tasks'])
        self.assertIn('operation_correction', self.gate.summary())

    def test_node_default_and_tap_reporters_count_cases(self):
        for output in ('ℹ tests 15\nℹ pass 15\nℹ fail 0\n', '\x1b[32mℹ pass 15\x1b[0m\nℹ fail 0\n', '# tests 15\n# pass 15\n# fail 0\n'):
            self.assertEqual(test_counts(output), {'passed': 15, 'failed': 0})


if __name__ == '__main__': unittest.main()
