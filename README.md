# AIDevFlow

AIDevFlow is an independent control layer for AI-native software delivery. It owns intent capture, workflow state, gates, evidence, and independent acceptance. It can invoke a delivery method such as the repository's separate `zsspec-*` Claude Code skills.

This repository is the framework and reliability POC. The initial runtime is deliberately single-task and local. It uses JSON state plus an append-only event log; Git is required in the target project so that evidence can be tied to a code revision.

## Current status

The current implementation changes are documented in [flow simplification v3](docs/aidevflow-flow-v3.md), on top of [reliability v2](docs/aidevflow-reliability-v2.md): short atomic bundles, infrastructure-only retry, observational Stop hooks, automatic method-loading observations for new runs, and proportionate task/check planning. V3 has not yet been regression-tested or rehearsed in a live Claude session. Reinstalling preserves older active contracts and evidence; it does not upgrade their acceptance rules or manufacture missing method history. Historical zsspec POC designs are research inputs, not the current execution instructions.

The local reliability POC now has a natural-language Intake path: Gate snapshots a request, the read-only Intent Analyst asks only decision-driving questions, a separate Contract Planner generates the task Contract, and Gate validates and pins it before zsspec Design/Build. An independent Verifier still evaluates a Gate-prepared evidence bundle. See the [implementation guide](docs/aidevflow-implementation-guide.md), [complete runbook](docs/aidevflow-complete-runbook.md), and [validation record](docs/aidevflow-poc-validation.md) for the workflow, commands, trust boundaries, and remaining limits. A live Claude Code end-to-end rehearsal remains necessary to establish actual subagent dispatch behavior.

For the current v3 rehearsal, follow the [step-by-step validation tutorial](docs/aidevflow-v3-validation-tutorial.md): framework regression, preservation and recovery of the v2 run, a clean v3 task, real-service checks, and recovery/negative cases. It provides expected outcomes; they are not recorded test results.

## Development

Requires Python 3.11+ and Git. The runtime has no third-party Python dependencies.

```powershell
python -m unittest discover -s tests -v
```

The sample app used for end-to-end reliability exercises is kept separate from this framework repository under `examples/`.
