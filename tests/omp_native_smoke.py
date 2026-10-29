"""Optional real OMP discovery probe; no model prompt and no production project.

Run from the framework checkout: python -B tests/omp_native_smoke.py
"""
from __future__ import annotations

import json
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
from aidevflow.installer import install


def main() -> int:
    executable = shutil.which("omp")
    if not executable:
        print("OMP is not installed on PATH", file=sys.stderr)
        return 2
    base = SOURCE / ".test-tmp"
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix="omp-discovery-", ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve()
        root.relative_to(base.resolve())  # Validate the recursive cleanup boundary.
        subprocess.run(["git", "-c", "core.fsmonitor=false", "init", "-q"], cwd=root, check=True, capture_output=True)
        install(SOURCE, root, runtime="omp")
        process = subprocess.Popen([executable, "--cwd", str(root), "--mode", "rpc", "--no-ui",
                                    "--no-title", "--no-context-files", "--no-rules", "--no-lsp"],
                                   cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        frames: queue.Queue[dict] = queue.Queue()

        def read_stdout() -> None:
            for line in process.stdout:
                try:
                    frames.put(json.loads(line))
                except ValueError:
                    frames.put({"type": "transport_error"})
            frames.put({"type": "eof"})

        def drain_stderr() -> None:
            for _ in process.stderr:
                pass  # Do not print potentially sensitive host diagnostics.

        readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=drain_stderr, daemon=True)]
        for reader in readers:
            reader.start()
        discovered = False
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    frame = frames.get(timeout=max(.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if frame.get("type") in ("eof", "transport_error", "extension_error"):
                    break
                if frame.get("type") == "ready":
                    process.stdin.write(json.dumps({"type": "get_available_commands", "id": "aidevflow-probe"}) + "\n")
                    process.stdin.flush()
                if frame.get("id") == "aidevflow-probe":
                    commands = {command.get("name") for command in frame.get("data", {}).get("commands", [])}
                    discovered = frame.get("success") is True and {"aidevflow-flow", "aidevflow-doctor", "skill:aidevflow-flow"} <= commands
                    break
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for reader in readers:
                reader.join(timeout=2)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        print(json.dumps({"native_discovery": discovered, "model_prompt_sent": False, "exit_code": process.returncode}))
        return 0 if discovered and process.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
