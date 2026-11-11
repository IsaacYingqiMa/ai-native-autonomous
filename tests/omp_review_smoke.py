"""Real OMP RPC UI round trip using synthetic requirements; never invokes a model.

Seeds protocol fixtures, opens /aidevflow-flow, answers Save/Pause, checks durable state.
This tests native UI transport, not real-model requirements analysis or business delivery.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from test_discovery import DiscoveryTests
from aidevflow.installer import install
from aidevflow.discovery import Discovery
from aidevflow.gate import read_json


def main(delay_seconds=0, stop_probe=False, runtime="omp"):
    native = ".zscode" if runtime == "zscode" else ".omp"
    executable = shutil.which(runtime)
    if not executable:
        return 2
    fixture = DiscoveryTests("test_no_legacy_entrypoint_or_product_write_can_bypass_discovery")
    fixture.RUNTIME, fixture.NATIVE = runtime, native
    fixture.setUp()
    process = None
    readers = []
    try:
        root = fixture.root
        root.resolve().relative_to((SOURCE / ".test-tmp").resolve())
        install(SOURCE, root, runtime, skills_root=native + "/skills", workflow_profile="reviewed-discovery-v1")
        example = read_json(root / ".aidevflow/contract.example.json")
        names = {name for group in example["method"]["stages"].values() for name in group}
        for name in names | {"zsspec-init", "zsspec-archive"}:
            shutil.copytree(SOURCE / '.claude/skills' / name, root / (native + "/skills") / name,
                            dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copy2(SOURCE / '.claude/skills/README.md', root / (native + "/skills/README.md"))
        fixture.record()
        if stop_probe:
            # Exercise the real extension's stop handler, without any model request.
            # Poison the stop-bound UI: review must use the completed lifecycle UI.
            from aidevflow.gate import sha256_file, write_json_atomic
            extension = root / (native + "/extensions/aidevflow")
            implementation = extension / 'implementation.ts'
            implementation.write_text((extension / 'index.ts').read_text(encoding='utf-8'), encoding='utf-8')
            (extension / 'index.ts').write_text('''import original from "./implementation";
export default function probe(pi: any) {
  let stop: any;
  original(new Proxy(pi, { get(target, key) {
    if (key === "on") return (event: string, handler: any) => {
      if (event === "session_stop") stop = handler;
      return target.on(event, handler);
    };
    const value = Reflect.get(target, key);
    return typeof value === "function" ? value.bind(target) : value;
  } }));
  pi.registerCommand("aidevflow-stop-probe", { description: "Synthetic stop probe; no model",
    handler: async (_args: string, ctx: any) => {
      const controller = new AbortController();
      const scoped = Object.create(ctx);
      Object.defineProperties(scoped, {
        ui: { value: { ...ctx.ui, select: async () => { throw new Error("stop-bound dialog used"); } } },
        abort: { value: () => { controller.abort(); } },
        isIdle: { value: () => true },
      });
      await stop({ signal: controller.signal }, scoped);
    } });
}
''', encoding='utf-8')
            manifest_path = root / '.aidevflow/runtime-manifest.json'
            manifest = read_json(manifest_path)
            for path in (extension / 'index.ts', implementation):
                manifest['files'][path.relative_to(root).as_posix()] = sha256_file(path)
            write_json_atomic(manifest_path, manifest)
        process = subprocess.Popen([executable, "--cwd", str(root), "--mode", "rpc-ui", "--no-title",
                                    "--no-context-files", "--no-rules", "--no-lsp"], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        frames = queue.Queue()
        def stdout():
            for line in process.stdout:
                try:
                    frames.put(json.loads(line))
                except ValueError:
                    frames.put({"type": "parse_error"})
            frames.put({"type": "eof"})
        def stderr():
            for _ in process.stderr:
                pass
        readers = [threading.Thread(target=stdout, daemon=True), threading.Thread(target=stderr, daemon=True)]
        for reader in readers:
            reader.start()
        def send(value):
            process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
            process.stdin.flush()
        saw_review, completed, model_started = False, False, False
        response_due, response_id, answered = None, None, False
        short_title, completed_before_answer = False, False
        deadline = time.monotonic() + 45 + delay_seconds
        while time.monotonic() < deadline:
            if response_due is not None and time.monotonic() >= response_due:
                send({"type": "extension_ui_response", "id": response_id, "value": "保存并暂停"})
                answered, response_due = True, None
            if answered and completed and Discovery(root).load()[0]['status'] == 'paused':
                break
            try:
                frame = frames.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if frame.get("type") in ("eof", "parse_error", "extension_error"):
                break
            if frame.get("type") == "agent_start":
                model_started = True
                send({"id": "abort", "type": "abort"})
                break
            if frame.get("type") == "ready":
                send({"id": "review-probe", "type": "prompt", "message": "/aidevflow-stop-probe" if stop_probe else "/aidevflow-flow"})
            if frame.get("type") == "extension_ui_request" and frame.get("method") == "select":
                saw_review = "确认并开始自主开发" in frame.get("options", [])
                short_title = len(frame.get('title', '')) < 160
                response_due, response_id = time.monotonic() + delay_seconds, frame['id']
            if frame.get("id") == "review-probe" and (frame.get("type") == "prompt_result" or frame.get("data", {}).get("agentInvoked") is False):
                completed = frame.get("status") in (None, "completed") and frame.get("success") is not False
                completed_before_answer = not answered
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        paused = Discovery(root).load()[0]["status"] == "paused"
        passed = saw_review and short_title and completed and paused and not model_started and process.returncode == 0
        if stop_probe:
            passed = passed and completed_before_answer
        print(json.dumps({"native_review_dialog": saw_review, "command_completed": completed,
                          "durably_paused": paused, "model_started": model_started,
                          "short_dialog_title": short_title, "delay_seconds": delay_seconds,
                          "synthetic_stop_probe": stop_probe, "stop_returned_before_answer": completed_before_answer,
                          "synthetic_requirements": True, "passed": passed}))
        return 0 if passed else 1
    finally:
        if process:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        fixture.tearDown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--delay-seconds', type=float, default=0)
    parser.add_argument('--from-stop', action='store_true')
    parser.add_argument("--runtime", choices=("omp", "zscode"), default="omp")
    args = parser.parse_args()
    if not 0 <= args.delay_seconds <= 120:
        parser.error('delay must be between 0 and 120 seconds')
    raise SystemExit(main(args.delay_seconds, args.from_stop, args.runtime))
