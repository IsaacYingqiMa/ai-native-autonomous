"""Bounded local browser checks; execution evidence, never automatic visual approval."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from .gate import Gate, GateError, read_json, sha256_file


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username or parsed.password):
        raise GateError("Browser checks require a local HTTP(S) test service, without credentials")
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def validate_plan(plan: Any) -> None:
    if not isinstance(plan, dict) or set(plan) != {"schema", "base_url", "checks"} or plan.get("schema") != "aidevflow/browser-plan-v1":
        raise GateError("Browser plan requires schema=aidevflow/browser-plan-v1, base_url and checks")
    if not isinstance(plan["base_url"], str):
        raise GateError("base_url must be a string")
    base = origin(plan["base_url"])
    checks = plan["checks"]
    if not isinstance(checks, list) or not 1 <= len(checks) <= 50:
        raise GateError("Browser plan requires 1..50 checks")
    seen = set()
    fields = {"goto": {"url"}, "click": {"selector"}, "fill": {"selector", "value"},
              "press": {"selector", "key"}, "visible": {"selector"}, "hidden": {"selector"},
              "text": {"selector", "text"}}
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"id", "steps"}:
            raise GateError("Each browser check requires id and steps")
        name = check["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name.casefold() in seen or name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise GateError("Browser check IDs must be unique portable filenames")
        seen.add(name.casefold())
        steps = check["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
            raise GateError("Each browser check requires 1..50 steps")
        for step in steps:
            if not isinstance(step, dict) or step.get("action") not in fields or set(step) != fields[step["action"]] | {"action"}:
                raise GateError("Unknown browser action or properties; arbitrary scripts are not supported")
            if any(not isinstance(value, str) or len(value) > 8192 for value in step.values()):
                raise GateError("Browser step values must be bounded strings")
            if step["action"] == "goto" and origin(urljoin(plan["base_url"], step["url"])) != base:
                raise GateError("Browser navigation must stay on the declared test origin")


def paths(root: Path, plan_path: str, output_path: str) -> tuple[Path, Path]:
    """Validate before any output write; an active run requires a frozen plan."""
    gate = Gate(root)
    plan = gate._safe_repo_path(plan_path)
    output = gate._safe_repo_path(output_path)
    if output == root.resolve() or output.is_relative_to(root / ".aidevflow"):
        raise GateError("Browser output must be a dedicated evidence directory")
    if plan.is_relative_to(output):
        raise GateError("Browser output must not contain the plan")
    if gate.active_path.is_file():
        state, _, contract = gate._load_active()
        gate._require_executable(state)
        if state["current_stage"] not in ("build", "verify") or not contract:
            raise GateError("Browser execution requires Build/Verify; prepare and freeze its plan during Design")
        rel = plan.relative_to(root).as_posix()
        if rel not in state.get("frozen_tests", {}) or sha256_file(plan) != state["frozen_tests"][rel]:
            raise GateError("Browser plan must be declared as a test/support file and unchanged since Design freeze")
        from .managed import artifact_path
        if not artifact_path(contract, output.relative_to(root).as_posix()):
            raise GateError("Browser output must be inside contract.evidence_paths")
    elif output.parts[len(root.parts):len(root.parts) + 1] not in (("test-results",), ("playwright-report",)):
        raise GateError("Before intake, use test-results/ or playwright-report/ for browser smoke output")
    return plan, output


def output_file(output: Path, name: str) -> Path:
    path = output / name
    if path.is_symlink() or not path.resolve().is_relative_to(output.resolve()):
        raise GateError("Browser output contains an unsafe file alias")
    return path


def run(root: Path, plan_path: str, output_path: str) -> dict[str, Any]:
    from datetime import datetime, timezone
    root = root.resolve()
    plan_file, output = paths(root, plan_path, output_path)
    if plan_file.stat().st_size > 1024 * 1024:
        raise GateError("Browser plan exceeds 1 MiB")
    plan = read_json(plan_file)
    validate_plan(plan)
    output_file(output, "report.json")
    for check in plan["checks"]:
        for index in range(1, len(check["steps"]) + 1):
            output_file(output, f"{check['id']}-{index:02d}.png")
    plan_sha = sha256_file(plan_file)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise GateError("Install Python playwright and its Chromium browser in the configured interpreter before intake") from exc
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"schema": "aidevflow/browser-report-v1", "source": "playwright_execution",
                              "plan": plan_file.relative_to(root).as_posix(), "plan_sha256": plan_sha,
                              "started_at": datetime.now(timezone.utc).isoformat(), "checks": [], "passed": False}
    base = origin(plan["base_url"])
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for check in plan["checks"]:
                    item: dict[str, Any] = {"id": check["id"], "passed": False, "screenshots": []}
                    report["checks"].append(item)
                    context = browser.new_context(accept_downloads=False, service_workers="block", viewport={"width": 1280, "height": 800})
                    context.set_default_timeout(10000)
                    context.set_default_navigation_timeout(15000)
                    denied: list[str] = []

                    def route_request(route: Any) -> None:
                        try:
                            allowed = origin(route.request.url) == base
                        except ValueError:
                            allowed = False
                        except GateError:
                            allowed = False
                        if allowed:
                            route.continue_()
                        else:
                            denied.append("outside_declared_origin")
                            route.abort()

                    context.route("**/*", route_request)
                    if not hasattr(context, "route_web_socket"):
                        raise GateError("Playwright with route_web_socket support is required")
                    context.route_web_socket("**/*", lambda socket: socket.close())
                    page = context.new_page()
                    try:
                        for index, step in enumerate(check["steps"], 1):
                            action = step["action"]
                            if action == "goto":
                                response = page.goto(urljoin(plan["base_url"], step["url"]), wait_until="domcontentloaded")
                                if response is not None and response.status >= 400:
                                    raise GateError(f"Test page returned HTTP {response.status}")
                            else:
                                locator = page.locator(step["selector"])
                                if action == "click":
                                    locator.click()
                                elif action == "fill":
                                    locator.fill(step["value"])
                                elif action == "press":
                                    locator.press(step["key"])
                                elif action == "text":
                                    from playwright.sync_api import expect
                                    expect(locator).to_contain_text(step["text"])
                                else:
                                    locator.wait_for(state="visible" if action == "visible" else "hidden")
                            screenshot = output_file(output, f"{check['id']}-{index:02d}.png")
                            page.screenshot(path=str(screenshot), full_page=True, timeout=10000)
                            item["screenshots"].append(screenshot.relative_to(root).as_posix())
                        if denied or origin(page.url) != base:
                            raise GateError("A browser request/navigation left the declared test origin")
                        item["passed"] = True
                    except Exception as exc:
                        # Never print credentials, page contents, filled values or raw driver logs.
                        item["error"] = "browser_check_failed"
                        item["error_type"] = type(exc).__name__
                    finally:
                        context.close()
            finally:
                browser.close()
        report["passed"] = all(item["passed"] for item in report["checks"])
    except Exception as exc:
        report["error"] = "browser_environment_unavailable"
        report["error_type"] = type(exc).__name__
    if sha256_file(plan_file) != plan_sha:
        report["passed"] = False
        report["error"] = "plan_changed_during_execution"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    output_file(output, "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        report = run(Path(args.root), args.plan, args.output)
        print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]), "report": f"{args.output}/report.json"}))
        return 0 if report["passed"] else 1
    except (GateError, ValueError, OSError) as exc:
        print(f"AIDevFlow browser: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
