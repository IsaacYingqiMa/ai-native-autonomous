# AIDevFlow

AIDevFlow is an independent control layer for AI-native software delivery. It owns intent capture, workflow state, gates, evidence, and independent acceptance. It can invoke a delivery method such as the repository's separate `zsspec-*` Claude Code skills.

This repository is the framework and reliability POC. The initial runtime is deliberately single-task and local. It uses JSON state plus an append-only event log; Git is required in the target project so that evidence can be tied to a code revision.

## Current status

The current workflow remains reliability v3. [Claude observer B and the complete usage tutorial](CLAUDE-COMPATIBILITY.md) add parent-dispatch observations, correlated results/completion, input-bound receipts, duplicate/out-of-order handling, read-only `doctor`, and migration of generated Hooks. The business stages and acceptance requirements are unchanged. Reinstalling preserves older active contracts and evidence; it does not upgrade their acceptance rules or manufacture missing method history. Historical zsspec POC designs are research inputs, not current execution instructions.

The local reliability POC has a natural-language Intake path: Gate snapshots a request, the read-only Intent Analyst asks only decision-driving questions, a separate Contract Planner generates the task Contract, and Gate validates and pins it before zsspec Design/Build. An independent Verifier evaluates a Gate-prepared evidence bundle. The observer B update passed 70 regression tests and a live Claude Code 2.1.310 three-role rehearsal through delivery_ready with scripted product fixtures. This establishes role/observer/Gate integration, not autonomous implementation or a complete zsspec/business rehearsal. Use the [current tutorial and validation record](CLAUDE-COMPATIBILITY.md) first; the older [implementation guide](docs/aidevflow-implementation-guide.md) and [runbook](docs/aidevflow-complete-runbook.md) include historical Stop-hook descriptions superseded by v3.

For the current v3 rehearsal, follow the [step-by-step validation tutorial](docs/aidevflow-v3-validation-tutorial.md): framework regression, preservation and recovery of the v2 run, a clean v3 task, real-service checks, and recovery/negative cases. It provides expected outcomes; they are not recorded test results.

## Development

Requires Python 3.11+ and Git. The runtime has no third-party Python dependencies.

```powershell
python -m unittest discover -s tests -v
```

The sample app used for end-to-end reliability exercises is kept separate from this framework repository under `examples/`.
