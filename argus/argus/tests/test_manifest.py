"""The manifest must exist before work does, and must not be editable afterwards."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from argus.core import manifest as MF


def _plan(src: Path, out: Path, **over) -> MF.Manifest:
    kw = dict(job_id="j", stage="s", action="run_stage", input_root=str(src),
              output_root=str(out), units=["a.txt", "b.txt"],
              inputs=[MF.Asset.of(src, "a.txt"), MF.Asset.of(src, "b.txt")],
              planned_outputs=["a.txt.sha256", "b.txt.sha256"],
              params={"algo": "sha256"}, code={"runner": "checksum", "runner_version": "1"})
    kw.update(over)
    return MF.Manifest(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="argus_t_manifest_"))
        self.src = self.tmp / "in"
        self.out = self.tmp / "out"
        self.stage = self.tmp / "stage"
        self.src.mkdir()
        self.out.mkdir()
        (self.src / "a.txt").write_text("alpha", encoding="utf-8")
        (self.src / "b.txt").write_text("beta", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestModule(Base):
    def test_module_selftest_passes(self):
        self.assertTrue(MF.selftest())


class TestWriteBeforeExecute(Base):
    def test_require_refuses_when_no_plan_was_written(self):
        with self.assertRaises(MF.ManifestViolation) as e:
            MF.require(self.stage, why="stage execution")
        self.assertEqual(e.exception.cls, "NO_MANIFEST")
        self.assertIn("BEFORE work begins", e.exception.detail)

    def test_require_returns_the_plan_once_it_exists(self):
        m = _plan(self.src, self.out)
        MF.write(self.stage, m)
        self.assertEqual(MF.require(self.stage).fingerprint(), m.fingerprint())

    def test_an_identical_rewrite_is_the_retry_path_and_is_allowed(self):
        MF.write(self.stage, _plan(self.src, self.out))
        MF.write(self.stage, _plan(self.src, self.out))
        self.assertTrue(MF.exists(self.stage))

    def test_a_different_plan_over_an_existing_one_is_refused(self):
        """SABOTAGE: rewrite the plan to match the outcome."""
        MF.write(self.stage, _plan(self.src, self.out))
        with self.assertRaises(MF.ManifestViolation) as e:
            MF.write(self.stage, _plan(self.src, self.out, units=["a.txt"]))
        self.assertEqual(e.exception.cls, "MANIFEST_REWRITTEN")

    def test_the_written_file_is_complete_json_not_a_half_write(self):
        MF.write(self.stage, _plan(self.src, self.out))
        r = json.loads(MF.path_for(self.stage).read_text(encoding="utf-8"))
        self.assertEqual(r["schema"], MF.MANIFEST_SCHEMA)
        self.assertEqual(len(r["fingerprint"]), 64)
        self.assertFalse(list(self.stage.glob("*.tmp")), "a temp file was left behind")


class TestFingerprint(Base):
    """Each case changes exactly one thing and states whether it should matter."""

    def test_a_different_unit_list_is_different_work(self):
        self.assertNotEqual(_plan(self.src, self.out).fingerprint(),
                            _plan(self.src, self.out, units=["a.txt"]).fingerprint())

    def test_a_different_parameter_is_different_work(self):
        self.assertNotEqual(_plan(self.src, self.out).fingerprint(),
                            _plan(self.src, self.out,
                                  params={"algo": "sha512"}).fingerprint())

    def test_a_different_runner_version_is_different_work(self):
        self.assertNotEqual(
            _plan(self.src, self.out).fingerprint(),
            _plan(self.src, self.out,
                  code={"runner": "checksum", "runner_version": "2"}).fingerprint())

    def test_changed_input_CONTENT_is_different_work(self):
        a = _plan(self.src, self.out).fingerprint()
        (self.src / "a.txt").write_text("ALPHA", encoding="utf-8")
        self.assertNotEqual(a, _plan(self.src, self.out).fingerprint())

    def test_a_moved_workspace_is_the_SAME_work(self):
        """Roots are excluded deliberately: a job carried to another drive computes the same thing, and a fingerprint that said otherwise would make every resume look like a new plan."""
        a = _plan(self.src, self.out).fingerprint()
        b = _plan(self.src, self.out, input_root="Z:/elsewhere",
                  output_root="Z:/elsewhere/out").fingerprint()
        self.assertEqual(a, b)

    def test_the_clock_is_not_an_input(self):
        m1 = _plan(self.src, self.out)
        m2 = _plan(self.src, self.out)
        m2.created_at = m1.created_at + 86_400
        self.assertEqual(m1.fingerprint(), m2.fingerprint())

    def test_unit_ORDER_is_part_of_the_plan(self):
        """Units run in order and a resume replays that order; a fingerprint that ignored it would call two different execution orders the same work."""
        self.assertNotEqual(_plan(self.src, self.out).fingerprint(),
                            _plan(self.src, self.out,
                                  units=["b.txt", "a.txt"]).fingerprint())


class TestHashes(Base):
    def test_inputs_and_outputs_are_both_hashed(self):
        m = _plan(self.src, self.out)
        for rel in m.planned_outputs:
            (self.out / rel).write_text("x", encoding="utf-8")
        MF.write(self.stage, m)
        rec = MF.record_outputs(self.stage, self.out, m.planned_outputs)
        self.assertTrue(all(len(a.sha256) == 64 for a in m.inputs))
        self.assertTrue(all(len(o["sha256"]) == 64 for o in rec["outputs"]))

    def test_a_planned_output_that_was_never_produced_is_refused(self):
        """SABOTAGE: claim completion with a missing artifact."""
        m = _plan(self.src, self.out)
        (self.out / "a.txt.sha256").write_text("x", encoding="utf-8")
        with self.assertRaises(MF.ManifestViolation) as e:
            MF.record_outputs(self.stage, self.out, m.planned_outputs)
        self.assertEqual(e.exception.cls, "OUTPUT_MISSING")

    def test_a_partial_record_is_still_written_for_a_cancelled_stage(self):
        m = _plan(self.src, self.out)
        (self.out / "a.txt.sha256").write_text("x", encoding="utf-8")
        rec = MF.record_outputs(self.stage, self.out, m.planned_outputs, strict=False)
        self.assertEqual(len(rec["outputs"]), 1)
        self.assertEqual(rec["missing"], ["b.txt.sha256"])

    def test_an_input_edited_after_the_plan_is_detected(self):
        """SABOTAGE: swap the data under a plan that already declared its hashes."""
        m = _plan(self.src, self.out)
        self.assertEqual(MF.verify_inputs(m, self.src), [])
        (self.src / "b.txt").write_text("swapped", encoding="utf-8")
        bad = MF.verify_inputs(m, self.src)
        self.assertEqual(len(bad), 1)
        self.assertIn("content changed", bad[0])

    def test_a_deleted_input_is_reported_as_gone_not_as_unchanged(self):
        m = _plan(self.src, self.out)
        (self.src / "a.txt").unlink()
        self.assertIn("gone", MF.verify_inputs(m, self.src)[0])


class TestForeignRecords(Base):
    def test_a_foreign_json_file_is_not_read_as_a_manifest(self):
        """This tree is full of other tools' receipts; reading one with ARGUS's field names is how a system invents a plan that was never made."""
        self.stage.mkdir(parents=True, exist_ok=True)
        MF.path_for(self.stage).write_text(
            json.dumps({"schema": "somebody-elses-v3", "job_id": "x", "stage": "y",
                        "action": "z", "input_root": "a", "output_root": "b"}),
            encoding="utf-8")
        with self.assertRaises(MF.ManifestViolation) as e:
            MF.read(self.stage)
        self.assertEqual(e.exception.cls, "BAD_MANIFEST")

    def test_an_unreadable_manifest_is_a_refusal_not_an_empty_plan(self):
        self.stage.mkdir(parents=True, exist_ok=True)
        MF.path_for(self.stage).write_text("{ this is not json", encoding="utf-8")
        with self.assertRaises(MF.ManifestViolation) as e:
            MF.read(self.stage)
        self.assertEqual(e.exception.cls, "UNREADABLE")

    def test_a_violation_carries_a_machine_readable_class(self):
        r = MF.ManifestViolation("NO_MANIFEST", "x").as_record()
        self.assertEqual(r["terminal"], "ARGUS_MANIFEST_VIOLATION")
        self.assertIn(r["class"], MF.ManifestViolation.CLASSES)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    total = res.testsRun
    ok = total - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, total))
    raise SystemExit(0 if ok == total else 1)
