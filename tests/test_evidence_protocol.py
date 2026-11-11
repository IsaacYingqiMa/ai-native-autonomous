"""Deterministic protocol checks; these do not claim browser/visual validation."""
import unittest

from aidevflow.test_execution import summary, parse_test_counts
from aidevflow.services.execution import reopen_affected
from aidevflow.services.verification import citation_paths


class EvidenceProtocolTests(unittest.TestCase):
    def test_node_counts_are_derived_from_execution_output(self):
        output = "ℹ tests 20\nℹ pass 20\nℹ fail 0\nℹ cancelled 0\nℹ skipped 0\nℹ todo 0\n"
        self.assertEqual(parse_test_counts(output)["tests"], 20)
        self.assertTrue(summary(None, output)["ok"])
        self.assertFalse(summary(None, output.replace("pass 20", "pass 19").replace("skipped 0", "skipped 1"))["ok"])
        self.assertFalse(summary(None, output.replace("20", "0"))["ok"])

    def test_short_citations_resolve_only_to_the_exact_manifest(self):
        paths = citation_paths({"files": [{"id": "E0001", "path": "objects/abc"}, {"id": "E0002", "path": "diff.patch"}]})
        self.assertEqual(paths["E0001"], "objects/abc")
        self.assertEqual(paths["diff.patch"], "diff.patch")
        self.assertNotIn("E9999", paths)

    def test_acceptance_repair_preserves_unrelated_completed_work(self):
        state = {"work_items": [{"id": "A", "status": "completed", "depends_on": [], "evidence": ["old"]},
                                {"id": "B", "status": "completed", "depends_on": [], "evidence": ["keep"]},
                                {"id": "R1", "status": "completed", "depends_on": ["A", "B"], "evidence": ["old"]}]}
        contract = {"method": {"task_checks": {"A": ["CA"], "B": ["CB"]}},
                    "required_checks": [{"id": "CA", "acceptance_items": ["AC-A"]}, {"id": "CB", "acceptance_items": ["AC-B"]}]}
        self.assertEqual(reopen_affected(state, contract, ["AC-A"]), ["A", "R1"])
        self.assertEqual(state["work_items"][1]["status"], "completed")
        self.assertEqual(state["work_items"][1]["evidence"], ["keep"])
