"""Exercise the installed Python bridge with explicit synthetic native receipts, no model."""
from __future__ import annotations
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE), str(SOURCE / 'tests')]
import test_light_workflow as workflow
from aidevflow.storage import read_json, write_json_atomic


def main():
    fixture = workflow.LightWorkflowTests(); fixture.setUp()
    worker = None
    try:
        fixture.begin(); fixture.write_test(1); fixture.gate.prepare_baseline()
        worker = subprocess.Popen([sys.executable, '-B', str(fixture.root / 'scripts/aidevflow_bridge.py')],
            cwd=fixture.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        frames = queue.Queue()
        def read():
            for line in worker.stdout: frames.put(json.loads(line))
        reader = threading.Thread(target=read, daemon=True); reader.start()
        index = 0
        def request(action, payload=None):
            nonlocal index
            index += 1
            worker.stdin.write(json.dumps({'protocol': 1, 'runtime': 'zscode', 'id': str(index),
                                           'action': action, 'payload': payload or {}}, ensure_ascii=False) + '\n')
            worker.stdin.flush()
            result = frames.get(timeout=20)
            assert result['id'] == str(index)
            return result
        public = request('tool', {'action': 'baseline_context'})['result']
        assert 'outputSchema' not in public['task_call']
        assignment = request('baseline-assignment')['result']
        assert assignment['task_call']['schemaMode'] == 'strict'
        observed = {'type': 'tool_call', 'session_id': 'explicit-transport-fixture', 'toolCallId': 'native-1',
                    'input': assignment['task_call']}
        assert request('observe-baseline', observed)['ok']
        assert request('baseline-assignment')['result']['phase'] == 'reviewing'
        manifest = read_json(fixture.root / assignment['path'] / 'BUNDLE.json')
        value = {'schema': 'aidevflow/baseline-review-v1', 'run_id': fixture.gate.status()['run_id'],
            'bundle_sha256': assignment['sha256'], 'decision': 'approve',
            'reason': 'Explicit synthetic transport fixture, not a real model review',
            'acceptance_ids': manifest['acceptance_ids'], 'changed_paths': manifest['changed_paths']}
        response = {**observed, 'type': 'tool_result', 'details': {'results': [{
            'id': 'fixture-role-1', 'agent': 'aidevflow-baseline-reviewer', 'exitCode': 0,
            'structuredOutput': {'status': 'valid', 'data': value}}]}}
        assert request('observe-baseline', response)['result']['status'] == 'completed'
        approved = request('baseline-assignment')['result']
        assert approved['phase'] == 'approved' and 'task_call' not in approved
        duplicate = request('observe-baseline', {**observed, 'toolCallId': 'native-duplicate'})
        assert not duplicate['ok']
        result = request('tool', {'action': 'run'})
        assert result['ok'] and result['result']['current_task']['kind'] == 'green'
        worker.stdin.close(); worker.wait(timeout=10); reader.join(timeout=3)
        assert worker.returncode == 0
        report = {'installed_bridge': True, 'strict_host_contract': True, 'duplicate_dispatch_blocked': True,
                  'real_red_to_green_cursor': True, 'native_receipts': 'explicit synthetic fixture', 'model_prompt_sent': False}
        write_json_atomic(SOURCE / '.test-tmp/lifecycle-transport-result.json', report)
        print(json.dumps(report))
    finally:
        if worker and worker.poll() is None:
            worker.terminate(); worker.wait(timeout=5)
        if worker:
            for stream in (worker.stdout, worker.stderr): stream.close()
        fixture.tearDown()


if __name__ == '__main__': main()
