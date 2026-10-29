from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .gate import Gate, GateError


def _json(value: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aidevflow", description="AIDevFlow single-task workflow gate")
    parser.add_argument("--root", default=".", help="target Git repository root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="install AIDevFlow and a native coding-agent adapter")
    install.add_argument("--runtime", choices=("omp", "claude-code"), default="omp")
    install.add_argument("--skills-root", help="project-local method root; auto discovers .omp/skills or .claude/skills")

    init = sub.add_parser("init", help="start one workflow run")
    init.add_argument("--task-id", required=True)
    init.add_argument("--contract", required=True, help="contract JSON path relative to target repository")

    intake = sub.add_parser("start-intake", help="start a durable run from a natural-language request document")
    intake.add_argument("--request", required=True, help="UTF-8 request document path relative to target repository")

    register = sub.add_parser("register-contract", help="validate and pin the generated contract after Intent")
    register.add_argument("--draft", required=True, help="generated contract JSON path relative to target repository")

    status = sub.add_parser("status", help="show active workflow state")
    status.add_argument("--summary", action="store_true", help="skip candidate hashing; never use as evidence of acceptance")
    context = sub.add_parser("role-context", help="prepare a read-only native role assignment from the current Gate checkpoint")
    context.add_argument("--role", choices=("intent_analyst", "contract_planner", "verifier"), required=True)
    doctor = sub.add_parser("doctor", help="read-only runtime preflight; does not dispatch agents")
    doctor.add_argument("--session-id", help="inspect native transcript capabilities without importing observations")
    recover = sub.add_parser("recover-runtime", help="recover actual native role results lost to cancelled/missing Hooks")
    recover.add_argument("--session-id", help="native session ID; otherwise use known sources")
    continuation = sub.add_parser("continuation", help="select one owner of turn continuation; never accepts business work")
    continuation.add_argument("--owner", required=True, choices=("native", "bounded_stop", "manual"))
    sub.add_parser("sync-projections", help="project Gate state into registered generated-document status cells")
    resume = sub.add_parser("resume-run", help="resume human handling only after an explicit user instruction")
    resume.add_argument("--by", required=True)
    resume.add_argument("--utterance", required=True)
    resume.add_argument("--additional-attempts", type=int, default=1)
    resume.add_argument("--mode", choices=("repair", "retry"), default="repair", help="repair invalidates checks; retry preserves the checkpoint for a recorded infrastructure/legacy Stop failure")

    rebuild = sub.add_parser("rebuild", help="restore a run-state snapshot from the append-only event log")
    rebuild.add_argument("--task-id", required=True)

    intent = sub.add_parser("record-intent", help="validate and store an IntentDraft JSON")
    intent.add_argument("--draft", required=True)

    answer = sub.add_parser("answer", help="record a user answer to a blocking Intent question")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--option-id")
    answer.add_argument("--text", required=True)

    sub.add_parser("finalize-intent", help="render the accepted draft into intent.md")

    approve = sub.add_parser("approve-intent", help="record an explicit approval for the current intent hash")
    approve.add_argument("--by", required=True)
    approve.add_argument("--utterance", required=True)

    advance = sub.add_parser("advance", help="perform a validated transition to the next stage")
    advance.add_argument("--to", required=True)

    freeze = sub.add_parser("freeze-tests", help="hash contract test files during Design")
    freeze.add_argument("paths", nargs="*")

    work_plan = sub.add_parser("register-work-plan", help="validate and pin a method-neutral work plan during Design")
    work_plan.add_argument("--draft", required=True, help="work-plan-v1 JSON path under specs/aidevflow-intake/<TASK_ID>/")

    work_start = sub.add_parser("start-work-item", help="mark a registered work item in progress during Build")
    work_start.add_argument("--id", required=True)

    work_complete = sub.add_parser("complete-work-item", help="complete a registered work item with evidence")
    work_complete.add_argument("--id", required=True)
    work_complete.add_argument("--evidence", required=True, nargs="+", help="one or more repository-relative evidence paths")

    work_reopen = sub.add_parser("reopen-work-item", help="reopen a completed work item during Build")
    work_reopen.add_argument("--id", required=True)
    work_reopen.add_argument("--reason", required=True)

    review = sub.add_parser("record-design-review", help="record an AI coordinator review of current design, work plan and executable tests")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--evidence", required=True, nargs="+")

    check = sub.add_parser("record-check", help="record a candidate-bound static/manual AI attestation with immutable evidence")
    check.add_argument("--id", required=True)
    check.add_argument("--result", choices=("passed", "failed", "blocked"), required=True)
    check.add_argument("--candidate", required=True, help="candidate SHA captured before performing the check")
    check.add_argument("--reviewer", required=True)
    check.add_argument("--reason", required=True)
    check.add_argument("--evidence", required=True, nargs="+")

    sub.add_parser("integrity", help="verify frozen test hashes")

    run = sub.add_parser("run-tests", help="execute a named argv test suite from the contract")
    run.add_argument("--suite", required=True)
    run.add_argument("--target", help="test file or ID substituted for {target} in the contract argv")
    run.add_argument("--evidence", nargs="+", help="expected report/screenshot files under declared evidence_paths, captured after execution")
    run.add_argument("--against-base", action="store_true", help="execute Red against original Git implementation with the current frozen tests")

    sub.add_parser("diagnose-evidence", help="read-only diagnosis of execution history and damaged evidence")
    repair = sub.add_parser("plan-evidence-repair", help="snapshot damaged evidence and propose audited quarantine")
    repair.add_argument("--reason", required=True)
    repair = sub.add_parser("apply-evidence-repair", help="authorize a concrete repair plan; real replacement executions remain required")
    repair.add_argument("--plan", required=True)
    revision = sub.add_parser("plan-baseline-revision", help="snapshot a draft test/spec/Contract revision for review")
    revision.add_argument("--draft", required=True)
    revision = sub.add_parser("apply-baseline-revision", help="apply a reviewed versioned baseline using a recoverable transaction")
    revision.add_argument("--proposal", required=True)
    revision.add_argument("--reviewer", required=True)
    revision.add_argument("--review", required=True)
    for command in (repair, revision):
        command.add_argument("--sha256", required=True)
        command.add_argument("--by", required=True)
        command.add_argument("--utterance", required=True)
    transaction = sub.add_parser("recover-baseline-transaction", help="finish or roll back an interrupted revision")
    transaction.add_argument("--mode", choices=("finish", "rollback"), required=True)
    transaction.add_argument("--by", required=True)
    transaction.add_argument("--utterance", required=True)
    transaction.add_argument("--accept-conflicts", action="store_true", help="preserve conflicting bytes before explicitly replacing them")
    waiver = sub.add_parser("record-test-waiver", help="record an authorized exception for exact test cases and acceptance items")
    waiver.add_argument("--suite", required=True)
    waiver.add_argument("--target")
    waiver.add_argument("--case", required=True, nargs="+")
    waiver.add_argument("--acceptance", required=True, nargs="+")
    for name in ("reason", "by", "utterance"):
        waiver.add_argument("--" + name, required=True)

    sub.add_parser("prepare-verifier", help="build a read-only evidence bundle")

    verdict = sub.add_parser("record-verdict", help="validate and record independent verdict JSON")
    verdict.add_argument("--path", required=True)

    route = sub.add_parser("route", help="route a failure and enforce repair limits")
    route.add_argument("--type", required=True)
    route.add_argument("--summary", required=True)

    stop = sub.add_parser("check-stop", help="return a non-zero code when the active run is incomplete")
    sub.add_parser("archive", help="close the active run after delivery or human escalation")
    return parser


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        gate = Gate(Path(args.root))
        if args.command == "install":
            from .installer import install
            source_root = Path(__file__).resolve().parents[1]
            result = install(source_root, Path(args.root), runtime=args.runtime, skills_root=args.skills_root)
        elif args.command == "init":
            result = gate.init(args.task_id, args.contract)
        elif args.command == "start-intake":
            result = gate.start_intake(args.request)
        elif args.command == "register-contract":
            result = gate.register_contract(args.draft)
        elif args.command == "status":
            result = gate.status(summary=args.summary)
        elif args.command == "role-context":
            from .contexts import role_context
            result = role_context(Path(args.root).resolve(), args.role)
        elif args.command == "doctor":
            from .diagnostics import doctor
            result = doctor(Path(args.root))
            if args.session_id:
                from .runtimes import selected
                if selected(Path(args.root)) == "omp":
                    from .omp import inspect_session
                else:
                    from .runtime import inspect_session
                report = inspect_session(Path(args.root), args.session_id)
                result["native_session"] = {k: v for k, v in report.items() if k != "events"}
                result["native_session"]["recoverable_events"] = len(report["events"])
        elif args.command == "recover-runtime":
            from .runtimes import recover
            result = recover(Path(args.root), args.session_id)
        elif args.command == "continuation":
            from .continuation import set_owner
            result = set_owner(Path(args.root), args.owner)
        elif args.command == "sync-projections":
            result = gate.sync_projections()
        elif args.command == "resume-run":
            result = gate.resume_run(args.by, args.utterance, args.additional_attempts, args.mode)
        elif args.command == "rebuild":
            result = gate.rebuild(args.task_id)
        elif args.command == "record-intent":
            result = gate.record_intent(args.draft)
        elif args.command == "answer":
            result = gate.answer(args.question_id, args.option_id, args.text)
        elif args.command == "finalize-intent":
            result = gate.finalize_intent()
        elif args.command == "approve-intent":
            result = gate.approve_intent(args.by, args.utterance)
        elif args.command == "advance":
            result = gate.advance(args.to)
        elif args.command == "freeze-tests":
            result = gate.freeze_tests(args.paths)
        elif args.command == "register-work-plan":
            result = gate.register_work_plan(args.draft)
        elif args.command == "start-work-item":
            result = gate.start_work_item(args.id)
        elif args.command == "complete-work-item":
            result = gate.complete_work_item(args.id, args.evidence)
        elif args.command == "reopen-work-item":
            result = gate.reopen_work_item(args.id, args.reason)
        elif args.command == "integrity":
            result = {"ok": not gate.integrity(), "mismatches": []}
        elif args.command == "record-design-review":
            result = gate.record_design_review(args.reviewer, args.reason, args.evidence)
        elif args.command == "record-check":
            result = gate.record_check(args.id, args.result, args.candidate, args.reviewer, args.reason, args.evidence)
        elif args.command == "run-tests":
            result = gate.run_tests(args.suite, args.target, args.evidence, against_base=args.against_base)
        elif args.command == "diagnose-evidence":
            result = gate.diagnose_evidence()
        elif args.command == "plan-evidence-repair":
            result = gate.plan_evidence_repair(args.reason)
        elif args.command == "apply-evidence-repair":
            result = gate.apply_evidence_repair(args.plan, args.sha256, args.by, args.utterance)
        elif args.command == "plan-baseline-revision":
            result = gate.plan_baseline_revision(args.draft)
        elif args.command == "apply-baseline-revision":
            result = gate.apply_baseline_revision(args.proposal, args.sha256, args.by, args.utterance, args.reviewer, args.review)
        elif args.command == "recover-baseline-transaction":
            result = gate.recover_baseline_transaction(args.mode, args.by, args.utterance, args.accept_conflicts)
        elif args.command == "record-test-waiver":
            result = gate.record_test_waiver(args.suite, args.target, args.case, args.acceptance, args.reason, args.by, args.utterance)
        elif args.command == "prepare-verifier":
            result = gate.prepare_verifier()
        elif args.command == "record-verdict":
            result = gate.record_verdict(args.path)
        elif args.command == "route":
            result = gate.route(args.type, args.summary)
        elif args.command == "check-stop":
            state = gate.status()
            if state["current_stage"] == "verify" and state["status"] not in ("accepted", "needs_human"):
                raise GateError("Verification is incomplete; record the independent verdict or route the blocker")
            result = {"ok": True, "status": state["status"]}
        elif args.command == "archive":
            result = gate.archive()
        else:
            parser.error("unknown command")
        _json(result)
        if args.command == "doctor" and not result["ok"]:
            return 1
        if args.command == "run-tests" and not (result["valid_red"] if args.suite == "red" else result["passed"] or result.get("accepted_with_exceptions")):
            return 1
        if args.command == "record-check" and result["result"] != "passed":
            return 1
        return 0
    except (GateError, OSError, ValueError) as exc:
        try:
            if args.command not in ("doctor", "status", "role-context", "diagnose-evidence") and gate.active_path.is_file():
                from .gate import read_json
                task_id = read_json(gate.active_path).get("task_id")
                if isinstance(task_id, str):
                    gate._event(task_id, "command_rejected", command=args.command, reason=str(exc))
        except (GateError, OSError):
            pass
        print(f"AIDevFlow: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("AIDevFlow: interrupted; state was not advanced", file=sys.stderr)
        return 130


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in ("doctor", "status", "role-context", "diagnose-evidence", "install"):
        return _main(argv)
    from .locking import writer_lock
    try:
        # Serialize command writers, including standalone CLI and OMP shell calls.
        # Read-only diagnostics stay available while a test command is running.
        with writer_lock(Path(args.root).resolve() / ".aidevflow"):
            return _main(argv)
    except (OSError, TimeoutError) as exc:
        print(f"AIDevFlow: another Gate writer is active or the lock is unavailable: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
