"""Reviewed, versioned test baseline repair without changing approved requirements."""
from __future__ import annotations
import ast
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from ...storage import GateError, read_json, sha256_bytes, sha256_file, write_bytes_new, write_json_new, write_json_atomic, replace_file_atomic, valid_red_result
from ...locking import writes
from .review import digest
from .progress import target_assertion_failure, omitted_tests, test_counts
from .revision import WorkflowError

REVIEWER = 'aidevflow-baseline-reviewer'


def cases(text, suffix):
    if suffix == '.py':
        try: return {n.name for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith('test_')}
        except SyntaxError as exc: raise GateError('修订测试存在语法错误') from exc
    return set(re.findall(r'\b(?:it|test)\s*\(\s*[\'\"]([^\'\"]+)', text))


class BaselineRepair:
    def repair_guidance(self, state):
        if state['stage'] == 'repairing': return self.repair_recovery(state)
        if state['stage'] not in ('applying', 'needs_attention') or state['cursor'] >= len(state['tasks']): return None
        task = state['tasks'][state['cursor']]
        review = state.get('baseline_review', {})
        initial_revise = (task['kind'] == 'red' and not task.get('completed') and task.get('group') not in state['baselines']
            and (review.get('mode') == 'initial' and review.get('decision') == 'revise'
                 or task.get('result', {}).get('status') == 'failed'))
        if not initial_revise and (not state['baselines'] or task.get('result', {}).get('status') != 'failed'): return None
        group = task.get('group')
        groups = {g: sorted(files) for g, files in state['baselines'].items()}
        if group not in groups and not initial_revise: group = next(iter(groups)) if len(groups) == 1 else None
        draft = f"specs/drafts/{state['run_id']}"
        tests = [f for f in (task['files'] if initial_revise else groups.get(group, [])) if re.search(r'^(?:test_|test\.)|\.(?:test|spec)\.|_test\.', Path(f).name, re.I)]
        changes = {f: f'{draft}/repairs/{f}' for f in tests[:1]}
        issues = []
        if group:
            try: self.check_test_targets(state, group)
            except (GateError, OSError) as exc: issues.append(str(exc)[:1200])
        return {'kind': 'edit_unfrozen_tests' if initial_revise else 'revise_frozen_baseline',
            'allowed_actions': ['prepare_baseline', 'repair_propose'] if initial_revise else ['repair_propose', 'run'],
            'unfrozen_files': task['files'] if initial_revise else [],
            'derived_revision': {'reason': '纠正派生规格或计划；保留批准验收', 'group': group, 'changes': {}, 'spec_path': draft + '/spec.json', 'plan_path': draft + '/plan.json'},
            'requires_user_resume': state['stage'] == 'needs_attention', 'detected_issues': issues,
            'schema_path': '.zscode/extensions/aidevflow/repair.schema.json', 'proposal_path': draft + '/repair.json',
            'proposal_exists': self.path(draft + '/repair.json').is_file(),
            'frozen_groups': groups,
            'proposal_template': {'reason': '说明真实失败与批准需求或计划路径的冲突', 'group': group or '<从 frozen_groups 选择>', 'changes': {} if initial_revise else changes},
            'next_steps': (['直接修改 unfrozen_files 中的测试；不要提交空 changes 的冻结修订。', '重新 prepare_baseline 并派发原生审查，通过后 run。', '规格/计划错误使用 derived_revision 模板，只填写确实需要修改的 spec_path/plan_path。'] if initial_revise else ['若 proposal_exists 为 true，先读取 proposal_path 及其 changes 指向的现有草稿；读取涉及的冻结测试和批准规格，按需求纠正路径或断言，保留全部业务用例。',
                '将修订文件写到 changes 对应的草稿路径，填写 reason，并把提案写到 proposal_path。',
                '调用 aidevflow_light(action=repair_propose,path=proposal_path)，随后按返回 task_call 派发真实原生基线审查。',
                'approve 后 repair_apply，再 run；revise 按审查意见修订草稿后重新提案。']),
            'note': '未冻结测试直接修改后重审；派生材料用 derived_revision；实现错误修改当前 files 并 run；冻结测试经原始 RED 重放。无需扫描 runtime 或 events。'}

    def group_files(self, state, group):
        return {f for t in state['tasks'] if t.get('group') == group for f in t['files']}

    def check_test_targets(self, state, group, contents=None):
        contents = contents or {}
        allowed = self.group_files(state, group)
        for task in state['tasks']:
            if task.get('group') != group or task['kind'] != 'red': continue
            for rel in task['files']:
                p = self.path(rel)
                if p.suffix not in ('.py', '.js', '.jsx', '.ts', '.tsx', '.mjs'): continue
                text = contents.get(rel, p.read_text(encoding='utf8') if p.is_file() else '')
                refs = set(re.findall(r'''[\'"](\.{1,2}/[^\'"\n]+)[\'"]''', text))
                if p.suffix == '.py':
                    try:
                        for n in ast.walk(ast.parse(text)):
                            if isinstance(n, ast.ImportFrom) and not n.level and n.module and n.module.startswith(('src.', 'app.')):
                                refs.add('/' + n.module.replace('.', '/'))
                    except SyntaxError as exc: raise GateError('RED 测试存在语法错误') from exc
                for ref in refs:
                    if any(c in ref for c in '*?[]'): continue
                    target = (self.root / ref[1:] if ref.startswith('/') else p.parent / ref).resolve()
                    if not target.is_relative_to(self.root): raise GateError('测试依赖越出项目：' + ref)
                    alternatives = [target] if target.suffix else [target, *(Path(str(target) + ext) for ext in ('.py', '.ts', '.tsx', '.js', '.jsx')), target / 'index.ts', target / 'index.tsx']
                    if not any(x.is_file() or x.relative_to(self.root).as_posix() in allowed for x in alternatives):
                        raise GateError(f'测试目标与计划不一致：{rel} 引用 {ref}，既不存在，也未在当前工作项声明；先纠正路径再审查/冻结')

    def baseline_subject(self, state, files):
        return digest({'approval': state['approval']['sha256'], 'plan': [{k: t.get(k) for k in ('id', 'kind', 'group', 'files', 'argv', 'regression', 'specs')} for t in state['tasks']],
                       'files': files, 'candidate': self.candidate()})

    def review_bundle(self, state, run, mode, group, files, proposal=None):
        folder = run / 'baseline-reviews' / secrets.token_hex(12)
        objects = []
        def add(label, data):
            sha = sha256_bytes(data);p = folder / 'objects' / sha
            if not p.exists(): write_bytes_new(p, data)
            objects.append({'label': label, 'path': 'objects/' + sha, 'sha256': sha})
        add('requirements.json', json.dumps(state['requirements']['value'], ensure_ascii=False).encode())
        add('spec.json', json.dumps(state['spec'], ensure_ascii=False).encode())
        add('plan.json', json.dumps(state['tasks'], ensure_ascii=False).encode())
        # A read-only reviewer must see the actual failure that justifies an
        # environment exception, not only a live path in the plan projection.
        for task in state['tasks']:
            if task.get('result'):
                rel = task['result']['path']
                add('execution/' + Path(rel).name, self.path(rel).read_bytes())
        for rel, value in files.items():
            add('proposed/' + rel, value)
            if self.path(rel).is_file(): add('current/' + rel, self.path(rel).read_bytes())
        if proposal:
            for index, proof in enumerate(proposal.get('preflight', [])):
                if sha256_file(self.path(proof['path'])) != proof['sha256']: raise GateError('预检证据已被修改')
                add(f'preflight/{index}.json', self.path(proof['path']).read_bytes())
            add('proposal.json', json.dumps(proposal, ensure_ascii=False).encode())
            if proposal.get('spec'): add('proposed-spec.json', json.dumps(proposal['spec'], ensure_ascii=False).encode())
            add('proposed-plan.json', json.dumps(proposal['plan'], ensure_ascii=False).encode())
        manifest = {'schema': 'aidevflow/baseline-review-bundle-v1', 'run_id': state['run_id'], 'mode': mode,
                    'group': group,
                    'group_acceptance_ids': sorted({a for t in state['tasks'] if t.get('group') == group for sx in t['specs'] for a in state['spec']['acceptance_map'][sx]}),
                    'changed_paths': sorted(proposal['changes']) if proposal else [],
                    'acceptance_ids': sorted({a for r in state['requirements']['value']['requirements'] for a in r['acceptance_ids']}), 'files': objects}
        write_json_new(folder / 'BUNDLE.json', manifest)
        ref = {'path': folder.relative_to(self.root).as_posix(), 'sha256': sha256_file(folder / 'BUNDLE.json')}
        return {**ref, 'mode': mode, 'group': group, 'subject': self.baseline_subject(state, {p: sha256_bytes(v) for p, v in files.items()}), 'proposal': proposal}

    @writes
    def prepare_baseline(self):
        state, run = self.load();self.integrity(state)
        if state['stage'] != 'applying' or state['tasks'][state['cursor']]['kind'] != 'red': raise GateError('只在当前 RED 文件准备完成后审查基线')
        task = state['tasks'][state['cursor']]
        self.check_test_targets(state, task['group'])
        files = {f: self.path(f).read_bytes() for f in task['files']}
        reused = self.reuse_review(state, self.review_key(state, 'initial', task['group'], files))
        if reused is not None: return reused
        self._review_available(state)
        state['baseline_review'] = self.review_bundle(state, run, 'initial', task['group'], files)
        state['baseline_review']['key'] = self.review_key(state, 'initial', task['group'], files)
        self.checkpoint(state, run, 'baseline_review_prepared')
        return self.baseline_context()

    def _review_available(self, state):
        old = state.get('baseline_invocation')
        if old and not read_json(self.path(old)).get('execution_finished'): raise GateError('基线审查仍在执行，先恢复原生结果')
        if state.get('baseline_attempts', 0) >= self.failure_limits()['baseline_reviews']: raise GateError('基线审查预算耗尽，保留证据并报告异常')

    @writes
    def propose_repair(self, path):
        state, run = self.load();self.integrity(state);self.baselines(state)
        if state['stage'] not in ('applying', 'repairing', 'needs_attention') or state.get('attention_stage', 'applying') != 'applying': raise GateError('当前没有可修订的执行基线')
        value = read_json(self.draft(path))
        if not isinstance(value, dict): raise GateError('修订提案必须为对象')
        if 'spec_path' in value or 'plan_path' in value:
            return self.propose_derived_revision(state, run, value)
        task = state['tasks'][state['cursor']]
        has_failure = task.get('result', {}).get('status') == 'failed' or state.get('repair_evidence') and read_json(self.path(state['repair_evidence'])).get('passed') is False
        if task.get('inflight') or not has_failure: raise GateError('修订必须关联已结束的真实失败；未冻结测试直接纠正并重审，派生材料使用 spec_path/plan_path 提案')
        value = read_json(self.draft(path))
        if not isinstance(value, dict) or set(value) - {'reason', 'group', 'changes', 'implementation_files'} or not isinstance(value.get('reason'), str) or not value['reason'].strip(): raise GateError('修订需要 reason、group、changes，可选 implementation_files')
        group = value.get('group')
        if group not in state['baselines']: raise GateError('修订需要现存冻结工作项')
        changes = value.get('changes')
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 20: raise GateError('changes 必须为原路径到草稿文件路径的对象，最多20项')
        allowed = {f for baseline in state['baselines'].values() for f in baseline}
        files = {};before = {}
        for rel, draft in changes.items():
            if rel not in allowed: raise GateError('只能修订已冻结的测试/支持文件，不能修改实现、需求或框架：' + str(rel))
            old = self.path(rel).read_text(encoding='utf8');new = self.draft(draft).read_text(encoding='utf8')
            if not new.strip() or cases(old, Path(rel).suffix) - cases(new, Path(rel).suffix): raise GateError('修订不能删除已有测试用例')
            weakening = r'\b(?:skip\w*|xfail|todo)\b|\.(?:only)\s*\('
            if len(re.findall(weakening, new)) > len(re.findall(weakening, old)): raise GateError('修订不能新增跳过、聚焦、豁免或待办测试')
            files[rel] = new.encode();before[rel] = sha256_file(self.path(rel))
        plan = json.loads(json.dumps(state['tasks']))
        updates = value.get('implementation_files', {})
        if not isinstance(updates, dict): raise GateError('implementation_files 必须为任务ID到路径列表的对象')
        for ident, paths in updates.items():
            target = next((t for t in plan if t['id'] == ident and t.get('group') == group and t['kind'] in ('green', 'refactor', 'build')), None)
            if target is None or not isinstance(paths, list) or not paths: raise GateError('仅可纠正当前工作项实现文件列表')
            for rel in paths:
                p = self.path(rel);normalized = p.relative_to(self.root).as_posix()
                key = normalized.casefold()
                frozen = {p.casefold() for p in allowed} if os.name == 'nt' else allowed
                if normalized != rel or (key if os.name == 'nt' else rel) in frozen or key.split('/')[0] in ('.git', '.aidevflow', '.zscode', 'specs') or key.startswith('scripts/aidevflow'): raise GateError('实现路径修订越界')
            target['files'] = paths
        trial = {**state, 'tasks': plan}
        affected = sorted({g for g, baseline in state['baselines'].items() if set(changes) & set(baseline)} | {group})
        if len(affected) > 3: raise GateError('一次修订最多影响三个工作项')
        for g in affected: self.check_test_targets(trial, g, {k: v.decode() for k, v in files.items()})
        proposal = {'reason': value['reason'], 'group': group, 'changes': {k: v.decode() for k, v in files.items()},
                    'before': before, 'plan': plan, 'affected': affected,
                    'binding': self.baseline_subject(state, before), 'approval': state['approval']['sha256']}
        return self.stage_repair(state, run, 'repair', group, files, proposal)

    def baseline_context(self):
        state, _ = self.load();review = state.get('baseline_review')
        if not review: raise GateError('先 prepare_baseline 或 repair_propose')
        self.check_review_bundle(review)
        route = self.baseline_route(state)
        if route['phase'] != 'review_required':
            return {**route, 'path': review['path'], 'sha256': review['sha256']}
        if review['mode'] == 'initial':
            task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
            if state['stage'] != 'applying' or task.get('kind') != 'red' or task.get('group') != review['group']:
                raise GateError('该初始基线审查已经结束')
        elif state['stage'] != 'repairing': raise GateError('当前不在基线修订审查阶段')
        self.check_review_bundle(review)
        return {**route, 'task_call': {'agent': REVIEWER, 'task': f"Read only {review['path']}/BUNDLE.json and its objects. Bundle SHA-256 {review['sha256']}. "
                'Independently check test meaning against approved requirements, module paths against plan, negative cases and assertions. '
                'For repairs verify that no approved behavior, acceptance ID or test case is removed/weakened. '
                'A test passing is not evidence that it is correct. Approve only a sound baseline; otherwise revise with precise findings. '
                'Use the host-bound strict outputSchema via yield({data: verdict}). Never edit, approve product scope or delegate.',
                'solutionSpace': 'read-only baseline correctness review'}, 'path': review['path'], 'sha256': review['sha256']}

    def check_review_bundle(self, review):
        p = self.path(review['path']) / 'BUNDLE.json'
        if sha256_file(p) != review['sha256']: raise GateError('基线审查包被修改')
        manifest = read_json(p)
        for item in manifest['files']:
            if sha256_file(p.parent / item['path']) != item['sha256']: raise GateError('基线审查对象被修改')
        return manifest

    @writes
    def observe_baseline(self, payload):
        state, run = self.load();self.integrity(state)
        session, call = payload.get('session_id'), payload.get('toolCallId')
        if not all(isinstance(x, str) and 0 < len(x) <= 256 for x in (session, call)): raise GateError('缺少准确原生审查身份')
        review = state.get('baseline_review');manifest = self.check_review_bundle(review) if review else None
        ident = digest(['baseline', session, call])[:32];p = run / 'native-baselines' / (ident + '.json')
        prior = read_json(p) if p.exists() else None
        expected_schema = prior.get('output_schema') if prior else self.baseline_output_schema(state, review) if review else None
        inp = self.role_input(payload.get('input'), REVIEWER, expected_schema)
        if payload.get('type') == 'tool_call':
            if not review or review['sha256'] not in inp['task'] or review['path'] not in inp['task']: raise GateError('审查派发未绑定当前基线包')
            if p.exists():
                prior = read_json(p)
                if prior['input_digest'] != digest(inp): raise GateError('原生审查调用复用冲突')
                return prior
            route = self.baseline_route(state)
            if route['phase'] != 'review_required':
                raise WorkflowError('BASELINE_NOT_DISPATCHABLE', '当前审查不应重新派发；执行 ' + route['next_action'], route)
            self._review_available(state)
            if 'outputSchema' not in inp: raise GateError('原生基线审查须由宿主绑定严格输出契约')
            receipt = {'id': ident, 'input': payload['input'], 'input_digest': digest(inp), 'session_id': session, 'toolCallId': call,
                       'bundle': review['sha256'], 'output_schema': expected_schema, 'status': 'dispatched', 'execution_finished': False}
            write_json_new(p, receipt);state['baseline_invocation'] = p.relative_to(self.root).as_posix()
            for key in ('decision', 'reason', 'findings'): state['baseline_review'].pop(key, None)
            self.checkpoint(state, run, 'baseline_review_dispatched');return receipt
        if payload.get('type') != 'tool_result' or not p.is_file(): raise GateError('无真实基线审查派发锚点')
        receipt = read_json(p);frame = digest(payload)
        if receipt['input_digest'] != digest(inp): raise GateError('原生基线结果输入冲突')
        if receipt.get('result_event_digest') == frame: return receipt
        if receipt.get('execution_finished'): raise GateError('已结束审查不能被迟到结果覆盖')
        if not review or receipt['bundle'] != review['sha256']:
            receipt.update(status='stale', execution_finished=True, result_event_digest=frame)
            write_json_atomic(p, receipt)
            return receipt
        details = payload.get('details') or {};results = details.get('results', []) if isinstance(details, dict) else None
        if not isinstance(results, list) or len(results) > 1: raise GateError('原生基线结果格式无效')
        if not results and not payload.get('isError'): return receipt
        result = results[0] if results else {}
        if not isinstance(result, dict): raise GateError('原生基线结果必须为对象')
        structured = result.get('structuredOutput', {})
        if not isinstance(structured, dict): structured = {}
        value = structured.get('data');receipt.update(execution_finished=True, result_event_digest=frame, status='invalid_output')
        if results and (result.get('agent') != REVIEWER or not isinstance(result.get('id'), str) or not result['id']): raise GateError('原生基线角色身份无效')
        if result.get('id'):
            owner = run / 'native-identities' / (digest([session, result['id']]) + '.json')
            if owner.exists() and read_json(owner)['invocation'] != ident: raise GateError('原生角色身份已被占用')
            if not owner.exists(): write_json_new(owner, {'invocation': ident})
        valid = (not payload.get('isError') and not result.get('aborted') and not result.get('error') and type(result.get('exitCode')) is int and result['exitCode'] == 0 and structured.get('status') == 'valid')
        if valid and isinstance(value, dict) and set(value) - {'findings'} == {'schema', 'run_id', 'bundle_sha256', 'decision', 'reason', 'acceptance_ids', 'changed_paths'}:
            valid = (value['schema'] == 'aidevflow/baseline-review-v1' and value['run_id'] == state['run_id'] and value['bundle_sha256'] == receipt['bundle'] == review['sha256'] and value['decision'] in ('approve', 'revise') and isinstance(value['reason'], str) and bool(value['reason'].strip()) and isinstance(value['acceptance_ids'], list) and all(isinstance(x, str) for x in value['acceptance_ids']) and sorted(value['acceptance_ids']) == manifest['acceptance_ids'] and isinstance(value['changed_paths'], list) and all(isinstance(x, str) for x in value['changed_paths']) and sorted(value['changed_paths']) == manifest['changed_paths'])
            if valid and 'findings' in value:
                from .artifacts import schema_errors
                valid = not schema_errors(value, read_json(self.path('.zscode/extensions/aidevflow/baseline.schema.json')))
                if value['decision'] == 'approve' and any(f.get('severity') == 'blocking' for f in value['findings']): valid = False
            if valid:
                receipt.update(status='completed', value=value)
                state['baseline_review']['decision'] = value['decision'];state['baseline_review']['reason'] = value['reason']
                state['baseline_review']['findings'] = value.get('findings', [])
                if value['decision'] == 'revise' and state['stage'] == 'repairing': state['stage'] = 'applying'
        write_json_atomic(p, receipt)
        if receipt['status'] == 'completed' and value['decision'] == 'approve':
            state['baseline_review']['approval_receipt'] = {'path': p.relative_to(self.root).as_posix(), 'sha256': sha256_file(p)}
        elif receipt['status'] == 'invalid_output':
            from .artifacts import schema_errors
            errors = schema_errors(value, expected_schema) if expected_schema else []
            receipt['error'] = '; '.join(errors)[:1800] or '原生基线输出不符合验收ID/路径或执行未正常完成；保留同一包纠正输出，不伪造 approve'
            write_json_atomic(p, receipt)
        state['baseline_attempts'] = 0 if receipt['status'] == 'completed' and value['decision'] == 'approve' else state.get('baseline_attempts', 0) + 1
        limit = self.failure_limits()['baseline_reviews']
        if state['baseline_attempts'] >= limit and (receipt['status'] != 'completed' or value['decision'] != 'approve'):
            state.update(attention_stage='applying', stage='needs_attention', attention_kind='baseline_review',
                         attention_reason=f"基线审查预算耗尽 {state['baseline_attempts']}/{limit}")
        self.checkpoint(state, run, 'baseline_review_finished');return receipt

    def require_baseline_review(self, state, task):
        review = state.get('baseline_review', {})
        hashes = {f: sha256_file(self.path(f)) for f in task['files']}
        if review.get('mode') != 'initial' or review.get('group') != task['group'] or not self.baseline_approval(state) or review.get('subject') != self.baseline_subject(state, hashes):
            raise GateError('RED 冻结前必须 prepare_baseline 并通过原生基线审查；文件变化后需重新审查')
        self.check_review_bundle(review)

    def capture_red_origin(self, state, run, group):
        origins = state.setdefault('red_origins', {})
        if group in origins: return
        paths = {f for t in state['tasks'] if t.get('group') == group and t['kind'] in ('green', 'refactor', 'build') for f in t['files']}
        # Preserve the whole source workspace at the successful RED, not later tests or implementations.
        paths.update(self.candidate_files())
        refs = {}
        for rel in paths:
            p = self.path(rel);ref = None
            if p.is_file():
                content = p.read_bytes();blob = run / 'red-origins' / sha256_bytes(content)
                if not blob.exists(): write_bytes_new(blob, content)
                ref = {'path': blob.relative_to(self.root).as_posix(), 'sha256': sha256_file(blob)}
            refs[rel] = ref
        origins[group] = refs
        state.setdefault('red_snapshot_groups', []).append(group)

    def replay_red(self, state, run, group, changes, plan, *, candidate_key=None):
        task = next(t for t in plan if t.get('group') == group and t['kind'] == 'red')
        base = run / 'red-replays';base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base, ignore_cleanup_errors=True) as temporary:
            target = Path(temporary).resolve();target.relative_to(base.resolve())
            full_snapshot = group in state.get('red_snapshot_groups', [])
            if not full_snapshot:
                red_index = next(i for i, t in enumerate(plan) if t['kind'] == 'red' and t.get('group') == group)
                if any(t['kind'] == 'red' and t.get('completed') for t in plan[red_index + 1:]):
                    raise GateError('旧任务缺少完整 RED 工作区快照，后续测试已执行，不能用其他工作项的失败伪造 RED')
                for rel in self.candidate_files():
                    p = self.path(rel)
                    if p.is_file():
                        dst = target / rel;dst.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(p, dst)
            # Dependency installation belongs to preparation; replay never calls an installer.
            if (self.root / 'node_modules').is_dir(): shutil.copytree(self.root / 'node_modules', target / 'node_modules')
            origins = state.get('red_origins', {}).get(group)
            paths = {f for t in plan if t.get('group') == group and t['kind'] in ('green', 'refactor', 'build') for f in t['files']}
            if full_snapshot: origins = {**origins, **{p: None for p in paths - set(origins)}}
            if origins is None or paths - set(origins):
                if any(t.get('completed') and t.get('group') != group and set(t['files']) & paths for t in plan if t['kind'] in ('green', 'refactor', 'build')): raise GateError('旧任务缺少该工作项原始实现快照，不能伪造 RED 重放')
                origins = dict(origins or {})
                for rel in paths - set(origins):
                    p = subprocess.run(['git', '-c', 'core.fsmonitor=false', 'show', state['base_commit'] + ':' + rel], cwd=self.root, capture_output=True, timeout=15)
                    origins[rel] = p.stdout if p.returncode == 0 else None
            for rel, ref in origins.items():
                dst = target / rel
                if isinstance(ref, dict):
                    blob = self.path(ref['path'])
                    if sha256_file(blob) != ref['sha256']: raise GateError('原始 RED 实现快照被修改')
                    content = blob.read_bytes()
                else: content = ref
                if content is None: dst.unlink(missing_ok=True)
                else: dst.parent.mkdir(parents=True, exist_ok=True);dst.write_bytes(content)
            for rel, content in changes.items():
                if rel not in task['files']: continue  # Future groups cannot supply this group's RED.
                dst = target / rel;dst.parent.mkdir(parents=True, exist_ok=True);dst.write_text(content, encoding='utf8', newline='\n')
            argv = [str(target) + arg[len(str(self.root)):] if arg.startswith(str(self.root)) and 'node_modules' not in arg else arg for arg in task['argv']]
            try:
                p = subprocess.run(argv, cwd=target, capture_output=True, encoding='utf8', errors='replace', timeout=60,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                code, output = p.returncode, p.stdout + p.stderr
            except subprocess.TimeoutExpired as exc:
                code, output = 124, 'RED replay timed out\n' + str(exc.stdout or '') + str(exc.stderr or '')
            except OSError as exc:
                code, output = 127, 'Could not start test command: ' + str(exc)
            omitted = omitted_tests(output)
            passed = code not in (124, 127) and valid_red_result(code, output) and not omitted and target_assertion_failure(output)
            evidence = run / 'repair-red-evidence' / (secrets.token_hex(16) + '.json')
            write_json_new(evidence, {'group': group, 'argv': argv, 'exit_code': code, 'output': output[-200000:],
                'passed': bool(passed), 'counts': test_counts(output), 'source': 'isolated_original_implementation',
                'requirements_sha256': state['approval']['sha256'], 'candidate_key': candidate_key,
                'review_sha256': None if candidate_key else state.get('baseline_review', {}).get('sha256'),
                'original_implementation': state.get('red_origins', {}).get(group, {'git_commit': state['base_commit']}),
                'baseline_hashes': {rel: sha256_file(target / rel) for rel in task['files']}})
            proof = {'status': 'passed' if passed else 'failed', 'exit_code': code,
                     'path': evidence.relative_to(self.root).as_posix(), 'sha256': sha256_file(evidence)}
            if not passed:
                error_code = 'REPAIR_EXECUTION_FAILED' if code in (124, 127) else 'REPAIR_RED_INVALID'
                raise WorkflowError(error_code, '修订后的测试未在原始实现上形成有效 RED：' + output[-1600:],
                    {'code': error_code, 'repair_replay': True, 'group': group, 'evidence': proof})
            return proof

    @writes
    def apply_repair(self):
        state, run = self.load();pending = run / 'baseline-repair-transaction.json'
        if pending.exists(): return self.finish_repair(read_json(pending), pending, run)
        self.integrity(state);self.baselines(state);review = state.get('baseline_review', {})
        if state.get('baseline_history') and state['baseline_history'][-1]['review']['sha256'] == review.get('sha256') and state['stage'] != 'repairing':
            return self.summary()
        self.no_inflight(state)
        if state['stage'] != 'repairing' or review.get('mode') not in ('repair', 'derived') or self.baseline_route(state)['phase'] != 'approved': raise GateError('修订必须先获得当前原生基线审查通过；失败候选先修正重提')
        try:
            return self.apply_reviewed_repair(json.loads(json.dumps(state)), run, review, pending)
        except WorkflowError as exc:
            if not (exc.details.get('repair_replay') or exc.details.get('repair_correctable')) or pending.exists(): raise
            self.record_repair_failure(state, run, self.existing_review_key(state, review), exc.details, str(exc), source='apply')
            raise

    def apply_reviewed_repair(self, state, run, review, pending):
        if review['mode'] == 'derived': return self.apply_derived_revision(state, run, review, pending)
        self.check_review_bundle(review);proposal = review['proposal']
        if proposal['binding'] != self.baseline_subject(state, proposal['before']) or proposal['approval'] != state['approval']['sha256']:
            raise WorkflowError('REPAIR_STALE', '修订提案已经过期；按当前工作区重新生成候选',
                                {'code': 'REPAIR_STALE', 'repair_correctable': True})
        for rel, expected in proposal['before'].items():
            if sha256_file(self.path(rel)) != expected: raise GateError('修订目标在审查后变化')
        current = state['tasks'][state['cursor']]
        if current['kind'] == 'red' and not current.get('completed') and current.get('group') in proposal['affected']:
            # This RED was never accepted. Its original workspace is the current pre-GREEN workspace,
            # including earlier completed groups; restoring the repository base would erase that work.
            group = current['group']
            if current.get('result', {}).get('status') != 'failed' or any(t.get('completed') and t.get('group') == group and t['kind'] in ('green', 'refactor', 'build') for t in state['tasks']):
                raise GateError('未通过 RED 的准备修订需要真实失败，且本组 GREEN 尚未执行')
            state.setdefault('red_origins', {}).pop(group, None)
            state['red_snapshot_groups'] = [g for g in state.get('red_snapshot_groups', []) if g != group]
            self.capture_red_origin(state, run, group)
        proofs = {g: self.replay_red(state, run, g, proposal['changes'], proposal['plan']) for g in proposal['affected']}
        after = json.loads(json.dumps(state));after['tasks'] = proposal['plan']
        after.setdefault('baseline_history', []).append({'review': {k: review[k] for k in ('path', 'sha256')}, 'receipt': state['baseline_invocation'], 'baselines': state['baselines'], 'tasks': state['tasks']})
        for g in proposal['affected']:
            after['baselines'][g].update({p: sha256_bytes(c.encode()) for p, c in proposal['changes'].items() if p in after['baselines'][g]})
            red = next(t for t in after['tasks'] if t.get('group') == g and t['kind'] == 'red');red.update(completed=True, result=proofs[g])
        cursor = min((i for i, t in enumerate(after['tasks']) if t.get('group') in proposal['affected'] and t['kind'] != 'red'),
                     default=next((i for i, t in enumerate(after['tasks']) if t.get('group') in proposal['affected']), 0))
        for t in after['tasks'][cursor:]:
            if t['kind'] != 'red': t.pop('completed', None);t.pop('result', None);t['consecutive_failures'] = 0
        after.update(stage='applying', cursor=cursor, baseline_version=state.get('baseline_version', 0) + 1, baseline_attempts=0)
        for key in ('final_candidate', 'verdict', 'verifier_bundle', 'attention_reason', 'attention_kind'): after.pop(key, None)
        after.pop('submitted_artifacts', None)
        tx = {'files': {p: {'before': self.path(p).read_text(encoding='utf8'), 'after': c} for p, c in proposal['changes'].items()},
              'state': after, 'parent_revision': state['revision']}
        write_json_new(pending, tx)
        return self.finish_repair(tx, pending, run)

    def finish_repair(self, tx, pending, run):
        state, _ = self.load()
        if state.get('baseline_version') == tx['state']['baseline_version'] and state['revision'] > tx['parent_revision']:
            pending.unlink();return self.summary()
        if state['revision'] != tx['parent_revision']: raise GateError('修订事务状态冲突，保留证据')
        for rel, images in tx['files'].items():
            p = self.path(rel)
            if (p.read_text(encoding='utf8') if p.is_file() else None) not in (images['before'], images['after']): raise GateError('修订事务文件冲突：' + rel)
        for rel, images in tx['files'].items():
            p = self.path(rel);p.parent.mkdir(parents=True, exist_ok=True);fd, temp = tempfile.mkstemp(dir=p.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf8', newline='\n') as stream: stream.write(images['after']);stream.flush();os.fsync(stream.fileno())
                replace_file_atomic(temp, p)
            finally:
                if os.path.exists(temp): os.unlink(temp)
        self.checkpoint(tx['state'], run, 'baseline_repair_committed');pending.unlink()
        return self.summary()

    def recover_baseline(self, session_id, entries):
        if not self.active.exists(): return
        state, _ = self.load()
        if not state.get('baseline_invocation'): return
        receipt = read_json(self.path(state['baseline_invocation']))
        if receipt.get('execution_finished') or receipt['session_id'] != session_id: return
        for entry in entries:
            m = entry.get('message', {})
            if m.get('role') == 'toolResult' and m.get('toolName') == 'task' and m.get('toolCallId') == receipt['toolCallId']:
                self.observe_baseline({'type': 'tool_result', 'session_id': session_id, 'toolCallId': receipt['toolCallId'], 'input': receipt['input'], 'details': m.get('details'), 'isError': m.get('isError', False)});return
