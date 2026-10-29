from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_gate import GateFixture
from test_managed import ManagedFixture
from aidevflow.browser import paths, run, validate_plan
from aidevflow.gate import GateError, read_json, sha256_file, write_json_atomic
from aidevflow.installer import install
from aidevflow.method_resources import installation_root, load_resource
from aidevflow.runtimes import skills_root


class MethodSourceTests(GateFixture):
    def setUp(self):
        super().setUp()
        self.gate.active_path.unlink()
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "omp", "python": sys.executable,
                                                                   "skills_root": ".claude/skills"})
        self.native = self.root / ".omp/skills/zsspec-req/SKILL.md"
        self.native.parent.mkdir(parents=True)
        self.native.write_text("---\nname: zsspec-req\ndescription: request\n---\nnative instructions", encoding="utf-8")

    def start(self):
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "omp", "python": sys.executable,
                                                                   "skills_root": ".omp/skills"})
        (self.root / "request.md").write_text("Feature", encoding="utf-8")
        self.gate.start_intake("request.md")

    def test_installer_repairs_missing_default_without_moving_vendor(self):
        original = self.native.read_bytes()
        install(Path(__file__).resolve().parents[1], self.root, runtime="omp")
        self.assertEqual(skills_root(self.root), self.root / ".omp/skills")
        self.assertEqual(self.native.read_bytes(), original)
        self.assertFalse((self.root / ".claude/skills").exists())

    def test_ambiguous_discovery_requires_choice_and_active_source_never_changes(self):
        other = self.root / ".claude/skills/zsspec-req/SKILL.md"
        other.parent.mkdir(parents=True)
        other.write_text("different", encoding="utf-8")
        with self.assertRaises(GateError):
            installation_root(self.root, "omp", {}, "auto")
        self.start()
        with self.assertRaises(GateError):
            install(Path(__file__).resolve().parents[1], self.root, runtime="omp", skills_root=".claude/skills")
        self.assertEqual(skills_root(self.root), self.root / ".omp/skills")

    def test_uri_and_legacy_alias_read_same_pinned_physical_source(self):
        self.start()
        for path in ("skill://zsspec-req", "skill://claude/zsspec-req", ".claude/skills/zsspec-req/SKILL.md"):
            loaded = load_resource(self.root, {"path": path})
            self.assertEqual(Path(loaded["input"]["path"]), self.native)
            self.assertEqual(loaded["source"]["sha256"], sha256_file(self.native))
            self.assertIn("serially", loaded["additionalContext"])
        result = load_resource(self.root, {"path": "skill://zsspec-req/SKILL.md:1-5", "limit": 20})
        self.assertTrue(result["input"]["path"].endswith("SKILL.md:1-5"))
        self.assertEqual(result["input"]["limit"], 20)

    def test_maintenance_and_uri_escape_are_blocked_but_business_documents_are_ordinary(self):
        self.start()
        for path in ("skill://zsspec-doctor", "skill://zsspec-req/../../request.md"):
            with self.assertRaises(GateError):
                load_resource(self.root, {"path": path})
        self.assertIsNone(load_resource(self.root, {"path": "docs/zsspec-req/notes.md"}))

    def test_doctor_explains_wrong_root_and_reports_readiness_without_writing(self):
        install(Path(__file__).resolve().parents[1], self.root, runtime="omp", skills_root=".claude/skills")
        from aidevflow.diagnostics import doctor_omp
        before = {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()}
        report = doctor_omp(self.root)
        self.assertFalse(report["ok"])
        self.assertIn(".omp/skills", report["next_actions"][0])
        self.assertTrue(report["readiness"]["initial_commit"])
        (self.root / ".claude/skills").mkdir(parents=True, exist_ok=True)
        empty_report = doctor_omp(self.root)
        self.assertTrue(empty_report["method_sources"]["exists"])
        self.assertEqual(empty_report["method_sources"]["skill_count"], 0)
        self.assertIn(".omp/skills", empty_report["next_actions"][0])
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.root / ".aidevflow").rglob("*") if p.is_file()})


class MethodPinTests(ManagedFixture):
    def test_legacy_package_fingerprint_stays_readable(self):
        from aidevflow.managed import package_manifest, package_version
        state, run_dir, _ = self.gate._load_active()
        policy_path = self.root / state["method_policy"]["path"]
        policy = read_json(policy_path)
        policy["package"] = package_manifest(self.root, self.contract, version=1)
        write_json_atomic(policy_path, policy)
        # Establish a disposable legacy snapshot, including its original policy reference.
        state["method_policy"]["sha256"] = sha256_file(policy_path)
        write_json_atomic(run_dir / "run-state.json", state)
        self.assertEqual(package_version(self.root, state), 1)
        self.assertEqual(self.gate.integrity(), [])

    def test_new_package_includes_readme_and_script_changes(self):
        from aidevflow.managed import package_manifest
        base = self.root / ".claude/skills"
        first = package_manifest(self.root, self.contract)
        (base / "README.md").write_text("new method workflow", encoding="utf-8")
        script = base / "zsspec-req/helper.py"
        script.write_text("print('method helper')", encoding="utf-8")
        second = package_manifest(self.root, self.contract)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertIn("zsspec-req/helper.py", second["files"])

    def test_managed_read_rejects_changed_instruction_at_its_source(self):
        self.gate.active_path.unlink()
        write_json_atomic(self.root / ".aidevflow/project.json", {"runtime": "omp", "python": sys.executable,
                                                                  "skills_root": ".claude/skills"})
        self._write_json("source-contract.json", {**self.contract, "task_id": "SOURCE"})
        self.gate.init("SOURCE", "source-contract.json")
        self.assertIsNotNone(load_resource(self.root, {"path": "skill://zsspec-req"}))
        source = self.root / ".claude/skills/zsspec-req/SKILL.md"
        source.write_text("changed provider instruction", encoding="utf-8")
        with self.assertRaises(GateError):
            load_resource(self.root, {"path": "skill://zsspec-req"})

    def test_active_browser_plan_must_be_frozen_and_output_declared(self):
        self.build()
        with self.assertRaises(GateError):
            paths(self.root, "specs/REQ-feature.md", "test-results/browser")
        with self.assertRaises(GateError):
            paths(self.root, "tests/test_feature.py", "src/browser-output")


class BrowserPlanTests(unittest.TestCase):
    def plan(self):
        return {"schema": "aidevflow/browser-plan-v1", "base_url": "http://127.0.0.1:8765",
                "checks": [{"id": "refresh", "steps": [{"action": "goto", "url": "/"}]}]}

    def test_remote_navigation_scripts_and_unsafe_ids_are_rejected(self):
        for url in ("https://example.com", "file:///etc/passwd", "javascript:alert(1)", "http://user:pass@localhost"):
            plan = self.plan()
            plan["checks"][0]["steps"][0]["url"] = url
            with self.assertRaises((GateError, ValueError)):
                validate_plan(plan)
        for name in ("../escape", "CON", "a/b"):
            plan = self.plan()
            plan["checks"][0]["id"] = name
            with self.assertRaises(GateError):
                validate_plan(plan)
        plan = self.plan()
        plan["checks"][0]["steps"] = [{"action": "eval", "code": "anything"}]
        with self.assertRaises(GateError):
            validate_plan(plan)


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Python Playwright is optional; run with the prepared browser interpreter")
class BrowserRealTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / ".test-tmp"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base, prefix="browser-", ignore_cleanup_errors=True)
        self.root = Path(self.temp.name).resolve()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'<h1>AIDevFlow</h1><button onclick="this.textContent=\'refreshed\'">refresh</button>')

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_real_browser_actions_produce_fresh_screenshots_and_failed_assertion_fails(self):
        plan = {"schema": "aidevflow/browser-plan-v1", "base_url": f"http://127.0.0.1:{self.server.server_port}",
                "checks": [{"id": "refresh", "steps": [{"action": "goto", "url": "/"},
                    {"action": "click", "selector": "button"}, {"action": "text", "selector": "button", "text": "refreshed"}]}]}
        write_json_atomic(self.root / "plan.json", plan)
        report = run(self.root, "plan.json", "test-results/pass")
        self.assertTrue(report["passed"], report)
        self.assertEqual(len(report["checks"][0]["screenshots"]), 3)
        self.assertTrue(all((self.root / path).stat().st_size > 100 for path in report["checks"][0]["screenshots"]))
        plan["checks"][0]["steps"][-1]["text"] = "missing-content"
        write_json_atomic(self.root / "plan.json", plan)
        report = run(self.root, "plan.json", "test-results/fail")
        self.assertFalse(report["passed"])
        self.assertEqual(report["checks"][0]["error"], "browser_check_failed")


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Requires the prepared browser interpreter")
class BrowserGateTests(ManagedFixture):
    contract_overrides = {
        "test_support_files": ["tests/browser-plan.json"],
        "test_commands": {
            "red": [sys.executable, "-c", "print('FAIL: expected 1'); import sys; sys.exit(1)", "{target}"],
            "green": [sys.executable, "-c", "print('passed')", "{target}"],
            "browser": [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/aidevflow_browser.py"),
                        "--plan", "tests/browser-plan.json", "--output", "test-results/browser"],
        },
    }

    def test_frozen_plan_executes_through_gate_and_captured_images_support_separate_attestation(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>fixture browser evidence</h1>")

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self._write_json("tests/browser-plan.json", {"schema": "aidevflow/browser-plan-v1",
                "base_url": f"http://127.0.0.1:{server.server_port}", "checks": [{"id": "local-page", "steps": [
                    {"action": "goto", "url": "/"}, {"action": "text", "selector": "h1", "text": "fixture browser evidence"}]}]})
            self.build()
            paths = ["test-results/browser/report.json", "test-results/browser/local-page-01.png", "test-results/browser/local-page-02.png"]
            result = self.gate.run_tests("browser", evidence_paths=paths)
            self.assertTrue(result["passed"], result)
            candidate = self.gate.status()["candidate_sha256"]
            record = self.gate.record_check("C-manual", "passed", candidate, "Integration test fixture",
                                           "Synthetic fixture screenshot pipeline only, not human or business acceptance", paths)
            self.assertEqual(record["source"], "ai_coordinator_attestation")
            self.assertTrue(record["evidence"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
