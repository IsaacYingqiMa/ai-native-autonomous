"""Bounded local worker. Human operations are absent from the model tool surface."""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

from ...storage import GateError, read_json, write_json_atomic
from .engine import LightGate, doctor, installed
from .revision import WorkflowError
from ...locking import observer_lock, writer_lock

TOOL_ACTIONS = {'report_conflict', 'status', 'requirements_context', 'validate', 'submit', 'run', 'retry',
                'environment', 'prepare_baseline', 'baseline_context', 'repair_propose', 'repair_apply',
                'prepare_verifier', 'verifier_context', 'archive'}
USER_ACTIONS = {'approve', 'confirm', 'pause', 'resume', 'abandon', 'requirement_feedback', 'delivery_feedback'}


def dispatch(gate, action, data, source):
    if action in TOOL_ACTIONS and source == 'model_tool':
        return model_operation(gate, {'action': action, **data})
    if source not in ('native_user_command', 'native_user_interaction') or action not in USER_ACTIONS:
        raise GateError('该操作只接受原生用户命令')
    if action == 'abandon':
        return gate.archive({}, abandoned=True, by=data['by'])
    return getattr(gate, action)(**data)


def model_operation(gate, params):
    action = params.get('action') if isinstance(params, dict) else None
    try:
        fields = {'status': (), 'requirements_context': (),
                  'environment': (), 'prepare_baseline': (), 'baseline_context': (), 'repair_propose': ('path',), 'repair_apply': (),
                  'report_conflict': ('reason',), 'validate': ('kind', 'path'), 'submit': ('kind', 'path'), 'run': (), 'retry': ('reason',),
                  'prepare_verifier': (), 'verifier_context': (), 'archive': ()}
        if action not in fields or set(params) != {'action', *fields[action]}:
            raise GateError('只接受当前操作的明确字段；使用文件路径提交，不支持 data 或内联对象')
        if any(not isinstance(params[k], str) or not params[k].strip() for k in fields[action]):
            raise GateError('操作参数必须为非空字符串')
        if gate.active.exists() and gate.load()[0]['stage'] in ('needs_attention', 'paused') and action not in ('status', 'environment'):
            raise GateError('当前已停止，使用 flow 恢复后再执行')
        if action == 'status': return gate.summary()
        if action == 'environment':
            from .environment import inspect
            return inspect(gate.root)
        if action == 'repair_propose': return gate.propose_repair(params['path'])
        if action == 'repair_apply': return gate.apply_repair()
        if action in ('validate', 'submit'):
            result = getattr(gate, action + '_artifact')(params['kind'], params['path'])
            if action == 'validate' and not result['valid']:
                result.update(gate.operation_failure('validate', '; '.join(result['errors']), code='SCHEMA_INVALID', artifact_kind=params['kind']))
            return result
        if action in ('requirements_context', 'verifier_context', 'prepare_verifier', 'prepare_baseline', 'baseline_context'):
            return getattr(gate, action)()
        result = getattr(gate, action)(**{k: params[k] for k in fields[action]})
        return gate.summary() if action != 'archive' else {**result, 'active_run': None}
    except (GateError, ValueError, KeyError, TypeError, OSError) as exc:
        diagnostic = getattr(exc, 'details', {})
        failure = gate.operation_failure(str(action), str(exc), code=getattr(exc, 'code', 'OPERATION_REJECTED'),
            artifact_kind=params.get('kind', '') if isinstance(params, dict) else '', diagnostic=diagnostic) if action != 'status' else {}
        if diagnostic: failure['diagnostic'] = diagnostic
        suffix = '；已停止重复操作，检查 flow 状态后恢复' if failure.get('stopped') else ''
        raise WorkflowError(getattr(exc, 'code', 'OPERATION_REJECTED'), str(exc) + suffix, failure) from exc


def serve(root: Path):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    gate = LightGate(root)
    lease = None
    try:
        while True:
            raw = sys.stdin.buffer.readline(8 * 1024 * 1024 + 1)
            if not raw:
                return 0
            if len(raw) > 8 * 1024 * 1024:
                return 2
            request = {}
            try:
                request = json.loads(raw)
                if not isinstance(request, dict) or request.get('protocol') != 1 or request.get('runtime') != 'zscode' or not isinstance(request.get('id'), str) or not installed(root):
                    raise GateError('无效 light bridge 身份或安装配置')
                action, payload = request['action'], request.get('payload', {})
                if not isinstance(payload, dict):
                    raise GateError('payload 必须为对象')
                actor = payload.get('actor', {})
                verifier = actor.get('name') in ('aidevflow-independent-verifier', 'aidevflow-requirements-analyst', 'aidevflow-baseline-reviewer')
                if verifier and action != 'guard':
                    raise GateError('独立验收角色只能读取固定 bundle')
                if action not in ('status', 'doctor') and not verifier and lease is None:
                    lease = observer_lock(gate.control, timeout=0, name='coordinator.lock')
                    try:
                        lease.__enter__()
                    except BaseException:
                        lease = None
                        raise
                if action == 'status':
                    result = gate.status()
                elif action == 'doctor':
                    result = doctor(root)
                elif action == 'verifier-assignment':
                    result = gate.verifier_assignment()
                elif action == 'baseline-assignment':
                    result = gate.baseline_assignment()
                elif action == 'guard':
                    result = gate.guard(payload['toolName'], payload['input'], actor)
                elif action == 'observe-verifier':
                    result = gate.observe_verifier(payload)
                elif action == 'observe-requirements':
                    result = gate.observe_requirements(payload)
                elif action == 'observe-baseline':
                    result = gate.observe_baseline(payload)
                elif action == 'questions':
                    result = gate.record_questions(payload)
                elif action == 'activity':
                    result = gate.activity(payload['category'])
                elif action == 'operation-failure':
                    result = gate.operation_failure(payload['action'], payload['reason'])
                elif action == 'start-request':
                    if payload.get('source') != 'native_user_interaction' or not isinstance(payload.get('text'), str):
                        raise GateError('原始需求只能由用户入口固定')
                    result = gate.create('change-' + secrets.token_hex(4), payload['text'])
                elif action == 'recover-verifier':
                    result = gate.recover_verifier(payload['session_id'], payload['entries'])
                    gate.recover_requirements(payload['session_id'], payload['entries'])
                    gate.recover_baseline(payload['session_id'], payload['entries'])
                    gate.recover_questions(payload['session_id'], payload['entries'])
                elif action == 'tool':
                    result = model_operation(gate, payload)
                else:
                    result = dispatch(gate, action, payload.get('data', {}), payload.get('source'))
                reply = {'id': request['id'], 'ok': True, 'result': result}
            except (GateError, ValueError, KeyError, TypeError, OSError, TimeoutError) as exc:
                reply = {'id': request.get('id') if isinstance(request, dict) else None, 'ok': False, 'error': str(exc), 'failure': getattr(exc, 'details', {})}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        if lease:
            lease.__exit__(None, None, None)
