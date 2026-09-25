"""The job control plane: what an interface can cause, and what it can never see."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from argus.core import jobs as J
from argus.service import jobs_api as API

SECRET_TEXT = "two columns of ink at x=412"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="argus_t_jobsapi_"))
        self.src = self.tmp / "inputs"
        self.src.mkdir()
        for i in range(3):
            (self.src / ("u%d.txt" % i)).write_text("payload %d" % i, encoding="utf-8")
        self._env = {k: os.environ.get(k) for k in
                     ("ARGUS_JOBS_ROOT", "ARGUS_JOB_INPUT_ROOTS", API.ENABLE_VAR)}
        os.environ["ARGUS_JOBS_ROOT"] = str(self.tmp / "jobs")
        os.environ["ARGUS_JOB_INPUT_ROOTS"] = str(self.tmp)
        os.environ[API.ENABLE_VAR] = "1"
        J._RUN_COUNTS.clear()
        self.c = TestClient(API.app)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def post(self, action, **args):
        return self.c.post("/api/jobs/action", json={"action": action, "args": args})

    def act(self, action, **args):
        r = self.post(action, **args)
        self.assertEqual(r.status_code, 200, "%s -> %s %s"
                         % (action, r.status_code, r.text[:200]))
        return r.json()

    def make_job(self):
        self.act("create_workspace", job_id="j1", input_root=str(self.src))
        self.act("stage_assets", job_id="j1", items=["u0.txt", "u1.txt"])
        fp = self.act("generate_manifest", job_id="j1", stage="s1",
                      runner="checksum")["manifest_fingerprint"]
        self.act("start_stage", job_id="j1", stage="s1", approved_fingerprint=fp)
        return fp

    def make_candidate(self):
        self.make_job()
        self.act("preserve_evidence", job_id="j1", candidate="c1", stage="s1",
                 items=["u0.txt.sha256"])
        self.act("index_candidate", job_id="j1", candidate="c1", stage="s1",
                 interpretation=SECRET_TEXT)


class TestModule(unittest.TestCase):
    def test_module_selftest_passes(self):
        self.assertTrue(API.selftest())


class TestTheGates(Base):
    def test_a_mutating_action_is_refused_while_the_switch_is_off(self):
        """SABOTAGE: importable is not the same as enabled."""
        os.environ.pop(API.ENABLE_VAR, None)
        r = self.post("create_workspace", job_id="j1", input_root=str(self.src))
        self.assertEqual(r.status_code, 403)
        self.assertIn(API.ENABLE_VAR, r.json()["detail"])
        self.assertFalse((self.tmp / "jobs" / "j1").exists(),
                         "the refusal still created a workspace")

    def test_reading_the_allowlist_works_while_it_is_off(self):
        os.environ.pop(API.ENABLE_VAR, None)
        r = self.c.get("/api/jobs/actions")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertEqual({a["action"] for a in r.json()["actions"]}, set(J.ACTIONS))

    def test_a_request_from_off_the_loopback_interface_is_refused(self):
        """Nothing here authenticates, so nothing here should be reachable."""
        remote = TestClient(API.app, client=("192.0.2.3", 51000))
        r = remote.get("/api/jobs/actions")
        self.assertEqual(r.status_code, 403)
        self.assertIn("loopback", r.json()["detail"] + r.json()["error"])


class TestTheAllowlistOverHttp(Base):
    def test_an_unlisted_action_is_refused_and_names_the_permitted_set(self):
        r = self.post("os.system", cmd="dir")
        self.assertEqual(r.status_code, 400)
        self.assertIn("permitted", r.text.lower())

    def test_a_helper_function_name_is_not_an_endpoint(self):
        for name in ("act_create_workspace", "_execute", "dispatch", "open_workspace"):
            self.assertEqual(self.post(name).status_code, 400, name)

    def test_a_field_outside_the_request_envelope_is_refused(self):
        r = self.c.post("/api/jobs/action",
                        json={"action": "list_jobs", "args": {}, "shell": True})
        self.assertEqual(r.status_code, 422)

    def test_an_unknown_argument_is_422_not_silently_dropped(self):
        self.assertEqual(self.post("list_jobs", cmd="x").status_code, 422)

    def test_a_traversal_or_shell_string_never_reaches_a_handler(self):
        self.act("create_workspace", job_id="j1", input_root=str(self.src))
        for evil in ("../../etc/passwd", "D:/Windows/system32", "a; rm -rf /", "a|b",
                     "..\\..\\x"):
            r = self.post("inspect_assets", job_id="j1", subdir=evil)
            self.assertIn(r.status_code, (403, 422), "%s -> %s" % (evil, r.status_code))

    def test_an_input_root_outside_the_declared_roots_is_403(self):
        r = self.post("create_workspace", job_id="j2",
                      input_root=str(Path(sys.executable).parent))
        self.assertEqual(r.status_code, 403)

    def test_refusal_classes_map_to_distinct_statuses(self):
        """A refusal is not a 500: the caller asked for something the system declines, and the status says which kind of declining it is."""
        self.assertEqual(len(set(API.STATUS.values())), len(set(API.STATUS.values())))
        for cls in J.JobRefusal.CLASSES:
            self.assertIn(cls, API.STATUS, "%s has no HTTP status" % cls)

    def test_an_action_that_needs_a_plan_answers_409_not_500(self):
        self.act("create_workspace", job_id="j1", input_root=str(self.src))
        r = self.post("start_stage", job_id="j1", stage="never-planned",
                      approved_fingerprint="0" * 64)
        self.assertEqual(r.status_code, 409)


class TestWhatTheInterfaceReceives(Base):
    def test_the_roots_are_not_sent(self):
        j = self.act("create_workspace", job_id="j1", input_root=str(self.src))
        for k in API.ROOT_FIELDS:
            self.assertNotIn(k, j)
        self.assertNotIn(str(self.tmp), json.dumps(j))

    def test_the_omission_is_visible_rather_than_silent(self):
        j = self.act("create_workspace", job_id="j1", input_root=str(self.src))
        self.assertTrue(set(j["withheld_fields"]) >= set(API.ROOT_FIELDS))
        self.assertIn("withheld by name", j["note"])

    def test_no_absolute_path_or_credential_survives_any_response(self):
        self.make_candidate()
        for r in (self.c.get("/api/jobs"), self.c.get("/api/jobs/j1"),
                  self.c.get("/api/jobs/j1/artifacts"),
                  self.c.get("/api/jobs/actions")):
            self.assertEqual(r.status_code, 200)
            from argus.service import receipts as R

            self.assertEqual(R.leaks(r.json()), [], r.url)

    def test_a_field_outside_the_interface_allowlist_does_not_pass_through(self):
        """SABOTAGE the projection with a field no action returns."""
        out = API.public_view({"ok": True, "job_id": "j1",
                               "internal_cursor": "/var/lib/secret"})
        self.assertNotIn("internal_cursor", out)
        self.assertIn("internal_cursor", out["withheld_fields"])

    def test_the_projection_fails_closed_on_something_it_cannot_sanitize(self):
        from argus.service import receipts as R

        with self.assertRaises(R.LeakGuard):
            R.guarded({"job_id": r"D:\scratch\alex"})


class TestTheSealOverHttp(Base):
    def test_a_sealed_candidate_is_refused_with_423(self):
        self.make_candidate()
        r = self.c.get("/api/jobs/j1/candidates/c1")
        self.assertEqual(r.status_code, 423)
        self.assertNotIn(SECRET_TEXT, r.text)

    def test_the_index_response_does_not_carry_the_interpretation(self):
        self.make_candidate()
        r = self.act("index_candidate", job_id="j1", candidate="c1", stage="s1",
                     interpretation=SECRET_TEXT)
        self.assertTrue(r["sealed"])
        self.assertNotIn(SECRET_TEXT, json.dumps(r))

    def test_status_names_the_sealed_candidate_without_its_content(self):
        self.make_candidate()
        j = self.c.get("/api/jobs/j1").json()
        self.assertEqual(j["sealed_candidates"], ["c1"])
        self.assertNotIn(SECRET_TEXT, json.dumps(j))

    def test_only_a_deliberate_unseal_opens_it(self):
        self.make_candidate()
        self.assertEqual(self.post("unseal_candidate", job_id="j1", candidate="c1",
                                   who="alice", reason="short").status_code, 422)
        self.assertEqual(self.c.get("/api/jobs/j1/candidates/c1").status_code, 423)
        self.act("unseal_candidate", job_id="j1", candidate="c1", who="alice",
                 reason="writing up the result")
        self.assertEqual(self.c.get("/api/jobs/j1/candidates/c1").status_code, 200)

    def test_interpretation_before_preservation_is_refused_over_http(self):
        self.make_job()
        r = self.post("index_candidate", job_id="j1", candidate="c9", stage="s1",
                      interpretation="ink")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Preservation precedes interpretation", r.json()["detail"])


class TestRunningAJob(Base):
    def test_a_stage_runs_only_against_the_plan_that_was_approved(self):
        self.act("create_workspace", job_id="j1", input_root=str(self.src))
        self.act("stage_assets", job_id="j1", items=["u0.txt", "u1.txt"])
        self.act("generate_manifest", job_id="j1", stage="s1", runner="checksum")
        r = self.post("start_stage", job_id="j1", stage="s1",
                      approved_fingerprint="c" * 64)
        self.assertEqual(r.status_code, 403)

    def test_a_completed_stage_re_run_over_http_is_idempotent(self):
        fp = self.make_job()
        before = dict(J._RUN_COUNTS)
        again = self.act("start_stage", job_id="j1", stage="s1",
                         approved_fingerprint=fp)
        self.assertTrue(again["reused"])
        self.assertEqual(J._RUN_COUNTS, before)

    def test_progress_is_counts_and_carries_no_percentage(self):
        self.make_job()
        prog = self.c.get("/api/jobs/j1").json()["stages"][0]["progress"]
        self.assertEqual((prog["done"], prog["total"]), (2, 2))
        self.assertNotIn("percent", json.dumps(prog))

    def test_artifacts_are_relative_paths_with_hashes(self):
        self.make_job()
        arts = self.c.get("/api/jobs/j1/artifacts",
                          params={"stage": "s1"}).json()["stages"][0]["artifacts"]
        self.assertTrue(arts)
        for a in arts:
            self.assertFalse(Path(a["rel"]).is_absolute())
            self.assertEqual(len(a["sha256"]), 64)

    def test_an_unknown_job_is_404_and_a_path_shaped_id_is_refused(self):
        self.assertEqual(self.c.get("/api/jobs/never-made").status_code, 404)
        self.assertIn(self.c.get("/api/jobs/..%2F..%2Fetc").status_code, (404, 422))


class TestSeparationFromTheObservatory(unittest.TestCase):
    def test_the_read_only_service_still_has_no_write_route(self):
        """The reason this app exists."""
        from argus.service import app as A

        verbs = set()
        for r_ in A.app.routes:
            try:
                verbs |= set(r_.methods)
            except AttributeError:
                pass
        self.assertFalse(verbs & A.WRITE_METHODS)

    def test_the_job_routes_are_not_reachable_on_the_observatory(self):
        from argus.service import app as A

        c = TestClient(A.app)
        self.assertEqual(c.post("/api/jobs/action",
                                json={"action": "list_jobs"}).status_code, 405)
        self.assertEqual(c.get("/api/jobs/actions").status_code, 404)

    def test_neither_service_module_has_an_execution_surface(self):
        for p in J.guarded_sources():
            self.assertTrue(p.is_file(), p)
            self.assertEqual(J.execution_surface_offences(p), [], p.name)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    total = res.testsRun
    ok = total - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, total))
    raise SystemExit(0 if ok == total else 1)
