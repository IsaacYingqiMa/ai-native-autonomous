"""Real installed zscode discovery probe; isolated project/profile, no model prompt."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-base', type=Path, default=Path(tempfile.gettempdir()), help='isolated writable config directory on the same drive as the user home')
    args = parser.parse_args()
    executable = shutil.which('zscode')
    if not executable:
        print('zscode is not on PATH'); return 2
    base = SOURCE / '.test-tmp'
    base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix='light-native-', ignore_cleanup_errors=True) as directory, tempfile.TemporaryDirectory(dir=args.config_base, prefix='light-profile-', ignore_cleanup_errors=True) as profile:
        root = Path(directory).resolve()
        root.relative_to(base.resolve())
        Path(profile).resolve().relative_to(args.config_base.resolve())
        subprocess.run(['git', '-c', 'core.fsmonitor=false', 'init', '-q'], cwd=root, check=True, capture_output=True)
        install(SOURCE, root)
        env = os.environ.copy()
        # Enables catalog model selection only; this probe never sends a prompt.
        env['OPENAI_API_KEY'] = 'light-discovery-not-a-real-api-key'
        env['PI_CONFIG_DIR'] = os.path.relpath(Path(profile), Path.home())
        env.update(PI_CODING_AGENT_DIR=str(root / 'host-agent'), PI_NATIVES_DIR=str(root / 'host-natives'))
        p = subprocess.Popen([executable, '--cwd', str(root), '--mode', 'rpc', '--no-ui', '--no-title',
                              '--no-context-files', '--no-rules', '--no-lsp'], cwd=root, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        frames = queue.Queue()
        errors = []
        def stdout():
            for line in p.stdout:
                try: frames.put(json.loads(line))
                except ValueError: pass
            frames.put({'type': 'eof'})
        def stderr():
            for line in p.stderr: errors.append(line)
        threads = [threading.Thread(target=stdout, daemon=True), threading.Thread(target=stderr, daemon=True)]
        for t in threads: t.start()
        found = False
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                try: frame = frames.get(timeout=max(.1, deadline - time.monotonic()))
                except queue.Empty: break
                if frame.get('type') == 'eof': break
                if frame.get('type') == 'ready':
                    p.stdin.write(json.dumps({'id': 'light-probe', 'type': 'get_available_commands'}) + '\n')
                    p.stdin.flush()
                if frame.get('id') == 'light-probe':
                    names = {c.get('name') for c in frame.get('data', {}).get('commands', [])}
                    public = {n for n in names if isinstance(n, str) and n.startswith('aidevflow-')}
                    found = public == {'aidevflow-flow', 'aidevflow-discover'} and {
                        'skill:zsspec-spec', 'skill:zsspec-plan', 'skill:zsspec-apply', 'skill:zsspec-archive'} <= names
                    break
        finally:
            p.stdin.close()
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.terminate(); p.wait(timeout=5)
            for t in threads: t.join(timeout=2)
            for stream in (p.stdout, p.stderr): stream.close()
        print(json.dumps({'native_light_discovery': found, 'model_prompt_sent': False, 'exit_code': p.returncode}))
        if not found:
            # Isolated profile: no host credentials or chat history is read or printed.
            print(''.join(errors)[-2000:])
        return 0 if found and p.returncode == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
