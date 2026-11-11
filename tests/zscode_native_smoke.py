"""Native zscode conformance probe using a local scripted model, never a paid API.

This is a protocol fixture, not a production zscode adapter or AI quality test.
It installs test assets only below the framework's .test-tmp directory.
"""
from __future__ import annotations

import argparse
import hashlib
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
from aidevflow.gate import GateError
from aidevflow.runtimes.registry import get_runtime

ROLE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["schema", "passed", "observations"],
    "properties": {"schema": {"const": "aidevflow/native-probe-v1"}, "passed": {"type": "boolean"},
                   "observations": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
}


def events(root):
    path = root / "probe-events.jsonl"
    values = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                values.append(json.loads(line))
            except ValueError:
                pass  # A different process may currently be appending its final line.
    return values


class NativeRPC:
    def __init__(self, executable, root, mode="rpc-ui", session=None, no_ui=False):
        self.root, self.frames, self.history, self.errors = root, queue.Queue(), [], []
        home = root / "isolated-home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ)
        for name in list(env):
            if name.upper().endswith(("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN")):
                env.pop(name)
        env.update(HOME=str(home), USERPROFILE=str(home), PI_CODING_AGENT_DIR=str(home / "agent"),
                   XDG_CONFIG_HOME=str(home / "config"), XDG_DATA_HOME=str(home / "data"),
                   XDG_CACHE_HOME=str(home / "cache"), CI="true", PI_NO_TITLE="1")
        for name in ("PI_CONFIG_DIR", "OMP_PROFILE", "PI_PROFILE"):
            env.pop(name, None)
        args = [executable, "--cwd", str(root), "--mode", mode, "--no-title", "--no-context-files",
                "--no-rules", "--no-lsp", "--provider", "anthropic", "--model", "claude-sonnet-4-5"]
        if session:
            args.extend(["--session", session])
        if no_ui:
            args.append("--no-ui")
        self.process = subprocess.Popen(args, cwd=root, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def stdout():
            for line in self.process.stdout:
                try:
                    self.frames.put(json.loads(line))
                except ValueError:
                    self.frames.put({"type": "transport_error"})
            self.frames.put({"type": "eof"})
        def stderr():
            for line in self.process.stderr:
                self.errors.append(line.rstrip())
        self.readers = [threading.Thread(target=stdout, daemon=True), threading.Thread(target=stderr, daemon=True)]
        for reader in self.readers:
            reader.start()
        try:
            self.wait(lambda frame: frame.get("type") == "ready")
        except Exception:
            self.close()
            raise

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def wait(self, predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=min(.25, max(.01, deadline - time.monotonic())))
            except queue.Empty:
                if predicate({}):
                    return {}
                continue
            self.history.append(frame)
            if frame.get("type") in ("eof", "transport_error", "extension_error"):
                raise RuntimeError("Native protocol failure: " + json.dumps(frame, ensure_ascii=False)[:1000]
                                   + " stderr=" + "\n".join(self.errors[-5:])[:1500])
            if predicate(frame):
                return frame
        raise TimeoutError("Timed out waiting for native RPC; recent frame types: " +
                           str([f.get("type") for f in self.history[-12:]]))

    def request(self, kind, **payload):
        ident = "request-" + str(time.monotonic_ns())
        self.send({"id": ident, "type": kind, **payload})
        return self.wait(lambda frame: frame.get("id") == ident and frame.get("type") == "response")

    def command(self, text):
        ident = "command-" + str(time.monotonic_ns())
        self.send({"id": ident, "type": "prompt", "message": text})
        return ident

    def complete(self, ident, timeout=40):
        return self.wait(lambda frame: frame.get("id") == ident and (
            frame.get("type") == "prompt_result" or
            frame.get("type") == "response" and frame.get("data", {}).get("agentInvoked") is False), timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        for reader in self.readers:
            reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if not stream.closed:
                stream.close()


def prepare(root):
    def write(relative, text):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "-c", "core.fsmonitor=false", "init", "-q"], cwd=root, check=True, capture_output=True)
    write("fixture-input.txt", "Native protocol fixture: expected integer is two.\n")
    write(".zscode/extensions/aidevflow-probe/index.ts",
          (SOURCE / "tests/fixtures/zscode-conformance.ts").read_text(encoding="utf-8"))
    write(".zscode/skills/aidevflow-flow/SKILL.md",
          (SOURCE / "assets/flows/aidevflow-flow/SKILL.md").read_text(encoding="utf-8"))
    for name in ("init", "spec", "plan", "apply", "archive"):
        shutil.copytree(SOURCE / ".claude/skills" / ("zsspec-" + name),
                        root / ".zscode/skills" / ("zsspec-" + name))
    write(".omp/skills/aidevflow-foreign-marker/SKILL.md",
          "---\nname: aidevflow-foreign-marker\ndescription: Must not be discovered by zscode\n---\nforeign\n")
    write(".zscode/config.yml", "modelRoles:\n  default: aidevflow-probe/native\n"
          "  smol: aidevflow-probe/native\n  task: aidevflow-probe/native\n"
          "async:\n  enabled: false\n")
    header = ("---\nname: aidevflow-probe-reviewer\ndescription: Local scripted protocol reviewer\n"
              "model: aidevflow-probe/native\ntools: [read, grep, glob]\nblocking: true\n"
              "structured: true\nadvisor: false\nreadSummarize: false\noutput: " +
              json.dumps(ROLE_SCHEMA) + "\n---\n")
    write(".zscode/agents/aidevflow-probe-reviewer.md", header +
          "This is a protocol fixture. Read fixture-input.txt and return the probe schema through yield data.\n")


def main(output=None, delay=35):
    started = time.monotonic()
    executable = shutil.which("zscode")
    if not executable:
        raise RuntimeError("zscode is not installed on PATH")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    with open(executable, "rb") as binary:
        executable_digest = hashlib.file_digest(binary, "sha256").hexdigest()
    report = {"schema": "aidevflow/zscode-native-conformance-v1", "runtime_version": version,
              "executable": executable, "executable_sha256": executable_digest,
              "scripted_model": True, "external_model_requests": 0, "ui_delay_seconds": delay,
              "production_adapter_enabled": False, "checks": {}}
    def check(name, condition, detail=None):
        report["checks"][name] = {"passed": bool(condition), **({"detail": detail} if detail is not None else {})}
        print(json.dumps({"check": name, "passed": bool(condition)}, ensure_ascii=False), flush=True)
    base = SOURCE / ".test-tmp"
    base.mkdir(exist_ok=True)
    rpc = None
    with tempfile.TemporaryDirectory(dir=base, prefix="zscode-native-", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        root.relative_to(base.resolve())
        prepare(root)
        try:
            rpc = NativeRPC(executable, root)
            response = rpc.request("get_available_commands")
            commands = {c["name"] for c in response.get("data", {}).get("commands", [])}
            expected = {"skill:aidevflow-flow", *("skill:zsspec-" + n for n in ("init", "spec", "plan", "apply", "archive"))}
            check("native_extension_and_five_skill_discovery", response.get("success") is True and
                  expected <= commands and "aidevflow-probe-state" in commands)
            check("omp_project_skill_isolation", "skill:aidevflow-foreign-marker" not in commands)
            selected = rpc.request("set_model", provider="aidevflow-probe", modelId="native")
            if selected.get("success") is not True:
                raise RuntimeError("Scripted provider unavailable; no model prompt will be sent: " + json.dumps(selected))
            rpc.complete(rpc.command("/aidevflow-probe-state"))
            capability = next(e for e in reversed(events(root)) if e["type"] == "capabilities")
            check("native_tools_and_main_identity", {"read", "bash", "task"} <= set(capability["tools"]) and capability["actor"]["kind"] == "main")
            session_file = capability["session_file"]
            check("native_session_handle_available", isinstance(session_file, str) and bool(session_file),
                  {"physical_before_model_turn": bool(session_file) and Path(session_file).is_file()})
            for scenario, answer, seconds in (("pause", "保存并暂停", 0), ("cancel", None, 0), ("delayed_pause", "保存并暂停", delay)):
                ident = rpc.command("/aidevflow-probe-review")
                dialog = rpc.wait(lambda f: f.get("type") == "extension_ui_request" and f.get("method") == "select")
                if seconds:
                    # Sleep only inside the separately launched test process, never the agent's tool wait.
                    time.sleep(seconds)
                reply = {"type": "extension_ui_response", "id": dialog["id"]}
                reply.update({"cancelled": True} if answer is None else {"value": answer})
                rpc.send(reply)
                rpc.complete(ident)
                state = json.loads((root / "probe-checkpoint.json").read_text(encoding="utf-8"))
                check("ui_" + scenario, state["status"] == "paused")
            before = len(events(root))
            rpc.complete(rpc.command("PROBE_PARENT:normal"))
            observed = events(root)[before:]
            calls = [e for e in observed if e["type"] == "tool_call" and e["tool"] == "task"]
            results = [e for e in observed if e["type"] == "tool_result" and e["tool"] == "task"]
            check("single_native_task_dispatch", len(calls) == 1 and len(results) == 1)
            result = results[-1].get("details", {}).get("results", [{}])[0] if results else {}
            check("structured_yield_result", result.get("agent") == "aidevflow-probe-reviewer"
                  and result.get("exitCode") == 0 and result.get("structuredOutput", {}).get("status") == "valid"
                  and result.get("structuredOutput", {}).get("data", {}).get("schema") == "aidevflow/native-probe-v1",
                  {k: result.get(k) for k in ("agent", "exitCode", "structuredOutput")})
            check("native_call_and_result_identity", bool(calls and results) and
                  calls[0]["toolCallId"] == results[0]["toolCallId"] and
                  calls[0]["input"] == results[0]["input"] and bool(result.get("id")))
            report["native_task_sample"] = {
                "call_id": calls[0]["toolCallId"] if calls else None,
                "input_fields": sorted(calls[0]["input"]) if calls else [],
                "result_fields": sorted(result),
                "agent_id": result.get("id"),
            }
            check("subagent_extension_identity_and_read_event", any(e["actor"]["kind"] == "sub" and
                  e["actor"]["name"] == "aidevflow-probe-reviewer" and e["type"] == "tool_call"
                  and e.get("tool") == "read" for e in observed))
            child_tools = next((set(e.get("tools", [])) for e in observed
                               if e["type"] == "before_agent_start" and e["actor"]["kind"] == "sub"), set())
            check("read_only_role_tool_scope", "read" in child_tools and
                  not child_tools.intersection({"write", "edit", "bash", "task"}), sorted(child_tools))
            before = len(events(root))
            rpc.complete(rpc.command("PROBE_PARENT:invalid"))
            invalid_results = [r for e in events(root)[before:] if e["type"] == "tool_result" and e.get("tool") == "task"
                               for r in (e.get("details") or {}).get("results", [])]
            check("invalid_schema_cannot_be_a_valid_structured_result", bool(invalid_results) and
                  all(r.get("structuredOutput", {}).get("status") != "valid" for r in invalid_results),
                  [{"exitCode": r.get("exitCode"), "structured_status": r.get("structuredOutput", {}).get("status")}
                   for r in invalid_results])
            rpc.complete(rpc.command("PROBE_GUARD"))
            check("native_tool_guard_blocks_mutation", not (root / "forbidden.txt").exists()
                  and any(e["type"] == "guard_blocked" for e in events(root)))
            before = len(events(root))
            ident = rpc.command("PROBE_PARENT:cancel")
            rpc.wait(lambda f: any(e["type"] == "before_agent_start" and e["actor"]["kind"] == "sub"
                     for e in events(root)[before:]))
            rpc.request("abort")
            finished = rpc.complete(ident)
            cancelled_results = [r for e in events(root)[before:] if e["type"] == "tool_result" and e.get("tool") == "task"
                                 for r in (e.get("details") or {}).get("results", [])]
            check("cancel_does_not_become_success", finished.get("status") in ("aborted", "cancelled") or
                  bool(cancelled_results) and all(r.get("aborted") or r.get("exitCode") != 0 for r in cancelled_results),
                  {"prompt_status": finished.get("status"), "result_count": len(cancelled_results)})
            rpc.close()
            rpc = NativeRPC(executable, root, session=session_file)
            rpc.complete(rpc.command("/aidevflow-probe-state"))
            resumed = next(e for e in reversed(events(root)) if e["type"] == "capabilities")
            check("session_restart_reopens_same_branch", resumed["session_file"] == session_file and
                  resumed["branch_entries"] > capability["branch_entries"])
            recovered = rpc.request("get_entries")
            entries = recovered.get("data", {}).get("entries", [])
            raw = json.dumps(entries)
            check("native_history_recovers_exact_task_call",
                  bool(calls and calls[0]["toolCallId"] in raw),
                  {"physical_after_model_turn": Path(session_file).is_file(), "entry_count": len(entries)})
            recovered_messages = [e.get("message", {}) for e in entries if isinstance(e, dict)]
            recovered_results = [r for m in recovered_messages if m.get("role") == "toolResult"
                                 and calls and m.get("toolCallId") == calls[0]["toolCallId"]
                                 for r in (m.get("details") or {}).get("results", [])]
            check("native_history_preserves_structured_result", len(recovered_results) == 1 and
                  recovered_results[0].get("id") == result.get("id") and
                  recovered_results[0].get("structuredOutput") == result.get("structuredOutput"))
            check("pause_checkpoint_survives_restart",
                  json.loads((root / "probe-checkpoint.json").read_text())["status"] == "paused")
            rpc.close()
            rpc = NativeRPC(executable, root, mode="rpc", no_ui=True)
            rpc.complete(rpc.command("/aidevflow-probe-state"))
            headless = next(e for e in reversed(events(root)) if e["type"] == "capabilities")
            check("headless_reports_no_review_ui", headless["has_ui"] is False)
            before = len(rpc.history)
            rpc.complete(rpc.command("/aidevflow-probe-review"))
            check("headless_does_not_approve", not any(f.get("type") == "extension_ui_request" for f in rpc.history[before:])
                  and json.loads((root / "probe-checkpoint.json").read_text())["status"] == "paused")
            adapter = get_runtime("zscode")
            report["production_adapter_enabled"] = adapter.enabled
            check("registered_production_adapter_requires_native_receipts",
                  adapter.enabled and adapter.receipt_schema == "aidevflow/invocation-v3")
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            if rpc:
                rpc.close()
    report["passed"] = "error" not in report and all(c["passed"] for c in report["checks"].values())
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    if output:
        destination = Path(output).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--delay-seconds", type=float, default=35)
    args = parser.parse_args()
    if not 0 <= args.delay_seconds <= 60:
        parser.error("delay must be between 0 and 60 seconds")
    raise SystemExit(main(args.output, args.delay_seconds))
