"""Values that arrive from a request must not be able to leave the directory they are joined onto, reach another machine, or be believed without checking."""
import sys
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from argus.core import paths as P
from argus.core import safe_names as SN


def test_safe_names_accept_ordinary_ids_and_refuse_anything_path_like():
    for ok in ("PHerc0139", "PHercParis4", "frag1", "20000101000000", "segA_2um", "a.b-c"):
        assert SN.is_safe_name(ok), ok
    for bad in ("", "..", "../x", "..\\x", "a/b", "a\\b", "D:/x", "\\\\host\\share", ".hidden", "a" * 65, "x..y", "a b", "a\x00b", None, 7):
        assert not SN.is_safe_name(bad), repr(bad)
    with pytest.raises(ValueError):
        SN.safe_name("../x", "scroll id")


def test_levels_are_short_keys_never_paths():
    for ok in ("0", "1", "12", "s0"):
        assert SN.is_safe_level(ok)
    for bad in ("", "../x", "0/../1", "D:\\x", "/abs/0", "E:/scratch/x/0", "0 1", "a" * 40):
        assert not SN.is_safe_level(bad), bad


def _two_stores(tmp_path):
    zarr = pytest.importorskip("zarr")
    for name, value in (("public.zarr", 7), ("private.zarr", 200)):
        group = zarr.open_group(str(tmp_path / name), mode="w", zarr_format=2)
        arr = group.create_array("0", shape=(4, 64, 64), chunks=(4, 64, 64), dtype="uint16", fill_value=0)
        arr[:] = value
    return tmp_path / "public.zarr", tmp_path / "private.zarr"


def test_an_absolute_path_as_a_level_no_longer_reads_a_different_array(tmp_path):
    from argus.core import zarr_volume as ZV

    root = pathlib_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    public, private = _two_stores(tmp_path)
    assert ZV.read_plane(public, "0", "xy", 0, 0, 0, 8, 8)["data"].max() == 7
    for level in (str(private / "0"), (private / "0").as_posix(), "../private.zarr/0", "0/../../private.zarr/0"):
        with pytest.raises(ZV.ZarrVolumeRefusal):
            ZV.read_plane(public, level, "xy", 0, 0, 0, 8, 8)
    with pytest.raises(ZV.ZarrVolumeRefusal):
        ZV.missing_chunks_in_region(public, str(private / "0"), 0, 1, 0, 8, 0, 8)


def pathlib_root():
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[2] / "src")


@pytest.fixture()
def client():
    from argus.service import app as A
    return A, TestClient(A.app)


@pytest.mark.parametrize("route", ["/api/scroll_metadata/", "/api/surface_status/"])
def test_a_backslash_or_unc_scroll_id_is_a_400_not_a_file_or_network_access(client, route):
    _, c = client
    for bad in ("..%5C..%5Cx", "%5C%5Cattacker%5Cs%5Cx", "..%2F..%2Fx", "a%00b"):
        assert c.get(route + bad).status_code in (400, 404, 422), (route, bad)
    assert c.get(route + "..%5C..%5Cx").status_code == 400
    assert c.get(route + "PHerc0139").status_code in (200, 404, 409)


def test_a_fragment_that_is_a_path_is_refused_before_any_join(client):
    _, c = client
    r = c.get("/api/plane", params={"fragment": "..\\..\\x", "scroll": "PHerc0139"})
    assert r.status_code == 400 and "not a path" in r.json()["why"]


def test_a_unc_or_device_path_is_never_resolved_and_lies_in_no_root(tmp_path):
    for remote in ("\\\\192.0.2.1\\share\\x", "//192.0.2.1/share/x", "\\\\?\\D:\\Windows", "//?/D:/Windows"):
        got = P.resolve_served_path(remote, [tmp_path])
        assert got == P._REFUSED_REMOTE and not got.exists()
        assert not P.within_root(got, tmp_path)
    (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
    assert P.resolve_served_path(str(tmp_path / "ok.txt"), [tmp_path]) == (tmp_path / "ok.txt").resolve()


def test_the_provider_plan_route_will_not_probe_paths_outside_the_declared_roots(client, tmp_path, monkeypatch):
    _, c = client
    root = tmp_path / "artifacts"
    (root / "in").mkdir(parents=True)
    monkeypatch.setattr(P, "serve_roots", lambda: [root])
    base = {"capability_id": "vc_flatten", "physical_scroll": "PHerc0139", "volume_id": "v", "acquisition_id": "a"}
    outside = c.get("/api/providers/plan", params=dict(base, input_path=str(tmp_path / "elsewhere"), output_path=str(root / "out")))
    assert outside.json()["state"] == "REFUSED" and "outside" in outside.json()["why"]
    real_file = c.get("/api/providers/plan", params=dict(base, input_path=str(__file__), output_path=str(root / "out")))
    assert real_file.json()["state"] == "REFUSED" and "outside" in real_file.json()["why"]
    inside = c.get("/api/providers/plan", params=dict(base, input_path=str(root / "in"), output_path=str(root / "out")))
    assert "outside" not in str(inside.json().get("why"))


def test_a_supplied_zarray_with_impossible_numbers_is_refused_before_any_arithmetic():
    from argus.core import volume_acquisition as VA

    good = {"shape": [10, 10, 10], "chunks": [5, 5, 5], "dtype": "|u1"}
    VA._validate_zarray_meta(good)
    for patch in ({"chunks": [0, 5, 5]}, {"chunks": [-1, 5, 5]}, {"chunks": [5, 5]}, {"shape": []}, {"shape": [10, "x", 10]}, {"dtype": "|u0"}, {"chunks": [True, 5, 5]}):
        with pytest.raises(VA.AcquisitionRefusal):
            VA._validate_zarray_meta(dict(good, **patch))


def test_a_redirect_is_followed_only_to_the_same_host_and_never_downgraded():
    from argus.core import volume_acquisition as VA

    handler = VA._SameHostRedirect()
    req = urllib.request.Request("https://dl.ash2txt.org/full-scrolls/v.zarr/0")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://dl.ash2txt.org/other/path") is not None
    for evil in ("https://evil.example/x", "http://dl.ash2txt.org/x", "https://dl.ash2txt.org.evil.example/x"):
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(req, None, 302, "Found", {}, evil)


def test_acquire_refuses_a_url_the_plan_did_not_vet_and_makes_no_request(tmp_path):
    from argus.core import volume_acquisition as VA
    from argus.core import volume_id_gate as VG

    url = "https://dl.ash2txt.org/full-scrolls/PHercFixture/volumes/20990303000000-9.362um-1.2m-113keV-masked.zarr"
    reg = {VG._key("PHercFixture"): {"scroll": "PHercFixture", "official": {"20990303000000"}, "superseded": set(), "sources": ["FIXTURE"]}}
    plan = VA.plan(url=url, array_path="0", roi=None, phase="A0", scroll="PHercFixture", volume_id="20990303000000")

    class Fetcher:
        calls = []
        max_bytes = 10 ** 9

        def get(self, u, timeout=60.0):
            self.calls.append(u)
            return b"{}", None

    f = Fetcher()
    tampered = dict(plan, mirrors={"dl": "https://dl.ash2txt.org/full-scrolls/SomethingElse/volumes/other.zarr"})
    with pytest.raises(VA.AcquisitionRefusal) as err:
        VA.acquire(tampered, fetcher=f, root=tmp_path, volume_registry=reg, frozen_targets={"state": "READ", "targets": {}},
                   declared={"pitch_um": 9.362, "energy_kev": 113})
    assert "validated mirrors" in str(err.value) and f.calls == []


def test_a_revision_prefix_must_be_long_hex_and_unique(monkeypatch):
    from argus.core import update_store as US

    recs = [{"update": {"source_id": "villa", "revision": "a" * 40}}, {"update": {"source_id": "villa", "revision": "ab" + "c" * 38}},
            {"update": {"source_id": "other", "revision": "f" * 40}}]
    monkeypatch.setattr(US, "list_updates", lambda: recs)
    assert US.find_revision("villa", "") is None and US.find_revision("villa", "aa") is None and US.find_revision("villa", "zzzzzzzz") is None
    assert US.find_revision("villa", "a" * 40) == "a" * 40 and US.find_revision("villa", "aaaaaaaa") == "a" * 40
    assert US.find_revision("villa", "a") is None
    recs.append({"update": {"source_id": "villa", "revision": "a" * 8 + "b" * 32}})
    assert US.find_revision("villa", "a" * 8) is None
