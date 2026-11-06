"""One output contract for native yield validation and final Gate registration."""
from __future__ import annotations
import copy


def bound_schema(base, run_id, bundle_sha, spec_ids, paths, failed_ids=(), require_revise=False):
    schema = copy.deepcopy(base)
    props = schema['properties']
    props['run_id'] = {'const': run_id}
    props['bundle_sha256'] = {'const': bundle_sha}
    if require_revise: props['decision'] = {'const': 'revise'}
    items = props['items']
    items.update(minItems=len(spec_ids), maxItems=len(spec_ids))
    item = items['items']['properties']
    item['id'] = {'type': 'string', 'enum': list(spec_ids)}
    item['evidence']['items'] = {'type': 'string', 'enum': sorted(set(paths)),
        'description': 'Exact BUNDLE.files[].path only. Put labels, line numbers and explanations in reason.'}
    # Exactly N items, each of the N IDs present, implies each ID occurs once.
    items['allOf'] = [{'contains': {'type': 'object', 'required': ['id'],
        'properties': {'id': {'const': ident}, **({'status': {'const': 'failed'}} if ident in failed_ids else {})}}}
        for ident in spec_ids]
    return schema


def verdict_errors(value, run_id, bundle_sha, spec_ids, paths, failed_ids=(), require_revise=False):
    errors = []
    if not isinstance(value, dict) or set(value) != {'schema', 'run_id', 'bundle_sha256', 'decision', 'reason', 'items'}:
        return ['$: 独立验收输出字段必须精确包含 schema/run_id/bundle_sha256/decision/reason/items']
    if value['schema'] != 'aidevflow/light-verdict-v1' or value['run_id'] != run_id or value['bundle_sha256'] != bundle_sha:
        errors.append('$.run_id/bundle_sha256: 必须绑定本任务和当前 bundle，不能沿用其他验收包')
    decision = value['decision']
    if decision not in ('pass', 'revise') or not isinstance(value['reason'], str) or not value['reason'].strip():
        errors.append('$.decision/reason: 需要 pass/revise 和非空原因')
    if require_revise and decision != 'revise': errors.append('$.decision: 本轮仅纠正格式，必须保留先前 revise/未通过发现，不能改成 pass')
    items = value['items']
    if not isinstance(items, list): return errors + ['$.items: 必须为数组']
    ids = [i.get('id') for i in items if isinstance(i, dict) and isinstance(i.get('id'), str)]
    if len(items) != len(spec_ids) or len(ids) != len(set(ids)) or set(ids) != set(spec_ids):
        errors.append('$.items.id: 必须精确覆盖全部规格且每项一次；expected=' + ','.join(spec_ids)
                      + '; missing=' + ','.join(sorted(set(spec_ids) - set(ids)))
                      + '; unexpected=' + ','.join(sorted(set(ids) - set(spec_ids)))
                      + '。总体检查与总结写在顶层 reason，不新增条目。')
    allowed = set(paths)
    statuses = {}
    for n, item in enumerate(items):
        prefix = f'$.items[{n}]'
        if not isinstance(item, dict) or set(item) != {'id', 'status', 'reason', 'evidence'}:
            errors.append(prefix + ': 字段必须精确包含 id/status/reason/evidence');continue
        ident = item.get('id')
        status = item.get('status')
        if status not in ('passed', 'failed') or not isinstance(item.get('reason'), str) or not item['reason'].strip():
            errors.append(prefix + '.status/reason: 需要 passed/failed 和非空原因')
        if isinstance(ident, str): statuses[ident] = status
        refs = item.get('evidence')
        if not isinstance(refs, list) or not refs:
            errors.append(prefix + '.evidence: 必须提供非空的精确 bundle 证据路径数组');continue
        for j, path in enumerate(refs):
            if not isinstance(path, str) or path not in allowed:
                errors.append(prefix + f'.evidence[{j}]: 需要精确 BUNDLE.files[].path；不能拼接括号、文件名、行号或说明，说明移到 reason。got=' + str(path)[:160])
                if len(errors) >= 20: break
        if len(errors) >= 20: break
    if decision == 'pass' and any(status != 'passed' for status in statuses.values()):
        errors.append('$.decision: 存在未通过规格时不能给出 pass')
    for ident in failed_ids:
        if statuses.get(ident) != 'failed': errors.append(f'$.items[{ident}].status: 格式纠错必须保留先前的 failed 发现')
    return errors
