"""Persisted semantic progress and a single continuation budget for the native host."""
from __future__ import annotations
import json
import hashlib
import time
from ...storage import GateError, read_json, write_json_atomic
from ...locking import writes

READS = {'read', 'grep', 'glob', 'web_search'}
LIMITS = {'providers': 160, 'reads': 256, 'seconds': 10800}
FAILURE_LIMITS = {'operation_warning': 3, 'operation_repeats': 12, 'operation_total': 30,
                  'execution_failures': 20, 'baseline_reviews': 16,
                  'verification_failures': 16, 'repair_rounds': 20}


def configured_limits(config, key, defaults):
    value = config.get(key, {})
    if not isinstance(value, dict) or set(value) - set(defaults) or any(type(v) is not int or v < 1 for v in value.values()):
        raise GateError(key + ' 仅接受已定义预算键的正整数上限：' + ', '.join(defaults))
    result = {**defaults, **value}
    if key == 'failure_limits' and not result['operation_warning'] < result['operation_repeats'] <= result['operation_total']:
        raise GateError('failure_limits 需要 operation_warning < operation_repeats <= operation_total')
    return result


def target_assertion_failure(output):
    """DOM queries assert required UI presence, just as expect/assert does."""
    import re
    clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', output)
    return bool(re.search(r'assertionerror|^FAIL:|^E\s+assert|expected:|testinglibraryelementerror:\s*unable to find', clean, re.I | re.M))


def omitted_tests(output):
    import re
    return bool(re.search(r'Ran 0 tests|no tests ran|skipped=[1-9]|\b[1-9]\d* skipped\b|^(?:#|ℹ) (?:tests\s+0|skipped\s+[1-9]\d*)\b', output, re.I | re.M))


def test_counts(output):
    import re
    clean = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', output)
    # Vitest/Jest print file/suite counts before test counts; do not mistake one failed file for five failed cases.
    summaries = re.findall(r'^\s*Tests\s+(.+)$', clean, re.M)
    line = summaries[-1] if summaries else clean
    failed = list(re.finditer(r'\b(\d+) failed\b|failures=(\d+)|^(?:#|ℹ) fail\s+(\d+)', line, re.M))
    passed = list(re.finditer(r'\b(\d+) passed\b|^(?:#|ℹ) pass\s+(\d+)', line, re.M))
    count = lambda matches: int(next(x for x in matches[-1].groups() if x is not None)) if matches else None
    return {'failed': count(failed), 'passed': count(passed)}


class Progress:
    def record_native_progress(self, state, run, event):
        roles = {'requirements_analyst_dispatched': 'analyst_invocation', 'requirements_analyst_finished': 'analyst_invocation',
                 'baseline_review_dispatched': 'baseline_invocation', 'baseline_review_finished': 'baseline_invocation',
                 'verifier_dispatched': 'verifier_invocation', 'verifier_result_observed': 'verifier_invocation'}
        key = roles.get(event)
        if not key or not state.get(key): return
        receipt = read_json(self.path(state[key]))
        value = receipt.get('value', receipt.get('verdict', {}))
        if receipt.get('status') == 'completed' and (key != 'baseline_invocation' or value.get('decision') == 'approve'):
            # The subject/result is progress once; call identity and repeated delivery are not.
            marker = {'subject': receipt.get('bundle', receipt.get('source_digest')),
                      'result': receipt.get('result_sha256'), 'decision': value.get('decision')}
            scope = self.operation_scope(state)
            if key == 'analyst_invocation': scope += ':' + str(receipt.get('source_digest'))
            milestones = state.setdefault('native_milestones', {})
            if milestones.get(key, {}).get('scope') != scope:
                milestones[key] = {'scope': scope, 'result': marker}
        path = run / 'progress.json'
        if path.is_file():
            budget = read_json(path); now = time.monotonic()
            waiting = any(not r['finished'] for r in self.native_progress(state).values())
            if not budget.get('waiting_for_native'):
                budget['active_seconds'] = budget.get('active_seconds', 0) + max(0, now - budget.get('last_activity', now))
            budget.update(last_activity=now, waiting_for_native=waiting)
            write_json_atomic(path, budget)

    def native_progress(self, state):
        receipts = {}
        for key in ('analyst_invocation', 'baseline_invocation', 'verifier_invocation'):
            if state.get(key):
                r = read_json(self.path(state[key]))
                receipts[key] = {'id': r.get('id'), 'status': r.get('status'), 'finished': r.get('execution_finished', False)}
        return receipts

    def progress_limits(self):
        return configured_limits(read_json(self.control / 'project.json'), 'progress_limits', LIMITS)

    def failure_limits(self):
        return configured_limits(read_json(self.control / 'project.json'), 'failure_limits', FAILURE_LIMITS)

    def budget_status(self):
        if not self.active.exists(): return None
        state, run = self.load();p = run / 'progress.json'
        value = read_json(p) if p.is_file() else {}
        # Report the current work segment even before its next activity hook.
        # Preserve an exhausted segment so the stop remains explainable.
        if value and not value.get('stopped') and state['stage'] not in ('paused', 'needs_attention', 'delivery_ready') and value.get('fingerprint') != self.progress_fingerprint(state):
            value = {}
        return {'limits': self.progress_limits(), 'failure_limits': self.failure_limits(),
                'operation_failures': {k: state.get('operation_failure', {}).get(k, 0) for k in ('count', 'total')},
                'used': {k: value.get(k, 0) for k in ('providers', 'reads', 'active_seconds')},
                'waiting_for_native': any(not r['finished'] for r in self.native_progress(state).values()),
                'triggered': value.get('triggered', []), 'scope': 'since last durable workflow milestone; native waiting time excluded'}

    def progress_fingerprint(self, state):
        # Revisions, retry messages, elapsed times and cosmetic source edits are not progress.
        return json.dumps({'cursor': state['cursor'],
            'business_source': self.requirements_source(state) if state['stage'] == 'discovery' else None,
            'approval': state.get('approval', {}).get('sha256'),
            'requirements': state.get('requirements', {}).get('sha256'),
            'spec': state.get('spec', {}).get('sha256'),
            'tasks': [(t['id'], bool(t.get('completed'))) for t in state['tasks']],
            'artifacts': {k: {field: v.get(field) for field in ('sha256', 'approval', 'baseline_version', 'candidate')}
                          for k, v in state.get('submitted_artifacts', {}).items()},
            'verifier_subject': state.get('verifier_subject'),
            'native': state.get('native_milestones', {}),
            'baseline_version': state.get('baseline_version', 0)}, sort_keys=True)

    @writes
    def activity(self, category):
        if not self.active.exists(): return {'stopped': False}
        self.failure_limits()  # Reject an invalid policy before spending or executing anything.
        state, run = self.load()
        if state['stage'] in ('paused', 'needs_attention', 'delivery_ready'):
            return {'stopped': True, 'stage': state['stage'], 'reason': state.get('attention_reason', '等待用户审查或恢复')}
        path = run / 'progress.json'
        now = time.monotonic()
        fingerprint = self.progress_fingerprint(state)
        previous = read_json(path) if path.is_file() else {}
        if previous.get('fingerprint') != fingerprint or previous.get('started', now) > now:
            previous = {'fingerprint': fingerprint, 'started': now, 'last_activity': now, 'active_seconds': 0, 'providers': 0, 'reads': 0}
        waiting = any(not r['finished'] for r in self.native_progress(state).values())
        if not waiting and not previous.get('waiting_for_native'): previous['active_seconds'] = previous.get('active_seconds', 0) + max(0, now - previous.get('last_activity', now))
        previous['last_activity'] = now
        previous['waiting_for_native'] = waiting
        if category == 'provider': previous['providers'] += 1
        if category in READS: previous['reads'] += 1
        if category in ('write', 'edit', 'apply_patch', 'run'): previous['reads'] = 0
        limits = self.progress_limits()
        triggered = [name for name, used in (('providers', previous['providers']), ('reads', previous['reads']), ('seconds', previous['active_seconds'])) if used >= limits[name]]
        exhausted = bool(triggered)
        previous['triggered'] = triggered
        previous['stopped'] = exhausted
        write_json_atomic(path, previous)
        if exhausted:
            try: self.integrity(state)
            except (GateError, OSError): return {'stopped': True, 'reason': '完整性检查失败，保持停止并保留原文件'}
            state.update(attention_stage=state['stage'], stage='needs_attention', attention_kind='no_progress',
                         attention_reason='当前工作项没有语义进展；达到 ' + ', '.join(f"{k}={int(previous['active_seconds']) if k == 'seconds' else previous[k]}/{limits[k]}" for k in triggered) + '，停止自动续跑。')
            self.checkpoint(state, run, 'semantic_progress_budget_exhausted')
        return {'stopped': exhausted, 'stage': state['stage'], 'providers': previous['providers'], 'reads': previous['reads'],
                **({'reason': state['attention_reason']} if exhausted else {})}

    @writes
    def record_questions(self, payload):
        state, run = self.load()
        if state['stage'] != 'discovery': raise GateError('ask 只用于入口业务澄清；开发问题通过修复机制处理')
        call = payload.get('toolCallId')
        session = payload.get('session_id')
        if not isinstance(call, str) or not isinstance(session, str): raise GateError('需要实际原生问答身份')
        key = hashlib.sha256((session + ':' + call).encode()).hexdigest()
        path = run / 'business-questions' / (key + '.json')
        if payload.get('type') == 'tool_call':
            value = state.get('requirements', {}).get('value')
            if not value and state.get('analyst_invocation'):
                receipt = read_json(self.path(state['analyst_invocation']))
                if receipt.get('status') == 'completed': value = read_json(self.path(receipt['result_path']))
            questions = {q['id']: q for q in (value or {}).get('intent', {}).get('open_questions', [])}
            asked = payload.get('input', {}).get('questions', [])
            if not asked or any(not isinstance(q, dict) or q.get('id') not in questions for q in asked):
                raise GateError('请使用当前 Intent 的真实问题 ID 提问；不得用 ask 代替暂停、放弃、重启或批准')
            from ...storage import write_json_new
            canonical = [{'id': q['id'], 'question': questions[q['id']]['question'],
                'options': [{'label': o['label'], 'description': o['impact']} for o in questions[q['id']]['options']]} for q in asked]
            record = {'session': session, 'call': call, 'source': self.requirements_source(state),
                'questions': {q['id']: questions[q['id']]['question'] for q in asked}}
            if path.exists():
                prior = read_json(path)
                if any(prior[k] != record[k] for k in record): raise GateError('原生问答调用身份复用冲突')
            else: write_json_new(path, record)
            return {'recorded': True, 'input': {'questions': canonical}}
        if not path.is_file(): raise GateError('问答没有原生派发锚点')
        record = read_json(path)
        if record.get('completed'): return {'recorded': True, 'replayed': True}
        if payload.get('isError'): return {'recorded': False}
        details = payload.get('details') or {}
        if isinstance(details, dict) and (details.get('timedOut') or details.get('chatRedirect') or
                any(r.get('timedOut') for r in details.get('results', []) if isinstance(r, dict))):
            return {'recorded': False, 'reason': '未将超时默认选择当作真实业务回答'}
        if record['source'] != self.requirements_source(state): raise GateError('业务问答来源已过期')
        # The host forwards a real tool_result, never model-authored answer parameters.
        text = '\n'.join(c.get('text', '') for c in payload.get('content', []) if isinstance(c, dict) and c.get('type') == 'text').strip()
        if not text or 'cancel' in text.lower() and len(text) < 80: return {'recorded': False}
        self.requirement_feedback('原生业务问答：\n' + json.dumps(record['questions'], ensure_ascii=False) + '\n' + text,
                                  by='zscode user:' + session)
        record.update(completed=True, answer=text)
        write_json_atomic(path, record)
        return {'recorded': True, 'source_updated': True}

    def blocking_questions(self, state):
        value = state.get('requirements', {}).get('value', {})
        decisions = {d['question_id'] for d in value.get('intent', {}).get('decisions', [])}
        _, run = self.load()
        answered = {qid for p in (run / 'business-questions').glob('*.json')
            if (r := read_json(p)).get('completed') for qid in r.get('questions', {})}
        return [q['id'] for q in value.get('intent', {}).get('open_questions', [])
            if q['blocking'] and q['id'] not in decisions.intersection(answered)]

    def recover_questions(self, session_id, entries):
        if not self.active.exists() or self.load()[0]['stage'] != 'discovery': return {'recovered': False}
        _, run = self.load()
        pending = {r['call']: r for p in (run / 'business-questions').glob('*.json')
                   if (r := read_json(p)).get('session') == session_id and not r.get('completed')}
        recovered = False
        for entry in entries:
            message = entry.get('message', {})
            record = pending.get(message.get('toolCallId'))
            if message.get('role') != 'toolResult' or message.get('toolName') != 'ask' or not record: continue
            if record['source'] != self.requirements_source(self.load()[0]): continue
            result = self.record_questions({'type': 'tool_result', 'session_id': session_id, 'toolCallId': record['call'],
                'details': message.get('details'), 'content': message.get('content', []), 'isError': message.get('isError', False)})
            recovered = recovered or result.get('source_updated', False)
        return {'recovered': recovered}
