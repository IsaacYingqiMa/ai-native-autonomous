"""The two review boundaries: approved Intent/PRD and independently verified delivery."""
from __future__ import annotations

import json
import secrets

from ...requirements import validate_requirements
from ...requirements import render_intent
from ...storage import (GateError, read_json, sha256_bytes, sha256_file,
                   utc_now, write_bytes_new, write_json_atomic, write_json_new)
from ...locking import writes
from .verdict import bound_schema, verdict_errors

VERIFIER = 'aidevflow-independent-verifier'


def digest(value):
    return sha256_bytes(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


class LightReview:
    @writes
    def requirements(self, value: dict, native_receipt: str | None = None):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] != 'discovery':
            raise GateError('Intent/PRD 只在需求讨论阶段修改；已批准需求不可被 AI 改写')
        errors = validate_requirements(value)
        if errors:
            raise GateError('; '.join(errors))
        if native_receipt is None:
            raise GateError('正式需求必须来自原生分析角色结果；本地调试使用 validate，不写正式需求')
        receipt = read_json(self.path(native_receipt))
        if (native_receipt != state.get('analyst_invocation') or receipt.get('status') != 'completed'
                or receipt.get('source_digest') != self.requirements_source(state)
                or read_json(self.path(receipt['result_path'])) != value
                or sha256_file(self.path(receipt['result_path'])) != receipt['result_sha256']):
            raise GateError('需求缺少当前有效原生结果来源')
        folder = run / 'requirements' / secrets.token_hex(12)
        intent = render_intent(state['run_id'], state['slug'], value['intent'])
        for name, content in (('intent.md', intent), ('prd.md', value['prd'])):
            write_bytes_new(folder / name, content.encode('utf-8'))
        write_json_new(folder / 'requirements.json', value)
        state['requirements'] = {'value': value, 'sha256': digest(value),
                                 'native_receipt': native_receipt,
                                 'revision': state.get('requirements', {}).get('revision', 0) + 1,
                                 'files': {p.relative_to(self.root).as_posix(): sha256_file(p) for p in folder.iterdir()}}
        state['requirements_feedback_pending'] = False
        self.checkpoint(state, run, 'intent_prd_submitted')
        return self.status()

    @writes
    def approve(self, sha256: str, by: str):
        state, run = self.load()
        self.integrity(state)
        requirements = state.get('requirements', {})
        if state['stage'] != 'discovery' or requirements.get('sha256') != sha256 or not by or state.get('requirements_feedback_pending'):
            raise GateError('Intent/PRD 批准版本已过期或不存在，请重新审查')
        if self.blocking_questions(state):
            raise GateError('仍有阻塞业务问题；先讨论并更新 Intent/PRD')
        if not requirements.get('native_receipt'):
            raise GateError('需求缺少原生来源，请重新生成正式 Intent/PRD')
        state['approval'] = {'sha256': sha256, 'revision': requirements['revision'], 'by': by,
                             'at': utc_now(), 'source': 'native_user_interaction'}
        state['stage'] = 'spec'
        self.checkpoint(state, run, 'intent_prd_approved')
        return self.status()

    @writes
    def requirement_feedback(self, text: str, by: str):
        state, run = self.load()
        self.integrity(state)
        conflict = state['stage'] == 'needs_attention' and state.get('attention_kind') == 'business_decision'
        if (state['stage'] not in ('discovery', 'spec', 'plan') and not conflict) or not text.strip() or not by:
            raise GateError('需要入口或业务冲突的实际用户需求反馈')
        if conflict:
            self.no_inflight(state)
            if state['baselines']:
                raise GateError('已存在有效测试基线；保留冲突检查点。需求语义变更需要专门基线迁移，不能直接清空或重启；可明确选择按原批准范围恢复修复')
            state.setdefault('requirements_history', []).append({k: state.get(k) for k in ('requirements', 'approval', 'spec', 'tasks')})
        for key in ('attention_stage', 'attention_kind', 'attention_reason'): state.pop(key, None)
        state.setdefault('feedback', []).append({'text': text, 'by': by, 'at': utc_now()})
        state['requirements_feedback_pending'] = True
        state.pop('approval', None)
        state.pop('spec', None)
        state.update(stage='discovery', tasks=[], cursor=0)
        self.checkpoint(state, run, 'human_requirements_feedback')
        return self.status()

    def bundle_intact(self, state):
        bundle = state.get('verifier_bundle')
        if not bundle or sha256_file(self.path(bundle['path']) / 'BUNDLE.json') != bundle['sha256']:
            raise GateError('独立验收 bundle 缺失或被修改')
        manifest = read_json(self.path(bundle['path']) / 'BUNDLE.json')
        for item in manifest['files']:
            p = self.path(bundle['path']) / item['path']
            if not p.is_file() or sha256_file(p) != item['sha256']:
                raise GateError('独立验收证据对象已修改')
        if bundle.get('contract_sha256') and sha256_file(self.path(bundle['path']) / 'OUTPUT-CONTRACT.json') != bundle['contract_sha256']:
            raise GateError('独立验收输出契约被修改')
        return manifest

    @writes
    def prepare_verifier(self, knowledge_files: dict | None = None):
        state, run = self.load()
        self.integrity(state)
        self.baselines(state)
        if state['stage'] != 'verify' or state.get('final_candidate') != self.candidate():
            raise GateError('需要当前候选的全量回归通过后准备独立验收')
        if state.get('verifier_invocation'):
            receipt = read_json(self.path(state['verifier_invocation']))
            if not receipt.get('execution_finished'):
                raise GateError('独立验收执行尚未结束，先恢复真实结果，不能重复派发')
        files = knowledge_files if knowledge_files is not None else state.get('knowledge_proposal', {})
        self.validate_knowledge(files)
        subject = digest({'candidate': state['final_candidate'], 'approval': state['approval']['sha256'],
            'spec': state['spec']['sha256'], 'tasks': state['tasks'], 'knowledge': files,
            'baseline_version': state.get('baseline_version', 0)})
        if state.get('verifier_subject') == subject and state.get('verifier_bundle'):
            self.bundle_intact(state)
            return self.verifier_context()
        state['knowledge_proposal'] = files
        folder = run / 'verifier-bundles' / secrets.token_hex(12)
        entries = []
        def add(label, data):
            sha = sha256_bytes(data)
            path = folder / 'objects' / sha
            if not path.exists():
                write_bytes_new(path, data)
            entries.append({'label': label, 'path': 'objects/' + sha, 'sha256': sha})
        for rel in self.candidate_files():
            p = self.path(rel)
            add(rel, p.read_bytes() if p.is_file() else b'[deleted]')
        for rel in state['requirements']['files']:
            add(rel, self.path(rel).read_bytes())
        for name in ('intent.md', 'prd.md', 'spec.md', 'plan.md'):
            p = self.path(state['directory']) / name
            if p.is_file(): add(name, p.read_bytes())
        for task in state['tasks']:
            result = task.get('result')
            if result: add(result['path'], self.path(result['path']).read_bytes())
        for history in state.get('baseline_history', []):
            review = history['review']
            manifest = self.check_review_bundle(review)
            add(review['path'] + '/BUNDLE.json', self.path(review['path'] + '/BUNDLE.json').read_bytes())
            for item in manifest['files']:
                rel = review['path'] + '/' + item['path']
                add(rel, self.path(rel).read_bytes())
            for task in history['tasks']:
                result = task.get('result')
                if result: add(result['path'], self.path(result['path']).read_bytes())
        for receipt in (run / 'native-baselines').glob('*.json'):
            add(receipt.relative_to(self.root).as_posix(), receipt.read_bytes())
        for history in state.get('execution_history', []):
            for task in history['tasks']:
                result = task.get('result')
                if result: add(result['path'], self.path(result['path']).read_bytes())
        # Every execution record binds its immutable screenshots/traces/reports.
        all_tasks = [*state['tasks'], *(t for h in state.get('execution_history', []) for t in h['tasks'])]
        for task in all_tasks:
            if task.get('result'):
                for record in read_json(self.path(task['result']['path'])).get('records', []):
                    for blob in record.get('evidence', []): add(blob['source'], self.path(blob['path']).read_bytes())
        add('execution-history.json', json.dumps(state.get('execution_history', []), ensure_ascii=False).encode())
        add('baseline-history.json', json.dumps(state.get('baseline_history', []), ensure_ascii=False).encode())
        add('spec-definition.json', json.dumps(state['spec'], ensure_ascii=False).encode())
        add('plan-definition.json', json.dumps(state['tasks'], ensure_ascii=False).encode())
        add('knowledge-proposal.json', json.dumps(files, ensure_ascii=False).encode())
        add('verdict-schema.json', self.path('.zscode/extensions/aidevflow/verifier.schema.json').read_bytes())
        add('diff.patch', self.git('diff', '--binary', state['base_commit']).encode())
        manifest = {'schema': 'aidevflow/light-bundle-v1', 'run_id': state['run_id'],
                    'candidate': state['final_candidate'], 'spec_ids': state['spec']['ids'],
                    'requirements_sha256': state['approval']['sha256'], 'files': entries}
        write_json_new(folder / 'BUNDLE.json', manifest)
        state['verifier_bundle'] = {'path': folder.relative_to(self.root).as_posix(),
                                    'sha256': sha256_file(folder / 'BUNDLE.json')}
        write_json_new(folder / 'OUTPUT-CONTRACT.json', self.verifier_schema(state['verifier_bundle']))
        state['verifier_bundle']['contract_sha256'] = sha256_file(folder / 'OUTPUT-CONTRACT.json')
        state['verifier_subject'] = subject
        state.pop('verifier_correction', None)
        state.pop('verifier_invocation', None)
        state.pop('verdict', None)
        self.checkpoint(state, run, 'verifier_bundle_prepared')
        return self.verifier_context()

    def verifier_context(self):
        state, _ = self.load()
        if state['stage'] != 'verify' or not state.get('verifier_bundle'):
            raise GateError('先 prepare_verifier，再派发受管独立验收角色')
        self.bundle_intact(state)
        bundle = state['verifier_bundle']
        correction = self.verifier_correction(state, bundle)
        expected = ', '.join(state['spec']['ids'])
        fix = (f" FORMAT CORRECTION: read {correction['path']} (SHA-256 {correction['sha256']}). "
               'Keep substantive findings, failed specifications and revise decisions. Move explanatory suffixes from evidence to reason; '
               'move extra summary checks into the top-level reason. Correct the complete output in the same frozen bundle; never invent pass. ') if correction else ''
        contract_hint = ('Read OUTPUT-CONTRACT.json. ' if bundle.get('contract_sha256')
                         else 'This legacy bundle has no OUTPUT-CONTRACT sidecar; use the strict schema attached by the host. ')
        return {'task_call': {'agent': VERIFIER,
            'task': f"Read only {bundle['path']}/BUNDLE.json and its objects. Bundle SHA-256 {bundle['sha256']}. "
                    'Check approved Intent/PRD, specification coverage, implementation, real TDD/regression evidence and knowledge proposal. '
                    f'Emit exactly one item for each of these IDs, with no extra summary items: {expected}. '
                    'Use the host-bound strict outputSchema, return yield({data: verdict}). ' + contract_hint +
                    'Cite ONLY exact objects/<sha> paths; all labels, line numbers and explanations belong in reason. '
                    + fix +
                    'report missing evidence or unmet requirements as failed, never infer success. Do not repair or delegate.',
            'solutionSpace': 'read-only frozen bundle; independent acceptance'}, **bundle,
            'format_correction': correction is not None}

    def verifier_correction(self, state, bundle):
        correction = state.get('verifier_correction')
        if not correction or correction.get('bundle_sha256') != bundle['sha256']: return None
        if sha256_file(self.path(correction['path'])) != correction['sha256']: raise GateError('验收纠错材料被修改')
        return correction

    def verifier_schema(self, bundle, correction=None):
        manifest = self.bundle_intact({'verifier_bundle': bundle})
        entry = next(x for x in manifest['files'] if x['label'] == 'verdict-schema.json')
        base = read_json(self.path(bundle['path']) / entry['path'])
        return bound_schema(base, manifest['run_id'], bundle['sha256'], manifest['spec_ids'],
            [x['path'] for x in manifest['files']], (correction or {}).get('failed_ids', []),
            (correction or {}).get('require_revise', False))

    def verifier_assignment(self):
        context = self.verifier_context()
        state, _ = self.load()
        schema = self.verifier_schema(state['verifier_bundle'], self.verifier_correction(state, state['verifier_bundle']))
        return {**context, 'task_call': {**context['task_call'], 'outputSchema': schema, 'schemaMode': 'strict'}}

    @staticmethod
    def role_input(inp, role=VERIFIER, output_schema=None):
        if not isinstance(inp, dict): raise GateError('原生任务参数无效')
        if any(k in inp for k in ('tools', 'sourceSession', 'model')) or inp.get('isolated'):
            raise GateError('不得覆盖独立角色的工具、输出、模型或隔离策略')
        contract = {k: inp[k] for k in ('outputSchema', 'schemaMode') if k in inp}
        if contract and (output_schema is None or contract != {'outputSchema': output_schema, 'schemaMode': 'strict'}):
            raise GateError('输出契约只能由宿主按固定 bundle 绑定，不能由协调者覆盖')
        if 'tasks' in inp:
            if not isinstance(inp['tasks'], list) or len(inp['tasks']) != 1:
                raise GateError('每次只派发一个独立验收角色')
            item = LightReview.role_input(inp['tasks'][0], role, output_schema)
            return {**item, 'context': inp.get('context', '')}
        if inp.get('agent') != role or not isinstance(inp.get('task'), str):
            raise GateError('只允许受管独立验收角色')
        return {**{k: inp.get(k, '') for k in ('agent', 'task', 'solutionSpace')}, **contract}

    @writes
    def observe_verifier(self, payload: dict):
        state, run = self.load()
        self.integrity(state)
        session, call = payload.get('session_id'), payload.get('toolCallId')
        if not all(isinstance(s, str) and 0 < len(s) <= 256 for s in (session, call)):
            raise GateError('需要准确的原生会话与调用身份')
        ident = digest(['zscode', session, call])[:32]
        path = run / 'native-invocations' / (ident + '.json')
        prior = read_json(path) if path.is_file() else None
        expected_schema = prior.get('output_schema') if prior else self.verifier_assignment()['task_call']['outputSchema']
        inp = self.role_input(payload.get('input'), output_schema=expected_schema)
        if payload.get('type') == 'tool_call':
            if state['stage'] != 'verify' or not state.get('verifier_bundle'):
                raise GateError('当前阶段不能派发独立验收')
            self.bundle_intact(state)
            bundle = state['verifier_bundle']
            assignment = inp.get('context', '') + inp['task']
            if bundle['path'] not in assignment or bundle['sha256'] not in assignment:
                raise GateError('独立验收必须绑定当前 bundle 路径和摘要')
            if path.exists():
                prior = read_json(path)
                if prior['input_digest'] != digest(inp): raise GateError('原生调用身份复用冲突')
                return prior
            if 'outputSchema' not in inp: raise GateError('原生验收须由宿主绑定严格输出契约后派发')
            if state.get('verifier_invocation'):
                prior = read_json(self.path(state['verifier_invocation']))
                if not prior.get('execution_finished'): raise GateError('独立验收尚在执行')
            receipt = {'id': ident, 'input_digest': digest(inp), 'session_id': session, 'toolCallId': call,
                       'bundle': bundle, 'candidate': state['final_candidate'], 'status': 'dispatched',
                       'execution_finished': False, 'output_schema': expected_schema}
            write_json_new(path, receipt)
            state['verifier_invocation'] = path.relative_to(self.root).as_posix()
            self.checkpoint(state, run, 'verifier_dispatched')
            return receipt
        if payload.get('type') != 'tool_result' or not path.is_file():
            raise GateError('没有真实派发锚点，不能补造独立验收完成')
        receipt = read_json(path)
        if receipt['input_digest'] != digest(inp): raise GateError('原生结果输入与派发不符')
        frame_hash = digest(payload)
        if receipt.get('result_event_digest') == frame_hash: return receipt
        if receipt.get('execution_finished'):
            raise GateError('已结束的原生结果不能改写或由迟到成功覆盖')
        details = payload.get('details') or {}
        if not isinstance(details, dict): raise GateError('原生结果 details 格式无效')
        results = details.get('results', [])
        if not isinstance(results, list): raise GateError('原生 results 必须为数组')
        if not results:
            if not payload.get('isError'): return receipt
            receipt.update(status='execution_failed', execution_finished=True)
        else:
            if not isinstance(results, list) or len(results) != 1: raise GateError('独立验收必须返回单个原生结果')
            result = results[0]
            if not isinstance(result, dict): raise GateError('原生结果格式无效')
            if result.get('agent') != VERIFIER or not isinstance(result.get('id'), str) or not result['id']:
                raise GateError('原生角色身份无效')
            owner = run / 'native-identities' / (digest([session, result['id']]) + '.json')
            if owner.exists() and read_json(owner)['invocation'] != ident:
                raise GateError('原生角色结果身份已被另一调用占用')
            if not owner.exists(): write_json_new(owner, {'invocation': ident})
            receipt.update(execution_finished=True, result_event_digest=frame_hash, agent_id=result['id'])
            structured = result.get('structuredOutput')
            value = structured.get('data') if isinstance(structured, dict) else None
            if payload.get('isError') or result.get('aborted') or result.get('error') or type(result.get('exitCode')) is not int or result['exitCode'] != 0:
                receipt['status'] = 'execution_failed'
            elif state.get('verifier_bundle') != receipt['bundle'] or self.candidate() != receipt['candidate']:
                receipt['status'] = 'stale'
            elif not isinstance(structured, dict) or structured.get('status') != 'valid':
                receipt['status'] = 'invalid_output'
            else:
                receipt['status'] = 'completed'
                try:
                    self.validate_verdict(state, receipt, value)
                except (GateError, TypeError, KeyError, AttributeError) as exc:
                    receipt.update(status='invalid_output', error=str(exc))
                else:
                    output = run / 'verdicts' / (ident + '.json')
                    write_json_new(output, value)
                    receipt.update(result_path=output.relative_to(self.root).as_posix(), result_sha256=sha256_file(output))
                    state['verdict'] = {**receipt, 'value': value}
                    if value['decision'] == 'pass':
                        state['stage'] = 'delivery_ready'
                    else:
                        self.route_repair(state, value['reason'])
            if receipt['status'] == 'invalid_output':
                issues = (self.verdict_problems(state, receipt, value) if isinstance(value, dict) else []) or ['原生结构化结果无效；返回完整 yield({data: verdict})，不要返回解释文本。']
                receipt['error'] = '; '.join(issues)[:4000]
                old = self.verifier_correction(state, receipt['bundle'])
                failed_ids = set((old or {}).get('failed_ids', []))
                items = value.get('items', []) if isinstance(value, dict) else []
                if isinstance(items, list): failed_ids.update(i['id'] for i in items if isinstance(i, dict) and i.get('id') in state['spec']['ids'] and i.get('status') == 'failed')
                has_failed = isinstance(items, list) and any(isinstance(i, dict) and i.get('status') == 'failed' for i in items)
                require_revise = bool(failed_ids) or has_failed or bool((old or {}).get('require_revise')) or isinstance(value, dict) and value.get('decision') == 'revise'
                correction_path = self.path(receipt['bundle']['path']) / 'corrections' / (ident + '.json')
                write_json_new(correction_path, {'schema': 'aidevflow/verdict-correction-v1', 'run_id': state['run_id'],
                    'bundle_sha256': receipt['bundle']['sha256'], 'source_receipt': path.relative_to(self.root).as_posix(),
                    'previous_output': value, 'errors': issues, 'failed_ids': sorted(failed_ids), 'require_revise': require_revise})
                state['verifier_correction'] = {'path': correction_path.relative_to(self.root).as_posix(),
                    'sha256': sha256_file(correction_path), 'bundle_sha256': receipt['bundle']['sha256'],
                    'failed_ids': sorted(failed_ids), 'require_revise': require_revise}
        if state.get('verifier_bundle') != receipt['bundle'] or self.candidate() != receipt['candidate']:
            receipt['status'] = 'stale'
            if state['stage'] == 'delivery_ready': state['stage'] = 'verify'
        write_json_atomic(path, receipt)
        if receipt['status'] in ('execution_failed', 'invalid_output'):
            state['verification_attempts'] = state.get('verification_attempts', 0) + 1
            limit = self.failure_limits()['verification_failures']
            if state['verification_attempts'] >= limit:
                state.update(attention_stage='verify', stage='needs_attention', attention_kind='verification_failure',
                             attention_reason=f"独立验收输出/执行连续失败 {state['verification_attempts']}/{limit} 次，保留回执与纠错材料")
        elif receipt['status'] == 'completed':
            state['verification_attempts'] = 0
            state.pop('verifier_correction', None)
        self.checkpoint(state, run, 'verifier_result_observed')
        return receipt

    def verdict_problems(self, state, receipt, value):
        manifest = self.bundle_intact(state)
        correction = self.verifier_correction(state, receipt['bundle'])
        return verdict_errors(value, state['run_id'], receipt['bundle']['sha256'], state['spec']['ids'],
            [i['path'] for i in manifest['files']], (correction or {}).get('failed_ids', []),
            (correction or {}).get('require_revise', False))

    def validate_verdict(self, state, receipt, value):
        issues = self.verdict_problems(state, receipt, value)
        if issues: raise GateError('; '.join(issues)[:4000])

    def guard_verifier_yield(self, inp):
        state, _ = self.load()
        issues = self.verdict_problems(state, {'bundle': state['verifier_bundle']}, inp.get('data'))
        return {'block': bool(issues), 'reason': ('请在当前独立审查中修正 yield({data: ...})；不结束任务、不改结论代替格式修复。\n' + '\n'.join(issues))[:4500] if issues else ''}

    def route_repair(self, state, reason, source='independent_verification'):
        state['repair_rounds'] = state.get('repair_rounds', 0) + 1
        state['repair_feedback'] = reason
        # Reopen implementation tasks even at the budget boundary so a later
        # user-authorized recovery always has a valid editable cursor.
        cursor = next((i for i, t in enumerate(state['tasks']) if t['kind'] in ('green', 'refactor', 'build')),
                      next((i for i, t in enumerate(state['tasks']) if t['kind'] != 'prepare'), 0))
        state.setdefault('execution_history', []).append({'source': source, 'reason': reason,
            'tasks': json.loads(json.dumps(state['tasks'])), 'candidate': state.get('final_candidate')})
        for task in state['tasks'][cursor:]:
            if task['kind'] != 'red':
                for key in ('completed', 'result', 'inflight'): task.pop(key, None)
                task['consecutive_failures'] = 0
        state.update(stage='applying', cursor=cursor)
        state.pop('final_candidate', None)
        limit = self.failure_limits()['repair_rounds']
        if state['repair_rounds'] > limit:
            state.update(stage='needs_attention', attention_stage='applying',
                attention_kind='repair_rounds',
                attention_reason=f"产品修复超过 {limit} 轮（当前 {state['repair_rounds']}）；已保留失败证据，恢复后从实现任务重新检查")

    @writes
    def confirm(self, candidate: str, by: str, bundle_sha256: str | None = None):
        state, run = self.load()
        self.integrity(state)
        self.baselines(state)
        verdict = state.get('verdict', {})
        if not by or state['stage'] != 'delivery_ready' or candidate != self.candidate() or candidate != state.get('final_candidate') or verdict.get('status') != 'completed' or verdict.get('value', {}).get('decision') != 'pass' or bundle_sha256 != state.get('verifier_bundle', {}).get('sha256'):
            raise GateError('交付尚未通过当前候选的独立验收，不能人工验收通过')
        self.bundle_intact(state)
        if sha256_file(self.path(verdict['result_path'])) != verdict['result_sha256']:
            raise GateError('独立验收结论被修改')
        state['completion'] = {'at': utc_now(), 'base_commit': state['base_commit'], 'commit': '未提交',
                               'by': by, 'candidate': candidate, 'verdict': verdict['result_path'],
                               'bundle_sha256': bundle_sha256, 'source': 'native_user_interaction'}
        state.update(stage='done', cursor=len(state['tasks']))
        self.checkpoint(state, run, 'human_delivery_accepted')
        return self.status()

    @writes
    def delivery_feedback(self, text: str, by: str):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] != 'delivery_ready' or not by or not text.strip(): raise GateError('需要实际交付审查反馈')
        state.setdefault('feedback', []).append({'text': text, 'by': by, 'at': utc_now()})
        self.route_repair(state, text)
        self.checkpoint(state, run, 'human_delivery_revision_requested')
        return self.status()

    def recover_verifier(self, session: str, entries: list):
        if not self.active.exists(): return {'recovered': 0}
        state, run = self.load()
        requests, count = {}, 0
        for entry in entries:
            message = entry.get('message', {})
            if message.get('role') == 'assistant':
                for block in message.get('content', []):
                    if block.get('type') == 'toolCall' and block.get('name') == 'task':
                        requests[block['id']] = block.get('arguments')
            elif message.get('role') == 'toolResult' and message.get('toolName') == 'task':
                call = message.get('toolCallId')
                path = run / 'native-invocations' / (digest(['zscode', session, call])[:32] + '.json')
                if call not in requests or not path.exists() or read_json(path).get('execution_finished'): continue
                self.observe_verifier({'type': 'tool_result', 'session_id': session, 'toolCallId': call,
                                       'input': requests[call], 'details': message.get('details'), 'isError': message.get('isError', False)})
                count += 1
        return {'recovered': count}
