"""End-to-end autonomy self-test: one driver plays the model role via the bridge
protocol and the native host roles, driving a zsspec v2.0 shaped delivery from
request to archive with three injected first-attempt failures (wrong parser for
GREEN, swallowed errors for a BUILD card, missing CLI for a BUILD card). Every
failure must be recovered autonomously from execution evidence, and human gates
must be exactly two.

Run: python -B tests/zscode_light_autonomy_demo.py
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from aidevflow.backends.zscode.installer import install
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.bridge import model_operation
from aidevflow.backends.zscode.review import VERIFIER
from aidevflow.storage import read_json, write_json_atomic

MAP = {'S1': ['AC-1'], 'S2': ['AC-2'], 'S3': ['AC-3']}
REQUIREMENTS = {'schema': 'aidevflow/requirements-v1',
    'intent': {'schema': 'aidevflow/intent-draft-v1', 'problem': '重复手算表达式容易出错',
               'users': ['终端使用者'], 'desired_outcome': '命令行可直接求值四则表达式',
               'scope': ['四则运算', '括号', '非法输入提示', 'CLI 入口'], 'non_goals': ['变量', '函数'],
               'constraints': ['Python 标准库'], 'success_criteria': ['1+2*3 求值为 7', '非法输入给出明确错误', 'python -m src 可用'],
               'assumptions': [], 'open_questions': [], 'decisions': []},
    'prd': '# PRD\n提供表达式求值：运算优先级正确、括号支持、非法输入明确报错、提供 CLI。',
    'requirements': [
        {'id': 'R1', 'statement': '按优先级求值含括号的四则表达式', 'source': 'request', 'acceptance_ids': ['AC-1']},
        {'id': 'R2', 'statement': '非法输入抛出带说明的错误', 'source': 'request', 'acceptance_ids': ['AC-2']},
        {'id': 'R3', 'statement': '提供 python -m src 命令行入口', 'source': 'request', 'acceptance_ids': ['AC-3']}],
    'scenarios': [{'id': 'SC1', 'requirement_ids': ['R1', 'R2', 'R3'], 'acceptance_ids': ['AC-1', 'AC-2', 'AC-3'],
                   'given': '安装了 src 包的项目', 'when': '调用 evaluate 或 CLI', 'then': '返回正确结果或明确错误'}]}

SPEC = '''# 规格：表达式求值器

## 需求

- **问题**：重复手算四则表达式容易出错且无法自动化
- **不做的后果**：下游脚本无法可靠求值，错误输入静默产生错误结果
- **期望结果**：evaluate 按优先级求值含括号的四则表达式；非法输入抛 CalcError；提供 CLI
- **约束**：Python 标准库实现，unittest 验证（用户点名测试锁定 S1）
- **受影响模块**：calc

## 边界

- 做：src/calc.py 求值与错误类型、src/__main__.py CLI 入口、tests/test_calc.py 验证
- 不做：变量、函数、浮点格式化（超出本变更范围）

## 规格

### S1 按优先级求值

- 做什么：evaluate(expr) 返回按乘除优先、加减次之、括号最内优先的数值结果
- 规则：
  - 调用 evaluate("1+2*3") → 返回 7
  - 调用 evaluate("(1+2)*3") → 返回 9
- 用例：
  | # | 场景 | 输入 | 期望 |
  |---|---|---|---|
  | 1 | 乘法优先 | 1+2*3 | 7 |
  | 2 | 括号提升 | (1+2)*3 | 9 |
- 验证：`python -m unittest discover -s tests` → 全绿（用户点名测试锁定）
- 依据：`src/calc.py::evaluate` · 覆盖：期望结果

### S2 非法输入报错

- 做什么：无法解析的表达式抛出 CalcError，消息含"非法"
- 规则：
  - 调用 evaluate("1+") → 抛 CalcError 且消息包含 非法
- 用例：
  | # | 场景 | 输入 | 期望 |
  |---|---|---|---|
  | 1 | 残缺表达式 | 1+ | CalcError(含 非法) |
- 验证：运行核验——导入并断言错误类型与消息
- 依据：`src/calc.py::CalcError` · 覆盖：期望结果

### S3 命令行入口

- 做什么：python -m src "表达式" 打印求值结果，退出码 0
- 规则：
  - 运行 python -m src "2*(3+4)" → 输出 14 且退出码 0
- 用例：
  | # | 场景 | 输入 | 期望 |
  |---|---|---|---|
  | 1 | 正常求值 | 2*(3+4) | 输出 14，退出码 0 |
- 验证：构建核验——实际运行 CLI 并检查退出码
- 依据：`src/__main__.py::main` · 覆盖：期望结果

## 设计

### 方案

递归下降解析：词法拆分数字与运算符，按 factor/term/expr 三层优先级求值；CLI 复用同一 evaluate。

### 模块与接口

- 修改：`src/calc.py` ← 依据：`src/calc.py`
- 新增：`src/__main__.py` ← 依据：`src/calc.py::evaluate`
- 验证：`tests/test_calc.py` ← 依据：`src/calc.py::evaluate`

### 数据与状态

无状态纯函数。

### 取舍

不引入外部解析库，标准库即可满足规模。

## 记录

### 知识维护

无

### 冲突声明

无
'''

TEST_CONTENT = '''import unittest
from src.calc import evaluate


class EvaluateTest(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(evaluate('1+2*3'), 7)

    def test_parentheses(self):
        self.assertEqual(evaluate('(1+2)*3'), 9)
'''

# 第一次尝试：忽略优先级（GREEN 会失败——注入失败 1）
CALC_BROKEN = '''class CalcError(Exception):
    pass


def evaluate(expr):
    import re
    tokens = re.findall(r'\\d+|[+*/()-]|\\S', expr)
    values, ops = [], []
    for t in tokens:
        if t.isdigit():
            values.append(int(t))
        else:
            ops.append(t)
    while ops:
        v1, v2 = values[0], values[1]
        op = ops[0]
        values = [v1 + v2 if op == '+' else v1 - v2 if op == '-' else v1 * v2 if op == '*' else v1 / v2 if v2 else 0] + values[2:]
        ops = ops[1:]
    if not values or ops:
        raise CalcError('非法表达式: ' + expr)
    return values[0]
'''

_PARSER = '''def _tokenize(expr):
    tokens, i = [], 0
    while i < len(expr):
        c = expr[i]
        if c.isspace():
            i += 1
        elif c.isdigit() or c == '.':
            j = i
            while j < len(expr) and (expr[j].isdigit() or expr[j] == '.'):
                j += 1
            text = expr[i:j]
            if text.count('.') > 1:
                raise CalcError('非法数字: ' + text)
            tokens.append(float(text) if '.' in text else int(text))
            i = j
        elif c in '+-*/()':
            tokens.append(c)
            i += 1
        else:
            raise CalcError('非法字符: ' + repr(c))
    return tokens


def _parse(expr):
    tokens = _tokenize(expr)
    if not tokens:
        raise CalcError('非法表达式: ' + repr(expr))
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        value = tokens[pos]
        pos += 1
        return value

    def factor():
        t = peek()
        if t == '(':
            take()
            value = expression()
            if peek() != ')':
                raise CalcError('非法表达式: 括号不匹配')
            take()
            return value
        if isinstance(t, (int, float)):
            return take()
        raise CalcError('非法表达式: 期望数字或左括号')

    def term():
        value = factor()
        while peek() in ('*', '/'):
            op, right = take(), factor()
            if op == '*':
                value = value * right
            else:
                if right == 0:
                    raise CalcError('非法表达式: 除以零')
                value = value / right
        return value

    def expression():
        value = term()
        while peek() in ('+', '-'):
            op, right = take(), term()
            value = value + right if op == '+' else value - right
        return value

    result = expression()
    if pos != len(tokens):
        raise CalcError('非法表达式: 存在未消费符号')
    return result
'''

CALC_CORRECT = 'class CalcError(Exception):\n    pass\n\n\n' + _PARSER + '\n\ndef evaluate(expr):\n    return _parse(expr)\n'

# S2 第一次尝试：求值正确但吞掉错误返回 0（运行核验会失败——注入失败 2）
CALC_NOERROR = ('class CalcError(Exception):\n    pass\n\n\n' + _PARSER
                + '\n\ndef evaluate(expr):\n    try:\n        return _parse(expr)\n    except Exception:\n        return 0\n')

CLI_BROKEN = "raise SystemExit('CLI 尚未实现')\n"

CLI_CORRECT = '''import sys

from .calc import CalcError, evaluate


def main(argv):
    if len(argv) != 1:
        print('用法: python -m src "表达式"')
        return 2
    try:
        print(evaluate(argv[0]))
        return 0
    except CalcError as exc:
        print('错误:', exc)
        return 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
'''

S2_CHECK = (
    "from src.calc import CalcError, evaluate;\n"
    "try:\n"
    "    evaluate('1+')\n"
    "    raise SystemExit('应抛出 CalcError')\n"
    "except CalcError as exc:\n"
    "    assert '非法' in str(exc), exc\n"
    "print('S2 错误处理核验通过')\n")


class AutonomyDemo:
    def __init__(self, root: Path):
        self.root = root
        self.gate = LightGate(root)
        self.ops: dict[str, int] = {}
        self.log: list[str] = []
        self.human_gates: list[dict] = []
        self.recoveries: list[dict] = []
        self.stage_trace: list[str] = []

    def note(self, line: str):
        self.log.append(line)
        print('  | ' + line)

    def op(self, **params):
        action = params['action']
        self.ops[action] = self.ops.get(action, 0) + 1
        result = model_operation(self.gate, params)
        stage = result.get('stage') if isinstance(result, dict) else None
        if isinstance(stage, str) and (not self.stage_trace or self.stage_trace[-1] != stage):
            self.stage_trace.append(stage)
        return result

    # ---- native host roles (production: zscode host dispatches these) ----
    def submit_requirements(self, value):
        prepared = self.gate.requirements_context()
        call = 'requirements-' + str(self.gate.status()['revision'])
        request = {'type': 'tool_call', 'session_id': 'demo-requirements', 'toolCallId': call,
                   'input': prepared['task_call']}
        self.gate.observe_requirements(request)
        response = {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'analyst-' + call, 'agent': 'aidevflow-requirements-analyst', 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': copy.deepcopy(value)}}]}}
        receipt = self.gate.observe_requirements(response)
        assert receipt['status'] == 'completed', receipt
        return self.op(action='submit', kind='requirements', path=receipt['result_path'])

    def dispatch_baseline_review(self):
        prepared = self.gate.baseline_assignment()
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        call = 'baseline-' + str(self.gate.status()['revision'])
        request = {'type': 'tool_call', 'session_id': 'demo-baseline', 'toolCallId': call,
                   'input': prepared['task_call']}
        self.gate.observe_baseline(request)
        value = {'schema': 'aidevflow/baseline-review-v1', 'run_id': self.gate.status()['run_id'],
                 'bundle_sha256': prepared['sha256'], 'decision': 'approve',
                 'reason': '独立基线审查：断言与批准验收一致', 'acceptance_ids': manifest['acceptance_ids'],
                 'changed_paths': manifest['changed_paths']}
        response = {**request, 'type': 'tool_result', 'details': {'results': [
            {'agent': 'aidevflow-baseline-reviewer', 'id': 'reviewer-' + call, 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': value}}]}}
        self.gate.observe_baseline(response)
        self.note('原生基线审查回执：approve（独立角色审查测试含义，非模型自批）')

    def dispatch_verifier(self):
        self.op(action='prepare_verifier')
        prepared = self.gate.verifier_assignment()
        state = self.gate.status()
        manifest = read_json(self.root / prepared['path'] / 'BUNDLE.json')
        call = 'verifier-' + str(state['revision'])
        request = {'type': 'tool_call', 'session_id': 'demo-verifier', 'toolCallId': call,
                   'input': prepared['task_call']}
        self.gate.observe_verifier(request)
        evidence = [i['path'] for i in manifest['files'] if i['label'] in ('src/calc.py', 'src/__main__.py')]
        value = {'schema': 'aidevflow/light-verdict-v1', 'run_id': state['run_id'],
                 'bundle_sha256': prepared['sha256'], 'decision': 'pass', 'reason': '独立核验：证据与规格一致',
                 'items': [{'id': ident, 'status': 'passed', 'reason': '证据与批准规格一致', 'evidence': evidence}
                           for ident in ('S1', 'S2', 'S3')]}
        response = {**request, 'type': 'tool_result', 'details': {'results': [
            {'id': 'agent-' + call, 'agent': VERIFIER, 'exitCode': 0,
             'structuredOutput': {'status': 'valid', 'data': value}}]}}
        self.gate.observe_verifier(response)
        self.note('原生独立验收回执：pass（仅读冻结 bundle，逐 Sx 精确引用证据 objects/<sha>）')

    # ---- model-side drafts ----
    def draft(self, name, value):
        rel = f"specs/drafts/{self.gate.status()['run_id']}/{name}"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding='utf-8')
        else:
            write_json_atomic(path, value)
        return rel

    # ---- autonomous execution policy (what the model does each turn) ----
    def attempt_file(self, task, failed_before: bool):
        """Implementation attempt per task. First attempts are deliberately wrong
        for 1.2 (ignores precedence), 2.1 (swallows errors) and 3.1 (CLI absent)
        to exercise evidence-driven autonomous recovery without human help."""
        rel = task['files'][0]
        content = None
        if task['kind'] == 'green':
            content = CALC_CORRECT if failed_before else CALC_BROKEN
        elif task['kind'] == 'build' and task['group'] == 'G2':
            content = CALC_CORRECT if failed_before else CALC_NOERROR
        elif task['kind'] == 'build' and task['group'] == 'G3':
            content = CLI_CORRECT if failed_before else CLI_BROKEN
        if content is not None:
            (self.root / rel).write_text(content, encoding='utf-8')

    def autonomous_loop(self):
        guard = 0
        while True:
            guard += 1
            if guard > 40:
                raise RuntimeError('自主循环超过 40 轮未收敛')
            s = self.op(action='status')
            stage, task = s['stage'], s.get('current_task') or {}
            if stage == 'verify':
                self.note('进入 verify：全部任务与全量回归通过（%s 轮状态驱动）' % guard)
                return
            if stage != 'applying':
                raise RuntimeError('意外阶段: ' + str(stage))
            failed_before = task.get('result', {}).get('status') == 'failed'
            if failed_before and task.get('attempts', 1) > len(
                    [r for r in self.recoveries if r['task'] == task['id']]):
                evidence = read_json(self.root / task['result']['path'])
                self.recoveries.append({'task': task['id'], 'kind': task['kind'],
                                        'round': task.get('attempts', 1),
                                        'counts': task['result'].get('counts'),
                                        'evidence': task['result']['path'],
                                        'tail': (s.get('failure_summary') or '').strip().splitlines()[-1:]})
                self.note(f"任务 {task['id']}（{task['kind']}）失败——按执行证据自主修复当前 files，不求助用户")
            if task['kind'] == 'red':
                for rel in task['files']:
                    path = self.root / rel
                    if not path.is_file():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(TEST_CONTENT, encoding='utf-8')
                        self.note(f'编写 RED 测试 {rel}（S1 用户点名测试锁定，先断言失败）')
                context = self.op(action='prepare_baseline')
                if context.get('task_call') or context.get('phase') == 'review_required':
                    self.note('基线包就绪 → 派发原生独立基线审查（冻结测试从此不可改写）')
                    self.dispatch_baseline_review()
                self.op(action='run')
            elif task['kind'] in ('green', 'build'):
                self.attempt_file(task, failed_before)
                self.op(action='run')
            else:
                self.op(action='run')

    def run(self):
        started = time.monotonic()
        self.gate.create('calc-evaluator', '实现一个表达式求值：支持四则运算和括号，非法输入给明确错误，提供命令行入口')
        self.note('收到需求 → requirements_context 派发原生需求分析角色')
        s = self.submit_requirements(copy.deepcopy(REQUIREMENTS))
        self.note('Intent/PRD 就绪 → 打开人工门禁 1（入口审查）')
        self.gate.approve(s['requirements']['sha256'], '自测·真实用户')
        self.human_gates.append({'gate': 'Intent/PRD 入口审查', 'action': 'approve'})
        spec_path = self.draft('spec.json', {'content': SPEC, 'acceptance_map': MAP})
        checked = self.op(action='validate', kind='spec', path=spec_path)
        assert checked['valid'], checked
        self.op(action='submit', kind='spec', path=spec_path)
        self.note('提交 zsspec v2.0 规格（需求/边界/规格/设计/记录，冲突声明=无，Sx↔AC 精确映射）')
        argv_test = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']
        tasks = {'tasks': [
            {'id': '1.1', 'title': 'RED 锁定 S1 求值行为', 'kind': 'red', 'group': 'G1', 'specs': ['S1'],
             'files': ['tests/test_calc.py'], 'argv': argv_test},
            {'id': '1.2', 'title': 'GREEN 递归下降求值', 'kind': 'green', 'group': 'G1', 'specs': ['S1'],
             'files': ['src/calc.py'], 'argv': argv_test, 'regression': argv_test},
            {'id': '2.1', 'title': '构建 S2 错误处理并运行核验', 'kind': 'build', 'group': 'G2', 'specs': ['S2'],
             'files': ['src/calc.py'], 'argv': [sys.executable, '-B', '-c', S2_CHECK]},
            {'id': '3.1', 'title': '构建 S3 CLI 并核验退出码', 'kind': 'build', 'group': 'G3', 'specs': ['S3'],
             'files': ['src/__main__.py'], 'argv': [sys.executable, '-B', '-m', 'src', '2*(3+4)']},
            {'id': '4.1', 'title': 'R1 全量回归', 'kind': 'regression', 'specs': ['S1', 'S2', 'S3'],
             'files': [], 'argv': argv_test}]}
        plan_path = self.draft('plan.json', tasks)
        checked = self.op(action='validate', kind='plan', path=plan_path)
        assert checked['valid'], checked
        self.op(action='submit', kind='plan', path=plan_path)
        self.note('提交 v2.0 计划映射：测试卡→RED/GREEN 对；运行核验/构建卡→BUILD；R1→全量回归')
        self.autonomous_loop()
        knowledge = self.draft('knowledge.json', {'files': {
            'specs/modules/calc.md': '# calc\n递归下降求值（factor/term/expr 三层优先级）；CalcError 表示非法输入。\n'}})
        self.op(action='submit', kind='knowledge', path=knowledge)
        self.note('提交知识提案（归档时才生效，运行中不改写模块文档）')
        s = self.gate.status()
        assert s.get('final_candidate'), s
        self.dispatch_verifier()
        s = self.gate.status()
        assert s['stage'] == 'delivery_ready', s['stage']
        self.note('delivery_ready → 打开人工门禁 2（最终交付验收）')
        done = self.gate.confirm(s['final_candidate'], '自测·真实用户', s['verifier_bundle']['sha256'])
        self.human_gates.append({'gate': '最终交付验收', 'action': 'accept'})
        assert done['stage'] == 'done', done
        self.note('人工门禁 2 通过 → 自动归档（应用知识提案 + 写入 specs/archive/INDEX.md）')
        archived = self.op(action='archive')
        elapsed = round(time.monotonic() - started, 1)
        return {'result': 'delivered', 'archive': archived['archive'], 'run_id': s['run_id'],
                'human_gates': self.human_gates, 'recoveries': self.recoveries,
                'model_operations': self.ops, 'stage_trace': self.stage_trace,
                'frozen_tests': sorted(f for group in s['baselines'].values() for f in group),
                'elapsed_seconds': elapsed, 'log': self.log}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    base = SOURCE / '.test-tmp'
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix='autonomy-demo-', ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        env = os.environ.copy()
        env.update({'GIT_CONFIG_COUNT': '3', 'GIT_CONFIG_KEY_0': 'core.fsmonitor', 'GIT_CONFIG_VALUE_0': 'false',
                    'GIT_CONFIG_KEY_1': 'user.email', 'GIT_CONFIG_VALUE_1': 'selftest@example.invalid',
                    'GIT_CONFIG_KEY_2': 'user.name', 'GIT_CONFIG_VALUE_2': 'autonomy selftest'})
        subprocess.run(['git', 'init', '-q'], cwd=root, env=env, check=True, capture_output=True)
        (root / 'src').mkdir()
        (root / 'src' / 'calc.py').write_text(
            "class CalcError(Exception):\n    pass\n\n\ndef evaluate(expr):\n    return 0\n", encoding='utf-8')
        subprocess.run(['git', 'add', '.'], cwd=root, env=env, check=True, capture_output=True)
        subprocess.run(['git', 'commit', '-qm', 'baseline'], cwd=root, env=env, check=True, capture_output=True)
        install(SOURCE, root)
        print('== AIDevFlow zsspec-light v2.0 自主交付自测（含三次失败注入）==')
        demo = AutonomyDemo(root)
        demo.gate.knowledge({'specs/MAP.md': '| 模块 | 职责 | 详情 |\n|---|---|---|\n| calc | 表达式求值 | 标准库 |\n',
                             'specs/modules/calc.md': '# calc\n占位\n'})
        report = demo.run()
        print('\n== 自测报告 ==')
        print(json.dumps({k: report[k] for k in ('result', 'run_id', 'archive', 'human_gates', 'recoveries',
                                                 'model_operations', 'stage_trace', 'frozen_tests', 'elapsed_seconds')},
                         ensure_ascii=False, indent=2))
        LightGate(root)
        assert not (root / '.aidevflow' / 'light-active.json').exists(), '归档后活动指针应清除'
        index = (root / 'specs' / 'archive' / 'INDEX.md').read_text(encoding='utf-8')
        assert 'calc-evaluator' in index
        cli = subprocess.run([sys.executable, '-B', '-m', 'src', '2*(3+4)'], cwd=root, capture_output=True,
                             encoding='utf-8', timeout=60)
        assert cli.returncode == 0 and cli.stdout.strip() == '14', (cli.returncode, cli.stdout, cli.stderr)
        tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'],
                               cwd=root, capture_output=True, encoding='utf-8', timeout=120)
        assert tests.returncode == 0, tests.stderr
        assert len(report['human_gates']) == 2, report['human_gates']
        module_doc = (root / 'specs' / 'modules' / 'calc.md').read_text(encoding='utf-8')
        assert '递归下降' in module_doc, module_doc
        print('\n交付后独立复核：CLI 输出 14 ✓  冻结测试全绿 ✓  知识提案已应用 ✓  归档 INDEX ✓  人工参与恰好 2 次 ✓')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
