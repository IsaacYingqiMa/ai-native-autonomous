"""Resume only already-authorized, byte-validated local transactions."""


def resume_checkpoint(root):
    from ..gate import Gate, read_json, safe_id
    gate = Gate(root)
    if not gate.active_path.is_file():
        return {"recovered": []}
    task = safe_id(read_json(gate.active_path)["task_id"])
    run = gate.runs / task
    recovered = []
    if (run / "engineering-transaction.json").is_file():
        from .revisions import recover
        recover(gate)
        recovered.append("engineering_revision")
    if (run / "archive-transaction.json").is_file():
        from .delivery import commit
        commit(gate, recover=True)
        recovered.append("delivery_archive")
    state, _, contract = gate._load_active()
    from ..method_adapters.zsspec_docs import enabled
    blocker = state.get("blocker", {})
    if enabled(contract) and state.get("status") == "needs_human" and (blocker.get("kind") == "repair_budget" or
            blocker.get("kind") == "product" and blocker.get("failure_type") in ("review_finding", "acceptance_test_failure")
            and contract.get("routing", {}).get(blocker["failure_type"], {}).get("to") == "build"):
        from .autonomy import recover_routine
        recover_routine(gate)
        recovered.append("routine_repair")
    return {"recovered": recovered}
