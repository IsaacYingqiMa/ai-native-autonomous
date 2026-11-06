"""Offline replay of the reported kanban domain failure in a disposable project.

Requires --project with the existing test, implementations and local node_modules.
Never installs dependencies, starts a model, or changes the input project.
"""
from __future__ import annotations
import argparse
import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from aidevflow.backends.zscode.engine import LightGate
from aidevflow.backends.zscode.environment import inspect
from aidevflow.backends.zscode.installer import install
from aidevflow.storage import sha256_file, write_json_atomic
from test_light_workflow import LightWorkflowTests, REQUIREMENTS, SPEC, MAP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args();source = args.project.resolve()
    inputs = ['package.json', 'vite.config.ts', 'src/tests/domain.test.ts', 'src/domain/task.ts', 'src/domain/storage.ts']
    before = {rel: sha256_file(source / rel) for rel in inputs}
    node = inspect(source, versions=False)['node']['executable']
    if not node or not (source / 'node_modules/vitest/vitest.mjs').is_file():
        raise RuntimeError('Requires existing local Node and Vitest; this probe never installs dependencies')
    base = SOURCE / '.test-tmp';base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base, prefix='kanban-replay-', ignore_cleanup_errors=True) as directory:
        root = Path(directory).resolve();root.relative_to(base.resolve())
        def git(*argv):
            subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'user.name=offline test', '-c',
                            'user.email=test@example.invalid', *argv], cwd=root, capture_output=True, check=True)
        git('init', '-q')
        (root / '.gitignore').write_text('node_modules/\n', encoding='utf8')
        git('add', '.');git('commit', '-qm', 'empty baseline')
        install(SOURCE, root)
        for rel in inputs[:3]:
            p = root / rel;p.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(source / rel, p)
        shutil.copytree(source / 'node_modules', root / 'node_modules')
        print(json.dumps({'phase': 'local_dependencies_copied', 'network_used': False}), flush=True)
        gate = LightGate(root)
        f = LightWorkflowTests();f.root = root;f.gate = gate
        value = copy.deepcopy(REQUIREMENTS)
        value['intent'].update(problem='Task domain behavior must match the kanban requirements',
            desired_outcome='Correct task model, persistence and case-insensitive title substring search',
            scope=['Task model', 'Local persistence', 'Case-insensitive title substring search'],
            success_criteria=['Task domain contract passes', 'Persistence and substring filtering pass'])
        value['prd'] = '# PRD\nTasks have title, description and status. Trim and require title. New tasks default to todo.\n' \
            'Count by status. Search only title using case-insensitive substring matching, without translation or keyword aliases.\n' \
            'Persist locally; seed the three examples once; deleted-all remains empty after reload.'
        value['requirements'][0]['statement'] = 'Task model and persistence obey the PRD; title search uses a case-insensitive substring'
        value['scenarios'][0].update(given='Tasks and empty local storage', when='Create, search and reload', then='The PRD contract holds')
        gate.create('kanban-domain', value['prd'])
        s = f.submit_requirements(value);gate.approve(s['requirements']['sha256'], 'simulated test review 1')
        spec = SPEC.replace('返回正确整数；不做则调用者结果错误。', value['prd']).replace('return one', 'satisfy the task model contract').replace('return two', 'persist tasks and use title substring search')
        gate.spec(spec, MAP)
        argv = [node, 'node_modules/vitest/vitest.mjs', 'run', 'src/tests/domain.test.ts']
        gate.plan([
            {'id': '1.1', 'title': 'domain RED', 'kind': 'red', 'group': 'domain', 'specs': ['S1', 'S2'], 'files': ['src/tests/domain.test.ts'], 'argv': argv},
            {'id': '1.2', 'title': 'domain GREEN', 'kind': 'green', 'group': 'domain', 'specs': ['S1', 'S2'], 'files': ['src/domain/task.ts', 'src/domain/storage.ts'], 'argv': argv, 'regression': argv},
            {'id': '2.1', 'title': 'full regression', 'kind': 'regression', 'specs': ['S1', 'S2'], 'files': [], 'argv': argv}])
        # Only this fixture simulates an already-frozen 2.2 RED, before new review/target checks existed.
        with patch.object(gate, 'check_test_targets'), patch.object(gate, 'require_baseline_review'), patch.object(gate, 'capture_red_origin'):
            assert gate.run()['cursor'] == 1
        for rel in inputs[3:]:
            p = root / rel;p.parent.mkdir(parents=True, exist_ok=True);shutil.copy2(source / rel, p)
        broken = gate.run()['current_task']['result']['counts']
        assert broken['failed'] == 5, broken
        print(json.dumps({'phase': 'original_failure_reproduced', 'counts': broken}), flush=True)
        draft = root / f"specs/drafts/{gate.status()['run_id']}"
        original = (root / 'src/tests/domain.test.ts').read_text(encoding='utf8')
        fixed = original.replace('../domain/taskStore.ts', '../domain/task.ts').replace('src/domain/taskStore.ts', 'src/domain/task.ts')
        bad = "expect(store.filterByTitle(tasks, 'DEV').map((task) => task.title)).toEqual(['开发首页']);"
        assert bad in fixed, 'The source test changed; review this targeted probe before running it'
        fixed = fixed.replace(bad, "expect(store.filterByTitle(tasks, 'DEV')).toEqual([]);\n    "
            "expect(store.filterByTitle([store.createTask('Dev page')], 'DEV').map((task) => task.title)).toEqual(['Dev page']);")
        (draft / 'fixed.test.ts').write_text(fixed, encoding='utf8', newline='\n')
        write_json_atomic(draft / 'repair.json', {'group': 'domain', 'reason': 'Fix mismatched planned module and erroneous translation expectation; preserve Chinese and English matching cases',
            'changes': {'src/tests/domain.test.ts': (draft / 'fixed.test.ts').relative_to(root).as_posix()}})
        approval = gate.status()['approval']
        gate.propose_repair((draft / 'repair.json').relative_to(root).as_posix());f.baseline_review();gate.apply_repair()
        after_test_repair = gate.run()['current_task']['result']['counts']
        assert after_test_repair['failed'] == 1, after_test_repair
        task = root / 'src/domain/task.ts';content = task.read_text(encoding='utf8')
        old = "const KEYWORD_ALIASES: Record<string, string> = { 开发: 'dev' };"
        assert old in content, 'Source implementation changed; review the targeted probe'
        task.write_text(content.replace(old, 'const KEYWORD_ALIASES: Record<string, string> = {};'), encoding='utf8', newline='\n')
        assert gate.run()['cursor'] == 2
        final = gate.run()
        assert final['stage'] == 'verify' and final['approval'] == approval
        assert final['tasks'][-1]['result']['counts'] == {'failed': None, 'passed': 9}, final['tasks'][-1]['result']['counts']
        assert {rel: sha256_file(source / rel) for rel in inputs} == before
        print(json.dumps({'network_used': False, 'input_project_unchanged': True, 'original': broken,
            'after_test_repair': after_test_repair, 'final': final['tasks'][-1]['result']['counts'],
            'baseline_version': final['baseline_version'], 'stage': final['stage'], 'native_reviews': 'simulated, no model'}, ensure_ascii=False))
    return 0


if __name__ == '__main__': raise SystemExit(main())
