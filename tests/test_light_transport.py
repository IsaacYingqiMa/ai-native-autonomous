"""Regression cases for the real session's malformed arrays and retry loop."""
from __future__ import annotations
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
import test_light_workflow as workflow
from aidevflow.storage import GateError, read_json, write_json_atomic, sha256_file
from aidevflow.backends.zscode.bridge import model_operation


class LightTransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.LightWorkflowTests()
        self.fixture.setUp()
        self.root, self.gate = self.fixture.root, self.fixture.gate
        self.gate.create('transport-case', '返回两个整数，one 为1，two 为2')

    def tearDown(self):
        self.fixture.tearDown()

    def draft(self, value, name='requirements.json'):
        path = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        write_json_atomic(self.root / path, value)
        return path

    def role(self, value=None, call='requirements-real', finish=True):
        prepared = self.gate.requirements_context()
        request = {'type': 'tool_call', 'session_id': 'native-session', 'toolCallId': call, 'input': prepared['task_call']}
        self.gate.observe_requirements(request)
        response = {**request, 'type': 'tool_result', 'details': {'results': [{'id': 'agent-' + call,
            'agent': 'aidevflow-requirements-analyst', 'exitCode': 0,
            'structuredOutput': {'status': 'valid', 'data': copy.deepcopy(value or workflow.REQUIREMENTS)}}]}}
        return self.gate.observe_requirements(response) if finish else None, request, response

    def test_malformed_arrays_are_diagnosed_without_writing_formal_documents(self):
        value = copy.deepcopy(workflow.REQUIREMENTS)
        value['intent']['users'] = {'item': 'u'}
        value['intent']['open_questions'] = ''
        path = self.draft(value)
        before = self.gate.status()['revision']
        result = self.gate.validate_artifact('requirements', path)
        self.assertFalse(result['valid'])
        self.assertIn('$.intent.users: expected array, got dict', result['errors'])
        self.assertEqual(self.gate.status()['revision'], before)
        self.assertNotIn('requirements', self.gate.status())
        self.assertFalse((self.root / self.gate.status()['directory'] / 'prd.md').exists())

    def test_three_bad_submissions_stop_inside_one_turn_and_status_does_not_reset_budget(self):
        config = read_json(self.root / '.aidevflow/project.json')
        config['failure_limits'] = {'operation_warning': 1, 'operation_repeats': 3, 'operation_total': 6}
        write_json_atomic(self.root / '.aidevflow/project.json', config)
        value = copy.deepcopy(workflow.REQUIREMENTS)
        value['intent']['users'] = {'item': 'u'}
        path = self.draft(value)
        for i in range(3):
            result = model_operation(self.gate, {'action': 'validate', 'kind': 'requirements', 'path': path})
            model_operation(self.gate, {'action': 'status'})
        self.assertTrue(result['stopped'])
        self.assertEqual(self.gate.status()['stage'], 'needs_attention')
        with self.assertRaises(GateError): model_operation(self.gate, {'action': 'requirements_context'})
        self.gate.resume('test operator')
        self.assertEqual(self.gate.status()['stage'], 'discovery')
        self.assertNotIn('requirements', self.gate.status())

    def test_probe_and_inline_payload_cannot_become_formal_requirements(self):
        value = copy.deepcopy(workflow.REQUIREMENTS)
        value['intent']['problem'], value['prd'] = 'probe', '# p'
        path = self.draft(value)
        self.assertTrue(self.gate.validate_artifact('requirements', path)['valid'])
        with self.assertRaises(GateError): self.gate.submit_artifact('requirements', path)
        with self.assertRaises(GateError): model_operation(self.gate, {'action': 'requirements', 'data': {'value': value}})
        self.assertNotIn('requirements', self.gate.status())

    def test_native_result_is_registered_by_path_and_replay_is_idempotent(self):
        receipt, request, response = self.role()
        self.assertNotIn('requirements', self.gate.status())
        self.assertEqual(self.gate.observe_requirements(response), receipt)
        self.gate.submit_artifact('requirements', receipt['result_path'])
        before = self.gate.status()['revision']
        self.gate.submit_artifact('requirements', receipt['result_path'])
        self.assertEqual(self.gate.status()['revision'], before)
        self.assertIn('native_receipt', self.gate.status()['requirements'])

    def test_async_result_and_forged_unanchored_result_cannot_register_requirements(self):
        _, request, response = self.role(finish=False)
        pending = self.gate.observe_requirements({**request, 'type': 'tool_result', 'details': {'results': []}})
        self.assertFalse(pending['execution_finished'])
        response['toolCallId'] = 'forged'
        with self.assertRaises(GateError): self.gate.observe_requirements(response)
        self.assertNotIn('requirements', self.gate.status())

    def test_feedback_invalidates_the_old_native_result(self):
        receipt, _, _ = self.role()
        self.gate.requirement_feedback('现在还需要返回三', 'actual user')
        with self.assertRaises(GateError): self.gate.submit_artifact('requirements', receipt['result_path'])
        self.assertNotIn('requirements', self.gate.status())

    def test_compact_status_omits_long_documents_but_preserves_artifact_references(self):
        value = copy.deepcopy(workflow.REQUIREMENTS)
        value['prd'] += '\n' + '完整需求说明。' * 4000
        receipt, _, _ = self.role(value)
        self.gate.submit_artifact('requirements', receipt['result_path'])
        summary = self.gate.summary()
        self.assertLess(len(json.dumps(summary)), 2200)
        self.assertNotIn('value', summary['requirements'])
        self.assertTrue(summary['requirements']['files'])

    def test_draft_writes_are_scoped_and_native_roles_are_read_only(self):
        draft = self.draft({'files': {}})
        self.assertFalse(self.gate.guard('write', {'path': draft})['block'])
        self.assertTrue(self.gate.guard('write', {'path': 'src/sample.py'})['block'])
        self.assertTrue(self.gate.guard('write', {'path': draft}, {'name': 'aidevflow-requirements-analyst'})['block'])
        self.assertTrue(self.gate.guard('bash', {'command': 'echo hi'})['block'])

    def test_installed_runtime_has_no_legacy_engine_and_skills_do_not_repeat_the_guide(self):
        runtime = self.root / '.aidevflow/runtime/aidevflow'
        self.assertFalse((runtime / 'gate.py').exists())
        self.assertFalse((runtime / 'discovery.py').exists())
        self.assertFalse((runtime / 'cli.py').exists())
        guide = (self.root / '.zscode/extensions/aidevflow/flow.md').read_text(encoding='utf8')
        skill = (self.root / '.zscode/skills/zsspec-plan/SKILL.md').read_text(encoding='utf8')
        self.assertNotIn(guide, skill)
        code = "import sys;sys.path.insert(0,'.aidevflow/runtime');import aidevflow.backends.zscode.engine;assert 'aidevflow.gate' not in sys.modules;assert 'aidevflow.discovery' not in sys.modules"
        result = subprocess.run([sys.executable, '-B', '-c', code], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cli = subprocess.run([sys.executable, '-B', str(self.root / 'scripts/aidevflow.py'), 'status'],
                             cwd=self.root, capture_output=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout.decode('utf8'))['stage'], 'discovery')

    def test_error_reporting_does_not_overwrite_a_tampered_document(self):
        self.fixture.submit_requirements(workflow.REQUIREMENTS)
        path = self.root / self.gate.status()['directory'] / 'prd.md'
        path.write_text('externally changed', encoding='utf8')
        with self.assertRaises(GateError): model_operation(self.gate, {'action': 'requirements_context'})
        self.assertEqual(path.read_text(encoding='utf8'), 'externally changed')

    def test_spec_and_plan_file_submissions_are_idempotent_after_registration(self):
        self.fixture.submit_requirements(workflow.REQUIREMENTS)
        state = self.gate.status()
        self.gate.approve(state['requirements']['sha256'], 'test user')
        spec = self.draft({'content': workflow.SPEC, 'acceptance_map': workflow.MAP}, 'spec.json')
        model_operation(self.gate, {'action': 'submit', 'kind': 'spec', 'path': spec})
        plan = self.draft({'tasks': self.fixture.tasks}, 'plan.json')
        model_operation(self.gate, {'action': 'submit', 'kind': 'plan', 'path': plan})
        before = self.gate.status()['revision']
        model_operation(self.gate, {'action': 'submit', 'kind': 'plan', 'path': plan})
        self.assertEqual(self.gate.status()['revision'], before)
        self.assertEqual(self.gate.status()['stage'], 'applying')

    def test_pending_native_requirements_recovers_only_an_anchored_branch_result(self):
        _, request, response = self.role(finish=False)
        entry = {'message': {'role': 'toolResult', 'toolName': 'task', 'toolCallId': request['toolCallId'], 'details': response['details']}}
        recovered = self.gate.recover_requirements('native-session', [entry])
        self.assertTrue(recovered['recovered'])
        result = self.gate.summary()['requirements_result']
        self.gate.submit_artifact('requirements', result['result_path'])
        self.assertIn('native_receipt', self.gate.status()['requirements'])

    def test_idle_upgrade_removes_only_unchanged_manifest_owned_legacy_files(self):
        # A separate idle project proves cleanup without bypassing active-task protection.
        self.gate.archive(abandoned=True, by='test user')
        legacy = self.root / '.aidevflow/runtime/aidevflow/legacy-example.py'
        legacy.write_text('# owned legacy module\n', encoding='utf8')
        manifest_path = self.root / '.aidevflow/light-install-manifest.json'
        manifest = read_json(manifest_path)
        manifest['files'][legacy.relative_to(self.root).as_posix()] = sha256_file(legacy)
        write_json_atomic(manifest_path, manifest)
        user = self.root / '.aidevflow/runtime/aidevflow/user-note.txt'
        user.write_text('user content', encoding='utf8')
        workflow.install(workflow.SOURCE, self.root)
        self.assertFalse(legacy.exists())
        self.assertEqual(user.read_text(encoding='utf8'), 'user content')
