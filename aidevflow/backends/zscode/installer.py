"""Project-local installer for the released zscode executable and bundled light skills."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ...storage import GateError, read_json, replace_file_atomic, sha256_bytes, sha256_file, write_json_atomic, write_json_new, write_bytes_new
from ...locking import observer_lock, writer_lock
from .engine import PROFILE, SKILLS, installed, LightGate
from .progress import LIMITS, FAILURE_LIMITS, configured_limits

RUNTIME_FILES = ('__init__.py', 'storage.py', 'requirements.py', 'locking.py', 'backends/__init__.py',
                 'backends/zscode/__init__.py', 'backends/zscode/engine.py', 'backends/zscode/review.py',
                 'backends/zscode/artifacts.py', 'backends/zscode/bridge.py', 'backends/zscode/cli.py',
                 'backends/zscode/environment.py', 'backends/zscode/progress.py', 'backends/zscode/repair.py',
                 'backends/zscode/verdict.py', 'backends/zscode/revision.py', 'backends/zscode/repair_lifecycle.py')


def install(source: Path, target: Path, *, skills_root=None, workflow_profile=None, upgrade_active=False, extend_budgets=False):
    target = target.resolve()
    if target == source.resolve() or not target.is_dir() or not (target / '.aidevflow').resolve().is_relative_to(target):
        raise GateError('请选择业务 Git 项目；安装控制目录不得链接越界')
    with observer_lock(target / '.aidevflow', timeout=0, name='coordinator.lock'), writer_lock(target / '.aidevflow'):
        return _install(source, target, skills_root=skills_root, workflow_profile=workflow_profile, upgrade_active=upgrade_active, extend_budgets=extend_budgets)


def _install(source: Path, target: Path, *, skills_root=None, workflow_profile=None, upgrade_active=False, extend_budgets=False):
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise GateError('不能将安装器安装到框架源码自身；请选择业务 Git 项目')
    probe = subprocess.run(['git', '-c', 'core.fsmonitor=false', 'rev-parse', '--show-toplevel'], cwd=target,
                           capture_output=True, text=True, check=False)
    if probe.returncode or Path(probe.stdout.strip()).resolve() != target:
        raise GateError('安装目标必须是业务 Git 项目根目录')
    if skills_root not in (None, 'auto', '.zscode/skills'):
        raise GateError('light 安装固定使用 .zscode/skills；方法包由安装器分发')
    if workflow_profile not in (None, 'reviewed-discovery-v1'):
        raise GateError('light 固定使用 Intent/PRD 入口审查和最终交付验收，不支持取消入口审查')
    control = target / '.aidevflow'
    if not control.resolve().is_relative_to(target) or not (target / '.gitignore').resolve().is_relative_to(target):
        raise GateError('安装控制目录或 .gitignore 链接越界')
    if any((control / p).exists() for p in ('active-run.json', 'active-discovery.json', 'discovery-handoff.json')) or (control / 'light-active.json').exists() and not upgrade_active:
        raise GateError('存在活动任务，先结束或归档，再安装/升级 light')
    if (control / 'project.json').exists() and not installed(target):
        raise GateError('这是旧版项目；先保留旧安装与运行数据，再在干净项目安装 light，不自动转换旧项目')
    config = read_json(control / 'project.json') if (control / 'project.json').exists() else {}
    for key, defaults in (('progress_limits', LIMITS), ('failure_limits', FAILURE_LIMITS)):
        values = configured_limits(config, key, defaults)
        config[key] = {k: max(v, defaults[k]) if extend_budgets else v for k, v in values.items()}
    resources = source / '.claude/skills-light'
    required = [resources / 'README.md', *(resources / s / 'SKILL.md' for s in SKILLS),
                source / 'integrations/zscode/index.ts', source / 'integrations/zscode/client.ts',
                source / 'integrations/zscode/flow.md', source / 'integrations/zscode/repair.schema.json']
    if any(not p.is_file() for p in required):
        raise GateError('安装源缺少完整 light 方法包或 zscode 扩展')
    old_manifest = read_json(control / 'light-install-manifest.json')['files'] if (control / 'light-install-manifest.json').is_file() else {}
    pending_path = control / 'light-install-pending.json'
    pending = read_json(pending_path)['files'] if pending_path.is_file() else {}
    active_state = None
    if upgrade_active and (control / 'light-active.json').exists():
        active_state, _ = LightGate(target).load()
        if active_state['stage'] in ('done', 'finalizing') or active_state.get('resume_stage') in ('done', 'finalizing'):
            raise GateError('该任务已最终验收或正在归档；先在旧安装中完成归档，再升级')
        if any(t.get('inflight') for t in active_state['tasks']):
            raise GateError('仍有未知在途测试，先核实旧进程已结束并恢复检查点，再升级')
        for key in ('analyst_invocation', 'verifier_invocation', 'baseline_invocation'):
            if active_state.get(key) and not read_json(target / active_state[key]).get('execution_finished'):
                raise GateError('仍有未知在途原生角色，先恢复真实结果再升级')
    files = {}
    # Prepare the whole copy map before mutation and detect source/target path escapes.
    for name in RUNTIME_FILES:
        files['.aidevflow/runtime/aidevflow/' + name] = (source / 'aidevflow' / name).read_bytes()
    for directory, dest in ((source / 'integrations/zscode', '.zscode/extensions/aidevflow'),
                            (resources, '.zscode/skills')):
        for p in directory.rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo'):
                if p.is_symlink() or not p.resolve().is_relative_to(directory.resolve()):
                    raise GateError('安装源不能包含越界链接')
                files[(Path(dest) / p.relative_to(directory)).as_posix()] = p.read_bytes()
    files['.zscode/skills/README.md'] = (
        '# AIDevFlow 托管安装说明\n\n本分发通过 `/aidevflow-flow` 和 `aidevflow_light` 工具运行。'
        '批准、勾选、验证和归档由 Gate 托管；以安装后的 `aidevflow-flow/SKILL.md` 为准。'
        '正常人工审查仅有入口Intent/PRD与最终产物验收，Git提交不作为门禁。\n\n---\n\n').encode() + files['.zscode/skills/README.md']
    for skill in SKILLS:
        rel = f'.zscode/skills/{skill}/SKILL.md'
        files[rel] += ('\n\n## AIDevFlow 安装约定\n\n本阶段受 Gate 管理；工具协议统一见 `.zscode/extensions/aidevflow/flow.md`。\n').encode('utf-8')
    files['.zscode/skills/aidevflow-flow/SKILL.md'] = (
        '---\nname: aidevflow-flow\ndescription: zscode 下审查Intent/PRD，自主执行light研发与独立验收，最后交付用户验收。\n---\n\n先读取 `.zscode/extensions/aidevflow/flow.md`，按 Gate 当前阶段执行。').encode()
    for name, module, call in (('aidevflow.py', 'cli', 'main()'), ('aidevflow_bridge.py', 'bridge', 'serve(root)')):
        files['scripts/' + name] = (
            "from pathlib import Path\nimport sys\nroot = Path(__file__).resolve().parents[1]\n"
            "sys.path.insert(0, str(root / '.aidevflow/runtime'))\n"
            f"from aidevflow.backends.zscode.{module} import {'serve' if module == 'bridge' else 'main'}\nraise SystemExit({call})\n").encode()
    for name in ('requirements', 'intent-draft', 'spec', 'plan', 'knowledge', 'verification'):
        files[f'.aidevflow/schemas/{name}.schema.json'] = (source / f'.aidevflow/schemas/{name}.schema.json').read_bytes()
    # The preparation phase belongs only to this runtime's installed plan schema.
    plan_schema = json.loads(files['.aidevflow/schemas/plan.schema.json'])
    if 'prepare' not in plan_schema['properties']['tasks']['items']['properties']['kind']['enum']:
        raise GateError('source plan schema must include prepare')
    files['.aidevflow/schemas/plan.schema.json'] = json.dumps(plan_schema, ensure_ascii=False, indent=2).encode()
    schema = json.loads((source / 'integrations/zscode/baseline.schema.json').read_text(encoding='utf8'))
    files['.zscode/agents/aidevflow-baseline-reviewer.md'] = (
        '---\nname: aidevflow-baseline-reviewer\ndescription: Independent read-only test baseline correctness review.\n'
        'tools: [read]\nblocking: true\nstructured: true\nadvisor: false\nreadSummarize: false\noutput: '
        + json.dumps(schema, ensure_ascii=False) + '\n---\n'
        + (source / 'integrations/zscode/baseline.md').read_text(encoding='utf8')).encode()
    schema = json.loads((source / 'integrations/zscode/verifier.schema.json').read_text(encoding='utf-8'))
    body = (source / 'integrations/zscode/verifier.md').read_text(encoding='utf-8')
    files['.zscode/agents/aidevflow-independent-verifier.md'] = (
        '---\nname: aidevflow-independent-verifier\ndescription: Independent read-only delivery verification of the exact frozen bundle.\n'
        'tools: [read]\nblocking: true\nstructured: true\nadvisor: false\nreadSummarize: false\noutput: '
        + json.dumps(schema, ensure_ascii=False) + '\n---\n' + body).encode()
    schema = json.loads((source / '.aidevflow/schemas/requirements.schema.json').read_text(encoding='utf-8'))
    files['.zscode/agents/aidevflow-requirements-analyst.md'] = (
        '---\nname: aidevflow-requirements-analyst\ndescription: Read-only requirements analysis with exact structured output.\n'
        'tools: [read, grep, glob]\nblocking: true\nstructured: true\nadvisor: false\nreadSummarize: false\noutput: '
        + json.dumps(schema, ensure_ascii=False) + '\n---\n'
        + (source / 'integrations/zscode/requirements.md').read_text(encoding='utf-8')).encode()
    for rel, data in files.items():
        p = target / rel
        if not p.resolve().is_relative_to(target):
            raise GateError('安装目标链接越界：' + rel)
        if p.exists() and rel not in old_manifest and rel not in pending:
            raise GateError('同名资源未登记为本安装器所有，保留原文件：' + rel)
        if p.is_file() and sha256_file(p) not in (old_manifest.get(rel), pending.get(rel)):
            raise GateError('受管文件已有本地修改，先审查并备份：' + rel)
    obsolete = []
    for rel, expected_hash in old_manifest.items():
        if rel in files or not rel.startswith('.aidevflow/runtime/aidevflow/'):
            continue
        p = target / rel
        if not p.resolve().is_relative_to(target):
            raise GateError('旧运行时路径越界：' + rel)
        if p.exists() and (not p.is_file() or sha256_file(p) != expected_hash):
            raise GateError('旧受管文件已有本地修改，先审查并备份：' + rel)
        obsolete.append(p)
    expected = {rel: sha256_bytes(data) for rel, data in files.items()}
    if pending and pending != expected:
        raise GateError('上次安装尚未完成，先使用同一版本安装源恢复')
    backup = read_json(pending_path).get('backup') if pending_path.is_file() else None
    if active_state and not pending:
        backup = f".aidevflow/light-runs/{active_state['run_id']}/upgrades/" + secrets.token_hex(12)
        folder = target / backup
        for rel in (*old_manifest, '.aidevflow/project.json', '.aidevflow/light-install-manifest.json'):
            p = target / rel
            if not p.resolve().is_relative_to(target): raise GateError('升级备份路径越界')
            if p.is_file(): write_bytes_new(folder / rel, p.read_bytes())
        write_json_new(folder / 'CHECKPOINT.json', active_state)
    write_json_atomic(pending_path, {'files': expected, 'backup': backup})
    for rel, data in files.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=p.parent, prefix='.light-install-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            replace_file_atomic(temporary, p)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for p in obsolete:
        p.unlink(missing_ok=True)
    config = {**config,
              'schema': 'aidevflow/project-v1', 'runtime': 'zscode', 'method_profile': PROFILE,
              'method_version': '2.4.2', 'transport_version': 3, 'workflow_profile': 'reviewed-discovery-v1', 'skills_root': '.zscode/skills', 'python': sys.executable}
    write_json_atomic(control / 'project.json', config)
    ignore = target / '.gitignore'
    old = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    entries = ('.aidevflow/light-runs/', '.aidevflow/light-active.json', '.aidevflow/light-knowledge.json',
               '.aidevflow/light-install-pending.json',
               '.zscode/sessions/', '.zscode/logs/', '.aidevflow/gate-write.lock', '.aidevflow/coordinator.lock', '__pycache__/', '*.py[cod]')
    missing = [line for line in entries if line not in old.splitlines()]
    if missing and not active_state:
        ignore.write_text(old.rstrip() + '\n# AIDevFlow light local execution data\n' + '\n'.join(missing) + '\n', encoding='utf-8')
    manifest = {rel: sha256_file(target / rel) for rel in files}
    write_json_atomic(control / 'light-install-manifest.json', {'schema': 'aidevflow/light-install-v1',
                                                              'profile': PROFILE, 'files': manifest})
    pending_path.unlink()
    if active_state: LightGate(target).migrate_repair_lifecycle()
    return {'target': str(target), 'runtime': 'zscode', 'method_profile': PROFILE,
            'progress_limits': config['progress_limits'], 'failure_limits': config['failure_limits'],
            'installed': sorted(files), 'upgrade_backup': backup,
            'next': ['重启 zscode', '/aidevflow-flow 继续' if active_state else '/aidevflow-flow <需求>（入口自动检查安装与环境）']}
