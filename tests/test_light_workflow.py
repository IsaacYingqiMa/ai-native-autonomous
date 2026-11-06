from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aidevflow.storage import GateError, read_json, sha256_bytes, sha256_file, write_json_atomic
from aidevflow.backends.zscode.installer import install
from aidevflow.backends.zscode.engine import LightGate, PROFILE, doctor
from aidevflow.backends.zscode.bridge import dispatch
from aidevflow.backends.zscode.review import digest, VERIFIER

SOURCE = Path(__file__).resolve().parents[1]
MAP = {'S1': ['AC-1'], 'S2': ['AC-2']}
REQUIREMENTS = {'schema': 'aidevflow/requirements-v1',
    'intent': {'schema': 'aidevflow/intent-draft-v1', 'problem': 'Wrong integers', 'users': ['caller'],
               'desired_outcome': 'Return one and two', 'scope': ['one and two'], 'non_goals': [],
               'constraints': [], 'success_criteria': ['one returns 1', 'two returns 2'],
               'assumptions': [], 'open_questions': [], 'decisions': []},
    'prd': '# PRD\nReturn one and two; preserve caller behavior.',
    'requirements': [{'id': 'R1', 'statement': 'Return correct integers', 'source': 'request', 'acceptance_ids': ['AC-1', 'AC-2']}],
    'scenarios': [{'id': 'SC1', 'requirement_ids': ['R1'], 'acceptance_ids': ['AC-1', 'AC-2'],
                   'given': 'A caller', 'when': 'functions called', 'then': 'one and two returned'}]}
SPEC = '''# 规格：整数值
## 需求
返回正确整数；不做则调用者结果错误。
## 规格
### S1 返回一
- 行为：WHEN called THE SYSTEM SHALL return one
- 验收：unittest → PASS
- 覆盖需求：返回整数
### S2 返回二
- 行为：WHEN second called THE SYSTEM SHALL return two
- 验收：unittest → PASS
- 覆盖需求：返回第二整数
## 已剔除
无
## 知识缺口
无
## knowledge 修正项
无
## 冲突声明
无
'''


class LightWorkflowTests(unittest.TestCase):
    def setUp(self):
        base = SOURCE / '.test-tmp'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.root.resolve().relative_to(base.resolve())
        self.git('init', '-q')
        (self.root / 'src').mkdir()
        (self.root / 'src/sample.py').write_text('def one(): return 0\ndef two(): return 0\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-qm', 'baseline')
        install(SOURCE, self.root)
        self.gate = LightGate(self.root)
        self.gate.knowledge({'specs/MAP.md': '| 模块 | 职责 | 详情 |\n|---|---|---|\n',
                             'specs/modules/sample.md': '# sample\n返回整数\n'})
        self.argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']
        self.tasks = []
        for i in (1, 2):
            for kind in ('red', 'green'):
                task = {'id': f'{i}.{1 if kind == "red" else 2}', 'title': f'{kind} S{i}', 'kind': kind,
                        'group': f'G{i}', 'specs': [f'S{i}'], 'argv': self.argv,
                        'files': [f'tests/test_{i}.py'] if kind == 'red' else ['src/sample.py']}
                if kind == 'green':
                    task['regression'] = self.argv
                self.tasks.append(task)
        self.tasks.append({'id': '3.1', 'title': '全量回归', 'kind': 'regression', 'specs': ['S1', 'S2'],
                           'argv': self.argv, 'files': []})

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        env = os.environ.copy()
        env.update({'GIT_CONFIG_COUNT': '3', 'GIT_CONFIG_KEY_0': 'core.fsmonitor', 'GIT_CONFIG_VALUE_0': 'false',
                    'GIT_CONFIG_KEY_1': 'user.email', 'GIT_CONFIG_VALUE_1': 'test@example.invalid',
                    'GIT_CONFIG_KEY_2': 'user.name', 'GIT_CONFIG_VALUE_2': 'light test'})
        return subprocess.run(['git', *args], cwd=self.root, env=env, capture_output=True,
                              encoding='utf-8', check=True).stdout

    def submit_requirements(self, value):
        prepared = self.gate.requirements_context()
        call = 'requirements-' + str(self.gate.status()['revision'])
        request = {'type': 'tool_call', 'session_id': 'requirements-session', 'toolCallId': call,
                   'input': prepared['task_call']}
        self.gate.observe_requirements(request)
        response = {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'analyst-' + call, 'agent': 'aidevflow-requirements-analyst', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': copy.deepcopy(value)}}]}}
        receipt = self.gate.observe_requirements(response)
        if receipt['status'] != 'completed': raise GateError(receipt.get('error', receipt['status']))
        self.gate.submit_artifact('requirements', receipt['result_path'])
        return self.gate.status()

    def entry(self):
        self.gate.create('integer-values', '返回两个整数')
        s = self.submit_requirements(copy.deepcopy(REQUIREMENTS))
        self.gate.approve(s['requirements']['sha256'], 'tester')
        return s

    def begin(self):
        self.entry()
        self.gate.spec(SPEC, MAP)
        self.gate.plan(self.tasks)

    def baseline_review(self, decision='approve', call=None):
        prepared = self.gate.baseline_assignment()
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        call = call or 'baseline-' + str(self.gate.status()['revision'])
        request = {'type': 'tool_call', 'session_id': 'baseline-session', 'toolCallId': call, 'input': prepared['task_call']}
        self.gate.observe_baseline(request)
        value = {'schema': 'aidevflow/baseline-review-v1', 'run_id': self.gate.status()['run_id'],
                 'bundle_sha256': prepared['sha256'], 'decision': decision, 'reason': 'Test fixture simulates native independent review',
                 'acceptance_ids': manifest['acceptance_ids'], 'changed_paths': manifest['changed_paths']}
        response = {**request, 'type': 'tool_result', 'details': {'results': [{'agent': 'aidevflow-baseline-reviewer',
                    'id': 'reviewer-' + call, 'exitCode': 0, 'structuredOutput': {'status': 'valid', 'data': value}}]}}
        return self.gate.observe_baseline(response), request, response

    def run_current(self):
        s = self.gate.status()
        t = s.get('current_task') or {}
        if s['stage'] == 'applying' and t.get('kind') == 'red' and t.get('group') not in s['baselines'] and all((self.root / f).is_file() for f in t['files']):
            self.gate.prepare_baseline()
            self.baseline_review()
        return self.gate.run()

    def accept(self, candidate, by):
        return self.gate.confirm(candidate, by, self.gate.status().get('verifier_bundle', {}).get('sha256'))

    def witness(self, decision='pass', files=None, call=None, finish=True):
        files = {'specs/modules/sample.md': '# sample\n返回一和二\n'} if files is None else files
        prepared = self.gate.prepare_verifier(files)
        prepared = self.gate.verifier_assignment()
        call = call or 'call-' + str(self.gate.status()['revision'])
        request = {'type': 'tool_call', 'session_id': 'verifier-session', 'toolCallId': call,
                   'input': prepared['task_call']}
        self.gate.observe_verifier(request)
        state = self.gate.status()
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        evidence = [i['path'] for i in manifest['files'] if i['label'] == 'src/sample.py']
        value = {'schema': 'aidevflow/light-verdict-v1', 'run_id': state['run_id'],
                 'bundle_sha256': prepared['sha256'], 'decision': decision, 'reason': 'Independent review',
                 'items': [{'id': ident, 'status': 'passed' if decision == 'pass' else 'failed',
                            'reason': 'Checked approved requirement and evidence', 'evidence': evidence} for ident in ('S1', 'S2')]}
        response = {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'agent-' + call, 'agent': VERIFIER, 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': value}}]}}
        return self.gate.observe_verifier(response) if finish else None, request, response

    def write_test(self, i):
        path = self.root / f'tests/test_{i}.py'
        path.parent.mkdir(exist_ok=True)
        function = 'one' if i == 1 else 'two'
        path.write_text(f'import unittest\nfrom src.sample import {function}\nclass Value(unittest.TestCase):\n'
                        f' def test_value(self): self.assertEqual({function}(), {i})\n', encoding='utf-8')

    def reach_regression(self):
        self.begin()
        for i in (1, 2):
            self.write_test(i)
            s = self.run_current()
            self.assertEqual(s['cursor'], (i - 1) * 2 + 1)
            path = self.root / 'src/sample.py'
            path.write_text(f'def one(): return 1\ndef two(): return {2 if i == 2 else 0}\n', encoding='utf-8')
            self.run_current()
        return self.run_current()

    def test_real_two_item_tdd_confirmation_and_archive(self):
        s = self.reach_regression()
        self.assertEqual(s['stage'], 'verify')
        self.assertTrue(s['tasks'][-1]['completed'])
        with self.assertRaisesRegex(GateError, '独立验收'):
            self.accept(s['final_candidate'], 'tester')
        self.witness()
        self.assertTrue(self.git('status', '--porcelain').strip())
        done = self.accept(s['final_candidate'], 'tester')
        self.assertEqual(done['stage'], 'done')
        self.assertEqual(len(done['baselines']), 2)
        result = self.gate.archive()
        self.assertEqual(result['stage'], 'archived')
        self.assertFalse(self.gate.active.exists())
        self.assertTrue((self.root / result['archive'] / 'intent.md').is_file())
        self.assertTrue((self.root / result['archive'] / 'prd.md').is_file())
        self.assertTrue((self.root / result['archive'] / 'plan.md').is_file())
        self.assertIn('integer-values', (self.root / 'specs/archive/INDEX.md').read_text(encoding='utf-8'))

    def test_no_plan_before_intent_prd_approval_and_stale_approval_rejected(self):
        self.gate.create('integer-values', 'request')
        s = self.submit_requirements(copy.deepcopy(REQUIREMENTS))
        with self.assertRaises(GateError): self.gate.spec(SPEC, MAP)
        with self.assertRaises(GateError): self.gate.plan(self.tasks)
        with self.assertRaises(GateError): self.gate.approve('old-hash', 'tester')
        self.assertEqual(self.gate.status()['stage'], 'discovery')
        self.gate.approve(s['requirements']['sha256'], 'tester')
        self.assertEqual(self.gate.spec(SPEC, MAP)['stage'], 'plan')
        self.assertEqual(self.gate.plan(self.tasks)['stage'], 'applying')

    def test_second_failure_is_repaired_autonomously_without_another_human_gate(self):
        self.begin()
        self.write_test(1)
        self.run_current()
        self.assertEqual(self.run_current()['stage'], 'applying')
        self.assertEqual(self.run_current()['stage'], 'applying')
        s = self.gate.status()
        self.assertEqual(s['tasks'][1]['attempts'], 2)
        self.assertFalse(s['tasks'][1].get('completed', False))
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 0\n', encoding='utf-8')
        self.assertEqual(self.run_current()['cursor'], 2)

    def test_infrastructure_failure_is_not_red_and_frozen_test_is_protected(self):
        self.begin()
        self.write_test(1)
        test = self.root / 'tests/test_1.py'
        test.write_text('import definitely_missing_light_module\n', encoding='utf-8')
        self.assertEqual(self.run_current()['tasks'][0]['result']['status'], 'failed')
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])

    def test_prior_tests_and_plan_are_immutable(self):
        self.begin()
        self.write_test(1)
        self.run_current()
        (self.root / 'tests/test_1.py').write_text('# weakened\n', encoding='utf-8')
        with self.assertRaisesRegex(GateError, '冻结'):
            self.run_current()

    def test_markdown_checkbox_cannot_replace_execution(self):
        self.begin()
        s = self.gate.status()
        path = self.root / s['directory'] / 'plan.md'
        path.write_text(path.read_text(encoding='utf-8').replace('- [ ]', '- [x]'), encoding='utf-8')
        with self.assertRaisesRegex(GateError, 'changed outside'):
            self.run_current()

    def test_guard_allows_current_file_and_rejects_shell_rename_and_other_file(self):
        self.begin()
        self.assertFalse(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        for tool, inp in [('write', {'path': 'src/sample.py'}), ('bash', {'command': 'echo bypass'}),
                          ('edit', {'path': 'tests/test_1.py', 'edits': [{'rename': '../escape.py'}]}),
                          ('task', {'agent': 'anything'})]:
            with self.subTest(tool=tool): self.assertTrue(self.gate.guard(tool, inp)['block'])

    def test_stale_regression_requires_a_real_rerun(self):
        s = self.reach_regression()
        (self.root / 'src/sample.py').write_text('def one(): return 1\ndef two(): return 2\n# changed\n', encoding='utf-8')
        self.assertFalse(self.gate.status()['regression_fresh'])
        with self.assertRaisesRegex(GateError, '独立验收'):
            self.accept(s['final_candidate'], 'tester')
        self.assertTrue(self.run_current()['regression_fresh'])

    def test_plan_rejects_missing_green_shared_test_and_path_escape(self):
        self.entry()
        self.gate.spec(SPEC, MAP)
        for change in ('missing-green', 'shared-test', 'escape'):
            tasks = copy.deepcopy(self.tasks)
            if change == 'missing-green': tasks.pop(3)
            elif change == 'shared-test': tasks[2]['files'] = tasks[0]['files']
            else: tasks[0]['files'] = ['../outside.py']
            with self.subTest(change=change), self.assertRaises(GateError): self.gate.plan(tasks)

    def test_archive_transaction_resumes_after_directory_move_failure(self):
        s = self.reach_regression()
        files = {'specs/modules/sample.md': '# sample\n更新后的接口\n'}
        self.witness(files=files)
        self.accept(s['final_candidate'], 'tester')
        rename = os.rename
        def fail_move(src, dst):
            if Path(src).name == 'integer-values':
                raise OSError('interrupted move')
            return rename(src, dst)
        with patch('aidevflow.backends.zscode.engine.os.rename', side_effect=fail_move):
            with self.assertRaises(OSError): self.gate.archive(files)
        self.assertEqual(self.gate.status()['stage'], 'finalizing')
        self.assertEqual(self.gate.archive(files)['stage'], 'archived')

    def test_installer_is_repeatable_preserves_config_and_detects_collision(self):
        config = self.root / '.zscode/config.yml'
        config.write_text('approvalMode: always-ask\n', encoding='utf-8')
        install(SOURCE, self.root)
        self.assertEqual(config.read_text(encoding='utf-8'), 'approvalMode: always-ask\n')
        self.assertEqual(read_json(self.root / '.aidevflow/project.json')['method_profile'], PROFILE)
        with patch('aidevflow.backends.zscode.engine.shutil.which', return_value='zscode.exe'):
            self.assertTrue(doctor(self.root)['ok'])
        target = self.root / '.zscode/skills/zsspec-plan/SKILL.md'
        target.write_text('local modifications', encoding='utf-8')
        with self.assertRaisesRegex(GateError, '本地修改'):
            install(SOURCE, self.root)

    def test_worker_rejects_model_approval_and_foreign_host(self):
        script = self.root / 'scripts/aidevflow_bridge.py'
        frames = [{'id': '1', 'protocol': 1, 'runtime': 'omp', 'action': 'status'},
                  {'id': '2', 'protocol': 1, 'runtime': 'zscode', 'action': 'tool',
                   'payload': {'action': 'approve', 'data': {}}},
                  {'id': '3', 'protocol': 1, 'runtime': 'zscode', 'action': 'tool',
                   'payload': {'action': 'archive', 'data': {'files': {}, 'abandoned': True, 'by': 'fake'}}},
                  {'id': '4', 'protocol': 1, 'runtime': 'zscode', 'action': 'status'},
                  {'id': '5', 'protocol': 1, 'runtime': 'zscode', 'action': 'start-request',
                   'payload': {'source': 'native_user_interaction', 'text': '返回整数'}},
                  {'id': '6', 'protocol': 1, 'runtime': 'zscode', 'action': 'tool',
                   'payload': {'action': 'requirements', 'data': {'value': REQUIREMENTS}}},
                  {'id': '7', 'protocol': 1, 'runtime': 'zscode', 'action': 'approve',
                   'payload': {'source': 'native_user_command', 'data': {
                       'sha256': digest(REQUIREMENTS), 'by': 'actual user'}}},
                  {'id': '8', 'protocol': 1, 'runtime': 'zscode', 'action': 'tool',
                   'payload': {'action': 'spec', 'data': {'content': SPEC, 'acceptance_map': MAP}}},
                  {'id': '9', 'protocol': 1, 'runtime': 'zscode', 'action': 'tool',
                   'payload': {'action': 'plan', 'data': {'tasks': self.tasks}}}]
        p = subprocess.run([sys.executable, '-B', str(script)], input='\n'.join(json.dumps(f) for f in frames) + '\n',
                           capture_output=True, encoding='utf-8', cwd=self.root, timeout=15)
        replies = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([r['ok'] for r in replies], [False, False, False, True, True, False, False, False, False])
        self.assertIn('内联', replies[-1]['error'])

    def test_first_install_interruption_is_recoverable_without_overwriting_user_files(self):
        target = self.root / 'another-project'
        target.mkdir()
        subprocess.run(['git', '-c', 'core.fsmonitor=false', 'init', '-q'], cwd=target,
                       capture_output=True, check=True)
        from aidevflow.storage import replace_file_atomic
        count = 0
        def fail_second(src, dst):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('power interruption')
            return replace_file_atomic(src, dst)
        with patch('aidevflow.backends.zscode.installer.replace_file_atomic', side_effect=fail_second):
            with self.assertRaises(OSError):
                install(SOURCE, target)
        self.assertTrue((target / '.aidevflow/light-install-pending.json').is_file())
        install(SOURCE, target)
        self.assertFalse((target / '.aidevflow/light-install-pending.json').exists())
        self.assertTrue((target / '.zscode/skills/zsspec-spec/SKILL.md').is_file())

    def test_committed_event_recovers_after_state_snapshot_write_interruption(self):
        self.gate.create('integer-values', 'request')
        s = self.submit_requirements(copy.deepcopy(REQUIREMENTS))
        original = write_json_atomic
        def fail_snapshot(path, value):
            if Path(path).name == 'state.json':
                raise OSError('snapshot interrupted')
            return original(path, value)
        with patch('aidevflow.backends.zscode.engine.write_json_atomic', side_effect=fail_snapshot):
            with self.assertRaises(OSError):
                self.gate.approve(s['requirements']['sha256'], 'tester')
        recovered = LightGate(self.root).status()
        self.assertEqual(recovered['stage'], 'spec')
        self.assertEqual(recovered['approval']['sha256'], s['requirements']['sha256'])

    def test_abandoned_change_retains_code_and_does_not_publish_knowledge(self):
        self.begin()
        before = (self.root / 'specs/modules/sample.md').read_bytes()
        result = dispatch(self.gate, 'abandon', {'by': 'tester'}, 'native_user_command')
        self.assertEqual(result['stage'], 'abandoned')
        self.assertFalse(self.gate.active.exists())
        self.assertTrue((self.root / 'src/sample.py').is_file())
        self.assertEqual((self.root / 'specs/modules/sample.md').read_bytes(), before)

    def test_active_task_prevents_reinstallation(self):
        self.begin()
        with self.assertRaisesRegex(GateError, '活动任务'):
            install(SOURCE, self.root)

    def test_ai_retry_can_reprepare_only_a_failed_red(self):
        self.begin()
        self.write_test(1)
        (self.root / 'tests/test_1.py').write_text('import missing_light_dependency\n', encoding='utf-8')
        self.run_current()
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        self.gate.retry('自动诊断：测试导入路径错误，重新准备尚未通过的RED')
        self.assertFalse(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])
        self.write_test(1)
        self.assertEqual(self.run_current()['cursor'], 1)
        self.assertTrue(self.gate.guard('write', {'path': 'tests/test_1.py'})['block'])

    def test_modified_prior_execution_evidence_is_rejected(self):
        self.begin()
        self.write_test(1)
        s = self.run_current()
        result = s['tasks'][0]['result']
        (self.root / result['path']).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(GateError, '证据'):
            self.run_current()

    def test_actual_intent_prd_files_and_approval_are_pinned(self):
        self.entry()
        state = self.gate.status()
        directory = self.root / state['directory']
        self.assertIn('Wrong integers', (directory / 'intent.md').read_text(encoding='utf-8'))
        self.assertIn('# PRD', (directory / 'prd.md').read_text(encoding='utf-8'))
        (directory / 'prd.md').write_text('changed scope', encoding='utf-8')
        with self.assertRaises(GateError): self.gate.spec(SPEC, MAP)

    def test_open_business_question_cannot_be_approved(self):
        self.gate.create('integer-values', 'request')
        value = copy.deepcopy(REQUIREMENTS)
        value['intent']['open_questions'] = [{'id': 'Q1', 'question': 'Which value?', 'blocking': True,
            'options': [{'id': 'A', 'label': 'one', 'impact': 'one'}, {'id': 'B', 'label': 'two', 'impact': 'two'}],
            'recommended_option': 'A', 'recommendation_reason': 'matches request'}]
        s = self.submit_requirements(value)
        with self.assertRaisesRegex(GateError, '业务问题'):
            self.gate.approve(s['requirements']['sha256'], 'tester')

    def test_ai_cannot_narrow_approved_acceptance_or_replace_approved_prd(self):
        self.entry()
        with self.assertRaises(GateError): self.gate.spec(SPEC, {'S1': ['AC-1'], 'S2': ['AC-1']})
        with self.assertRaises(GateError): self.submit_requirements(copy.deepcopy(REQUIREMENTS))
        self.assertEqual(self.gate.status()['stage'], 'spec')

    def test_independent_failure_routes_automatic_repair_then_reverification(self):
        self.reach_regression()
        self.witness('revise')
        self.assertEqual(self.gate.status()['stage'], 'applying')
        self.assertEqual(self.gate.status()['cursor'], 1)
        self.run_current()  # Green 1, skip previously completed Red 2
        self.assertEqual(self.gate.status()['cursor'], 3)
        self.run_current()  # Green 2
        self.run_current()  # Full regression
        self.witness()
        self.assertEqual(self.gate.status()['stage'], 'delivery_ready')
        self.assertEqual(self.gate.status()['repair_rounds'], 1)

    def test_verifier_launch_without_result_is_pending_not_delivery(self):
        self.reach_regression()
        _, request, _ = self.witness(finish=False)
        receipt = self.gate.observe_verifier({**request, 'type': 'tool_result', 'details': {'results': []}})
        self.assertFalse(receipt['execution_finished'])
        with self.assertRaises(GateError): self.gate.prepare_verifier()
        self.assertEqual(self.gate.status()['stage'], 'verify')
        with self.assertRaises(GateError): self.accept(self.gate.status()['final_candidate'], 'tester')

    def test_cancelled_native_role_cannot_be_overwritten_by_late_success(self):
        self.reach_regression()
        _, _, response = self.witness(finish=False)
        cancelled = copy.deepcopy(response)
        cancelled['details']['results'][0]['aborted'] = True
        self.assertEqual(self.gate.observe_verifier(cancelled)['status'], 'execution_failed')
        with self.assertRaises(GateError): self.gate.observe_verifier(response)
        self.assertEqual(self.gate.status()['stage'], 'verify')

    def test_invalid_structured_output_cannot_use_text_as_verdict(self):
        self.reach_regression()
        _, _, response = self.witness(finish=False)
        response['details']['results'][0]['structuredOutput']['status'] = 'invalid'
        self.assertEqual(self.gate.observe_verifier(response)['status'], 'invalid_output')
        self.assertEqual(self.gate.status()['stage'], 'verify')

    def test_orphan_result_cannot_create_an_independent_dispatch(self):
        self.reach_regression()
        _, _, response = self.witness(finish=False)
        response['toolCallId'] = 'never-dispatched'
        with self.assertRaises(GateError): self.gate.observe_verifier(response)

    def test_current_branch_recovers_only_a_previously_dispatched_role(self):
        self.reach_regression()
        _, request, response = self.witness(finish=False)
        entries = [{'message': {'role': 'assistant', 'content': [{'type': 'toolCall', 'name': 'task',
                    'id': request['toolCallId'], 'arguments': request['input']}]}},
                   {'message': {'role': 'toolResult', 'toolName': 'task', 'toolCallId': request['toolCallId'],
                                'details': response['details']}}]
        self.assertEqual(self.gate.recover_verifier('wrong-session', entries)['recovered'], 0)
        self.assertEqual(self.gate.recover_verifier('verifier-session', entries)['recovered'], 1)
        self.assertEqual(self.gate.recover_verifier('verifier-session', entries)['recovered'], 0)
        self.assertEqual(self.gate.status()['stage'], 'delivery_ready')

    def test_verifier_can_only_read_the_sealed_bundle(self):
        self.reach_regression()
        prepared = self.gate.prepare_verifier()
        actor = {'name': VERIFIER}
        self.assertFalse(self.gate.guard('read', {'path': prepared['path'] + '/BUNDLE.json'}, actor)['block'])
        for tool, data in [('read', {'path': 'src/sample.py'}), ('aidevflow_light', {}), ('bash', {}), ('task', {})]:
            self.assertTrue(self.gate.guard(tool, data, actor)['block'])

    def test_bundle_tampering_rejects_delivery_acceptance(self):
        self.reach_regression()
        self.witness()
        state = self.gate.status()
        manifest = read_json(self.root / state['verifier_bundle']['path'] / 'BUNDLE.json')
        target = self.root / state['verifier_bundle']['path'] / manifest['files'][0]['path']
        target.write_text('changed', encoding='utf-8')
        with self.assertRaises(GateError): self.accept(state['final_candidate'], 'tester')

    def test_human_final_feedback_returns_to_ai_repair_without_new_approval(self):
        self.reach_regression()
        self.witness()
        approval = copy.deepcopy(self.gate.status()['approval'])
        self.gate.delivery_feedback('请核实负数输入在原范围内正确', 'tester')
        self.assertEqual(self.gate.status()['stage'], 'applying')
        self.assertEqual(self.gate.status()['approval'], approval)

    def test_verifier_cannot_pass_with_missing_spec_evidence(self):
        self.reach_regression()
        _, _, response = self.witness(finish=False)
        response['details']['results'][0]['structuredOutput']['data']['items'].pop()
        self.assertEqual(self.gate.observe_verifier(response)['status'], 'invalid_output')
        self.assertEqual(self.gate.status()['stage'], 'verify')

    def test_pending_requirement_feedback_requires_new_documents_before_approval(self):
        old = self.entry()
        self.gate.requirement_feedback('补充用户提出的验收细节', 'actual-user')
        with self.assertRaises(GateError):
            self.gate.approve(old['requirements']['sha256'], 'actual-user')
        revised = copy.deepcopy(REQUIREMENTS)
        revised['prd'] += '\n明确保留零和负数行为。'
        current = self.submit_requirements(revised)
        self.assertFalse(current['requirements_feedback_pending'])
        self.gate.approve(current['requirements']['sha256'], 'actual-user')
        self.assertEqual(self.gate.status()['stage'], 'spec')

    def test_operator_recovery_never_counts_unknown_role_as_verified(self):
        self.reach_regression()
        _, _, response = self.witness(finish=False)
        self.gate.pause()
        self.gate.resume('actual-user-confirmed-old-process-ended')
        state = self.gate.status()
        receipt = read_json(self.root / state['verifier_invocation'])
        self.assertEqual(receipt['status'], 'user_stopped_unknown')
        self.assertEqual(state['stage'], 'verify')
        with self.assertRaises(GateError): self.accept(state['final_candidate'], 'tester')
        with self.assertRaises(GateError): self.gate.observe_verifier(response)
        self.witness()
        self.assertEqual(self.gate.status()['stage'], 'delivery_ready')

    def test_final_review_must_bind_the_reviewed_bundle(self):
        self.reach_regression()
        self.witness()
        state = self.gate.status()
        with self.assertRaises(GateError):
            self.gate.confirm(state['final_candidate'], 'tester', 'unreviewed-bundle')
        self.assertEqual(self.gate.status()['stage'], 'delivery_ready')
        self.assertEqual(self.accept(state['final_candidate'], 'tester')['stage'], 'done')
