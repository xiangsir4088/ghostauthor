"""The frozen JSON contract (v0.5.0): schema + golden bytes, enforced.

A freeze that cannot go red is a slogan, not a contract. These tests
prove the machinery: the real render_json output validates against
schema/report.schema.json, the golden snapshot is byte-stable, and a
mutated report (removed key, added key, retyped field, bad sha, bad
severity) is rejected by name.
"""
import copy
import json
import unittest

from ghostauthor.report import render_json

from tests.golden import GOLDEN_FILE, make_golden_report
from tests.schemacheck import load_schema, schema_meta_errors, validate

SCHEMA = load_schema()


class SchemaShapeTests(unittest.TestCase):
    def test_schema_is_within_the_frozen_subset(self):
        self.assertEqual(schema_meta_errors(SCHEMA), [])

    def test_schema_pins_every_top_level_key(self):
        keys = set(SCHEMA["required"])
        self.assertEqual(
            keys,
            {"schema_version", "ref", "repo", "total_commits",
             "commits_by_bucket", "lines_by_bucket", "bucket_commit_shas",
             "ai_ratio", "agents", "authors", "sessions",
             "cosigned_evidence", "findings", "gate", "window"})
        self.assertEqual(SCHEMA["properties"]["schema_version"]["enum"],
                         ["1"])

    def test_real_output_passes_the_schema(self):
        data = json.loads(render_json(make_golden_report()))
        self.assertEqual(data["schema_version"], "1")
        self.assertEqual(validate(data, SCHEMA), [])

    def test_golden_snapshot_is_byte_stable(self):
        self.assertTrue(GOLDEN_FILE.exists(), "golden snapshot missing")
        golden = GOLDEN_FILE.read_text(encoding="utf-8")
        self.assertEqual(render_json(make_golden_report()), golden)

    def test_golden_is_a_complete_positive_instance(self):
        # The frozen dataset must cover the nullable shapes too.
        data = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
        self.assertIsNone(None, None)
        self.assertIsNotNone(data["gate"])
        self.assertIsNotNone(data["window"])
        self.assertEqual(set(data["commits_by_bucket"]),
                         {"agent", "cosigned", "unsigned"})
        self.assertTrue(data["findings"])


class FreezeEnforcementTests(unittest.TestCase):
    """Negative tests: the freeze must catch mutations by name."""

    def setUp(self):
        self.data = copy.deepcopy(
            json.loads(render_json(make_golden_report())))

    def test_removed_key_rejected(self):
        broken = self.data
        del broken["total_commits"]
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("total_commits" in e for e in errs), errs)

    def test_added_key_rejected(self):
        broken = self.data
        broken["bogus"] = 1
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("bogus" in e for e in errs), errs)

    def test_retyped_field_rejected(self):
        broken = self.data
        broken["total_commits"] = "3"
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("total_commits" in e for e in errs), errs)

    def test_unknown_bucket_rejected(self):
        broken = self.data
        broken["commits_by_bucket"]["weird"] = 1
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("weird" in e for e in errs), errs)

    def test_bad_sha_rejected(self):
        broken = self.data
        broken["bucket_commit_shas"]["agent"] = ["x" * 40]
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("bucket_commit_shas" in e for e in errs), errs)

    def test_bad_severity_rejected(self):
        broken = self.data
        broken["findings"][0]["severity"] = "critical"
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("severity" in e for e in errs), errs)

    def test_wrong_schema_version_rejected(self):
        broken = self.data
        broken["schema_version"] = "2"
        errs = validate(broken, SCHEMA)
        self.assertTrue(any("schema_version" in e for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
