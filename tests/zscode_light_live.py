"""Opt-in real-model checks in a disposable project, with isolated host state.

Credentials must be supplied explicitly in MINIMAX_CODE_CN_API_KEY by the operator.
This test never reads host credential databases or copies secrets to the project.
--delivery simulates the two human choices solely for this automated test project.
"""
from __future__ import annotations
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.backends.zscode.installer import install
from aidevflow.storage import read_json, write_json_atomic

BOARD = '''帮我做一个个人任务看板网页：待办、进行中、已完成三列，任务有标题和描述。
支持新建、编辑、改状态、删除前确认、按标题关键词搜索及清空搜索。
标题不能只包含空格，描述可空，新任务默认待办。顶部统计全部及各状态数量，不受搜索影响。
用 localStorage 保存，刷新后不丢失。首次放入“整理需求-待办、开发首页-进行中、验证登录-已完成”三个任务，删光后刷新保持为空。
界面清爽，电脑和手机均可操作；空项目使用 React、TypeScript、Vite。提供启动说明和覆盖主要业务规则的测试。
先生成完整 Intent 和 PRD 供我审查，批准后自主开发和独立验收，最后交付。'''
INTEGER = '''修复现有 src/sample.py：one() 必须返回整数1，two() 必须返回整数2。
调用签名不变，使用 Python 标准库和 unittest，分别验证两个函数，不增加第三方依赖。
先生成 Intent/PRD 供审查，确认后自主完成 light TDD、回归、原生独立验收和最终交付，再归档。'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-base', type=Path, required=True)
    parser.add_argument('--delivery', action='store_true')
    parser.add_argument('--timeout', type=int, default=900)
    args = parser.parse_args()
    executable = shutil.which('zscode')
    if not executable: raise RuntimeError('zscode missing')
    base = SOURCE / '.test-tmp'
    base.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=base, prefix='live-light-')).resolve()
    root.relative_to(base.resolve())
    env = os.environ.copy()
    if not env.get('MINIMAX_CODE_CN_API_KEY'): raise RuntimeError('MiniMax credential is required')
    def git(*argv):
        subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'user.name=Light live test',
            '-c', 'user.email=light-test@example.invalid', *argv], cwd=root, capture_output=True, check=True)
    git('init', '-q')
    if args.delivery:
        (root / 'src').mkdir()
        (root / 'src/sample.py').write_text('def one(): return 0\ndef two(): return 0\n', encoding='utf8')
    else:
        (root / 'README.md').write_text('# Disposable task-board requirements test\n', encoding='utf8')
    git('add', '.')
    git('commit', '-qm', 'test baseline')
    install(SOURCE, root)
    print(json.dumps({'test_project': str(root), 'scenario': 'delivery' if args.delivery else 'requirements',
                      'real_model': 'minimax-code-cn/MiniMax-M3.1-Flash'}, ensure_ascii=False), flush=True)
    frames = queue.Queue()
    reviews = []
    calls = []
    provider_errors = []
    passed = False
    errors = []
    with tempfile.TemporaryDirectory(dir=args.config_base, prefix='light-live-profile-', ignore_cleanup_errors=True) as profile:
        env.update(PI_CONFIG_DIR=os.path.relpath(profile, Path.home()), PI_CODING_AGENT_DIR=str(root / '.aidevflow/host-agent'),
                   PI_NATIVES_DIR=str(root / '.aidevflow/host-natives'))
        child = subprocess.Popen([executable, '--cwd', str(root), '--mode', 'rpc-ui', '--no-title', '--no-context-files',
            '--no-rules', '--no-lsp', '--model', 'minimax-code-cn/MiniMax-M3.1-Flash', '--thinking', 'low'],
            cwd=root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def output():
            for line in child.stdout:
                try: frames.put(json.loads(line))
                except ValueError: pass
            frames.put({'type': 'eof'})
        def stderr():
            for line in child.stderr: errors.append(line[-1000:])
        readers = [threading.Thread(target=output, daemon=True), threading.Thread(target=stderr, daemon=True)]
        for thread in readers: thread.start()
        def send(value):
            child.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
            child.stdin.flush()
        try:
            deadline = time.monotonic() + args.timeout
            last_stage = None
            while time.monotonic() < deadline:
                try: frame = frames.get(timeout=1)
                except queue.Empty: continue
                typ = frame.get('type')
                if typ == 'ready': send({'id': 'live', 'type': 'prompt', 'message': '/aidevflow-flow ' + (INTEGER if args.delivery else BOARD)})
                if typ == 'eof': break
                if typ == 'tool_execution_start':
                    name = frame.get('toolName')
                    params = frame.get('args', frame.get('input', {}))
                    calls.append({'tool': name, 'action': params.get('action') if isinstance(params, dict) else None})
                if typ == 'extension_ui_request':
                    title = frame.get('title', '')
                    if frame.get('method') == 'select' and title.startswith('人工审查'):
                        reviews.append(title)
                        print(json.dumps({'review': title, 'test_choice_simulated': bool(args.delivery)}, ensure_ascii=False), flush=True)
                        if not args.delivery:
                            send({'type': 'extension_ui_response', 'id': frame['id'], 'value': '暂不批准'})
                            active = read_json(root / '.aidevflow/light-active.json')
                            state = read_json(root / '.aidevflow/light-runs' / active['run_id'] / 'state.json')
                            value = state['requirements']['value']
                            passed = bool(state['requirements'].get('native_receipt')) and len(value['requirements']) >= 6 and not state.get('approval')
                            break
                        choice = '确认需求并开始自主开发' if '①' in title else '验收通过'
                        send({'type': 'extension_ui_response', 'id': frame['id'], 'value': choice})
                    elif frame.get('method') == 'confirm':
                        send({'type': 'extension_ui_response', 'id': frame['id'], 'confirmed': True})
                    elif frame.get('method') in ('select', 'input'):
                        raise RuntimeError('Unexpected business question; test does not invent user answers')
                if typ == 'message_end':
                    message = frame.get('message', {})
                    if message.get('errorMessage'): provider_errors.append(message['errorMessage'])
                pointer = root / '.aidevflow/light-active.json'
                if pointer.is_file():
                    active = read_json(pointer)
                    state = read_json(root / '.aidevflow/light-runs' / active['run_id'] / 'state.json')
                    if state['stage'] != last_stage:
                        last_stage = state['stage']
                        print(json.dumps({'stage': last_stage}, ensure_ascii=False), flush=True)
                    if last_stage == 'needs_attention': break
                elif args.delivery and len(reviews) == 2:
                    index = root / 'specs/archive/INDEX.md'
                    passed = index.is_file() and any((root / 'specs/archive').glob('*/intent.md'))
                    break
        finally:
            try: send({'id': 'stop-live-test', 'type': 'abort'})
            except (OSError, BrokenPipeError): pass
            child.stdin.close()
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.terminate(); child.wait(timeout=5)
            for thread in readers: thread.join(timeout=2)
            child.stdout.close(); child.stderr.close()
    report = {'passed': passed, 'real_model': True, 'scenario': 'delivery' if args.delivery else 'requirements',
              'human_review_choices_simulated': bool(args.delivery), 'reviews': reviews, 'tool_calls': calls,
              'provider_errors': provider_errors, 'last_stage': last_stage}
    write_json_atomic(root / '.aidevflow/live-test-result.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'tool_calls'}, ensure_ascii=False), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
