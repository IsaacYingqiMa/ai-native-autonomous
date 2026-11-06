"""Pure design checks and reviewed derived revisions; old approved snapshots stay intact."""
from __future__ import annotations
import copy
import os
import re
from pathlib import Path
from ...storage import GateError, read_json, sha256_file, sha256_bytes, write_json_new
from .review import digest


class WorkflowError(GateError):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DerivedRevision:
    def canonical_file(self, rel):
        normalized = self.path(rel).relative_to(self.root).as_posix()
        if rel != normalized or '\\' in rel:
            raise GateError('文件需要完整规范相对路径：' + str(rel))
        return normalized.casefold() if os.name == 'nt' else normalized

    def validate_design(self, design):
        if not isinstance(design, dict) or set(design) != {'source_refs', 'modules'}:
            raise GateError('design 需要 source_refs 和 modules')
        refs, modules = design['source_refs'], design['modules']
        if not isinstance(refs, list) or not isinstance(modules, list) or not modules:
            raise GateError('design 需要路径数组与非空模块数组')
        for ref in refs:
            self.canonical_file(ref)
            if not self.path(ref).is_file(): raise GateError('只读代码依据不存在：' + ref)
        seen = set()
        for m in modules:
            if not isinstance(m, dict) or set(m) != {'id', 'implementation_files', 'test_files', 'depends_on', 'contracts'}:
                raise GateError('模块需要 id/implementation_files/test_files/depends_on/contracts')
            if not isinstance(m['id'], str) or not m['id'] or m['id'] in seen:
                raise GateError('模块 ID 必须唯一')
            if not isinstance(m['depends_on'], list) or any(d not in seen for d in m['depends_on']):
                raise GateError('模块依赖只能引用前序模块')
            seen.add(m['id'])
            if not isinstance(m['contracts'], str) or not m['contracts'].strip():
                raise GateError('模块需要明确输入、输出与错误契约')
            for key in ('implementation_files', 'test_files'):
                if not isinstance(m[key], list) or not m[key]: raise GateError('模块文件数组不能为空')
                paths = [self.canonical_file(p) for p in m[key]]
                if len(set(paths)) != len(paths): raise GateError('模块文件重复')

    def validate_layout(self, spec, tasks):
        declared = {self.canonical_file(f) for t in tasks for f in t['files']}
        design = spec.get('design')
        if design:
            self.validate_design(design)
            if {m['id'] for m in design['modules']} != {t['group'] for t in tasks if t.get('group')}:
                raise GateError('设计模块必须精确对应计划 group')
            for m in design['modules']:
                for kinds, key in ((('red',), 'test_files'), (('green', 'refactor'), 'implementation_files')):
                    actual = {self.canonical_file(f) for t in tasks if t.get('group') == m['id'] and t['kind'] in kinds for f in t['files']}
                    if actual != {self.canonical_file(f) for f in m[key]}:
                        raise WorkflowError('PATH_LAYOUT', '规格路径与计划不一致：' + m['id'] + '/' + key)
            return
        # Compatibility for existing specs: retain the WHOLE path, including dashboard/.
        refs = set(re.findall(r'(?<![\w./~:\\-])((?:[\w.@-]+/)+[\w.@-]+\.(?:tsx?|jsx?|mjs|cjs|py|css|html|json))(?![\w.])', spec['content']))
        for ref in sorted(refs):
            if ref.startswith(('.aidevflow/', '.zscode/', '.git/', 'specs/')): continue
            if self.canonical_file(ref) not in declared and not self.path(ref).is_file():
                raise WorkflowError('PATH_LAYOUT', '规格路径与计划不一致：' + ref + '；纠正派生规格/计划；执行后使用 repair_propose')

    def no_inflight(self, state):
        if any(t.get('inflight') for t in state['tasks']): raise GateError('存在未知在途执行，先恢复真实结果')
        for key in ('analyst_invocation', 'baseline_invocation', 'verifier_invocation'):
            if state.get(key) and not read_json(self.path(state[key])).get('execution_finished'):
                raise GateError('原生角色仍在执行，先恢复真实结果')

    def propose_derived_revision(self, state, run, value):
        self.no_inflight(state)
        if set(value) - {'reason', 'group', 'changes', 'spec_path', 'plan_path'}:
            raise GateError('派生修订只接受 reason/group/changes/spec_path/plan_path')
        if not isinstance(value.get('reason'), str) or not value['reason'].strip(): raise GateError('修订需要 reason')
        group = value.get('group')
        if group not in {t.get('group') for t in state['tasks'] if t.get('group')}:
            raise GateError('修订需要现存 group')
        spec = copy.deepcopy(state['spec'])
        if value.get('spec_path'):
            data = self.checked_draft('spec', value['spec_path'])
            spec = self.spec_definition(state, **data)
            if spec['ids'] != state['spec']['ids'] or spec['acceptance_map'] != state['spec']['acceptance_map']:
                raise GateError('执行期派生修订必须保留规格 ID 和完整验收映射')
        raw = self.checked_draft('plan', value['plan_path'])['tasks'] if value.get('plan_path') else state['tasks']
        plan = self.plan_definition({**state, 'spec': spec}, raw)
        if len(plan) != len(state['tasks']): raise GateError('执行期修订不能删除或增加任务；新范围返回需求讨论')
        current = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        environment_failure = self.environment_failure(current)
        accepted_groups = {t['group'] for t in state['tasks'] if t['kind'] == 'red' and t.get('completed')}
        fixed = ('id', 'kind', 'group', 'specs', 'checks')
        conflicts = [{'task_id': old['id'], 'field': key, 'current': old.get(key), 'proposed': new.get(key)}
                     for old, new in zip(state['tasks'], plan) for key in fixed if old.get(key) != new.get(key)]
        if conflicts:
            keys = ('id', 'title', 'kind', 'group', 'specs', 'files', 'argv', 'regression', 'checks')
            current_plan = {'tasks': [{k: t[k] for k in keys if k in t} for t in state['tasks']]}
            raise WorkflowError('PLAN_CONFLICT', '修订保留任务顺序、验收覆盖与附加检查；冲突：' +
                ', '.join(c['task_id'] + '.' + c['field'] for c in conflicts),
                {'conflicts': conflicts, 'current_plan': current_plan, 'plan_digest': digest(current_plan),
                 'baseline_version': state.get('baseline_version', 0),
                 'correction': '以 current_plan 为准，只合入本次声明所需的文件/设计修正；去掉旧草稿夹带的验收变更，然后重新提案。'})
        for old, new in zip(state['tasks'], plan):
            env_group = bool(old.get('group')) and old.get('group') == current.get('group') and old.get('group') not in accepted_groups
            env_prepare = old['kind'] == 'prepare' and not accepted_groups
            for key in ('argv', 'regression'):
                if old.get(key) == new.get(key): continue
                if not (environment_failure and (env_group or env_prepare)) and not self.runner_relocation(old.get(key), new.get(key)):
                    raise GateError('修订保留验证命令；仅真实环境失败的未接受工作项或同运行器路径迁移可审查修订')
            if old['kind'] == 'red' and old['files'] != new['files']:
                raise GateError('修订保留测试路径；需要纠正导入时提交测试草稿')
            if old['kind'] == 'prepare' and old['files'] != new['files'] and not (environment_failure and env_prepare):
                raise GateError('准备支持文件仅可在真实环境失败且尚无有效 RED 时修订')
        changes = value.get('changes', {})
        if not isinstance(changes, dict) or len(changes) > 20: raise GateError('changes 需要至多20项路径对象')
        current = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        allowed = {f for b in state['baselines'].values() for f in b}
        if current.get('kind') == 'red': allowed.update(current['files'])
        if environment_failure and not accepted_groups:
            allowed.update(f for t in [*state['tasks'], *plan] if t['kind'] == 'prepare' for f in t['files'])
        files, before = {}, {}
        from .repair import cases
        for rel, draft in changes.items():
            if rel not in allowed: raise GateError('只能修订当前未冻结 RED 或已冻结测试：' + str(rel))
            old = self.path(rel).read_text(encoding='utf8') if self.path(rel).is_file() else ''; new = self.draft(draft).read_text(encoding='utf8')
            if not new.strip() or cases(old, Path(rel).suffix) - cases(new, Path(rel).suffix): raise GateError('修订不能删除已有测试用例')
            if len(re.findall(r'\b(?:skip\w*|xfail|todo)\b|\.only\s*\(', new)) > len(re.findall(r'\b(?:skip\w*|xfail|todo)\b|\.only\s*\(', old)):
                raise GateError('修订不能新增跳过、聚焦或豁免')
            files[rel] = new.encode(); before[rel] = sha256_file(self.path(rel)) if self.path(rel).is_file() else None
        changed = [i for i, (old, new) in enumerate(zip(state['tasks'], plan)) if any(old.get(k) != new.get(k) for k in new)]
        if spec != state['spec']: changed.append(0)
        changed.extend(i for i, t in enumerate(plan) if set(t['files']) & set(changes))
        if not changed: raise GateError('修订没有实际变更')
        start = min(changed)
        affected = sorted({t['group'] for t in plan[start:] if t.get('group')})
        for g in {t.get('group') for t in plan if t.get('group')}:
            self.check_test_targets({**state, 'tasks': plan}, g, {k: v.decode() for k, v in files.items()})
        proposal = {'reason': value['reason'], 'group': group, 'changes': {k: v.decode() for k, v in files.items()},
            'before': before, 'plan': plan, 'spec': spec, 'affected': affected, 'start': start,
            'binding': self.baseline_subject(state, before), 'approval': state['approval']['sha256']}
        return self.stage_repair(state, run, 'derived', group, files, proposal)

    def apply_derived_revision(self, state, run, review, pending):
        self.no_inflight(state); self.check_review_bundle(review)
        p = review['proposal']
        if p['binding'] != self.baseline_subject(state, p['before']) or p['approval'] != state['approval']['sha256']:
            raise WorkflowError('REPAIR_STALE', '修订提案已经过期；按当前工作区重新生成候选',
                                {'code': 'REPAIR_STALE', 'repair_correctable': True})
        for rel, sha in p['before'].items():
            if (sha256_file(self.path(rel)) if self.path(rel).is_file() else None) != sha: raise GateError('修订目标在审查后变化')
        self.validate_layout(p['spec'], p['plan'])
        after = copy.deepcopy(state)
        after['spec'] = p['spec']
        after['tasks'] = [{**old, **new} for old, new in zip(after['tasks'], p['plan'])]
        after.setdefault('baseline_history', []).append({'review': {k: review[k] for k in ('path', 'sha256')},
            'receipt': state['baseline_invocation'], 'spec': state['spec'], 'tasks': state['tasks'], 'baselines': state['baselines']})
        # Preserve every accepted RED, replaying against its original workspace when impacted.
        for g in p['affected']:
            if g not in state['baselines']: continue
            red = next(t for t in state['tasks'] if t.get('group') == g and t['kind'] == 'red')
            if red.get('completed'):
                proof = self.replay_red(state, run, g, p['changes'], p['plan'])
                target = next(t for t in after['tasks'] if t.get('group') == g and t['kind'] == 'red')
                target.update(completed=True, result=proof)
                after['baselines'][g].update({f: sha256_bytes(c.encode()) for f, c in p['changes'].items() if f in after['baselines'][g]})
            else:
                after['baselines'].pop(g, None)
        for i, t in enumerate(after['tasks']):
            if i < p['start'] or t['kind'] == 'red' and t.get('completed'): continue
            if t['kind'] == 'prepare' and all(state['tasks'][i].get(k) == t.get(k) for k in ('files', 'argv')) and not set(t['files']).intersection(p['changes']): continue
            for key in ('completed', 'result', 'inflight'): t.pop(key, None)
            t['consecutive_failures'] = 0
        cursor = next((i for i, t in enumerate(after['tasks']) if not t.get('completed')), len(after['tasks']) - 1)
        after.update(stage='applying', cursor=cursor, baseline_version=state.get('baseline_version', 0) + 1, baseline_attempts=0)
        self.invalidate_delivery(after)
        after.pop('submitted_artifacts', None)
        tx = {'files': {f: {'before': self.path(f).read_text(encoding='utf8') if self.path(f).is_file() else None, 'after': c} for f, c in p['changes'].items()},
            'state': after, 'parent_revision': state['revision']}
        write_json_new(pending, tx)
        return self.finish_repair(tx, pending, run)

    def environment_failure(self, task):
        result = task.get('result', {})
        if result.get('status') != 'failed': return False
        if task.get('kind') == 'prepare': return True
        records = read_json(self.path(result['path'])).get('records', [])
        markers = ('modulenotfounderror', 'importerror', 'syntaxerror', 'referenceerror',
            'cannot find module', 'err_module_not_found', 'could not start test command', 'command not found')
        return any(r.get('exit_code') in (124, 127) or any(m in r.get('output', '').lower() for m in markers) for r in records)

    def runner_relocation(self, old, new):
        if not isinstance(old, list) or not isinstance(new, list) or old[1:] != new[1:]: return False
        from .environment import inspect
        facts = inspect(self.root, versions=False)
        paths = {v['executable'] for k, v in facts.items() if k in ('python', 'node', 'git') and v.get('executable')}
        name = lambda x: Path(x).name.casefold().removesuffix('.exe')
        return new[0] in paths and name(old[0]) == name(new[0]) and not Path(old[0]).is_file()

    def checked_draft(self, kind, path):
        checked = self.validate_artifact(kind, path)
        if not checked['valid']: raise GateError('; '.join(checked['errors']))
        return read_json(self.draft(path))

    @staticmethod
    def invalidate_delivery(state):
        for key in ('final_candidate', 'verdict', 'verifier_bundle', 'verifier_subject', 'verifier_correction', 'attention_reason', 'attention_kind'):
            state.pop(key, None)
