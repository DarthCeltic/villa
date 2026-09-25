"""The receipts routes: sealed content must not be readable, and the disk must not be walked once per browser request."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.adapters.vigiles_decision import BLINDING_MARKERS
from argus.core import jobs as J
from argus.core import scroll_ids as SI
from argus.service import receipts as R

SEALED_TEXT = "SYNTHETIC SEALED VERDICT"


class Base(unittest.TestCase):
    def setUp(self):
        import os

        self.tmp = Path(tempfile.mkdtemp(prefix="argus_t_receipts_"))
        self.arts, self.keys = R._fixture(self.tmp)
        self._env = {k: os.environ.get(k) for k in ("ARGUS_REPO", "ARGUS_LEGACY_ROOT")}
        os.environ["ARGUS_REPO"] = str(self.tmp)
        os.environ["ARGUS_LEGACY_ROOT"] = str(self.tmp)
        self._keys = R.KEYS
        R.KEYS = self.keys
        R.reset_caches()
        app = FastAPI()
        app.include_router(R.router)
        self.c = TestClient(app)

    def tearDown(self):
        import os

        R.KEYS = self._keys
        R.reset_caches()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestModule(unittest.TestCase):
    """Deliberately NOT a `Base` subclass: the module selftest arranges its own roots and key table, and running it inside a fixture that has already swapped both makes it fail for reasons unrelated to..."""

    def test_module_selftest_passes(self):
        try:
            self.assertTrue(R.selftest())
        finally:
            R.reset_caches()


class TestTheSeal(Base):
    def test_a_sealed_receipt_keeps_metadata_and_loses_content(self):
        r = self.c.get("/api/receipts").json()["receipts"]["sealed_one"]
        self.assertTrue(r["present"], "a sealed receipt still exists and says so")
        self.assertTrue(r["sealed"])
        self.assertIsNone(r["content"])
        self.assertIn("blinding marker", r["withheld_reason"])
        self.assertEqual(len(r["sha256_16"]), 16)

    def test_the_sealed_text_is_nowhere_in_the_whole_payload(self):
        """SABOTAGE: search the serialized body, not the field that was supposed to hold it."""
        body = self.c.get("/api/receipts").text
        self.assertNotIn(SEALED_TEXT, body)
        self.assertNotIn("candidate_count", body)

    def test_asking_for_the_sealed_key_directly_does_not_unlock_it(self):
        r = self.c.get("/api/receipts", params={"key": "sealed_one"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(SEALED_TEXT, r.text)
        self.assertIsNone(r.json()["receipts"]["sealed_one"]["content"])

    def test_a_sealed_run_in_the_tree_index_is_reduced_to_its_existence(self):
        body = self.c.get("/api/evidence-index").text
        self.assertNotIn(SEALED_TEXT, body)
        self.assertNotIn("preview_ink", body, "a sealed run's artifact names leaked")
        row = next(x for x in self.c.get("/api/evidence-index").json()["runs"]
                   if x["sealed"])
        self.assertEqual(row["artifacts"], [])
        self.assertIsNone(row["terminal"])
        self.assertIn(row["marker"], BLINDING_MARKERS)

    def test_the_per_run_route_refuses_a_sealed_run_with_423(self):
        r = self.c.get("/api/evidence-index/fixture_sealed")
        self.assertEqual(r.status_code, 423)
        self.assertNotIn(SEALED_TEXT, r.text)

    def test_the_marker_is_named_by_filename_and_never_by_path(self):
        row = next(x for x in self.c.get("/api/evidence-index").json()["runs"]
                   if x["sealed"])
        self.assertNotIn(str(self.tmp), json.dumps(row))
        self.assertEqual(R.leaks(row), [])


class TestSanitizing(Base):
    def test_a_credential_inside_receipt_content_does_not_survive(self):
        body = self.c.get("/api/receipts").text
        self.assertNotIn("hf_AAAABBBB", body)
        self.assertNotIn("api_token", body)

    def test_an_absolute_path_inside_receipt_content_does_not_survive(self):
        r = self.c.get("/api/receipts").json()["receipts"]["open_one"]
        self.assertEqual(r["content"]["written_from"], R._WITHHELD)
        self.assertNotIn("alex", json.dumps(r))

    def test_the_redaction_is_counted_rather_than_done_silently(self):
        r = self.c.get("/api/receipts").json()["receipts"]["open_one"]
        self.assertEqual(r["content_redactions"], 1)
        self.assertNotIn("api_token", r["content"],
                         "a credential-shaped FIELD is dropped whole, not blanked")

    def test_a_path_inside_a_declared_root_is_rewritten_rather_than_blanked(self):
        """The interface is entitled to know which root a store sits in and where."""
        inside = str(self.arts / "fixture_open" / "run.json")
        self.assertEqual(R.relativize(inside), "<repo>/artifacts/fixture_open/run.json")
        self.assertEqual(R.sanitize_value(inside), "<repo>/artifacts/fixture_open/run.json")
        self.assertEqual(R.leaks({"p": R.sanitize_value(inside)}), [])

    def test_a_path_outside_every_declared_root_is_still_withheld(self):
        """SABOTAGE the rewrite: it must not become a way to send any path at all."""
        for outside in (_argus_public_path('home', 'data/store'), r"D:\scratch\alex\secrets",
                        "/var/lib/elsewhere"):
            self.assertIsNone(R.relativize(outside), outside)
            self.assertEqual(R.sanitize_value(outside), R._WITHHELD, outside)

    def test_every_spelling_of_an_absolute_path_is_caught(self):
        """SABOTAGE: the drive-relative form matched none of the other four patterns and was passing through untouched."""
        for v in (r"D:\scratch\alex\x", "\\\\server\\share\\x", r"\Users\alex\secrets",
                  "/etc/passwd", "//host/share", "D:/scratch/alex"):
            self.assertEqual(R.sanitize_value(v), R._WITHHELD, v)
            self.assertTrue(R.leaks({"p": v}), v)

    def test_a_same_origin_service_route_is_not_mistaken_for_a_path(self):
        """The diary evidence manifest records `/api/file?path=...` per row so the UI fetches exactly the file that was hashed."""
        url = "/api/file?path=artifacts/diary_evidence/shots/example.png"
        self.assertEqual(R.sanitize_value(url), url)
        self.assertEqual(R.leaks({"serve_url": url}), [])
        self.assertEqual(R.sanitize_value("/api/file?path=D:/private/x"), R._WITHHELD)
        self.assertEqual(R.sanitize_value("/apiary/x"), R._WITHHELD)

    def test_a_regular_expression_fragment_is_not_mistaken_for_a_path(self):
        """The failure registry ships fixtures full of these."""
        for v in (r"\d+", r"^\w+$", r"\s*ok\s*", "not a path at all"):
            self.assertEqual(R.sanitize_value(v), v, v)
            self.assertEqual(R.leaks({"re": v}), [], v)

    def test_the_counts_separate_a_loss_from_a_rewrite(self):
        """A `redactions` field that counted rewrites would overstate what was lost, and a reader comparing the panel to the file would infer a bigger gap than exists."""
        self.assertEqual(
            R.content_stats({"a": R._WITHHELD, "b": "<legacy>/data/x", "c": "plain",
                             "d": [R._REDACTED, "<cache>/chunks"]}), (2, 2))

    def test_a_sha256_is_not_mistaken_for_a_credential(self):
        """The scan is prefix-anchored on purpose: a 64-character hex string is evidence and a redaction that ate hashes would be worse than useless."""
        digest = "a" * 64
        self.assertEqual(R.sanitize_value(digest), digest)
        self.assertEqual(R.leaks({"sha256": digest}), [])

    def test_the_leak_scan_catches_a_planted_path_and_credential(self):
        """SABOTAGE the guard: a scan that cannot fail is not a guard."""
        found = R.leaks({"p": r"D:\scratch\alex\x", "u": "/etc/passwd",
                         "t": "gh" + "p_" + "ABCDEFGH12345678", "auth_token": "x"})
        self.assertGreaterEqual(len(found), 4, found)

    def test_a_route_that_tried_to_return_one_would_raise(self):
        with self.assertRaises(R.LeakGuard):
            R.guarded({"nested": [{"leak": _argus_public_path('repo', 'secret')}]})


class TestAbsenceIsData(Base):
    def test_a_missing_receipt_is_present_false_with_a_reason(self):
        r = self.c.get("/api/receipts").json()["receipts"]["absent_one"]
        self.assertFalse(r["present"])
        self.assertIn("has not run", r["missing_reason"])
        self.assertTrue(r["relpath"].endswith("nothing.json"))
        self.assertIsNone(r["content"])

    def test_every_declared_key_is_answered_whether_or_not_it_exists(self):
        got = set(self.c.get("/api/receipts").json()["receipts"])
        self.assertEqual(got, {k.key for k in self.keys})

    def test_a_missing_root_is_reported_rather_than_raising(self):
        """A single evidence root may not be mounted; that is a per-root gap, not a 500, and (multi-root: canonical + legacy) it must not hide the OTHER root's evidence."""
        import os

        os.environ["ARGUS_REPO"] = str(self.tmp / "not-mounted")
        R.reset_caches()
        r = self.c.get("/api/evidence-index").json()
        self.assertTrue(r["root_present"], "the legacy root is still mounted")
        self.assertGreater(r["total"], 0, "legacy evidence must not vanish because the "
                                          "canonical root went missing")
        by_root = {row["root"]: row["present"] for row in r["roots"]}
        self.assertEqual(by_root, {"canonical": False, "legacy": True})

        os.environ["ARGUS_LEGACY_ROOT"] = str(self.tmp / "also-not-mounted")
        R.reset_caches()
        r = self.c.get("/api/evidence-index")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["root_present"])
        self.assertEqual(r.json()["total"], 0)


class TestTheCache(Base):
    def test_many_requests_do_not_cause_many_walks(self):
        self.c.get("/api/receipts")
        builds = R.RECEIPTS.builds
        for _ in range(20):
            self.c.get("/api/receipts")
        self.assertEqual(R.RECEIPTS.builds, builds)

        self.c.get("/api/evidence-index")
        idx = R.INDEX.builds
        for _ in range(20):
            self.c.get("/api/evidence-index")
        self.assertEqual(R.INDEX.builds, idx)

    def test_the_build_counter_actually_moves(self):
        """SABOTAGE the previous test: a counter stuck at one would pass it perfectly."""
        self.c.get("/api/receipts")
        builds = R.RECEIPTS.builds
        with mock.patch.object(R.Cache, "FORCE_MIN_AGE_S", 0.0):
            self.c.get("/api/receipts", params={"refresh": "true"})
        self.assertEqual(R.RECEIPTS.builds, builds + 1)

    def test_a_zero_ttl_rebuilds_every_time(self):
        R.reset_caches(receipts_ttl=0.0)
        for _ in range(3):
            self.c.get("/api/receipts")
        self.assertEqual(R.RECEIPTS.builds, 3)

    def test_freshness_is_reported_so_a_stale_panel_can_read_as_stale(self):
        j = self.c.get("/api/receipts").json()
        self.assertIsInstance(j["generated_at"], float)
        self.assertIsInstance(j["index_age_s"], float)
        self.assertEqual(j["ttl_s"], R.RECEIPTS_TTL_S)


class TestArgumentGuards(Base):
    def test_an_unknown_key_is_404_and_names_the_declared_set(self):
        r = self.c.get("/api/receipts", params={"key": "whatever"})
        self.assertEqual(r.status_code, 404)
        self.assertIn("open_one", r.text)

    def test_a_key_that_is_a_path_or_a_shell_string_is_refused(self):
        for evil in ("../../etc/passwd", "D:/Windows", "a; rm -rf /", "a/b"):
            self.assertEqual(
                self.c.get("/api/receipts", params={"key": evil}).status_code, 422, evil)

    def test_the_client_never_names_a_filesystem_path(self):
        """Structural: no route parameter here is a path kind."""
        for r_ in R.router.routes:
            for name in ("path", "file", "root", "dir"):
                self.assertNotIn(name, str(r_.path).lower().split("{")[-1])

    def test_a_run_id_that_is_a_path_is_refused(self):
        self.assertEqual(self.c.get("/api/evidence-index/C:%5CWindows").status_code, 422)
        self.assertIn(self.c.get("/api/evidence-index/..").status_code, (404, 422))

    def test_out_of_range_paging_is_refused(self):
        self.assertEqual(
            self.c.get("/api/evidence-index", params={"limit": 99999}).status_code, 422)
        self.assertEqual(
            self.c.get("/api/evidence-index", params={"sealed": "maybe"}).status_code, 422)

    def test_the_module_has_no_execution_surface(self):
        self.assertEqual(J.execution_surface_offences(Path(R.__file__)), [])


class TestScrollIds(Base):
    """The identity rules, served rather than restated."""

    def test_the_payload_is_computed_from_the_module_not_restated(self):
        j = self.c.get("/api/scroll-ids").json()
        self.assertEqual(sorted(j["canonical"]), sorted(SI.CANONICAL))
        self.assertEqual(j["canonical_count"], len(SI.CANONICAL))
        self.assertEqual(j["aliases"], dict(SI.ALIASES))
        self.assertEqual(sorted(j["placeholders"]), sorted(SI.PLACEHOLDERS))
        self.assertEqual(j["confusable_pairs"],
                         [sorted(p) for p in SI.confusable_pairs(tuple(SI.CANONICAL))])

    def test_a_new_canonical_id_reaches_the_payload_without_an_edit_here(self):
        """SABOTAGE: the failure this route prevents is the served list going stale while the engine's list moves."""
        original = SI.CANONICAL
        try:
            SI.CANONICAL = tuple(list(original) + ["PHerc9999"])
            R.reset_caches()
            j = self.c.get("/api/scroll-ids").json()
            self.assertIn("PHerc9999", j["canonical"])
            self.assertEqual(j["canonical_count"], len(original) + 1)
        finally:
            SI.CANONICAL = original
            R.reset_caches()

    def test_a_new_confusable_pair_reaches_it_too(self):
        """The safety property: two ids that differ by a digit transposition are easy to misread, so every such pair must be served for the interface to flag it."""
        original = SI.CANONICAL
        try:
            SI.CANONICAL = tuple(list(original) + ["PHerc1023", "PHerc1032"])
            R.reset_caches()
            pairs = self.c.get("/api/scroll-ids").json()["confusable_pairs"]
            self.assertIn(["PHerc1023", "PHerc1032"], pairs)
        finally:
            SI.CANONICAL = original
            R.reset_caches()

    def test_the_pairs_are_drawn_from_the_list_in_the_same_payload(self):
        j = self.c.get("/api/scroll-ids").json()
        served = set(j["canonical"])
        for pair in j["confusable_pairs"]:
            self.assertTrue(set(pair) <= served, pair)

    def test_the_known_transposition_hazards_are_both_present(self):
        pairs = [set(p) for p in self.c.get("/api/scroll-ids").json()["confusable_pairs"]]
        self.assertIn({"PHerc0814", "PHerc0841"}, pairs)
        self.assertIn({"PHerc0268", "PHerc0826"}, pairs)

    def test_a_placeholder_is_never_a_canonical_id(self):
        """The interface asserts no fixture value renders as a scroll; it can only do that against the whole list, or the assertion passes vacuously as the list grows."""
        j = self.c.get("/api/scroll-ids").json()
        self.assertFalse(set(j["placeholders"]) & set(j["canonical"]))
        self.assertTrue(j["placeholders"])

    def test_every_alias_target_is_canonical(self):
        j = self.c.get("/api/scroll-ids").json()
        self.assertTrue(set(j["aliases"].values()) <= set(j["canonical"]))

    def test_it_is_cached_and_read_only(self):
        self.c.get("/api/scroll-ids")
        builds = R.SCROLL_IDS.builds
        for _ in range(10):
            self.c.get("/api/scroll-ids")
        self.assertEqual(R.SCROLL_IDS.builds, builds)
        with mock.patch.object(R.Cache, "FORCE_MIN_AGE_S", 0.0):
            self.c.get("/api/scroll-ids", params={"refresh": "true"})
        self.assertEqual(R.SCROLL_IDS.builds, builds + 1)

    def test_it_leaks_nothing(self):
        self.assertEqual(R.leaks(self.c.get("/api/scroll-ids").json()), [])


class TestMountedOnTheReadOnlyService(unittest.TestCase):
    """The routes are mounted on the observational app, which must stay read-only."""

    def test_the_receipts_route_is_reachable_on_the_main_service(self):
        from argus.service import app as A

        c = TestClient(A.app)
        r = c.get("/api/receipts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["schema"], R.RECEIPTS_SCHEMA)

    def test_the_scroll_ids_route_is_reachable_on_the_main_service(self):
        from argus.service import app as A

        r = TestClient(A.app).get("/api/scroll-ids")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["canonical_count"], len(SI.CANONICAL))

    def test_mounting_it_added_no_write_route(self):
        from argus.service import app as A

        verbs = set()
        for r_ in A.app.routes:
            try:
                verbs |= set(r_.methods)
            except AttributeError:
                pass
        self.assertFalse(verbs & A.WRITE_METHODS)
        self.assertEqual(TestClient(A.app).post("/api/receipts").status_code, 405)

    def test_the_real_declared_keys_are_all_answered(self):
        """Against the real repo: every key comes back, present or honestly absent."""
        c = TestClient(FastAPI())
        app = FastAPI()
        app.include_router(R.router)
        c = TestClient(app)
        R.reset_caches()
        j = c.get("/api/receipts").json()
        self.assertEqual(set(j["receipts"]), {k.key for k in R.KEYS})
        for key, rec in j["receipts"].items():
            if rec["present"]:
                self.assertTrue(rec["content"] is not None or rec["withheld_reason"],
                                "%s is present with neither content nor a reason" % key)
            else:
                self.assertTrue(rec["missing_reason"], key)
        self.assertEqual(R.leaks(j), [])

    def test_optional_ui_evidence_is_declared_and_absence_is_a_200_contract(self):
        required = {k.key for k in R.KEYS}
        self.assertTrue(required)
        app = FastAPI()
        app.include_router(R.router)
        c = TestClient(app)
        R.reset_caches()
        for key in required:
            response = c.get("/api/receipts", params={"key": key})
            self.assertEqual(response.status_code, 200, key)
            row = response.json()["receipts"][key]
            self.assertEqual(row["key"], key)
            if not row["present"]:
                self.assertTrue(row["missing_reason"], key)


class TestBuildIndexCollectionScopedIdentity(unittest.TestCase):
    """`build_index`'s dedup used to key `seen_ids` by the leaf run directory's bare name alone."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="argus_t_receipts_collidx_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_run(self, path: Path, schema: str):
        path.mkdir(parents=True, exist_ok=True)
        (path / "RUN_RECEIPT.json").write_text(
            json.dumps({"schema": schema, "terminal": "COMPLETE"}), encoding="utf-8")

    def test_two_collections_sharing_a_run_name_both_survive(self):
        run_name = "PHerc0139"
        root = self.tmp / "artifacts"
        self._write_run(root / "collection_a" / run_name, "argus-fake-a-v1")
        self._write_run(root / "collection_z" / run_name, "argus-fake-z-v1")

        idx = R.build_index([root])
        rows = [e for e in idx["entries"] if e["run_id"] == run_name]
        self.assertEqual({r["collection"] for r in rows}, {"collection_a", "collection_z"},
                         "both collections' same-named run must appear, not just whichever "
                         "sorted first")
        self.assertEqual(len(rows), 2)

    def test_the_same_run_genuinely_duplicated_across_two_roots_still_dedups(self):
        """The one dedup this module's docstring actually documents: the SAME collection's SAME run appearing under both a canonical and a legacy root reports once, from whichever root is walked first --..."""
        canonical = self.tmp / "canonical"
        legacy = self.tmp / "legacy"
        self._write_run(canonical / "collection_a" / "same_run", "argus-fake-canonical-v1")
        self._write_run(legacy / "collection_a" / "same_run", "argus-fake-legacy-v1")

        idx = R.build_index([canonical, legacy])
        rows = [e for e in idx["entries"] if e["run_id"] == "same_run"]
        self.assertEqual(len(rows), 1, "the same collection+run_id across two roots must "
                                       "still dedup to one entry")
        self.assertEqual(rows[0]["schema"], "argus-fake-canonical-v1",
                         "canonical is walked first and must win")


DRIVE = "C" + ":"


class TestPublicPipelineRunReceipt(Base):
    """An `argus run` receipt (schema argus-public-pipeline-run-v1) is listed by the evidence index -- and therefore by the run archive and the scroll panel -- with its result class banner, target,..."""

    BANNER = ("KNOWN-DOMAIN HELD-OUT CONTROL — a labelled fragment or labelled-scroll region "
              "read out-of-sample. This proves the pipeline, not a discovery.")

    def _receipt(self, scroll="PHerc0139", name="pherc0139-w016-ink9um-control-20260925T134612Z"):
        d = self.arts / "public_pipeline_runs" / name
        d.mkdir(parents=True)
        rec = {
            "schema": "argus-public-pipeline-run-v1", "run_id": "20260925T134612Z",
            "target": "pherc0139-w016-ink9um-control", "manifest_sha256": "b" * 64,
            "started_utc": "2026-09-25T13:46:12Z", "finished_utc": "2026-09-25T13:48:41Z",
            "outcome": "COMPLETED",
            "terms": {"ct_data": "CC BY-NC 4.0, Vesuvius Challenge open data (https://scrollprize.org/data).",
                      "model": "scrollprize/ink_9um, MIT per its Hugging Face card."},
            "stages": [{"stage": "identify", "uses": "argus.core.official_identity", "state": "RAN",
                        "seconds": 0.0, "detail": {"scroll": scroll, "volume_id": "20260102150214"}},
                       {"stage": "infer", "uses": "villa", "state": "RAN", "seconds": 11.5}],
            "launch": {"authorization_id": "FINAL-20260925", "consumed": True, "ceiling": "DEVELOPMENT_ONLY"},
            "environment": {"source_commit": "d5c87c0bce0d87e6ef53d0edf558f5c12ce3dc12"},
            "outputs": {"prediction.tif": {"bytes": 287892, "sha256": "4" * 64},
                        "prepared_input": {"path": "prepared.zarr", "note": "regenerable"}},
            "sealed_crop": {"store": DRIVE + r"\scratch\argus_pipeline_home\cache\stores\535f",
                            "acquire_manifest_sha256": "1" * 64},
            "result_class": {"target": "PHerc0139 / w016", "target_class": "LABELLED_SCROLL",
                             "exposure_basis": "HELD_OUT_BY_FOLD", "metric": "AUC (argus-metric-v1)",
                             "score": 0.7722, "presentation": "KNOWN_DOMAIN_HELD_OUT_CONTROL",
                             "banner": self.BANNER, "may_claim_discovery": False,
                             "may_claim_ink_found": False,
                             "detector_cross_scroll_qualified": False,
                             "not_established": ["nothing about an unread scroll"]},
            "limits": ["a control proves the pipeline, not a discovery"]}
        (d / "PIPELINE_RUN_RECEIPT.json").write_text(json.dumps(rec), encoding="utf-8")
        return d

    def _rows(self, **params):
        R.reset_caches()
        return self.c.get("/api/evidence-index", params=params).json()

    def test_the_run_is_listed_with_its_banner_target_stages_hashes_and_terms(self):
        self._receipt()
        rows = self._rows(category="PUBLIC_PIPELINE_RUN")["runs"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["category"], "PUBLIC_PIPELINE_RUN")
        pr = row["public_run"]
        self.assertEqual(pr["result_class"]["banner"], self.BANNER)
        self.assertEqual(pr["result_class"]["presentation"], "KNOWN_DOMAIN_HELD_OUT_CONTROL")
        self.assertIs(pr["result_class"]["may_claim_discovery"], False)
        self.assertEqual((pr["target"], pr["scroll"]), ("pherc0139-w016-ink9um-control", "PHerc0139"))
        self.assertEqual([s["stage"] for s in pr["stages"]], ["identify", "infer"])
        self.assertEqual(pr["manifest_sha256"], "b" * 64)
        self.assertEqual(pr["outputs"]["prediction.tif"]["sha256"], "4" * 64)
        self.assertNotIn("prepared_input", pr["outputs"], "an entry with no hash is not a hash")
        self.assertEqual(pr["crop_manifest_sha256"], "1" * 64)
        self.assertIn("https://scrollprize.org/data", pr["terms"]["ct_data"],
                      "a URL is not a drive path and must survive")
        self.assertEqual(row["terminal"], "COMPLETED")

    def test_the_scratch_home_never_leaves(self):
        self._receipt()
        text = json.dumps(self._rows())
        self.assertNotIn("argus_pipeline_home", text)
        self.assertNotIn("authorization_id", text)
        self.assertNotIn("FINAL-20260925", text)

    def test_filtering_by_scroll_uses_the_scroll_the_receipt_names(self):
        self._receipt()
        self.assertEqual(len(self._rows(scroll="PHerc0139")["runs"]), 1)
        self.assertEqual(len(self._rows(scroll="pherc0139")["runs"]), 1)
        self.assertEqual(self._rows(scroll="PHerc0343P")["runs"], [])
        self.assertEqual(self.c.get("/api/evidence-index", params={"scroll": "../x"}).status_code, 422)
        self.assertEqual(self.c.get("/api/evidence-index", params={"category": "nope"}).status_code, 422)

    def test_a_receipt_that_names_no_scroll_is_never_matched_to_one(self):
        d = self._receipt()
        rec = json.loads((d / "PIPELINE_RUN_RECEIPT.json").read_text(encoding="utf-8"))
        rec["stages"][0].pop("detail")
        (d / "PIPELINE_RUN_RECEIPT.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(self._rows(scroll="PHerc0139")["runs"], [])
        self.assertEqual(len(self._rows(category="PUBLIC_PIPELINE_RUN")["runs"]), 1)

    def test_a_drive_letter_is_still_a_path_but_a_url_scheme_is_not(self):
        self.assertEqual(R.sanitize_value("https://scrollprize.org/data"), "https://scrollprize.org/data")
        self.assertEqual(R.sanitize_value("see (" + DRIVE + "/x)"), R._WITHHELD)
        self.assertEqual(R.sanitize_value(DRIVE + "\\x"), R._WITHHELD)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    total = res.testsRun
    ok = total - len(res.failures) - len(res.errors)
    print("selftest: %d/%d passed" % (ok, total))
    raise SystemExit(0 if ok == total else 1)
