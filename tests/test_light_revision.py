"""SDD repair contract: real checks, simulated native role receipts, isolated projects."""
import copy
import json
import unittest
from unittest.mock import patch
import test_light_workflow as w
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.storage import GateError, read_json, sha256_file, write_json_atomic


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.f = w.LightWorkflowTests(); self.f.setUp()
        self.gate, self.root = self.f.gate, self.f.root

    def tearDown(self): self.f.tearDown()

    def draft(self, name, value):
        rel = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        p = self.root / rel; p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str): p.write_text(value, encoding='utf8')
        else: write_json_atomic(p, value)
        return rel

    def test_complete_nested_paths_and_read_only_references(self):
        self.f.entry()
        spec = w.SPEC.replace('### S1 返回一', '### S1 返回一\n代码依据：`dashboard/src/new.mjs`')
        self.gate.spec(spec, w.MAP)
        bad = copy.deepcopy(self.f.tasks); bad[1]['files'] = ['src/new.mjs']
        with self.assertRaisesRegex(GateError, '规格路径'): self.gate.plan(bad)
        good = copy.deepcopy(bad); good[1]['files'] = ['dashboard/src/new.mjs']
        self.assertEqual(self.gate.plan(good)['stage'], 'applying')

    def test_initial_revise_exposes_unfrozen_branch(self):
        self.f.begin(); self.f.write_test(1); self.gate.prepare_baseline(); self.f.baseline_review('revise')
        s = self.gate.summary()
        self.assertEqual(s['recovery']['kind'], 'edit_unfrozen_tests')
        self.assertEqual(s['recovery']['proposal_template']['group'], 'G1')
        self.assertFalse(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        self.assertEqual(s['skill'], 'zsspec-apply')

    def test_initial_derived_revision_preserves_approval_and_requires_new_red(self):
        self.f.begin(); self.f.write_test(1); self.gate.prepare_baseline(); self.f.baseline_review('revise')
        before = self.gate.status()
        spec = self.draft('spec-fixed.json', {'content': w.SPEC.replace('## 边界', '说明：保持原业务约定。\n## 边界'), 'acceptance_map': w.MAP})
        proposal = self.draft('repair.json', {'reason': 'Correct derived design', 'group': 'G1', 'changes': {}, 'spec_path': spec})
        self.gate.propose_repair(proposal); self.f.baseline_review()
        s = self.gate.apply_repair()
        self.assertEqual(s['stage'], 'applying'); self.assertEqual(s['current_task']['kind'], 'red')
        self.assertEqual(self.gate.status()['approval'], before['approval'])
        self.assertFalse(self.gate.status()['baselines'])
        with self.assertRaisesRegex(GateError, '基线审查'): self.gate.run()
        self.assertEqual(self.f.run_current()['cursor'], 1)

    def test_partial_completion_revision_keeps_prior_frozen_red(self):
        self.f.begin(); self.f.write_test(1); self.f.run_current()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf8')
        self.gate.run(); self.f.write_test(2); self.gate.prepare_baseline(); self.f.baseline_review('revise')
        before = self.gate.status(); old = before['tasks'][0]['result']
        tasks = copy.deepcopy(self.f.tasks); tasks[3]['title'] += ' clarified'
        proposal = self.draft('repair.json', {'reason': 'Clarify next task', 'group': 'G2', 'changes': {}, 'plan_path': self.draft('plan-fixed.json', {'tasks': tasks})})
        self.gate.propose_repair(proposal); self.f.baseline_review(); self.gate.apply_repair()
        after = self.gate.status()
        self.assertEqual(after['baselines'], before['baselines'])
        self.assertEqual(after['tasks'][0]['result'], old)
        self.assertTrue(after['tasks'][1]['completed'])
        self.assertEqual(after['current_task']['id'], '2.1')
        self.assertEqual(sha256_file(self.root / old['path']), old['sha256'])
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])

    def test_full_regression_failure_reopens_implementation_with_evidence(self):
        self.f.reach_regression()
        (self.root / 'src/sample.py').write_text('def one(): return 99\ndef two(): return 2\n', encoding='utf8')
        s = self.gate.run()
        self.assertEqual(s['current_task']['kind'], 'green')
        self.assertFalse(self.gate.guard('write', {'path': 'src/sample.py'})['block'])
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        self.assertEqual(read_json(self.root / s['repair_evidence'])['passed'], False)
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 2\n', encoding='utf8')
        self.gate.run(); self.gate.run()
        self.assertEqual(self.gate.run()['stage'], 'verify')

    def test_different_artifact_errors_have_distinct_repeat_budgets(self):
        self.f.begin()
        for kind, value in [('spec', {'content': w.SPEC + '\n', 'acceptance_map': w.MAP}),
                            ('plan', {'tasks': self.f.tasks})]:
            with self.assertRaises(GateError):
                model_operation(self.gate, {'action': 'submit', 'kind': kind, 'path': self.draft(kind + '.json', value)})
        failure = self.gate.status()['operation_failure']
        self.assertEqual(failure['count'], 1); self.assertEqual(failure['total'], 2)
        self.assertEqual(failure['artifact_kind'], 'plan')

    def test_knowledge_updates_are_proposals_until_archive(self):
        self.f.begin(); original = (self.root / 'specs/modules/sample.md').read_bytes()
        path = self.draft('knowledge.json', {'files': {'specs/modules/sample.md': '# sample\nNew finding\n'}})
        model_operation(self.gate, {'action': 'submit', 'kind': 'knowledge', 'path': path})
        self.assertEqual((self.root / 'specs/modules/sample.md').read_bytes(), original)
        self.assertIn('New finding', self.gate.status()['knowledge_proposal']['specs/modules/sample.md'])

    def test_status_does_not_consume_research_reads(self):
        self.f.entry()
        config = read_json(self.root / '.aidevflow/project.json'); config['progress_limits'] = {'reads': 2}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        self.gate.activity('read')
        for _ in range(3): self.assertFalse(self.gate.activity('status')['stopped'])
        self.assertTrue(self.gate.activity('grep')['stopped'])

    def test_direct_spec_resubmission_cannot_erase_frozen_baselines(self):
        self.f.begin(); self.f.write_test(1); self.f.run_current(); before = self.gate.status()
        with self.assertRaises(GateError): self.gate.spec(w.SPEC + '\n', w.MAP)
        self.assertEqual(self.gate.status()['baselines'], before['baselines'])

    def test_business_conflict_stops_without_waiting_for_budget_or_changing_approval(self):
        self.f.begin(); before = self.gate.status()
        s = model_operation(self.gate, {'action': 'report_conflict', 'reason': 'Data has no end marker but PRD requires a known end'})
        self.assertEqual(s['stage'], 'needs_attention')
        self.assertEqual(self.gate.status()['attention_kind'], 'business_decision')
        self.assertEqual(self.gate.status()['approval'], before['approval'])
        self.assertTrue(self.gate.activity('provider')['stopped'])
        self.gate.requirement_feedback('Use last-record time explicitly', 'actual user')
        self.assertEqual(self.gate.status()['stage'], 'discovery')
        self.assertNotIn('approval', self.gate.status())

    def test_structured_design_separates_existing_references_and_target_files(self):
        self.f.entry()
        design = {'source_refs': ['src/sample.py'], 'modules': [
            {'id': 'G1', 'implementation_files': ['src/sample.py'], 'test_files': ['tests/test_1.py'], 'depends_on': [], 'contracts': 'one() -> integer 1'},
            {'id': 'G2', 'implementation_files': ['src/sample.py'], 'test_files': ['tests/test_2.py'], 'depends_on': ['G1'], 'contracts': 'two() -> integer 2'}]}
        self.gate.spec(w.SPEC, w.MAP, design=design)
        bad = copy.deepcopy(self.f.tasks); bad[1]['files'] = ['src/wrong.py']
        with self.assertRaisesRegex(GateError, '规格路径'): self.gate.plan(bad)
        self.assertEqual(self.gate.plan(self.f.tasks)['stage'], 'applying')

    def prepare_revision(self):
        self.f.begin(); self.f.write_test(1)
        spec = self.draft('spec-fixed.json', {'content': w.SPEC.replace('## 边界', '说明\n## 边界'), 'acceptance_map': w.MAP})
        proposal = self.draft('repair.json', {'reason': 'Derived clarification', 'group': 'G1', 'changes': {}, 'spec_path': spec})
        self.gate.propose_repair(proposal); self.f.baseline_review()
        return proposal

    def test_derived_transaction_recovers_after_checkpoint_interruption(self):
        self.prepare_revision(); original = self.gate.checkpoint
        def interrupted(state, run, event):
            if event == 'baseline_repair_committed': raise OSError('power loss')
            return original(state, run, event)
        with patch.object(self.gate, 'checkpoint', side_effect=interrupted):
            with self.assertRaises(OSError): self.gate.apply_repair()
        self.assertEqual(self.gate.apply_repair()['stage'], 'applying')
        self.assertEqual(self.gate.status()['baseline_version'], 1)

    def test_stale_derived_review_and_inflight_execution_are_rejected(self):
        self.prepare_revision()
        (self.root / 'src/sample.py').write_text('def one(): return 7\ndef two(): return 0\n', encoding='utf8')
        with self.assertRaisesRegex(GateError, '过期'): self.gate.apply_repair()

    def test_repair_limit_retains_an_editable_cursor_and_reason(self):
        config = read_json(self.root / '.aidevflow/project.json')
        config['failure_limits'] = {'repair_rounds': 6}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        self.f.reach_regression()
        state, run = self.gate.load(); state['repair_rounds'] = 6
        self.gate.route_repair(state, 'business behavior is wrong')
        self.gate.checkpoint(state, run, 'test_exhausted')
        s = self.gate.status()
        self.assertEqual(s['stage'], 'needs_attention'); self.assertIn('6', s['attention_reason'])
        self.gate.resume('actual user')
        self.assertEqual(self.gate.status()['current_task']['kind'], 'green')
        self.assertEqual(self.gate.status()['repair_rounds'], 0)
        self.assertEqual(self.gate.status()['repair_budget_history'][-1]['rounds'], 7)

    def test_forged_decision_cannot_resolve_a_blocking_question(self):
        self.gate.create('question-case', 'request')
        value = copy.deepcopy(w.REQUIREMENTS)
        value['intent']['open_questions'] = [{'id': 'Q1', 'question': 'Which value?', 'blocking': True,
            'options': [{'id': 'A', 'label': 'one', 'impact': 'one'}, {'id': 'B', 'label': 'two', 'impact': 'two'}],
            'recommended_option': 'A', 'recommendation_reason': 'matches request'}]
        value['intent']['decisions'] = [{'question_id': 'Q1', 'option_id': 'A', 'answer': 'one', 'recorded_at': '2026-11-05'}]
        self.f.submit_requirements(value)
        with self.assertRaisesRegex(GateError, '业务问题'):
            self.gate.approve(self.gate.status()['requirements']['sha256'], 'actual user')

    def test_real_answer_resolves_question_without_rewriting_native_result(self):
        self.test_forged_decision_cannot_resolve_a_blocking_question()
        event = {'type': 'tool_call', 'session_id': 'native', 'toolCallId': 'q1', 'input': {'questions': [{'id': 'Q1'}]}}
        self.gate.record_questions(event)
        self.gate.record_questions({**event, 'type': 'tool_result', 'content': [{'type': 'text', 'text': 'one'}]})
        value = copy.deepcopy(self.gate.status()['requirements']['value'])
        self.f.submit_requirements(value)
        s = self.gate.status(); self.assertEqual(s['blocking_questions'], [])
        self.assertTrue(s['requirements']['value']['intent']['open_questions'][0]['blocking'])
        self.assertEqual(self.gate.approve(s['requirements']['sha256'], 'actual user')['stage'], 'spec')

    def test_declared_browser_check_requires_fresh_physical_evidence(self):
        self.f.entry(); self.gate.spec(w.SPEC, w.MAP)
        tasks = copy.deepcopy(self.f.tasks)
        rel = f"specs/drafts/{self.gate.status()['run_id']}/evidence/screen.png"
        import sys
        tasks[-1]['checks'] = [{'id': 'ui', 'kind': 'browser', 'specs': ['S1', 'S2'],
            'argv': [sys.executable, '-c', "print('fixture browser check does not create evidence')"], 'evidence_paths': [rel]}]
        self.gate.plan(tasks)
        for i in (1, 2):
            self.f.write_test(i); self.f.run_current()
            (self.root / 'src/sample.py').write_text(f'def one(): return 1\ndef two(): return {2 if i == 2 else 0}\n', encoding='utf8')
            self.gate.run()
        p = self.root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'old screenshot')
        s = self.gate.run()
        self.assertEqual(s['stage'], 'applying')
        self.assertEqual(s['current_task']['kind'], 'green')
        evidence = read_json(self.root / s['repair_evidence'])
        self.assertFalse(evidence['passed']); self.assertIn('证据', evidence['records'][-1]['output'])

    def test_failed_prepare_can_be_reviewed_and_retried_with_correct_environment(self):
        import sys
        self.f.entry(); self.gate.spec(w.SPEC, w.MAP)
        tasks = [{'id': '0.1', 'title': 'prepare runner', 'kind': 'prepare', 'specs': ['S1'],
            'files': ['runner.txt'], 'argv': [sys.executable, '-c', 'raise SystemExit(1)']}, *copy.deepcopy(self.f.tasks)]
        self.gate.plan(tasks); self.gate.run()
        tasks[0]['argv'] = [sys.executable, '-c', "print('runner ready')"]
        proposal = self.draft('repair.json', {'reason': 'Correct failed environment preparation', 'group': 'G1', 'changes': {},
            'plan_path': self.draft('fixed-plan.json', {'tasks': tasks})})
        prepared = self.gate.propose_repair(proposal)
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        self.assertTrue(any(x['label'].startswith('execution/') for x in manifest['files']))
        self.f.baseline_review(); self.gate.apply_repair()
        self.assertEqual(self.gate.status()['current_task']['id'], '0.1')
        self.assertEqual(self.gate.run()['cursor'], 1)

    def test_failed_unaccepted_red_can_correct_runner_without_unlocking_accepted_tests(self):
        import sys
        self.f.entry(); self.gate.spec(w.SPEC, w.MAP)
        tasks = copy.deepcopy(self.f.tasks)
        broken = [sys.executable, '-c', "raise ImportError('runner setup is missing')"]
        tasks[0]['argv'] = broken; tasks[1]['argv'] = broken
        self.gate.plan(tasks); self.f.write_test(1); self.f.run_current()
        self.assertIn('G1', self.gate.status()['baselines'])
        tasks[0]['argv'] = self.f.argv; tasks[1]['argv'] = self.f.argv
        proposal = self.draft('repair.json', {'reason': 'Restore declared unittest runner after real import failure', 'group': 'G1',
            'changes': {}, 'plan_path': self.draft('runner-plan.json', {'tasks': tasks})})
        self.gate.propose_repair(proposal); self.f.baseline_review(); self.gate.apply_repair()
        self.assertFalse(self.gate.status()['baselines'])
        self.assertEqual(self.f.run_current()['cursor'], 1)

    def test_http_check_runs_and_seals_its_report_in_verifier_bundle(self):
        import sys
        self.f.entry(); self.gate.spec(w.SPEC, w.MAP)
        rel = f"specs/drafts/{self.gate.status()['run_id']}/evidence/http.json"
        code = "\n".join([
            'import http.server,threading,urllib.request,json,pathlib',
            'class Handler(http.server.BaseHTTPRequestHandler):',
            ' def do_GET(self):',
            "  self.send_response(200);self.end_headers();self.wfile.write(b'{\"scope\":\"project\"}')",
            "server=http.server.HTTPServer(('127.0.0.1',0),Handler)",
            'threading.Thread(target=server.serve_forever,daemon=True).start()',
            "opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))",
            "data=json.load(opener.open('http://127.0.0.1:%d/'%server.server_port))",
            "assert data['scope']=='project'",
            f"p=pathlib.Path({rel!r});p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))",
            'server.shutdown();server.server_close()'])
        tasks = copy.deepcopy(self.f.tasks)
        tasks[-1]['checks'] = [{'id': 'http', 'kind': 'http', 'specs': ['S1'],
            'argv': [sys.executable, '-c', code], 'evidence_paths': [rel]}]
        self.gate.plan(tasks)
        for i in (1, 2):
            self.f.write_test(i); self.f.run_current()
            (self.root / 'src/sample.py').write_text(f'def one(): return 1\ndef two(): return {2 if i == 2 else 0}\n', encoding='utf8')
            self.gate.run()
        final = self.gate.run()
        diagnostic = read_json(self.root / final['repair_evidence']) if final.get('repair_evidence') else final['stage']
        self.assertEqual(final['stage'], 'verify', diagnostic)
        bundle = self.gate.prepare_verifier(); manifest = read_json(self.root / bundle['path'] / 'BUNDLE.json')
        asset = next(x for x in manifest['files'] if x['label'] == rel)
        (self.root / rel).write_text('changed draft report', encoding='utf8')
        self.assertEqual(read_json(self.root / bundle['path'] / asset['path']), {'scope': 'project'})


if __name__ == '__main__': unittest.main()
