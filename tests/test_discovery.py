from __future__ import annotations

import copy
import json
from unittest.mock import patch

from test_gate import GateFixture
from aidevflow.discovery import Discovery, PROFILE, validate_requirements, check_contract
from aidevflow.gate import GateError, read_json, sha256_file, write_json_atomic
from aidevflow.omp import observe
from aidevflow.bridge import guard
from aidevflow.contexts import role_context
from aidevflow.installer import install
from aidevflow.workflow import Workflow
from pathlib import Path


class DiscoveryTests(GateFixture):
    RUNTIME = "omp"
    NATIVE = ".omp"

    def observe(self, root, payload):
        from aidevflow.runtimes.registry import get_runtime
        return get_runtime(self.RUNTIME).observe(root, payload)

    def setUp(self):
        super().setUp()
        self.gate.active_path.unlink()
        self._write_json('.aidevflow/project.json', {'runtime': self.RUNTIME, 'workflow_profile': PROFILE})
        (self.root / 'request.md').write_text('Return one. No extra features.', encoding='utf-8')
        self.discovery = Discovery(self.root)
        self.discovery.create('request.md')
        self.value = {'schema': 'aidevflow/requirements-v1', 'intent': self._draft(),
                      'prd': '# Product\nReturn one to the caller.',
                      'requirements': [{'id': 'R1', 'statement': 'Return one', 'source': 'request.md', 'acceptance_ids': ['REQ-1']}],
                      'scenarios': [{'id': 'S1', 'requirement_ids': ['R1'], 'acceptance_ids': ['REQ-1'],
                                     'given': 'A caller', 'when': 'It asks for the value', 'then': 'It receives one'}]}
        self.value['intent']['open_questions'] = []

    def record(self, value=None, call='call-1'):
        value = self.value if value is None else value
        request = {'type': 'tool_call', 'session_id': 's1', 'toolCallId': call,
                   'input': {'agent': 'aidevflow-requirements-analyst', 'task': 'requirements', 'solutionSpace': 'bounded'}}
        self.observe(self.root, request)
        receipt = self.observe(self.root, {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'agent-' + call, 'agent': 'aidevflow-requirements-analyst', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': value}}]}})
        return self.discovery.record(receipt['result_path'])

    def approve(self):
        state = self.record()
        return self.discovery.approve(state['task_id'], state['requirements_revision'], state['manifest_sha256'], 'owner', 'I approve this Intent and PRD for delivery')

    def test_no_legacy_entrypoint_or_product_write_can_bypass_discovery(self):
        for action in (lambda: self.gate.start_intake('request.md'), lambda: self.gate.init('OTHER', '.aidevflow/contract.json'), lambda: self.gate.rebuild('TEST-1')):
            with self.assertRaises(GateError):
                action()
        for tool, inp in [('write', {'path': 'src/sample.py', 'content': 'wrong'}), ('bash', {'command': 'python -c "print(1)"'})]:
            self.assertTrue(guard(self.root, {'toolName': tool, 'input': inp})['block'])
        self.assertFalse(guard(self.root, {'toolName': 'read', 'input': {'path': 'src/sample.py'}})['block'])
        with self.assertRaises(GateError):
            self.discovery.start_delivery()

    def test_native_result_and_exact_revision_are_required(self):
        self._write_json('fake.json', self.value)
        with self.assertRaisesRegex(GateError, 'native analyst receipt'):
            self.discovery.record('fake.json')
        state = self.record()
        with self.assertRaisesRegex(GateError, 'current review-ready'):
            self.discovery.approve(state['task_id'], 99, state['manifest_sha256'], 'owner', 'approved')
        context = role_context(self.root, 'requirements_analyst')
        self.assertIn('human approval', context['task_call']['context'])

    def test_prd_tampering_invalidates_approval_and_delivery(self):
        state = self.record()
        directory = self.root / state['manifest_path']
        (directory.parent / 'prd.md').write_text('Changed scope', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'content changed'):
            self.discovery.approve(state['task_id'], 1, state['manifest_sha256'], 'owner', 'approved')

    def test_approved_delivery_is_bound_and_idempotent(self):
        approved = self.approve()
        result = self.discovery.start_delivery()
        state, _, _ = self.gate._load_active()
        self.assertEqual(state['status'], 'intent_approved')
        self.assertEqual(state['requirements_ref'], approved['requirements_ref'])
        self.assertNotIn('autonomous_after_blockers', json.dumps(state))
        self.assertEqual(self.discovery.start_delivery()['task_id'], result['task_id'])
        with self.assertRaisesRegex(GateError, 'immutable'):
            self.gate.record_intent('fake.json')
        contract = {**self.contract, 'task_id': result['task_id'], 'requirements_ref': state['requirements_ref']}
        check_contract(self.root, state['requirements_ref'], contract)
        contract['acceptance_items'] = []
        with self.assertRaisesRegex(GateError, 'acceptance IDs'):
            check_contract(self.root, state['requirements_ref'], contract)
        manifest = read_json(self.root / state['requirements_ref']['path'])
        (self.root / manifest['intent_path']).write_text('tampered', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'content changed'):
            self.gate.status()

    def test_interrupted_handoff_recovers_same_task(self):
        self.approve()
        with patch.object(self.gate.__class__, '_create_intake', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.discovery.start_delivery()
        planned = read_json(self.discovery.transaction)['task_id']
        with self.assertRaises(GateError):
            self.gate.start_intake('request.md')
        result = self.discovery.start_delivery()
        self.assertEqual(result['task_id'], planned)
        self.assertFalse(self.discovery.transaction.exists())

    def test_handoff_recovers_after_run_was_persisted(self):
        self.approve()
        original = self.gate.__class__._create_intake
        def interrupted(gate, *args):
            original(gate, *args)
            raise OSError('after run creation')
        with patch.object(self.gate.__class__, '_create_intake', interrupted):
            with self.assertRaises(OSError):
                self.discovery.start_delivery()
        with self.assertRaisesRegex(GateError, 'handoff'):
            self.gate.status()
        expected = read_json(self.discovery.transaction)['task_id']
        self.assertEqual(self.discovery.start_delivery()['task_id'], expected)
        self.assertEqual(len(list(self.gate.runs.glob('ADF-*'))), 1)

    def test_blocking_answer_requires_new_analyst_review(self):
        blocked = copy.deepcopy(self.value)
        blocked['intent']['open_questions'] = self._draft()['open_questions']
        state = self.record(blocked)
        self.assertEqual(state['status'], 'awaiting_answers')
        with self.assertRaises(GateError):
            self.discovery.approve(state['task_id'], 1, state['manifest_sha256'], 'owner', 'approved')
        self.discovery.answer('Q1', 'Return one')
        state = self.record(call='call-2')
        self.assertEqual(state['status'], 'review_ready')
        manifest = read_json(self.root / state['manifest_path'])
        self.assertTrue(any('/answers/' in path for path in manifest['files']))

    def test_revision_preserves_old_approval_and_requires_new_one(self):
        old = self.approve()
        self.discovery.revise('Owner requested a clarification')
        state = self.record(call='call-2')
        self.assertEqual(state['requirements_revision'], 2)
        self.assertTrue((self.root / old['requirements_ref']['approval_path']).exists())
        with self.assertRaises(GateError):
            self.discovery.start_delivery()

    def test_schema_rejects_uncovered_or_fabricated_requirements(self):
        value = copy.deepcopy(self.value)
        value['scenarios'][0]['acceptance_ids'] = ['missing']
        self.assertTrue(validate_requirements(value))
        value = copy.deepcopy(self.value)
        value['intent']['decisions'] = [{'question_id': 'invented'}]
        self.assertTrue(validate_requirements(value))

    def test_cancellation_retains_history_and_allows_a_new_discussion(self):
        original = self.discovery.load()[0]['task_id']
        state = self.discovery.cancel('Owner cancelled this request')
        self.assertEqual(state['status'], 'cancelled')
        self.assertTrue((self.root / '.aidevflow/discoveries' / original / 'state.json').is_file())
        self.assertNotEqual(self.discovery.create('request.md')['task_id'], original)

    def test_agent_cannot_approve_through_shell(self):
        for verb in ('discovery-approve', "dis'covery-approve'", '--root . dis"covery-approve"', '> /dev/null'):
            with self.subTest(verb=verb):
                result = guard(self.root, {'toolName': 'bash', 'input': {
                    'command': f'python scripts/aidevflow.py {verb} --id DISC-x --revision 1 --sha256 x --by agent --utterance yes'}})
                self.assertTrue(result['block'])

    def test_literal_gate_commands_remain_available_to_the_coordinator(self):
        for verb in ('status', '--help', 'discovery-status'):
            result = guard(self.root, {'toolName': 'bash', 'input': {
                'command': f'python scripts/aidevflow.py {verb}'}})
            self.assertFalse(result['block'])

    def test_old_native_result_cannot_be_used_after_context_revision(self):
        request = {'type': 'tool_call', 'session_id': 's1', 'toolCallId': 'stale',
                   'input': {'agent': 'aidevflow-requirements-analyst', 'task': 'prepare'}}
        self.observe(self.root, request)
        self.discovery.revise('New material changes the discussion')
        receipt = self.observe(self.root, {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'stale-agent', 'agent': 'aidevflow-requirements-analyst', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': self.value}}]}})
        self.assertEqual(receipt['status'], 'stale')

    def test_install_retains_active_profile_and_installs_native_schema(self):
        source = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(GateError, 'active context'):
            install(source, self.root, self.RUNTIME, workflow_profile='legacy')
        install(source, self.root, self.RUNTIME, workflow_profile=PROFILE)
        role = (self.root / (self.NATIVE + "/agents/aidevflow-requirements-analyst.md")).read_text(encoding='utf-8')
        self.assertIn('aidevflow/requirements-v1', role)
        self.assertEqual(read_json(self.root / '.aidevflow/project.json')['workflow_profile'], PROFILE)

    def test_event_replays_an_interrupted_snapshot_and_approval_is_idempotent(self):
        approved = self.approve()
        directory = self.root / '.aidevflow/discoveries' / approved['task_id']
        snapshot = read_json(directory / 'state.json')
        snapshot.update(status='review_ready', revision=snapshot['revision'] - 1, requirements_ref=None)
        write_json_atomic(directory / 'state.json', snapshot)
        self.assertEqual(self.discovery.load()[0]['status'], 'approved')
        again = self.discovery.approve(approved['task_id'], 1, approved['manifest_sha256'], 'owner', 'I approve this Intent and PRD for delivery')
        self.assertEqual(again['requirements_ref'], approved['requirements_ref'])

    def test_native_planner_preserves_approved_contract_and_advances_to_design(self):
        approved = self.approve()
        task = self.discovery.start_delivery()['task_id']
        context = role_context(self.root, 'contract_planner')
        self.assertIn('requirements_ref', context['task_call']['tasks'][0]['task'])
        contract = {**self.contract, 'task_id': task, 'requirements_ref': approved['requirements_ref'],
                    'work_plan_required': True, 'required_checks': [
                        {'id': 'CHECK-1', 'suite': 'green', 'target': 'tests/test_feature.py', 'acceptance_items': ['REQ-1']}]}
        request = {'type': 'tool_call', 'session_id': 's1', 'toolCallId': 'planner',
                   'input': {'agent': 'aidevflow-contract-planner', 'task': 'plan approved requirements'}}
        self.observe(self.root, request)
        receipt = self.observe(self.root, {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'planner-agent', 'agent': 'aidevflow-contract-planner', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': contract}}]}})
        self.gate.register_contract(receipt['result_path'])
        self.gate.advance('design')
        self.assertEqual(self.gate._load_active()[0]['current_stage'], 'design')

    def test_claude_install_cannot_corrupt_active_reviewed_profile(self):
        before = (self.root / '.aidevflow/project.json').read_bytes()
        with self.assertRaisesRegex(GateError, 'active omp discovery'):
            install(Path(__file__).resolve().parents[1], self.root, 'claude-code')
        self.assertEqual((self.root / '.aidevflow/project.json').read_bytes(), before)

    def test_single_entry_can_start_from_natural_language_without_a_request_file(self):
        self.discovery.cancel('Start through the unified entry')
        route = Workflow(self.root).enter('Build a procurement system: reports and approvals')
        self.assertEqual(route['action'], 'discuss')
        state = route['state']
        self.assertIn('procurement', (self.root / state['request_snapshot_path']).read_text(encoding='utf-8'))
        self.assertFalse(self.gate.active_path.exists())

    def test_review_choice_approves_starts_and_replays_without_a_second_task(self):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        result = workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        again = workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        self.assertEqual(result['task_id'], again['task_id'])
        self.assertTrue(again['replayed'])
        self.assertEqual(len(list(self.gate.runs.glob('ADF-*'))), 1)
        state, _, _ = self.gate._load_active()
        approval = read_json(self.root / state['requirements_ref']['approval_path'])
        self.assertEqual(approval['source'], 'native_review_choice')

    def test_review_rejects_other_sessions_and_stale_versions(self):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        with self.assertRaisesRegex(GateError, 'another session'):
            workflow.respond(review['discovery_id'], review['token'], 's2', 'approve_start')
        self.discovery.revise('Need a different flow')
        with self.assertRaisesRegex(GateError, 'state changed'):
            workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        self.assertFalse(self.gate.active_path.exists())

    def test_pause_is_durable_and_only_explicit_entry_resumes_review(self):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        workflow.respond(review['discovery_id'], review['token'], 's1', 'pause')
        self.assertEqual(Discovery(self.root).load()[0]['status'], 'paused')
        with self.assertRaisesRegex(GateError, 'Resume paused'):
            self.discovery.revise('Agent attempted to resume without human input')
        self.assertEqual(workflow.enter()['action'], 'review')
        self.assertFalse(self.gate.active_path.exists())

    def test_review_feedback_is_a_persisted_source_and_needs_fresh_analysis(self):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        result = workflow.respond(review['discovery_id'], review['token'], 's1', 'revise', 'Require finance approval')
        self.assertEqual(result['action'], 'discuss')
        state = self.discovery.load()[0]
        self.assertEqual(state['status'], 'collecting')
        self.assertIn('finance approval', read_json(self.root / state['answers'][-1]['path'])['answer'])
        with self.assertRaises(GateError):
            self.discovery.start_delivery()

    def test_discussion_only_cannot_turn_approval_into_delivery(self):
        self.record()
        workflow = Workflow(self.root)
        workflow.enter(discussion_only=True)
        review = workflow.prepare_review('s1')
        with self.assertRaisesRegex(GateError, 'did not authorize'):
            workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        result = workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_save')
        self.assertEqual(result['action'], 'approved_saved')
        self.assertFalse(self.gate.active_path.exists())
        with self.assertRaisesRegex(GateError, 'saving only'):
            self.discovery.start_delivery()
        self.assertEqual(workflow.enter()['action'], 'deliver')

    def test_new_feedback_after_approval_reopens_discussion_before_delivery(self):
        self.approve()
        result = Workflow(self.root).enter('Add finance approval to the requirements')
        self.assertEqual(result['action'], 'choose_approved_action')
        self.assertIsNotNone(self.discovery.load()[0]['requirements_ref'])
        result = Workflow(self.root).enter('Add finance approval to the requirements', approved_action='revise')
        self.assertEqual(result['action'], 'discuss')
        self.assertIsNone(self.discovery.load()[0]['requirements_ref'])
        self.assertFalse(self.gate.active_path.exists())

    def test_execution_instruction_does_not_invalidate_approved_requirements(self):
        self.approve()
        text = '请根据已经确认的需求继续设计开发，直到完成'
        self.assertEqual(Workflow(self.root).enter(text)['action'], 'choose_approved_action')
        result = Workflow(self.root).enter(text, approved_action='continue')
        self.assertEqual(result['action'], 'deliver')
        self.assertEqual(result['user_text'], text)

    def test_interrupted_review_handoff_recovers_the_same_operation(self):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        original = Discovery.start_delivery
        def interrupted(discovery):
            original(discovery)
            raise OSError('Response lost after delivery persisted')
        with patch.object(Discovery, 'start_delivery', interrupted):
            with self.assertRaises(OSError):
                workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        result = workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        self.assertEqual(result['action'], 'deliver')
        self.assertEqual(len(list(self.gate.runs.glob('ADF-*'))), 1)

    def test_review_cannot_approve_content_changed_while_dialog_was_open(self):
        state = self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        manifest = self.root / state['manifest_path']
        (manifest.parent / 'prd.md').write_text('Changed while the reviewer was reading', encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'content changed'):
            workflow.respond(review['discovery_id'], review['token'], 's1', 'approve_start')
        self.assertFalse(self.gate.active_path.exists())

    def test_handoff_recovery_preserves_the_new_human_instruction(self):
        self.approve()
        with patch.object(self.gate.__class__, '_create_intake', side_effect=OSError('Interrupted')):
            with self.assertRaises(OSError):
                self.discovery.start_delivery()
        result = Workflow(self.root).enter('Stop and review the scope before implementation')
        self.assertEqual(result['action'], 'deliver')
        self.assertIn('Stop and review', result['user_text'])

    def test_discussion_only_does_not_resume_an_active_delivery(self):
        self.approve()
        task = self.discovery.start_delivery()['task_id']
        result = Workflow(self.root).enter(discussion_only=True)
        self.assertEqual(result['action'], 'delivery_active')
        self.assertEqual(result['task_id'], task)

    def test_discussion_only_does_not_complete_a_pending_handoff(self):
        self.approve()
        with patch.object(self.gate.__class__, '_create_intake', side_effect=OSError('Interrupted')):
            with self.assertRaises(OSError):
                self.discovery.start_delivery()
        result = Workflow(self.root).enter(discussion_only=True)
        self.assertEqual(result['action'], 'delivery_active')
        self.assertTrue(self.discovery.transaction.exists())
        self.assertFalse(self.gate.active_path.exists())

    def _check_interrupted_review_response(self, choice, feedback=''):
        self.record()
        workflow = Workflow(self.root)
        review = workflow.prepare_review('s1')
        def interrupted(path, value):
            if value.get('status') == 'completed':
                raise OSError('Reply lost after the state event committed')
            write_json_atomic(path, value)
        with patch('aidevflow.workflow.write_json_atomic', side_effect=interrupted):
            with self.assertRaises(OSError):
                workflow.respond(review['discovery_id'], review['token'], 's1', choice, feedback)
        committed = self.discovery.load()[0]
        result = workflow.respond(review['discovery_id'], review['token'], 's1', choice, feedback)
        replay = workflow.respond(review['discovery_id'], review['token'], 's1', choice, feedback)
        self.assertEqual(result['action'], 'paused' if choice == 'pause' else 'discuss')
        self.assertTrue(replay['replayed'])
        self.assertEqual(self.discovery.load()[0], committed)
        self.assertEqual(len(committed.get('answers', [])), 1 if choice == 'revise' else 0)
        self.assertFalse(self.gate.active_path.exists())

    def test_pause_response_recovers_after_state_committed(self):
        self._check_interrupted_review_response('pause')

    def test_feedback_response_recovers_without_duplicating_the_answer(self):
        self._check_interrupted_review_response('revise', 'Finance must approve the purchase')

    def test_multiple_answers_can_be_recorded_before_one_fresh_analysis(self):
        blocked = copy.deepcopy(self.value)
        questions = copy.deepcopy(self._draft()['open_questions'])
        questions.append({**questions[0], 'id': 'Q2', 'question': 'Who approves the purchase?'})
        blocked['intent']['open_questions'] = questions
        self.record(blocked)
        self.discovery.answer('Q1', 'Return one')
        self.discovery.answer('Q2', 'The finance manager')
        state = self.discovery.load()[0]
        self.assertEqual(len(state['answers']), 2)
        self.assertEqual(state['status'], 'collecting')
        self.assertEqual(self.record(call='call-2')['status'], 'review_ready')

    def test_revision_role_context_includes_previous_requirements_to_preserve_scope(self):
        previous = self.record()
        self.discovery.revise('Finance approval needs clarification')
        task = role_context(self.root, 'requirements_analyst')['task_call']['tasks'][0]['task']
        self.assertIn(previous['manifest_path'], task)
        self.assertIn('Preserve stable IDs', task)
