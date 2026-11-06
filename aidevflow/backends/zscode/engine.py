"""Installed zsspec-light workflow. Documents are projections of durable Gate facts."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

from ...storage import (GateError, read_json, sha256_file, sha256_bytes, utc_now,
                   valid_red_result, write_json_atomic, write_json_new, write_bytes_new)
from ...locking import writes
from .review import LightReview, digest
from .artifacts import LightArtifacts, ANALYST
from .repair import BaselineRepair, REVIEWER
from .revision import DerivedRevision, WorkflowError
from .progress import Progress, test_counts, target_assertion_failure, omitted_tests
from .repair_lifecycle import RepairLifecycle
from .environment import inspect as inspect_environment

PROFILE = 'zsspec-light-managed-v2'
SKILLS = ('zsspec-init', 'zsspec-spec', 'zsspec-plan', 'zsspec-apply', 'zsspec-archive')
READ_TOOLS = {'read', 'grep', 'glob', 'ask', 'web_search', 'todo', 'wait'}


def installed(root: Path) -> bool:
    path = root / '.aidevflow/project.json'
    return path.is_file() and read_json(path).get('method_profile') in (PROFILE, 'zsspec-light-managed-v1')


class LightGate(RepairLifecycle, DerivedRevision, BaselineRepair, Progress, LightArtifacts, LightReview):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.control = self.root / '.aidevflow'
        self.active = self.control / 'light-active.json'

    def path(self, value: str) -> Path:
        if not isinstance(value, str) or not value or Path(value).is_absolute() or PureWindowsPath(value).drive:
            raise GateError('Expected a project-relative path')
        path = (self.root / value).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise GateError('Path escapes project') from exc
        if path == self.root:
            raise GateError('Expected a file inside the project')
        return path

    def git(self, *args: str) -> str:
        try:
            p = subprocess.run(['git', '-c', 'core.fsmonitor=false', *args], cwd=self.root,
                               capture_output=True, encoding='utf-8', errors='replace', timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError('Local Git check failed or timed out; preserve the checkpoint and inspect repository health') from exc
        if p.returncode:
            raise GateError('Git failed: ' + p.stderr.strip()[:500])
        return p.stdout.strip()

    def load(self):
        pointer = read_json(self.active)
        ident = pointer.get('run_id', '')
        if not re.fullmatch(r'LIGHT-[a-f0-9]{24}', ident):
            raise GateError('Invalid light task pointer')
        run = self.control / 'light-runs' / ident
        # Immutable event snapshots recover a process killed before state projection.
        events = sorted((run / 'events').glob('*.json'))
        if not events:
            raise GateError('Light task has no committed checkpoint')
        state = read_json(events[-1])['state']
        if state.get('run_id') != ident or state.get('schema') != 'aidevflow/light-run-v2':
            raise GateError('Invalid light checkpoint')
        return state, run

    def save(self, state, run, event):
        state['revision'] += 1
        state['updated_at'] = utc_now()
        write_json_new(run / 'events' / f"{state['revision']:08d}.json", {'event': event, 'state': state})
        write_json_atomic(run / 'state.json', state)
        self.project(state)

    def project(self, state):
        base = self.path(state['directory'])
        if not base.exists():
            return
        if state.get('requirements'):
            from ...requirements import render_intent
            value = state['requirements']['value']
            (base / 'intent.md').write_text(render_intent(state['run_id'], state['slug'], value['intent']), encoding='utf-8', newline='\n')
            (base / 'prd.md').write_text(value['prd'], encoding='utf-8', newline='\n')
        if state.get('spec'):
            content = state['spec']['content']
            status = '已依据批准需求登记'
            (base / 'spec.md').write_text(f"状态：{status}\n\n{content}\n", encoding='utf-8', newline='\n')
        if not state.get('tasks'):
            return
        lines = [f"# 计划：{state['slug']}", '', '## 状态', f"- 阶段：{state['stage']}",
                 f"- 下一步：{state['tasks'][state['cursor']]['id'] if state['cursor'] < len(state['tasks']) else '无'}",
                 '- 规格引用：spec.md', '', '## 任务']
        for task in state['tasks']:
            lines += ['', f"- [{'x' if task.get('completed') else ' '}] {task['id']} {task['title']} "
                      + ' '.join(f'[{s}]' for s in task['specs']),
                      f"  - 验证：`{json.dumps(task['argv'], ensure_ascii=False)}` → {'FAIL' if task['kind'] == 'red' else 'PASS'}",
                      f"  - 修改：{', '.join(task['files']) or '无'}"]
            if task.get('regression'):
                lines.append('  - 回归：`' + json.dumps(task['regression'], ensure_ascii=False) + '`')
            if task.get('result'):
                r = task['result']
                lines.append(f"  - 结果：{r['status']}；退出码 {r['exit_code']}；证据 {r['path']}")
        if state['stage'] == 'needs_attention':
            lines += ['', '## 异常', '- 原因：' + state.get('attention_reason', '执行失败；保留证据并查看 status.recovery。')]
        if state.get('completion'):
            lines += ['', '## 完成', f"- 时间：{state['completion']['at']}",
                      f"- 提交：{state['completion']['commit']}", '- 覆盖规格：' + '、'.join(state['spec']['ids'])]
        (base / 'plan.md').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')

    def integrity(self, state):
        manifest = read_json(self.control / 'light-install-manifest.json')
        for rel, expected in manifest['files'].items():
            path = self.path(rel)
            if not path.is_file() or sha256_file(path) != expected:
                raise GateError('Installed light runtime changed: ' + rel + '; reinstall before continuing')
        historical = [t for h in [*state.get('baseline_history', []), *state.get('execution_history', [])] for t in h['tasks']]
        for task in [*state.get('tasks', []), *historical]:
            result = task.get('result')
            if result:
                evidence = self.path(result['path'])
                if not evidence.is_file() or sha256_file(evidence) != result['sha256']:
                    raise GateError('执行证据已被修改，保留任务并诊断：' + result['path'])
                for record in read_json(evidence).get('records', []):
                    for blob in record.get('evidence', []):
                        if sha256_file(self.path(blob['path'])) != blob['sha256']:
                            raise GateError('检查的物理证据已被修改：' + blob['path'])
        requirement_files = {rel: sha for h in [*state.get('requirements_history', []), state] for rel, sha in h.get('requirements', {}).get('files', {}).items()}
        for rel, expected in requirement_files.items():
            if not self.path(rel).is_file() or sha256_file(self.path(rel)) != expected:
                raise GateError('Intent/PRD 固定快照被修改')
        if state.get('approval') and state['approval']['sha256'] != state['requirements']['sha256']:
            raise GateError('批准的需求版本已改变')
        base = self.path(state['directory'])
        for name in ('intent.md', 'prd.md', 'spec.md', 'plan.md'):
            # Rendered state is protected by the extension; compare definitions on every operation.
            path = base / name
            if name in state.get('document_hashes', {}) and (not path.is_file() or state['document_hashes'][name] != sha256_file(path)):
                raise GateError(name + ' changed outside Gate; preserve it and use the revision operation')

    def checkpoint(self, state, run, event):
        self.settle_operation_failure(state, event)
        self.record_native_progress(state, run, event)
        self.project(state)
        state['document_hashes'] = {p.name: sha256_file(p) for p in self.path(state['directory']).glob('*.md')
                                    if p.name in ('intent.md', 'prd.md', 'spec.md', 'plan.md')}
        self.save(state, run, event)

    def candidate_files(self):
        names = self.git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split('\0')
        return sorted({n for n in names if n and n not in ('scripts/aidevflow.py', 'scripts/aidevflow_bridge.py')
                       and not n.startswith(('.aidevflow/', '.zscode/', 'specs/active/', 'specs/archive/', 'specs/drafts/'))})

    def candidate(self):
        files = {n: sha256_file(self.path(n)) if self.path(n).is_file() else None for n in self.candidate_files()}
        return sha256_bytes(json.dumps(files, sort_keys=True).encode())

    def status(self):
        if not self.active.is_file():
            return {'profile': PROFILE, 'active_run': None, 'next_action': '讨论需求，生成 Intent/PRD 后人工审查'}
        state, _ = self.load()
        task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else None
        fresh = state.get('final_candidate') == self.candidate() if state.get('final_candidate') else None
        skills = {'discovery': 'zsspec-init' if not (self.control / 'light-knowledge.json').exists() else None, 'spec': 'zsspec-spec', 'plan': 'zsspec-plan', 'applying': 'zsspec-apply',
                  'repairing': 'zsspec-apply', 'verify': 'zsspec-apply', 'delivery_ready': None, 'done': 'zsspec-archive', 'finalizing': 'zsspec-archive'}
        route = self.baseline_route(state) if state.get('baseline_review') else None
        result = {**state, 'blocking_questions': self.blocking_questions(state), 'current_task': task, 'regression_fresh': fresh, 'skill': skills.get(state['stage']),
                'next_action': {'discovery': 'requirements_context 派发需求分析；完成后 submit requirements 结果路径，等待入口审查',
                                'spec': '依据已批准 Intent/PRD 自动生成 light 规格及验收映射，无需再次人工批准',
                                'plan': '自动提交 TDD 计划', 'applying': '当前 RED 先 prepare_baseline 并原生审查，再 run；GREEN 失败先诊断，冻结测试有错用 repair_propose，不能直接改写',
                                'repairing': 'baseline_context 派发原生基线审查；approve 后 repair_apply，隔离重放 RED 并重新执行 GREEN/全量回归',
                                'verify': '自动 prepare_verifier 并通过原生 task 派发独立验收，不可自签结论',
                                'delivery_ready': '独立验收已通过，自动展示最终产物审查；flow 可重新打开',
                                'needs_attention': '异常阻塞或自主修复预算耗尽，保留证据并说明具体原因',
                                'done': '自动 archive，应用已经验收的知识提案并归档',
                                'paused': '在 flow 中输入继续即可恢复', 'finalizing': '自动恢复 archive 事务'}.get(state['stage'], '查看状态')}
        if route and (state['stage'] == 'repairing' or state['stage'] == 'applying' and task and task['kind'] == 'red'):
            result['review_route'] = route
            result['next_action'] = {'review_required': 'baseline_context 获取唯一原生审查任务并派发',
                'reviewing': '等待当前原生审查完成；未知结果先恢复，不重新派发',
                'approved': route['next_action'] + '；当前候选已通过审查，不再派发相同审查',
                'revise': route['next_action'] + '；先按实际审查意见修改材料',
                'correction_required': '修正 recovery 中失败证据对应的草稿，再 repair_propose；不重审旧提案',
                'consumed': result['next_action']}.get(route['phase'], result['next_action'])
        return result

    @writes
    def create(self, slug: str, request: str):
        if self.active.exists() or (self.control / 'active-run.json').exists() or (self.control / 'active-discovery.json').exists():
            raise GateError('已有活动变更，请先继续或明确放弃')
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+){1,4}', slug) or not request.strip():
            raise GateError('slug 必须为 2–5 个小写单词；需求不能为空')
        self.git('rev-parse', '--verify', 'HEAD')
        if self.path('specs/active').exists() and any(self.path('specs/active').iterdir()):
            raise GateError('specs/active 存在未登记变更，先处理其归属')
        index = self.path('specs/archive/INDEX.md')
        if index.exists() and re.search(r'\|\s*' + re.escape(slug) + r'\s*\|', index.read_text(encoding='utf-8')):
            raise GateError('slug 已归档，请使用新的 slug')
        ident = 'LIGHT-' + secrets.token_hex(12)
        run = self.control / 'light-runs' / ident
        directory = f'specs/active/{slug}'
        self.path(directory).mkdir(parents=True)
        self.path(f'specs/drafts/{ident}').mkdir(parents=True)
        state = {'schema': 'aidevflow/light-run-v2', 'run_id': ident, 'profile': PROFILE, 'runtime': 'zscode',
                 'slug': slug, 'request': request, 'directory': directory, 'revision': 0, 'stage': 'discovery', 'base_commit': self.git('rev-parse', 'HEAD'),
                 'tasks': [], 'cursor': 0, 'baselines': {}, 'created_at': utc_now()}
        # A task has a committed source before the active pointer becomes visible.
        self.checkpoint(state, run, 'created')
        write_json_atomic(self.active, {'run_id': ident})
        return self.status()

    @writes
    def spec(self, content: str, acceptance_map: dict, design=None):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] not in ('spec', 'plan') or not state.get('approval'):
            raise WorkflowError('DERIVED_REVISION_REQUIRED', '规格执行中不可改写；请使用 repair_propose 提交派生修订，保留批准需求和冻结基线')
        state['spec'] = self.spec_definition(state, content, acceptance_map, design)
        state.update(tasks=[], cursor=0, stage='plan')
        self.checkpoint(state, run, 'spec_submitted')
        return self.status()

    def spec_definition(self, state, content, acceptance_map, design=None):
        for heading in ('需求', '规格', '已剔除', '知识缺口', 'knowledge 修正项', '冲突声明'):
            if f'## {heading}' not in content:
                raise GateError('规格缺少章节：' + heading)
        ids = re.findall(r'^### (S\d+)\b', content, re.M)
        if not ids or len(set(ids)) != len(ids):
            raise GateError('规格需要唯一 S1、S2 等编号')
        for block in re.split(r'^### S\d+\b', content, flags=re.M)[1:]:
            if not all(term in block for term in ('WHEN', 'THE SYSTEM SHALL', '验收：', '覆盖需求：')):
                raise GateError('每条规格需要 WHEN/SHALL、验收及覆盖需求')
        conflict = content.split('## 冲突声明', 1)[1].split('\n## ', 1)[0].strip()
        if conflict != '无':
            raise GateError('冲突声明尚未解决，不能提交待批准规格')
        approved = {a for req in state['requirements']['value']['requirements'] for a in req['acceptance_ids']}
        if not isinstance(acceptance_map, dict) or set(acceptance_map) != set(ids) or any(
                not isinstance(v, list) or not v or any(not isinstance(x, str) for x in v) or set(v) - approved for v in acceptance_map.values()
                ) or {a for v in acceptance_map.values() for a in v} != approved:
            raise GateError('规格必须精确映射并完整覆盖 Intent/PRD 已批准的验收 ID')
        definition = {'content': content.strip(), 'ids': ids, 'sha256': sha256_bytes(content.strip().encode()),
                         'acceptance_map': acceptance_map, 'requirements_sha256': state['approval']['sha256']}
        if design is not None:
            self.validate_design(design)
            definition['design'] = design
            definition['sha256'] = digest(definition)
        return definition

    @staticmethod
    def command(argv):
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x or '\x00' in x for x in argv):
            raise GateError('验证命令必须为非空 argv 字符串数组')

    @writes
    def plan(self, tasks: list):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] != 'plan':
            raise WorkflowError('DERIVED_REVISION_REQUIRED', '计划执行中不可改写；请使用 repair_propose 提交派生修订')
        clean = self.plan_definition(state, tasks)
        state.update(tasks=clean, stage='applying', cursor=0)
        self.checkpoint(state, run, 'plan_registered')
        return self.status()

    def plan_definition(self, state, tasks):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 20:
            raise GateError('计划需要 1–20 个原子任务')
        ids, covered, groups = set(), set(), {}
        clean = []
        all_tests = set()
        file_key = lambda f: f.casefold() if os.name == 'nt' else f
        for i, task in enumerate(tasks):
            if not isinstance(task, dict) or task.get('kind') not in ('prepare', 'red', 'green', 'refactor', 'regression'):
                raise GateError('任务 kind 必须为 prepare/red/green/refactor/regression')
            ident = task.get('id', '')
            if not re.fullmatch(r'\d+\.\d+', ident) or ident in ids or not task.get('title', '').strip():
                raise GateError('任务需要唯一数字编号与标题')
            ids.add(ident)
            refs = task.get('specs')
            if not isinstance(refs, list) or not refs or set(refs) - set(state['spec']['ids']):
                raise GateError('每项任务必须引用已批准的 Sx')
            covered.update(refs)
            self.command(task.get('argv'))
            files = task.get('files')
            if not isinstance(files, list) or any(not isinstance(f, str) for f in files):
                raise GateError('任务需要精确 files 列表')
            for f in files:
                canonical = self.path(f).relative_to(self.root).as_posix()
                if f != canonical: raise GateError('任务文件使用规范项目相对路径，不允许别名、反斜线或 ..：' + f)
                reserved = canonical.casefold()
                if reserved.split('/')[0] in ('.aidevflow', '.zscode', '.git', 'specs') or reserved.startswith('scripts/aidevflow'):
                    raise GateError('任务不能修改受管资源')
            checks = task.get('checks', [])
            if checks and task['kind'] != 'regression': raise GateError('附加验收检查只能登记在最终回归任务')
            if not isinstance(checks, list) or len(checks) > 20: raise GateError('checks 必须为至多20项数组')
            check_ids = set()
            for check in checks:
                if not isinstance(check, dict) or set(check) != {'id', 'kind', 'specs', 'argv', 'evidence_paths'}:
                    raise GateError('检查需要 id/kind/specs/argv/evidence_paths')
                if not isinstance(check['id'], str) or not check['id'] or check['id'] in check_ids or check['kind'] not in ('http', 'browser', 'static'):
                    raise GateError('检查需要唯一 ID 与 http/browser/static kind')
                check_ids.add(check['id']); self.command(check['argv'])
                if not isinstance(check['specs'], list) or not check['specs'] or set(check['specs']) - set(state['spec']['ids']): raise GateError('检查必须引用已批准规格')
                if not isinstance(check['evidence_paths'], list) or not check['evidence_paths']:
                    raise GateError('附加检查需要非空 evidence_paths，由 runner 每次生成真实证据')
                for rel in check['evidence_paths']: self.check_evidence_path(state, rel)
            kind, group = task['kind'], task.get('group', '')
            if kind == 'prepare':
                if groups or not files:
                    raise GateError('环境准备必须在全部 RED 之前，明确声明支持文件，不承担验收断言')
            elif kind == 'regression':
                if i != len(tasks) - 1 or files:
                    raise GateError('全量回归必须是最后一项，files 为空')
            elif not group or not files:
                raise GateError('RED/GREEN/REFACTOR 需要 group 和精确修改文件')
            if kind == 'red':
                if group in groups:
                    raise GateError('每个 group 只能有一个 RED')
                if all_tests.intersection(map(file_key, files)):
                    raise GateError('首版按工作项使用独立测试文件，不能覆盖前序测试基线')
                groups[group] = {'argv': task['argv'], 'files': files, 'green': False}
                all_tests.update(map(file_key, files))
            elif kind in ('green', 'refactor'):
                if group not in groups or task['argv'] != groups[group]['argv']:
                    raise GateError('GREEN/REFACTOR 必须关联前序 RED 并使用相同验证命令')
                if set(map(file_key, files)).intersection(all_tests):
                    raise GateError('实现和重构不能修改已冻结测试')
                if kind == 'refactor' and not groups[group]['green']:
                    raise GateError('REFACTOR 必须在 GREEN 之后')
                groups[group]['green'] = True
                self.command(task.get('regression'))
            clean.append({k: task[k] for k in ('id', 'title', 'kind', 'argv', 'files', 'specs', 'group', 'regression', 'checks') if k in task})
        if covered != set(state['spec']['ids']) or not groups or any(not g['green'] for g in groups.values()) or clean[-1]['kind'] != 'regression':
            raise GateError('计划必须覆盖全部规格、完整 RED/GREEN，并以全量回归结束')
        all_impl = {file_key(f) for t in clean if t['kind'] in ('green', 'refactor') for f in t['files']}
        if all_impl.intersection(all_tests):
            raise GateError('测试文件不能同时声明为实现文件')
        self.validate_layout(state['spec'], clean)
        return clean

    def check_evidence_path(self, state, rel):
        p = self.path(rel)
        base = self.path(f"specs/drafts/{state['run_id']}/evidence")
        if p == base or not p.is_relative_to(base) or rel != p.relative_to(self.root).as_posix():
            raise GateError('检查证据必须位于当前 run 的 specs/drafts/<run_id>/evidence/ 内')
        return p

    def baselines(self, state):
        for group, files in state['baselines'].items():
            for rel, expected in files.items():
                p = self.path(rel)
                if not p.is_file() or sha256_file(p) != expected:
                    raise GateError(f'冻结测试已修改：{group} {rel}')

    @writes
    def retry(self, reason: str):
        state, run = self.load()
        self.integrity(state)
        task = state['tasks'][state['cursor']] if state['cursor'] < len(state['tasks']) else {}
        if state['stage'] == 'repairing' and state.get('repair_failure') and task.get('kind') == 'red' and not task.get('completed'):
            self.no_inflight(state)
            if (run / 'baseline-repair-transaction.json').exists(): raise GateError('先恢复原换版事务')
            state['stage'] = 'applying'
            state.setdefault('repair_failure_history', []).append(state.pop('repair_failure'))
        if state['stage'] != 'applying' or not reason.strip():
            if state['stage'] == 'repairing': raise GateError('当前正在修订：审查 approve 后调用 repair_apply；retry 只用于 applying 阶段尚未通过的 RED 准备，不可通过改写 reason 切换阶段')
            raise GateError('只可在授权执行范围内说明诊断原因后重试')
        task = state['tasks'][state['cursor']]
        if task.get('inflight') or not task.get('result') or task['result']['status'] != 'failed':
            raise GateError('需要已结束的真实失败结果，未知执行不可重派')
        if task['kind'] != 'red':
            raise GateError('retry 只用于未通过 RED；实现失败请修改当前 files 后 run')
        if task['kind'] == 'red':
            self.baselines(state)
            state['baselines'].pop(task['group'], None)
            state.pop('baseline_review', None)
        task['diagnosis'] = reason
        self.checkpoint(state, run, 'autonomous_failure_diagnosed')
        return self.status()

    @writes
    def run(self):
        state, run = self.load()
        self.integrity(state)
        failure_limits = self.failure_limits()
        if state['stage'] in ('verify', 'delivery_ready') and state.get('final_candidate') != self.candidate():
            state.update(stage='applying', cursor=len(state['tasks']) - 1)
            state['tasks'][-1].pop('completed', None)
        if state['stage'] != 'applying':
            raise GateError('当前阶段不能执行任务')
        task = state['tasks'][state['cursor']]
        if task.get('inflight'):
            raise GateError('上次执行结果未知；确认旧进程已结束后运行原生恢复命令')
        self.baselines(state)
        if task['kind'] == 'red':
            missing = [f for f in task['files'] if not self.path(f).is_file()]
            if missing:
                raise GateError('先编写当前 RED 测试：' + ', '.join(missing))
            self.check_test_targets(state, task['group'])
            self.require_baseline_review(state, task)
            baseline = {f: sha256_file(self.path(f)) for f in task['files']}
            previous = state['baselines'].get(task['group'])
            if previous and previous != baseline:
                raise GateError('RED 重试不能改写已经登记的测试')
            state['baselines'][task['group']] = baseline
        ident = secrets.token_hex(16)
        task['inflight'] = ident
        task['attempts'] = task.get('attempts', 0) + 1
        before = self.candidate()
        self.checkpoint(state, run, 'execution_started')
        records = []
        commands = [{'argv': task['argv']}] + ([{'argv': task['regression']}] if task.get('regression') else []) + task.get('checks', [])
        for check in commands:
            argv = check['argv']
            for rel in check.get('evidence_paths', []):
                self.check_evidence_path(state, rel).unlink(missing_ok=True)
            started = utc_now()
            try:
                p = subprocess.run(argv, cwd=self.root, capture_output=True, encoding='utf-8', errors='replace',
                                   timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                code, output = p.returncode, p.stdout + p.stderr
            except subprocess.TimeoutExpired as exc:
                code, output = 124, 'Execution timed out\n' + str(exc.stdout or '') + str(exc.stderr or '')
            except OSError as exc:
                code, output = 127, 'Could not start test command: ' + str(exc)
            expected_red = task['kind'] == 'red' and not records
            assertion = target_assertion_failure(output)
            omitted = omitted_tests(output)
            ok = (valid_red_result(code, output) and assertion and not omitted) if expected_red else (code == 0 and not omitted)
            blobs = []
            if ok:
                for rel in check.get('evidence_paths', []):
                    asset = self.check_evidence_path(state, rel)
                    if not asset.is_file() or not 0 < asset.stat().st_size <= 20 * 1024 * 1024:
                        ok = False; output += '\n检查缺少本轮生成的物理证据：' + rel
                        continue
                    data = asset.read_bytes(); sha = sha256_bytes(data)
                    blob = run / 'check-objects' / sha
                    if not blob.exists(): write_bytes_new(blob, data)
                    blobs.append({'source': rel, 'path': blob.relative_to(self.root).as_posix(), 'sha256': sha})
            records.append({'check_id': check.get('id'), 'kind': check.get('kind'), 'evidence': blobs, 'argv': argv, 'exit_code': code, 'output': output[-200000:],
                            'passed': ok, 'counts': test_counts(output), 'started_at': started, 'finished_at': utc_now()})
            if not ok:
                break
        after = self.candidate()
        ok = all(r['passed'] for r in records) and before == after
        try:
            self.baselines(state)
        except GateError:
            ok = False
        evidence = {'id': ident, 'task': task['id'], 'candidate': after, 'candidate_changed': before != after,
                    'baselines': state['baselines'], 'records': records, 'passed': ok}
        evidence_path = run / 'executions' / (ident + '.json')
        write_json_new(evidence_path, evidence)
        task.pop('inflight')
        task['result'] = {'status': 'passed' if ok else 'failed', 'exit_code': records[-1]['exit_code'],
                          'path': evidence_path.relative_to(self.root).as_posix(), 'sha256': sha256_file(evidence_path),
                          'counts': records[-1]['counts']}
        if ok and task['kind'] == 'regression':
            task['consecutive_failures'] = 0
            task['completed'] = True
            state.update(stage='verify', final_candidate=after, cursor=len(state['tasks']))
        elif ok:
            if task['kind'] == 'red': self.capture_red_origin(state, run, task['group'])
            task['consecutive_failures'] = 0
            task['completed'] = True
            state['cursor'] += 1
            while state['cursor'] < len(state['tasks']) and state['tasks'][state['cursor']].get('completed'):
                state['cursor'] += 1
        else:
            task['consecutive_failures'] = task.get('consecutive_failures', 0) + 1
            if task['kind'] == 'regression':
                state.pop('final_candidate', None)
                state['stage'] = 'applying'
            if task['consecutive_failures'] >= failure_limits['execution_failures']:
                state['stage'] = 'needs_attention'
                state['attention_stage'] = 'applying'
                state['attention_kind'] = 'execution_failure'
                state['attention_reason'] = f"任务 {task['id']} 连续执行失败 {task['consecutive_failures']}/{failure_limits['execution_failures']} 次；已保留真实执行证据"
        if not ok and task['kind'] == 'regression':
            state['repair_evidence'] = evidence_path.relative_to(self.root).as_posix()
            self.route_repair(state, '全量回归失败；修复实现后重新取得 GREEN 和回归证据', source='regression')
        self.checkpoint(state, run, 'execution_finished')
        return self.status()

    @writes
    def report_conflict(self, reason):
        state, run = self.load()
        self.integrity(state)
        if not isinstance(reason, str) or not reason.strip(): raise GateError('业务冲突需要具体原因')
        if state['stage'] not in ('spec', 'plan', 'applying', 'repairing', 'verify'):
            raise GateError('当前阶段不能报告开发业务冲突')
        self.no_inflight(state)
        state.update(attention_stage=state['stage'], stage='needs_attention',
            attention_kind='business_decision', attention_reason=reason)
        self.checkpoint(state, run, 'business_conflict_reported')
        return self.summary()

    @writes
    def pause(self):
        state, run = self.load()
        if state['stage'] not in ('done', 'finalizing', 'paused'):
            state['resume_stage'] = state['stage']
            state['stage'] = 'paused'
            self.checkpoint(state, run, 'user_paused')
        return self.status()

    @writes
    def resume(self, by: str):
        state, run = self.load()
        self.integrity(state)
        if state['stage'] == 'paused':
            state['stage'] = state.pop('resume_stage')
        elif state['stage'] == 'needs_attention':
            if state.get('repair_rounds', 0) > self.failure_limits()['repair_rounds']:
                state.setdefault('repair_budget_history', []).append({'rounds': state['repair_rounds'], 'by': by, 'at': utc_now()})
                state['repair_rounds'] = 0
            state['stage'] = state.pop('attention_stage', 'verify' if state['cursor'] == len(state['tasks']) else 'applying')
        elif state['stage'] not in ('applying', 'verify', 'repairing'):
            return self.status()
        if state.get('verifier_invocation'):
            path = self.path(state['verifier_invocation'])
            receipt = read_json(path)
            if not receipt.get('execution_finished'):
                receipt.update(status='user_stopped_unknown', execution_finished=True,
                               stopped_by=by, termination_source='user_confirmed_old_execution_ended')
                write_json_atomic(path, receipt)
        state['verification_attempts'] = 0
        state['baseline_attempts'] = 0
        (run / 'progress.json').unlink(missing_ok=True)
        if state.get('baseline_invocation'):
            path = self.path(state['baseline_invocation'])
            receipt = read_json(path)
            if not receipt.get('execution_finished'):
                receipt.update(status='user_stopped_unknown', execution_finished=True, stopped_by=by)
                write_json_atomic(path, receipt)
        if state.get('operation_failure'):
            state.setdefault('operation_failure_history', []).append({**state.pop('operation_failure'),
                'recovered_event': 'user_resumed', 'by': by, 'revision': state['revision'] + 1})
        state.pop('attention_reason', None)
        state.pop('attention_kind', None)
        if state.get('analyst_invocation'):
            path = self.path(state['analyst_invocation'])
            receipt = read_json(path)
            if not receipt.get('execution_finished'):
                receipt.update(status='user_stopped_unknown', execution_finished=True, stopped_by=by)
                write_json_atomic(path, receipt)
        if state['cursor'] < len(state['tasks']):
            task = state['tasks'][state['cursor']]
            # Explicit user recovery authorizes another bounded episode; history remains immutable.
            task['consecutive_failures'] = 0
            if task['kind'] == 'red' and task.get('result', {}).get('status') == 'failed':
                # Human-authorized preparation retry affects only an uncompleted RED.
                state['baselines'].pop(task['group'], None)
            task.pop('inflight', None)
        state['last_resume_by'] = by
        self.checkpoint(state, run, 'user_resumed_after_old_execution_ended')
        return self.status()

    @writes
    def submit_knowledge(self, files):
        state, run = self.load()
        self.validate_knowledge(files)
        if state['stage'] not in ('discovery', 'spec', 'plan', 'applying', 'repairing', 'verify'):
            raise GateError('当前阶段不接受知识提案')
        if not (self.control / 'light-knowledge.json').is_file() and state['stage'] in ('discovery', 'spec'):
            return self.knowledge(files)
        state['knowledge_proposal'] = {**state.get('knowledge_proposal', {}), **files}
        self.checkpoint(state, run, 'knowledge_proposal_updated')
        return self.status()

    @writes
    def knowledge(self, files: dict):
        if self.active.exists() and self.load()[0]['stage'] not in ('discovery', 'spec'):
            raise GateError('知识初始化只在需求入口或首次初始化时执行，交付后通过 archive 更新')
        self.validate_knowledge(files)
        for rel, content in files.items():
            p = self.path(rel)
            if p.exists() and p.read_text(encoding='utf-8') != content:
                raise GateError('初始化不能覆盖已有知识：' + rel)
        for rel, content in files.items():
            p = self.path(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8', newline='\n')
        self.path('specs/active').mkdir(parents=True, exist_ok=True)
        index = self.path('specs/archive/INDEX.md')
        index.parent.mkdir(parents=True, exist_ok=True)
        if not index.exists():
            index.write_text('| 完成日期 | slug | 一句话 | 提交 | 状态 |\n|---|---|---|---|---|\n', encoding='utf-8', newline='\n')
        reviewed = True  # Knowledge is automatically cross-checked against source and the final verifier bundle.
        write_json_atomic(self.control / 'light-knowledge.json', {'reviewed': reviewed})
        return {'initialized': True, 'needs_initial_review': not reviewed}

    def validate_knowledge(self, files):
        if not isinstance(files, dict) or any(not isinstance(v, str) for v in files.values()):
            raise GateError('知识更新需要 path 到 Markdown 内容的对象')
        for rel, content in files.items():
            p = self.path(rel)
            normalized = p.relative_to(self.root).as_posix()
            if normalized != rel:
                raise GateError('知识路径需要规范的正斜杠相对路径')
            if normalized != 'specs/MAP.md' and not (normalized.startswith('specs/modules/') and p.suffix == '.md'):
                raise GateError('只能更新 MAP.md 和 modules/*.md')
            if len(content.splitlines()) > (30 if normalized == 'specs/MAP.md' else 200):
                raise GateError('知识文档超过 MAP 30 行或模块 200 行限制')

    @writes
    def archive(self, files: dict | None = None, abandoned: bool = False, by: str = ''):
        state, run = self.load()
        if abandoned and not by:
            raise GateError('废弃变更需要实际用户操作')
        files = state.get('knowledge_proposal', {}) if files is None else files
        tx_path = run / 'archive-transaction.json'
        if tx_path.exists():
            tx = read_json(tx_path)
        else:
            self.integrity(state)
            if not abandoned:
                if state['stage'] != 'done' or state['completion']['candidate'] != self.candidate():
                    raise GateError('只可归档已确认且未发生业务变化的变更')
                self.baselines(state)
                self.validate_knowledge(files)
                if files != state.get('knowledge_proposal', {}):
                    raise GateError('归档知识必须等于独立验收和人工验收绑定的提案')
            elif files:
                raise GateError('废弃变更不沉淀知识')
            today = dt.datetime.now().astimezone().strftime('%Y-%m-%d')
            target = f"specs/archive/{state['slug']}_{today}"
            if self.path(target).exists():
                raise GateError('归档目录已存在，禁止覆盖')
            tx = {'target': target, 'files': files, 'abandoned': abandoned,
                  'before': {f: sha256_file(self.path(f)) if self.path(f).exists() else None for f in files},
                  'row': f"| {today} | {state['slug']} | {state['request'].splitlines()[0].replace('|', '/')} | "
                         + ('—' if abandoned else state['completion']['commit'][:12]) + f" | {'abandoned' if abandoned else 'done'} |"}
            write_json_new(tx_path, tx)
            state['stage'] = 'finalizing'
            self.checkpoint(state, run, 'archive_started')
        for rel, content in tx['files'].items():
            p = self.path(rel)
            actual = sha256_file(p) if p.is_file() else None
            if actual not in (tx['before'][rel], sha256_bytes(content.encode())):
                raise GateError('知识文件在归档事务中被外部修改：' + rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding='utf-8', newline='\n')
        src, dst = self.path(state['directory']), self.path(tx['target'])
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.exists() and not dst.exists():
            os.rename(src, dst)
        elif src.exists() or not dst.exists():
            raise GateError('归档目录状态冲突，保留事务并诊断')
        index = self.path('specs/archive/INDEX.md')
        text = index.read_text(encoding='utf-8') if index.exists() else '| 完成日期 | slug | 一句话 | 提交 | 状态 |\n|---|---|---|---|---|\n'
        if tx['row'] not in text.splitlines():
            index.write_text(text.rstrip() + '\n' + tx['row'] + '\n', encoding='utf-8', newline='\n')
        state.update(stage='abandoned' if tx['abandoned'] else 'archived', directory=tx['target'])
        self.checkpoint(state, run, 'archive_completed')
        self.active.unlink()
        return {'stage': state['stage'], 'archive': tx['target'], 'run_id': state['run_id']}

    def guard(self, tool: str, inp: dict, actor: dict | None = None):
        if (actor or {}).get('name') == ANALYST:
            if tool == 'yield': return {'block': False}
            try:
                raw = re.sub(r':\d+(?:-\d+)?$', '', inp.get('path', '.'))
                target = (self.root / raw).resolve()
                safe = tool in ('read', 'grep', 'glob') and target.is_relative_to(self.root)
            except (TypeError, ValueError, OSError): safe = False
            return {'block': not safe, 'reason': '需求分析角色只读项目内文件，不得写入、批准或委派'}
        if (actor or {}).get('name') in ('aidevflow-independent-verifier', REVIEWER):
            state, _ = self.load()
            if tool == 'yield':
                return self.guard_verifier_yield(inp) if actor['name'] == 'aidevflow-independent-verifier' else {'block': False}
            try:
                key = 'baseline_review' if actor['name'] == REVIEWER else 'verifier_bundle'
                bundle = self.path(state[key]['path'])
                safe = tool == 'read' and isinstance(inp.get('path'), str) and self.path(inp['path']).is_relative_to(bundle)
            except (GateError, KeyError, TypeError): safe = False
            return {'block': not safe, 'reason': '' if safe else '独立验收只能读取固定 bundle，禁止修改和旁路上下文'}
        if tool in READ_TOOLS:
            if tool in ('read', 'grep', 'glob') and self.active.exists():
                raw = inp.get('path', '.')
                if isinstance(raw, str):
                    raw = re.sub(r':(?:\d+(?:-\d+)?(?::raw)?|raw)$', '', raw)
                    target = (self.root / raw).resolve()
                    state, run = self.load()
                    internal = target.is_relative_to(self.control / 'runtime') or target.is_relative_to(run / 'events') or target in (self.control / 'light-runs', run, run / 'state.json', run / 'progress.json')
                    if internal:
                        return {'block': True, 'reason': '运行事实使用 aidevflow_light status 查询；修订模板、group、冻结文件和 schema 由 status.recovery 提供。请直接准备草稿并 repair_propose，不扫描运行时代码或历史状态。'}
            return {'block': False}
        if tool == 'aidevflow_light':
            return {'block': False}
        if not self.active.exists():
            return {'block': tool not in ('read', 'grep', 'glob', 'ask'),
                    'reason': '先使用 aidevflow_light 初始化知识或创建变更'}
        state, _ = self.load()
        self.integrity(state)
        paths = []
        if tool in ('write', 'edit', 'apply_patch'):
            if inp.get('path') or inp.get('file_path'):
                paths.append(inp.get('path') or inp['file_path'])
            raw = inp.get('input', inp.get('_input'))
            if isinstance(raw, str):
                for line in raw.splitlines():
                    if re.match(r'^\*\*\* (Add File|Update File|Delete File|Move to): ', line):
                        paths.append(line.split(': ', 1)[1])
                    elif re.fullmatch(r'\[.+#[0-9A-Fa-f]{4}\]', line):
                        paths.append(line[1:-6])
                    elif line.startswith('MV '):
                        paths.append(line[3:])
            for item in inp.get('edits', []):
                if isinstance(item, dict) and item.get('rename'):
                    paths.append(item['rename'])
        try:
            draft = self.path(f"specs/drafts/{state['run_id']}")
            if state['stage'] in ('discovery', 'spec', 'plan', 'applying', 'repairing', 'verify', 'needs_attention') and paths and all(self.path(p).is_relative_to(draft) for p in paths):
                return {'block': False}
            if state['stage'] != 'applying':
                return {'block': True, 'reason': '当前阶段只允许读取、当前任务草稿和受管 light 操作'}
            task = state['tasks'][state['cursor']]
            allowed = {self.path(f) for f in task['files']}
            frozen = {self.path(f) for b in state['baselines'].values() for f in b}
            safe = bool(paths) and not task.get('inflight') and all(self.path(p) in allowed and self.path(p) not in frozen for p in paths)
        except (GateError, ValueError, TypeError):
            safe = False
        return {'block': not safe, 'reason': '' if safe else '仅允许修改当前任务声明的未冻结文件；验证使用 aidevflow_light run，不允许 shell/task/eval 绕过'}


def doctor(root: Path):
    gate = LightGate(root)
    errors = []
    try:
        manifest = read_json(gate.control / 'light-install-manifest.json')
        for rel, expected in manifest['files'].items():
            p = gate.path(rel)
            if not p.is_file() or sha256_file(p) != expected:
                errors.append('受管文件缺失或变化：' + rel)
    except (GateError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    executable = shutil.which('zscode')
    if not executable:
        errors.append('PATH 中没有 zscode')
    config = read_json(gate.control / 'project.json')
    if config.get('runtime') != 'zscode' or not Path(config.get('python', '')).is_file():
        errors.append('zscode runtime 或安装时固定的 Python 不可用')
    try:
        gate.git('rev-parse', '--verify', 'HEAD')
        baseline = True
    except GateError:
        baseline = False
    return {'ok': not errors, 'runtime': 'zscode', 'method_profile': PROFILE, 'executable': executable,
            'errors': errors, 'initial_commit': baseline, 'live_dispatch': 'not_probed',
            'environment': inspect_environment(root), 'active': gate.summary(), 'warnings': ['静态检查不等于真实模型与业务流程验收。']}
