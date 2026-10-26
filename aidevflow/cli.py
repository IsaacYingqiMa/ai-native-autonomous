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

    sub.add_parser("install", help="install the AIDevFlow runtime, Claude agents/skill, and hooks into a Git project")

    init = sub.add_parser("init", help="start one workflow run")
    init.add_argument("--task-id", required=True)
    init.add_argument("--contract", required=True, help="contract JSON path relative to target repository")

    intake = sub.add_parser("start-intake", help="start a durable run from a natural-language request document")
    intake.add_argument("--request", required=True, help="UTF-8 request document path relative to target repository")

    register = sub.add_parser("register-contract", help="validate and pin the generated contract after Intent")
    register.add_argument("--draft", required=True, help="generated contract JSON path relative to target repository")

    sub.add_parser("status", help="show active workflow state")
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

    sub.add_parser("integrity", help="verify frozen test hashes")

    run = sub.add_parser("run-tests", help="execute a named argv test suite from the contract")
    run.add_argument("--suite", required=True)
    run.add_argument("--target", help="test file or ID substituted for {target} in the contract argv")

    sub.add_parser("prepare-verifier", help="build a read-only evidence bundle")

    verdict = sub.add_parser("record-verdict", help="validate and record independent verdict JSON")
    verdict.add_argument("--path", required=True)

    route = sub.add_parser("route", help="route a failure and enforce repair limits")
    route.add_argument("--type", required=True)
    route.add_argument("--summary", required=True)

    stop = sub.add_parser("check-stop", help="return a non-zero code when the active run is incomplete")
    sub.add_parser("archive", help="close the active run after delivery or human escalation")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        gate = Gate(Path(args.root))
        if args.command == "install":
            from .installer import install
            source_root = Path(__file__).resolve().parents[1]
            result = install(source_root, Path(args.root))
        elif args.command == "init":
            result = gate.init(args.task_id, args.contract)
        elif args.command == "start-intake":
            result = gate.start_intake(args.request)
        elif args.command == "register-contract":
            result = gate.register_contract(args.draft)
        elif args.command == "status":
            result = gate.status()
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
        elif args.command == "run-tests":
            result = gate.run_tests(args.suite, args.target)
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
        if args.command == "run-tests" and not (result["valid_red"] if args.suite == "red" else result["passed"]):
            return 1
        return 0
    except GateError as exc:
        try:
            if gate.active_path.is_file():
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


if __name__ == "__main__":
    raise SystemExit(main())
