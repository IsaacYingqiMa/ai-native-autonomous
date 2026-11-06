"""Actual zscode RPC UI for deferred reviews, using synthetic state, never a model."""
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
from test_light_workflow import LightWorkflowTests, REQUIREMENTS
from aidevflow.storage import read_json, sha256_file, write_json_atomic


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-base', type=Path, default=Path(tempfile.gettempdir()), help='isolated writable config directory on the same drive as the user home')
    parser.add_argument('--delivery', action='store_true')
    parser.add_argument('--submission', action='store_true', help='exercise the early tool_result handoff')
    args = parser.parse_args()
    executable = shutil.which('zscode')
    if not executable: return 2
    fixture = LightWorkflowTests('test_real_two_item_tdd_confirmation_and_archive')
    fixture.setUp()
    p, readers = None, []
    try:
        root = fixture.root
        if args.delivery:
            fixture.reach_regression()
            fixture.witness()  # Synthetic role events: tests the review UI, not model verification.
        else:
            fixture.gate.create('native-review', 'Synthetic UI requirement')
            fixture.submit_requirements(REQUIREMENTS)
        extension = root / '.zscode/extensions/aidevflow'
        implementation = extension / 'implementation.ts'
        implementation.write_bytes((extension / 'index.ts').read_bytes())
        (extension / 'index.ts').write_text('''import original from "./implementation";
export default function probe(pi: any) {
  let stop: any, result: any;
  original(new Proxy(pi, { get(target, key) {
    if (key === "on") return (event: string, handler: any) => {
      if (event === "session_stop") stop = handler;
      if (event === "tool_result") result = handler;
      return target.on(event, handler);
    };
    const value = Reflect.get(target, key);
    return typeof value === "function" ? value.bind(target) : value;
  } }));
  pi.registerCommand("light-stop-probe", { description: "Synthetic lifecycle probe; no model",
    handler: async (_text: string, ctx: any) => {
      const scoped = Object.create(ctx);
      Object.defineProperty(scoped, "ui", { value: { ...ctx.ui,
        select: async () => { throw new Error("stop-bound UI used"); } } });
      if (''' + ('true' if args.submission else 'false') + ''') {
        await result({ toolName: "aidevflow_light", isError: false,
          input: { action: "submit", kind: "requirements", path: "synthetic-already-registered" } }, scoped);
      } else await stop({}, scoped);
    } });
}
''', encoding='utf-8')
        manifest_path = root / '.aidevflow/light-install-manifest.json'
        manifest = read_json(manifest_path)
        for path in (extension / 'index.ts', implementation):
            manifest['files'][path.relative_to(root).as_posix()] = sha256_file(path)
        write_json_atomic(manifest_path, manifest)
        with tempfile.TemporaryDirectory(dir=args.config_base, prefix='light-review-profile-', ignore_cleanup_errors=True) as profile:
            Path(profile).resolve().relative_to(args.config_base.resolve())
            env = os.environ.copy()
            env.update(PI_CONFIG_DIR=os.path.relpath(profile, Path.home()),
                       PI_CODING_AGENT_DIR=str(root / '.aidevflow/probe-agent'),
                       PI_NATIVES_DIR=str(root / '.aidevflow/probe-natives'),
                       OPENAI_API_KEY='light-review-probe-not-a-real-key')
            p = subprocess.Popen([executable, '--cwd', str(root), '--mode', 'rpc-ui', '--no-title',
                                  '--no-context-files', '--no-rules', '--no-lsp'], cwd=root, env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            frames, errors = queue.Queue(), []
            def stdout():
                for line in p.stdout:
                    try: frames.put(json.loads(line))
                    except ValueError: frames.put({'type': 'parse_error'})
                frames.put({'type': 'eof'})
            def stderr():
                for line in p.stderr: errors.append(line)
            readers = [threading.Thread(target=stdout, daemon=True), threading.Thread(target=stderr, daemon=True)]
            for reader in readers: reader.start()
            def send(value):
                p.stdin.write(json.dumps(value, ensure_ascii=False) + '\n'); p.stdin.flush()
            saw_review = completed = model_started = answered = before_answer = False
            response_id, due = None, None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if due is not None and time.monotonic() >= due:
                    send({'type': 'extension_ui_response', 'id': response_id,
                          'value': '暂不验收' if args.delivery else '暂不批准'})
                    answered, due = True, None
                if completed and answered: break
                try: frame = frames.get(timeout=.1)
                except queue.Empty: continue
                if frame.get('type') in ('eof', 'parse_error', 'extension_error'): break
                if frame.get('type') == 'ready':
                    send({'id': 'review-probe', 'type': 'prompt', 'message': '/light-stop-probe'})
                if frame.get('type') == 'agent_start':
                    model_started = True; send({'id': 'abort', 'type': 'abort'}); break
                if frame.get('type') == 'extension_ui_request' and frame.get('method') == 'select':
                    saw_review = ('验收通过' if args.delivery else '确认需求并开始自主开发') in frame.get('options', [])
                    response_id, due = frame['id'], time.monotonic() + .25
                if frame.get('id') == 'review-probe' and (frame.get('type') == 'prompt_result' or frame.get('data', {}).get('agentInvoked') is False):
                    completed = frame.get('success') is not False and frame.get('status') in (None, 'completed')
                    before_answer = not answered
            p.stdin.close()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.terminate(); p.wait(timeout=5)
            for reader in readers: reader.join(timeout=2)
            state = fixture.gate.status()
            unchanged = state['stage'] == ('delivery_ready' if args.delivery else 'discovery')
            passed = saw_review and completed and answered and before_answer and unchanged and not model_started and p.returncode == 0
            print(json.dumps({'review': 'delivery' if args.delivery else 'requirements', 'native_dialog': saw_review,
                              'stop_returned_before_answer': before_answer, 'no_implicit_acceptance': unchanged,
                              'model_started': model_started, 'synthetic_state': True, 'passed': passed}))
            if not passed: print(''.join(errors)[-1500:])
            return 0 if passed else 1
    finally:
        if p:
            if p.poll() is None: p.kill(); p.wait(timeout=5)
            for reader in readers: reader.join(timeout=2)
            for stream in (p.stdin, p.stdout, p.stderr):
                if stream and not stream.closed: stream.close()
        fixture.tearDown()


if __name__ == '__main__':
    raise SystemExit(main())
