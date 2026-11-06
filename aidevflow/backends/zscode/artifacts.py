"""File-based submissions and native requirements receipts for the light workflow."""
from __future__ import annotations

import json
import re
from ...storage import GateError, read_json, sha256_file, write_json_new, write_json_atomic
from ...requirements import validate_requirements
from ...locking import writes
from .review import digest

ANALYST = 'aidevflow-requirements-analyst'
KINDS = ('requirements', 'spec', 'plan', 'knowledge', 'verification')


def schema_errors(value, schema, path='$'):
    """The JSON Schema subset used by the bundled artifact schemas; never coerce types."""
    errors = []
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'integer': int}
    expected = schema.get('type')
    choices = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(t == 'null' and value is None or t in types and
            isinstance(value, types[t]) and not (t == 'integer' and isinstance(value, bool)) for t in choices):
        return [f'{path}: expected {expected}, got {type(value).__name__}']
    if 'const' in schema and value != schema['const']:
        errors.append(f'{path}: expected constant {schema["const"]!r}')
    if 'enum' in schema and value not in schema['enum']:
        errors.append(f'{path}: expected one of {schema["enum"]!r}')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        errors += [f'{path}.{key}: required' for key in schema.get('required', []) if key not in value]
        for key, item in value.items():
            sub = props.get(key, schema.get('additionalProperties', {}))
            if sub is False:
                errors.append(f'{path}.{key}: unexpected field')
            elif isinstance(sub, dict):
                errors += schema_errors(item, sub, f'{path}.{key}')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0): errors.append(f'{path}: too few items')
        if len(value) > schema.get('maxItems', len(value)): errors.append(f'{path}: too many items')
        for i, item in enumerate(value): errors += schema_errors(item, schema.get('items', {}), f'{path}[{i}]')
    if isinstance(value, str):
        if len(value.strip()) < schema.get('minLength', 0): errors.append(f'{path}: non-empty text required')
        if schema.get('pattern') and not re.search(schema['pattern'], value): errors.append(f'{path}: invalid format')
    return errors


class LightArtifacts:
    def summary(self):
        s = self.status()
        if s.get('active_run') is None and 'stage' not in s: return s
        keys = ('run_id', 'stage', 'revision', 'directory', 'next_action', 'regression_fresh',
                'requirements_feedback_pending', 'repair_feedback', 'attention_reason', 'skill', 'repair_evidence')
        result = {k: s[k] for k in keys if k in s}
        if s.get('skill'): result['skill_path'] = '.zscode/skills/' + s['skill'] + '/SKILL.md'
        result['knowledge'] = {'initialized': (self.control / 'light-knowledge.json').is_file(), 'proposal_paths': sorted(s.get('knowledge_proposal', {})), 'submit_kind': 'knowledge'}
        task = s.get('current_task')
        if task:
            result['current_task'] = {k: task[k] for k in ('id', 'title', 'kind', 'group', 'specs', 'files', 'argv', 'regression', 'result', 'consecutive_failures') if k in task}
            evidence = task.get('result')
            if evidence and evidence.get('status') == 'failed':
                p = self.path(evidence['path'])
                if p.is_file() and sha256_file(p) == evidence['sha256']:
                    records = read_json(p).get('records', [])
                    failed = next((r for r in records if not r.get('passed')), None)
                    if failed:
                        output = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', failed.get('output', ''))
                        result['failure_summary'] = output if len(output) <= 2400 else output[:1400] + '\n...\n' + output[-1000:]
            recovery = self.repair_guidance(s)
            if recovery:
                result['recovery'] = recovery
                if s['stage'] == 'applying' and recovery['detected_issues'] and recovery['kind'] != 'edit_unfrozen_tests':
                    result['next_action'] = '冻结测试与计划路径不一致；使用 recovery.proposal_template 写修订草稿并 repair_propose，再派发原生基线审查。'
        if not result.get('failure_summary') and s.get('repair_evidence'):
            failed = next((r for r in read_json(self.path(s['repair_evidence'])).get('records', []) if not r.get('passed')), None)
            if failed: result['failure_summary'] = failed.get('output', '')[-2400:]
        if s.get('repair_failure'):
            failure = s['repair_failure']; evidence = failure.get('evidence', {})
            result['repair_failure'] = failure
            result['failure_summary'] = failure['message']
            if evidence and not evidence.get('legacy'):
                path = self.path(evidence['path'])
                if sha256_file(path) != evidence['sha256']: raise GateError('修订失败证据已被修改')
                result['failure_summary'] = read_json(path).get('output', failure['message'])[-4000:]
        result['draft_directory'] = f"specs/drafts/{s['run_id']}"
        result['requirements'] = {k: s.get('requirements', {}).get(k) for k in ('sha256', 'revision', 'files')}
        if s.get('analyst_invocation'):
            receipt = read_json(self.path(s['analyst_invocation']))
            result['requirements_result'] = {k: receipt[k] for k in ('status', 'result_path', 'result_sha256', 'error') if k in receipt}
        if s.get('verifier_invocation'):
            receipt = read_json(self.path(s['verifier_invocation']))
            result['verification'] = {k: receipt[k] for k in ('status', 'result_path', 'result_sha256', 'error') if k in receipt}
            if s['stage'] == 'verify' and receipt.get('status') == 'invalid_output':
                result['next_action'] = '验收回执格式无效。使用 verifier_context 再派发原生验收角色纠错；保持同一 bundle，不重建材料、不由协调者代写结论。'
                result['verification']['correction'] = s.get('verifier_correction')
        if s.get('baseline_review'):
            result['baseline_review'] = {k: s['baseline_review'][k] for k in ('path', 'sha256', 'mode', 'decision', 'reason', 'findings') if k in s['baseline_review']}
        if s.get('operation_failure', {}).get('count'):
            result['operation_correction'] = self.operation_correction(s)
        if s['stage'] in ('spec', 'plan', 'applying', 'repairing', 'verify', 'needs_attention'): result['autonomy_budget'] = self.budget_status()
        return result

    @writes
    def operation_failure(self, action, reason, *, code='OPERATION_REJECTED', artifact_kind='', diagnostic=None):
        """Bound failures inside a single model turn, not just at session_stop."""
        if not self.active.exists(): return {'stopped': False}
        state, run = self.load()
        try:
            self.integrity(state)
        except (GateError, OSError):
            return {'stopped': True, 'reason': '完整性检查失败，保留原文件，不重写投影'}
        if state['stage'] in ('paused', 'needs_attention', 'done', 'finalizing'): return {'stopped': True}
        limits = self.failure_limits()
        old = state.get('operation_failure', {})
        scope = self.operation_scope(state)
        if old.get('scope') and old['scope'] != scope:
            self.settle_operation_failure(state, 'durable_progress')
            old = state.get('operation_failure', {})
        identity = digest([action, code, artifact_kind, str(reason) if code == 'OPERATION_REJECTED' else ''])
        count = old.get('count', 0) + 1 if old.get('identity') == identity else 1
        total = old.get('total', 0) + 1
        state['operation_failure'] = {'action': action, 'identity': identity, 'scope': scope, 'code': code, 'artifact_kind': artifact_kind, 'count': count, 'total': total, 'reason': str(reason)[:1200]}
        if diagnostic: state['operation_failure']['diagnostic'] = diagnostic
        if count >= limits['operation_repeats'] or total >= limits['operation_total']:
            state.update(attention_stage=state['stage'], stage='needs_attention',
                         attention_kind='operation_failure',
                         attention_reason=f"{action} 失败预算耗尽（同类 {count}/{limits['operation_repeats']}，累计 {total}/{limits['operation_total']}）：{reason}")
        self.checkpoint(state, run, 'operation_rejected')
        return {'stopped': state['stage'] == 'needs_attention', 'attempts': count, 'total': total,
                'code': code, 'artifact_kind': artifact_kind, 'limits': limits,
                'correction': self.operation_correction(state), 'recovery': self.repair_guidance(state)}

    @staticmethod
    def operation_scope(state):
        # Do not include revision, stage, dispatches, draft names, reasons or failed executions.
        return digest({'run_id': state['run_id'], 'cursor': state['cursor'],
            'approval': state.get('approval', {}).get('sha256'),
            'requirements': state.get('requirements', {}).get('sha256'),
            'spec': state.get('spec', {}).get('sha256'), 'baseline_version': state.get('baseline_version', 0),
            'tasks': [(t['id'], t.get('group'), bool(t.get('completed'))) for t in state['tasks']]})

    def settle_operation_failure(self, state, event):
        """Settle errors inside the milestone's own atomic event, never after a return."""
        failure = state.get('operation_failure')
        if not failure: return
        scope = self.operation_scope(state)
        old_scope = failure.get('scope')
        if old_scope is None:
            previous, _ = self.load()
            old_scope = self.operation_scope(previous)
        if old_scope != scope:
            state.setdefault('operation_failure_history', []).append({**failure, 'recovered_event': event, 'revision': state['revision'] + 1})
            state.pop('operation_failure')
        elif event in ('derived_revision_proposed', 'baseline_repair_proposed') and failure['action'] == 'repair_propose':
            # Accepting a proposal resolves its rejection, not the overall no-progress episode.
            failure.update(count=0, identity=None, resolved=True)

    def operation_correction(self, state):
        failure = state.get('operation_failure', {})
        limits = self.failure_limits()
        task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        result = {'required': failure.get('count', 0) >= limits['operation_warning'] or failure.get('total', 0) >= 2 * limits['operation_warning'],
            'action': failure.get('action'), 'reason': failure.get('reason'), 'task_id': task.get('id'),
            'strategy': '按具体错误核对当前权威材料，改变修复方案后重试；不得重复提交同一错误草稿。',
            'next_steps': ['读取本次 diagnostic 或 schema 指出的具体字段与当前规格。',
                           '只修正错误涉及的草稿/当前许可实现文件，保持验收关联和冻结测试。',
                           '需要修订时提交提案并派发实际原生审查，通过后应用并继续；普通工程纠错不要求用户确认。']}
        if failure.get('diagnostic'): result['diagnostic'] = failure['diagnostic']
        return result

    def draft(self, path):
        state, _ = self.load()
        p = self.path(path)
        base = self.path(f"specs/drafts/{state['run_id']}")
        if not p.is_relative_to(base) or not p.is_file() or p.stat().st_size > 2 * 1024 * 1024:
            raise GateError('草稿必须是当前任务 specs/drafts/<run_id>/ 内不超过 2 MiB 的文件')
        return p

    def validate_artifact(self, kind, path):
        if kind not in KINDS: raise GateError('未知产物类型')
        value = read_json(self.draft(path))
        schema = read_json(self.control / 'schemas' / f'{kind}.schema.json')
        errors = schema_errors(value, schema)
        if kind == 'requirements' and not errors: errors = validate_requirements(value)
        return {'valid': not errors, 'errors': errors, 'path': path, 'sha256': sha256_file(self.draft(path))}

    @writes
    def submit_artifact(self, kind, path):
        state, _ = self.load()
        self.integrity(state)
        if state['stage'] in ('needs_attention', 'paused'): raise GateError('当前已停止，请从 flow 恢复')
        if kind == 'requirements':
            receipt = read_json(self.path(state['analyst_invocation'])) if state.get('analyst_invocation') else {}
            if receipt.get('status') != 'completed' or receipt.get('result_path') != path:
                raise GateError('正式需求只接受当前原生分析角色的不可变结果路径；调试草稿只能 validate')
            p = self.path(path)
            if sha256_file(p) != receipt['result_sha256'] or receipt['source_digest'] != self.requirements_source(state):
                raise GateError('原生需求结果或来源已过期')
            if state.get('requirements', {}).get('native_receipt') == state['analyst_invocation']:
                return self.summary()
            self.requirements(read_json(p), native_receipt=state['analyst_invocation'])
        else:
            checked = self.validate_artifact(kind, path)
            if not checked['valid']: raise GateError('; '.join(checked['errors']))
            binding = {'path': path, 'sha256': checked['sha256'], 'approval': state.get('approval', {}).get('sha256')}
            if kind in ('spec', 'plan'): binding['baseline_version'] = state.get('baseline_version', 0)
            if kind == 'verification': binding['candidate'] = self.candidate()
            if state.get('submitted_artifacts', {}).get(kind) == binding:
                return self.summary()
            value = read_json(self.draft(path))
            if kind == 'spec': self.spec(**value)
            elif kind == 'plan': self.plan(**value)
            elif kind == 'knowledge': self.submit_knowledge(**value)
            elif kind == 'verification': self.prepare_verifier(**value)
            latest, run = self.load()
            latest.setdefault('submitted_artifacts', {})[kind] = binding
            self.checkpoint(latest, run, 'artifact_registered')
        return self.summary()

    @staticmethod
    def requirements_source(state):
        return digest({'run_id': state['run_id'], 'request': state['request'], 'feedback': state.get('feedback', [])})

    @writes
    def requirements_context(self):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] != 'discovery': raise GateError('需求角色只在入口讨论阶段执行')
        source_digest = self.requirements_source(state)
        folder = run / 'requirements-inputs' / source_digest
        source = folder / 'SOURCE.json'
        value = {'run_id': state['run_id'], 'request': state['request'], 'feedback': state.get('feedback', [])}
        # Sources remain byte-stable for the same request/feedback digest. Additional
        # context is read separately and never represented as a user decision.
        if not source.exists():
            prior = state.get('requirements', {}).get('value')
            if not prior and state.get('analyst_invocation'):
                receipt = read_json(self.path(state['analyst_invocation']))
                if receipt.get('status') == 'completed': prior = read_json(self.path(receipt['result_path']))
            value['prior_questions'] = (prior or {}).get('intent', {}).get('open_questions', [])
            value['recorded_answers'] = [{k: r.get(k) for k in ('questions', 'answer', 'session', 'call')}
                for p in (run / 'business-questions').glob('*.json') if (r := read_json(p)).get('completed')]
            write_json_new(source, value)
        # Existing v2 source snapshots keep their exact bytes and contract.
        stored = read_json(source)
        value = {k: stored[k] for k in ('run_id', 'request', 'feedback')}
        if value != {'run_id': state['run_id'], 'request': state['request'], 'feedback': state.get('feedback', [])}: raise GateError('固定需求来源被修改')
        return {'source_digest': source_digest, 'task_call': {'agent': ANALYST,
            'task': f"Read {source.relative_to(self.root).as_posix()} (source digest {source_digest}) and "
                    '.aidevflow/schemas/requirements.schema.json. Read specs/MAP.md and relevant modules/source when present; distinguish verified facts from assumptions. Preserve historical question IDs with their option definitions when decisions refer to them; resolved questions must be non-blocking. Produce the complete approved-for-review Intent/PRD draft, '
                    'requirements and Given/When/Then scenarios using the installed structured output schema. '
                    'Every requirement source must be request or feedback:<zero-based index>. Cover all user requests; '
                    'record genuinely unresolved business choices as blocking open_questions. Never use probe/placeholder '
                    'content, invent user decisions, approve, implement, or delegate. Return yield({data: complete_object}).',
            'solutionSpace': 'read-only requirements analysis; no implementation or approval'}}

    @writes
    def observe_requirements(self, payload):
        state, run = self.load()
        self.integrity(state)
        session, call = payload.get('session_id'), payload.get('toolCallId')
        if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (session, call)):
            raise GateError('缺少准确的原生会话或调用身份')
        inp = self.role_input(payload.get('input'), ANALYST)
        ident = digest(['requirements', session, call])[:32]
        path = run / 'native-requirements' / (ident + '.json')
        if payload.get('type') == 'tool_call':
            assignment = self.requirements_context()
            if assignment['source_digest'] not in inp['task']: raise GateError('需求角色未绑定当前来源')
            if path.exists():
                receipt = read_json(path)
                if receipt['input_digest'] != digest(inp): raise GateError('原生调用身份复用冲突')
                return receipt
            if state.get('analyst_invocation') and not read_json(self.path(state['analyst_invocation'])).get('execution_finished'):
                raise GateError('需求分析尚在执行，不能重复派发')
            receipt = {'id': ident, 'session_id': session, 'toolCallId': call, 'input_digest': digest(inp),
                       'input': payload['input'],
                       'source_digest': assignment['source_digest'], 'status': 'dispatched', 'execution_finished': False}
            write_json_new(path, receipt)
            state['analyst_invocation'] = path.relative_to(self.root).as_posix()
            self.checkpoint(state, run, 'requirements_analyst_dispatched')
            return receipt
        if payload.get('type') != 'tool_result' or not path.is_file(): raise GateError('没有原生需求派发锚点')
        receipt = read_json(path)
        if receipt['input_digest'] != digest(inp): raise GateError('原生需求输入与派发不符')
        frame = digest(payload)
        if receipt.get('result_event_digest') == frame: return receipt
        if receipt.get('execution_finished'): raise GateError('原生需求结果已结束，不能覆盖')
        details = payload.get('details') or {}
        if not isinstance(details, dict): raise GateError('原生 details 必须为对象')
        results = details.get('results', [])
        if not isinstance(results, list) or len(results) > 1: raise GateError('原生需求必须返回一个结果')
        if not results and not payload.get('isError'): return receipt
        receipt.update(execution_finished=True, result_event_digest=frame)
        result = results[0] if results else {}
        if not isinstance(result, dict): raise GateError('原生结果必须为对象')
        if results and (result.get('agent') != ANALYST or not isinstance(result.get('id'), str) or not result['id']):
            raise GateError('原生需求角色身份无效')
        if results:
            owner = run / 'native-identities' / (digest([session, result['id']]) + '.json')
            if owner.exists() and read_json(owner)['invocation'] != ident: raise GateError('角色身份已被另一调用使用')
            if not owner.exists(): write_json_new(owner, {'invocation': ident})
        structured = result.get('structuredOutput')
        if not isinstance(structured, dict): structured = {}
        value = structured.get('data')
        errors = schema_errors(value, read_json(self.control / 'schemas/requirements.schema.json'))
        if not errors: errors = validate_requirements(value)
        sources = {'request', *(f'feedback:{i}' for i in range(len(state.get('feedback', []))))}
        if not errors and any(r['source'] not in sources for r in value['requirements']): errors.append('需求 source 必须引用 request 或实际 feedback:<index>')
        if receipt['source_digest'] != self.requirements_source(state) or state['stage'] != 'discovery':
            receipt['status'] = 'stale'
        elif payload.get('isError') or result.get('aborted') or result.get('error') or type(result.get('exitCode')) is not int or result['exitCode'] != 0:
            receipt['status'] = 'execution_failed'
        elif structured.get('status') != 'valid' or errors:
            receipt.update(status='invalid_output', error='; '.join(errors) or '原生结构化输出未通过校验')
        else:
            output = run / 'requirements-results' / (ident + '.json')
            write_json_new(output, value)
            receipt.update(status='completed', result_path=output.relative_to(self.root).as_posix(), result_sha256=sha256_file(output))
        write_json_atomic(path, receipt)
        self.checkpoint(state, run, 'requirements_analyst_finished')
        if receipt['status'] in ('execution_failed', 'invalid_output'):
            self.operation_failure('requirements_analysis', receipt.get('error', '原生需求角色执行失败'))
        return receipt

    def recover_requirements(self, session_id, entries):
        if not self.active.exists(): return {'recovered': False}
        state, _ = self.load()
        if not state.get('analyst_invocation'): return {'recovered': False}
        receipt = read_json(self.path(state['analyst_invocation']))
        if receipt.get('execution_finished') or receipt['session_id'] != session_id: return {'recovered': False}
        for entry in entries:
            message = entry.get('message', {})
            if message.get('role') == 'toolResult' and message.get('toolName') == 'task' and message.get('toolCallId') == receipt['toolCallId']:
                # Only replay a result for an already persisted dispatch; never synthesize an anchor.
                inp = receipt.get('input')
                if inp is None: return {'recovered': False}
                self.observe_requirements({'type': 'tool_result', 'session_id': session_id, 'toolCallId': receipt['toolCallId'],
                    'input': inp, 'details': message.get('details'), 'isError': message.get('isError', False)})
                return {'recovered': True}
        return {'recovered': False}
