"""`argus run MANIFEST` -- a real end-to-end run over public data, from one target manifest."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

from argus.core import paths, public_identity, receipts

SCHEMA = "argus-public-target-v1"
RECEIPT_SCHEMA = "argus-public-pipeline-run-v1"
TARGET_DIR = ("argus", "public_targets")
RUNNER_REL = "argus/cli/public_pipeline.py"
BOUND_MODULES = ("official_identity", "volume_acquisition", "volume_id_gate", "metrics",
                 "result_class", "stage_lineage", "gpu_budget", "public_identity")
ARM = "public_ink_control"

STAGES = (
    ("identify", "the surface volume URL resolves, through the public survey, to the declared scroll and volume", ()),
    ("gate_target", "the scroll is on no prize list; the target is a labelled public control", ("identify",)),
    ("acquire_ct", "fetch the declared crop of the public surface volume, byte-ceilinged and sealed", ("identify", "gate_target")),
    ("acquire_labels", "fetch the declared crop of the public ink, supervision and validation labels", ("gate_target",)),
    ("acquire_model", "obtain the pinned checkpoint from a local hashed cache or its public URL; verify sha256", ()),
    ("inspect", "chunk-identity probe over the crop: present, not fill", ("acquire_ct",)),
    ("prepare", "Villa's preparer pools the crop into the 21-slice 9 um input", ("inspect",)),
    ("contract", "the checkpoint's own config accepts this input; exposure is read from it", ("prepare", "acquire_model")),
    ("infer", "Villa's flat inference, fp32, inside the crop", ("contract",)),
    ("score", "AUC and AP inside the validation mask, with the ink prior", ("infer", "acquire_labels")),
    ("receipt", "result class, run receipt and stage lineage", ("score",)),
)
STAGE_IDS = tuple(s[0] for s in STAGES)


class StageRefusal(RuntimeError):
    def __init__(self, code, detail, fix=""):
        super().__init__("%s: %s" % (code, detail))
        self.code, self.detail, self.fix = code, detail, fix


def target_dir() -> pathlib.Path:
    return pathlib.Path(paths.repo(*TARGET_DIR))


def public_target_paths() -> list:
    d = target_dir()
    return sorted(d.glob("*.json")) if d.is_dir() else []


def resolve_manifest(ref: str):
    p = pathlib.Path(ref)
    if p.is_file():
        return p
    for c in public_target_paths():
        if c.stem == ref:
            return c
    return None


def _lf_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def load_manifest(path) -> tuple:
    """(manifest, sha256 of its LF-normalised bytes)."""
    raw = pathlib.Path(path).read_bytes()
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise StageRefusal("MANIFEST_INVALID", "%s is not JSON: %s" % (path, exc))
    if m.get("schema") != SCHEMA:
        raise StageRefusal("MANIFEST_INVALID", "schema is %r, expected %r" % (m.get("schema"), SCHEMA))
    for k in ("id", "scroll", "alias", "upstream_segment", "volume_id", "source", "labels", "model",
              "input_contract", "stages", "terms"):
        if not m.get(k):
            raise StageRefusal("MANIFEST_INVALID", "missing %r" % k)
    ids = [s.get("id") for s in m["stages"]]
    unknown = [i for i in ids if i not in STAGE_IDS]
    if unknown:
        raise StageRefusal("MANIFEST_INVALID", "unknown stage id(s) %s; this driver runs %s" % (unknown, list(STAGE_IDS)))
    if ids != [i for i in STAGE_IDS if i in ids]:
        raise StageRefusal("MANIFEST_INVALID", "stages are out of order; the order is %s" % list(STAGE_IDS))
    need = {s[0]: s[2] for s in STAGES}
    for i in ids:
        gone = [d for d in need[i] if d not in ids]
        if gone:
            raise StageRefusal("MANIFEST_INVALID", "stage %s needs %s, which the manifest does not list" % (i, gone))
    return m, _lf_sha256(raw)


def _ledger() -> dict:
    doc = json.loads(pathlib.Path(__file__).resolve().parents[1].joinpath("capabilities.json").read_text(encoding="utf-8"))
    return {c["capability_id"]: c for c in doc["capabilities"]}


def ledger_gaps(m) -> list:
    """Capabilities a manifest names that the ledger lacks, or holds below CALLABLE."""
    led, out = _ledger(), []
    for st in m["stages"]:
        cap = st.get("capability")
        if cap and (cap not in led or led[cap]["state"] not in ("CALLABLE", "REAL_DATA_PROVEN", "ARGUS_WIRED", "UI_OPERABLE", "REGRESSION_LOCKED")):
            out.append("%s (%s)" % (cap, led[cap]["state"] if cap in led else "absent from the ledger"))
    return out


def villa_python():
    """The interpreter that has Villa's `vesuvius` package and PyTorch, or None."""
    env = os.environ.get("ARGUS_VILLA_PYTHON")
    if env:
        return pathlib.Path(env) if pathlib.Path(env).is_file() else None
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = pathlib.Path(paths.villa_runtime(*rel))
        if p.is_file():
            return p
    return None


def _store_root() -> pathlib.Path:
    from argus.core import acquisition_identity as AI
    return pathlib.Path(AI.store_roots()[0])


def _model_cache_dirs(m) -> list:
    out = [pathlib.Path(p) for p in os.environ.get("ARGUS_MODEL_CACHE", "").split(os.pathsep) if p]
    out.append(pathlib.Path(paths.model_store()))
    return out


def _find_model(m):
    """A file whose sha256 is the pinned one, searched read-only in every cache dir, or None."""
    want, name = m["model"]["sha256"], m["model"]["cache_name"]
    for d in _model_cache_dirs(m):
        p = d / name
        if p.is_file() and p.stat().st_size == int(m["model"]["bytes"]) and receipts.sha_file(p) == want:
            return p
    return None


def explain(m, out) -> int:
    """What each stage does, what it needs, and whether this machine has it."""
    led = _ledger()
    vp = villa_python()
    seg = _segment(m)
    print("target   %s   (%s)" % (m["id"], seg.public_name()), file=out)
    print("purpose  %s" % m["purpose"], file=out)
    r = m["source"]["roi"]
    print("crop     %s array %s  z[%d:%d] y[%d:%d] x[%d:%d], at most %d bytes" % (
        "surface volume", m["source"]["array_path"], r["z0"], r["z1"], r["y0"], r["y1"], r["x0"], r["x1"],
        m["source"]["byte_ceiling"]), file=out)
    plan = _plan_a2(m)
    print("plan     %d objects, upper bound %d bytes, acquisition_id %s (zero requests made)" % (
        plan["planned_objects"], plan["planned_upper_bound_bytes"], plan["acquisition_id"]), file=out)
    ledger_ok = True
    print("stages", file=out)
    by = {s["id"]: s for s in m["stages"]}
    for sid, what, _need in STAGES:
        if sid not in by:
            continue
        cap = by[sid].get("capability")
        row = led.get(cap) if cap else None
        need = ""
        if cap and row is None:
            need, ledger_ok = "capability %s is not in the ledger" % cap, False
        elif cap:
            need = "capability %s (%s); runtime %s" % (cap, row["state"], "found" if vp else "NOT FOUND: set ARGUS_VILLA_PYTHON to a Python with Villa's vesuvius package and PyTorch")
        elif sid == "acquire_model":
            hit = _find_model(m)
            need = ("held, sha256 verified at %s" % hit) if hit else "not held; will download %d bytes from %s" % (m["model"]["bytes"], m["model"]["url"])
        elif sid == "infer":
            need = "a GPU that argus doctor supports, fp32 only, VRAM capped by gpu_budget"
        print("  %-15s %s" % (sid, what), file=out)
        if need:
            print("  %-15s   needs: %s" % ("", need), file=out)
    print("launch   real runs need a single-use launch authorisation: `argus run %s --authorize ID` "
          "then `argus run %s --authorization-id ID`" % (m["id"], m["id"]), file=out)
    print("terms    %s" % m["terms"]["ct_data"], file=out)
    print("DRY RUN: nothing was fetched, nothing was written, nothing was consumed.", file=out)
    return 0 if ledger_ok else 1


def _segment(m):
    return public_identity.PublicSegment(
        physical_scroll=m["scroll"], argus_alias=m["alias"], upstream_segment_id=m["upstream_segment"],
        volume_id=m["volume_id"], pyramid_level=str(m["source"]["array_path"]),
        source_url=m["source"]["surface_volume_url"])


def _plan_a2(m):
    from argus.core import volume_acquisition as VA
    s = m["source"]
    return VA.dry_run(url=s["surface_volume_url"], array_path=s["array_path"], roi=_roi_text(s["roi"]),
                      phase=s["phase"], zarray_meta=s["declared_zarray"], scroll=m["scroll"],
                      volume_id=m["volume_id"], byte_ceiling=s["byte_ceiling"])


def _roi_text(r):
    return ",".join(str(r[k]) for k in ("z0", "z1", "y0", "y1", "x0", "x1"))


_MAX_REDIRECTS = 5


def _http_get(url: str, budget: list, timeout: float = 60.0):
    """(bytes | None, status)."""
    if not url.startswith("https://"):
        raise StageRefusal("FETCH_REFUSED", "only https URLs are fetched, got %s" % url)
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "argus-public-run/1"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(budget[0] + 1)
            if len(data) > budget[0]:
                raise StageRefusal("BYTE_CEILING", "%s exceeded the remaining %d-byte budget" % (url, budget[0]))
            budget[0] -= len(data)
            return data, 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, 404
            last = e
        except StageRefusal:
            raise
        except Exception as e:
            last = e
        time.sleep(1.5 * (2 ** attempt))
    raise StageRefusal("FETCH_FAILED", "%s failed after 4 attempts (%s)" % (url, type(last).__name__))


def _fetch_chunked(base: str, meta: dict, roi: dict, dest: pathlib.Path, ceiling: int, extra=(".zarray", ".zattrs")) -> dict:
    """Metadata plus exactly the chunks touching `roi` (z0..z1 y0..y1 x0..x1), into `dest`."""
    from argus.core import volume_acquisition as VA
    budget = [int(ceiling)]
    manifest, got = {}, 0
    keys = list(extra) + list(VA.chunk_keys(meta, VA.exact_roi(_roi_text(roi), meta["shape"])))
    for k in keys:
        data, code = _http_get("%s/%s" % (base.rstrip("/"), k), budget)
        if data is None:
            manifest[k] = {"state": "absent_404"}
            continue
        p = dest / k.replace("/", os.sep)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        got += len(data)
        manifest[k] = {"state": "present", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return {"objects": len(keys), "present": sum(1 for v in manifest.values() if v["state"] == "present"),
            "bytes": got, "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            "manifest": manifest}


def s_identify(m, ctx):
    from argus.core import official_identity as OI
    url = m["source"]["surface_volume_url"]
    res = OI.identify_volume({"names": [{"kind": "url", "text": url}], "scroll_hints": []})
    if res["state"] != "IDENTIFIED":
        raise StageRefusal("IDENTITY_REFUSED", "; ".join(res["reasons"]), "argus identify %s" % url)
    i = res["identity"]
    if i["scroll"] != m["scroll"] or i["volume_id"] != m["volume_id"]:
        raise StageRefusal("IDENTITY_MISMATCH", "the survey says %s / volume %s, the manifest says %s / %s"
                           % (i["scroll"], i["volume_id"], m["scroll"], m["volume_id"]))
    ctx["identity"] = i
    ctx["public_name"] = _segment(m).public_name()
    return {"scroll": i["scroll"], "volume_id": i["volume_id"], "scan_id": i["scan_id"],
            "pixel_size_um": i["pixel_size_um"], "energy_kev": i["energy_kev"], "license": "CC BY-NC 4.0",
            "confidence": res["confidence"], "public_name": ctx["public_name"],
            "survey_checked_at": (res.get("survey") or {}).get("checked_at")}


def s_gate_target(m, ctx):
    from argus.core import official_identity as OI
    survey = OI.load_survey() or {}
    hits = sorted(k for k, v in (survey.get("prize_sets") or {}).items() if m["scroll"] in (v.get("scrolls") or []))
    if hits or ctx["identity"].get("prizes"):
        raise StageRefusal("PRIZE_TARGET", "%s is in %s; public controls never touch prize targets" % (m["scroll"], hits or ctx["identity"]["prizes"]))
    return {"prize_sets_checked": sorted((survey.get("prize_sets") or {})), "on_a_prize_list": False,
            "target_class": "labelled public control"}


def s_acquire_ct(m, ctx):
    from argus.core import store_identity as SI
    from argus.core import volume_acquisition as VA
    s = m["source"]
    root = _store_root()
    fetcher = VA.HttpFetcher(max_bytes=s["byte_ceiling"])
    url = s["surface_volume_url"]
    meta_plan = VA.plan(url=url, array_path=s["array_path"], roi=None, phase="A0", scroll=m["scroll"], volume_id=m["volume_id"])
    a0 = VA.acquire(meta_plan, fetcher=fetcher, root=root)
    store = root / meta_plan["store_id"]
    fetched = json.loads((store / s["array_path"] / ".zarray").read_text(encoding="utf-8"))
    declared = dict(s["declared_zarray"])
    if {k: fetched.get(k) for k in declared} != declared:
        raise StageRefusal("ARRAY_HEADER_MISMATCH", "the published .zarray %s is not the one the manifest pins %s" % (fetched, declared))
    plan = VA.plan(url=url, array_path=s["array_path"], roi=_roi_text(s["roi"]), phase=s["phase"], zarray_meta=fetched,
                   scroll=m["scroll"], volume_id=m["volume_id"], byte_ceiling=s["byte_ceiling"])
    body = VA.acquire(plan, fetcher=VA.HttpFetcher(max_bytes=s["byte_ceiling"]), root=root)
    if not body["complete"]:
        raise StageRefusal("ACQUISITION_INCOMPLETE", "some planned objects were neither fetched nor proven absent (404)")
    ctx["store"] = store
    ctx["acquire_manifest"] = store / ("ACQUIRE_MANIFEST_%s.json" % s["phase"])
    return {"store_id": plan["store_id"], "store": str(store), "acquisition_id": plan["acquisition_id"],
            "objects": plan["planned_objects"], "absent_404": body["absent_404"], "bytes": body["fetched_bytes"],
            "key_list_hash": plan["key_list_hash"], "holding_state": SI.holding_state(body["store_identity"]),
            "identity_assertable": body["store_identity"].get("identity_assertable"),
            "unproven_by_the_store": sorted((body["store_identity"].get("unproven") or {})),
            "volume_metadata_requests": fetcher.requests}


def s_acquire_labels(m, ctx):
    lab = m["labels"]
    dest = ctx["run_dir"] / "labels"
    r = m["source"]["roi"]
    roi = {"z0": 0, "z1": None, "y0": r["y0"], "y1": r["y1"], "x0": r["x0"], "x1": r["x1"]}
    out, total = {}, 0
    for name, rel in lab["arrays"].items():
        base = "%s/%s" % (lab["base_url"].rstrip("/"), rel)
        budget = [lab["byte_ceiling"]]
        raw, code = _http_get(base + "/.zarray", budget)
        if raw is None:
            raise StageRefusal("LABELS_UNAVAILABLE", "%s/.zarray is 404" % base)
        meta = json.loads(raw)
        roi["z1"] = meta["shape"][0]
        d = dest / name
        got = _fetch_chunked(base, meta, roi, d, lab["byte_ceiling"])
        attrs_parent = base.rsplit("/", 1)[0]
        zattrs, _ = _http_get(attrs_parent + "/.zattrs", [1 << 20])
        if zattrs is not None:
            (d / "root.zattrs.json").parent.mkdir(parents=True, exist_ok=True)
            (d / "root.zattrs.json").write_bytes(zattrs)
            center = json.loads(zattrs).get("annotation_center_channel")
            if center is not None and int(center) != int(lab["label_plane"]):
                raise StageRefusal("LABEL_PLANE_MISMATCH", "%s declares its annotation plane as %s, the manifest says %s" % (name, center, lab["label_plane"]))
        out[name] = {"shape": meta["shape"], "objects": got["objects"], "present": got["present"], "bytes": got["bytes"],
                     "manifest_sha256": got["manifest_sha256"]}
        total += got["bytes"]
    ctx["labels_dir"] = dest
    return {"arrays": out, "bytes": total, "source": lab["base_url"]}


def s_acquire_model(m, ctx):
    mod = m["model"]
    hit = _find_model(m)
    if hit:
        ctx["checkpoint"] = hit
        return {"source": "local hashed cache (read-only)", "path": str(hit), "sha256": mod["sha256"], "downloaded_bytes": 0}
    dst = _model_cache_dirs(m)[-1] / mod["cache_name"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".partial")
    h, n = hashlib.sha256(), 0
    if not mod["url"].startswith("https://"):
        raise StageRefusal("FETCH_REFUSED", "checkpoint URL must be https")
    with urllib.request.urlopen(urllib.request.Request(mod["url"], headers={"User-Agent": "argus-public-run/1"}), timeout=120) as r, open(tmp, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            n += len(b)
            if n > int(mod["bytes"]):
                tmp.unlink()
                raise StageRefusal("BYTE_CEILING", "checkpoint is larger than the pinned %d bytes" % mod["bytes"])
            h.update(b)
            fh.write(b)
    if n != int(mod["bytes"]) or h.hexdigest() != mod["sha256"]:
        tmp.unlink()
        raise StageRefusal("CHECKPOINT_HASH_MISMATCH", "downloaded %d bytes with sha256 %s; the manifest pins %d bytes and %s" % (n, h.hexdigest(), mod["bytes"], mod["sha256"]))
    os.replace(tmp, dst)
    ctx["checkpoint"] = dst
    return {"source": mod["url"], "path": str(dst), "sha256": mod["sha256"], "downloaded_bytes": n}


def s_inspect(m, ctx):
    from evidence_gate import measure
    r = m["source"]["roi"]
    box = (r["z0"], r["z1"], r["y0"], r["y1"], r["x0"], r["x1"])
    rep = measure.probe_volume(ctx["store"].as_uri(), box, level=int(m["source"]["array_path"]), command="argus run (inspect)")
    if rep.status != "PASS":
        raise StageRefusal("CHUNK_IDENTITY_" + str(rep.status), "; ".join("%s: %s" % (g.name, g.reason) for g in rep.gates))
    ctx["inspect"] = rep.to_dict()
    return {"status": rep.status, "gates": [{"gate": g.name, "status": g.status, "detail": g.reason} for g in rep.gates],
            "network_used": rep.network_used, "cuda_used": rep.cuda_used}


def _villa(args, *, timeout, env=None, cwd=None):
    vp = villa_python()
    if vp is None:
        raise StageRefusal("VILLA_RUNTIME_MISSING", "no Python with Villa's vesuvius package was found",
                           "set ARGUS_VILLA_PYTHON (uv sync --extra models in a Villa checkout)")
    e = dict(os.environ if env is None else env)
    e["PYTHONNOUSERSITE"] = "1"
    e.pop("PYTHONPATH", None)
    return subprocess.run([str(vp)] + list(args), capture_output=True, text=True, timeout=timeout, env=e, cwd=cwd)


def _materialize_crop(store: pathlib.Path, m, dest: pathlib.Path) -> dict:
    """Re-key the sealed, chunk-aligned crop as a standalone array of exactly the crop's shape."""
    import shutil
    s = m["source"]
    r = s["roi"]
    ap = s["array_path"]
    meta = json.loads((store / ap / ".zarray").read_text(encoding="utf-8"))
    cz, cy, cx = meta["chunks"]
    if r["z0"] % cz or r["y0"] % cy or r["x0"] % cx:
        raise StageRefusal("CROP_NOT_CHUNK_ALIGNED", "the crop origin must sit on the chunk grid %s to be re-keyed without touching bytes" % meta["chunks"])
    shape = [r["z1"] - r["z0"], r["y1"] - r["y0"], r["x1"] - r["x0"]]
    sep = meta.get("dimension_separator", ".")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / ".zarray").write_text(json.dumps(dict(meta, shape=shape), indent=1), encoding="utf-8")
    n = 0
    for zi in range(r["z0"] // cz, (r["z1"] - 1) // cz + 1):
        for yi in range(r["y0"] // cy, (r["y1"] - 1) // cy + 1):
            for xi in range(r["x0"] // cx, (r["x1"] - 1) // cx + 1):
                src = store / ap / sep.join(str(v) for v in (zi, yi, xi)).replace("/", os.sep)
                if not src.is_file():
                    continue
                dst = dest / sep.join(str(v) for v in (zi - r["z0"] // cz, yi - r["y0"] // cy, xi - r["x0"] // cx)).replace("/", os.sep)
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copyfile(src, dst)
                n += 1
    return {"shape": shape, "chunks_copied": n}


def s_prepare(m, ctx):
    crop = _materialize_crop(ctx["store"], m, ctx["run_dir"] / "crop" / str(m["source"]["array_path"]))
    src = ctx["run_dir"] / "crop" / str(m["source"]["array_path"])
    out = ctx["run_dir"] / "prepared.zarr"
    t = time.time()
    r = _villa(["-m", "vesuvius.ink_detection.preprocessing.prepare_9um_isotropic_input", str(src), str(out),
                "--level", str(m["source"]["array_path"]), "--workers", "4"], timeout=900)
    if r.returncode != 0 or not out.exists():
        raise StageRefusal("PREPARE_FAILED", (r.stderr or r.stdout)[-600:])
    ctx["prepared"] = out
    attrs = json.loads((out / ".zattrs").read_text(encoding="utf-8"))
    return {"command": "python -m vesuvius.ink_detection.preprocessing.prepare_9um_isotropic_input crop/%s prepared.zarr --level %s"
            % (m["source"]["array_path"], m["source"]["array_path"]), "crop": crop, "attrs": attrs,
            "seconds": round(time.time() - t, 1)}


_FP32_BOOTSTRAP = ("import contextlib,runpy,sys,torch\n"
                   "torch.autocast = lambda *a, **k: contextlib.nullcontext()\n"
                   "print('ARGUS_FP32 autocast=null cuda=%s device=%s' % (torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None), flush=True)\n"
                   "sys.argv = ['infer'] + sys.argv[1:]\n"
                   "runpy.run_module('vesuvius.ink_detection.inference.infer', run_name='__main__')\n")

_CONFIG_PROBE = ("import json,sys,torch\n"
                 "c=torch.load(sys.argv[1],map_location='cpu',weights_only=False)['config']\n"
                 "d=c.get('data_provenance') or {}\n"
                 "print(json.dumps({'mode':c.get('mode'),'patch_size':c.get('patch_size'),'in_channels':c.get('in_channels'),"
                 "'normalization':(c.get('image_normalization') or {}).get('mode'),'depth_channels':d.get('depth_channels'),"
                 "'roles':sorted({x.get('provenance_role') for x in c.get('datasets') or []}),"
                 "'reserved_validation_cases':d.get('reserved_validation_cases'),"
                 "'online_validation_cases':d.get('online_validation_cases'),"
                 "'trained_cases':sorted(k for x in c.get('datasets') or [] for k in (x.get('sampling_physical_segment_keys') or {}))}))\n")


def s_contract(m, ctx):
    ic = m["input_contract"]
    r = _villa(["-c", _CONFIG_PROBE, str(ctx["checkpoint"])], timeout=300)
    if r.returncode != 0:
        raise StageRefusal("CHECKPOINT_UNREADABLE", (r.stderr or "")[-400:])
    cfg = json.loads(r.stdout.strip().splitlines()[-1])
    attrs = json.loads((ctx["prepared"] / ".zattrs").read_text(encoding="utf-8"))
    arr = json.loads((ctx["prepared"] / "0" / ".zarray").read_text(encoding="utf-8"))
    checks = [
        ("prepared format tag", attrs.get("format"), ic["prepared_format_tag"]),
        ("prepared planes", arr["shape"][0], ic["prepared_planes"]),
        ("checkpoint mode", cfg["mode"], ic["checkpoint_mode"]),
        ("checkpoint patch xy", (cfg["patch_size"] or [None, None, None])[-1], ic["checkpoint_patch_xy"]),
        ("checkpoint normalization", cfg["normalization"], ic["checkpoint_normalization"]),
        ("checkpoint provenance role covers this input", ic["checkpoint_provenance_role"] in (cfg["roles"] or []), True),
        ("precision (this driver runs fp32 only)", "fp32", ic["precision"]),
        ("source pitch of the crop's level (um)", m["source"]["level_um"], round(float(ctx["identity"]["pixel_size_um"]) * 4, 3)),
    ]
    bad = [c for c in checks if c[1] != c[2]]
    if bad:
        raise StageRefusal("INPUT_CONTRACT_VIOLATED", "; ".join("%s is %r, the contract needs %r" % c for c in bad))
    case = ic["case_name"]
    reserved = case in (cfg.get("reserved_validation_cases") or [])
    trained = case in (cfg.get("trained_cases") or [])
    exposure = "HELD_OUT_BY_FOLD" if (reserved and trained) else "TRAINED_ON" if trained else "EXPOSURE_UNKNOWN"
    ctx["exposure"] = exposure
    ctx["cfg"] = cfg
    return {"checks": [{"check": c[0], "got": c[1], "need": c[2]} for c in checks], "exposure_basis": exposure,
            "exposure_evidence": {"case": case, "in_checkpoint_reserved_validation_cases": reserved,
                                  "case_in_training_datasets": trained},
            "checkpoint_config": {k: cfg[k] for k in ("mode", "patch_size", "in_channels", "normalization", "depth_channels", "roles")}}


def s_infer(m, ctx):
    import numpy as np
    from PIL import Image
    from argus.core import gpu_budget, side_permit
    permit = side_permit.permit_state()
    if not permit.get("allow") and permit.get("reason") not in ("NO_PERMIT_FILE", "PERMIT_STALE"):
        raise StageRefusal("SIDE_WORK_SUSPENDED", "%s -- %s" % (permit.get("reason"), permit.get("detail")), "wait, then re-run")
    pred = ctx["run_dir"] / "prediction.tif"
    env = gpu_budget.child_env()
    t = time.time()
    args = ["-c", _FP32_BOOTSTRAP, str(ctx["prepared"]), str(ctx["checkpoint"]), str(pred),
            "--batch-size", "2", "--gpus", "0", "--no-compile", "--amp-dtype", "default",
            "--num-workers", "0", "--overlap", "0.25", "--direction", "forward"]
    res = _villa(args, timeout=280, env=env)
    (ctx["run_dir"] / "infer.log").write_text((res.stdout or "") + "\n" + (res.stderr or ""), encoding="utf-8")
    if res.returncode != 0 or not pred.exists():
        raise StageRefusal("INFERENCE_FAILED", (res.stderr or res.stdout)[-600:])
    marker = next((l for l in (res.stdout or "").splitlines() if l.startswith("ARGUS_FP32 ")), None)
    if marker is None:
        raise StageRefusal("PRECISION_UNPROVEN", "the fp32 start-up did not report itself; the run is not trusted as fp32")
    cast_warning = "invalid value encountered in cast" in (res.stderr or "") + (res.stdout or "")
    with Image.open(str(pred)) as im:
        alive = float((np.asarray(im, np.uint8) > 0).mean())
    if alive == 0.0:
        raise StageRefusal("EMPTY_OUTPUT", "the prediction is all zero%s; a NaN cast writes zeros, so this is not a silent model" % (" and the run warned of a NaN cast" if cast_warning else ""))
    ctx["prediction"] = pred
    return {"command": "python -c <fp32 bootstrap> -> vesuvius.ink_detection.inference.infer prepared.zarr <checkpoint> prediction.tif "
                       "--batch-size 2 --gpus 0 --no-compile --amp-dtype default --overlap 0.25 --direction forward",
            "precision": "fp32: torch.autocast replaced by a null context before Villa's inference runs (its --amp-dtype default still autocasts to fp16 on CUDA)", "vram_cap_env": env.get("PYTORCH_CUDA_ALLOC_CONF"),
            "seconds": round(time.time() - t, 1), "prediction_sha256": receipts.sha_file(pred), "log": "infer.log",
            "nonzero_fraction": round(alive, 5), "nan_cast_warning_in_log": cast_warning, "fp32_marker": marker}


def _read_label(dirpath, meta, plane, roi):
    """Reconstruct the label plane over the crop from the fetched chunks (absent chunk = fill)."""
    import numpy as np
    cz, cy, cx = meta["chunks"]
    fill = int(meta.get("fill_value") or 0)
    out = np.full((roi["y1"] - roi["y0"], roi["x1"] - roi["x0"]), fill, np.uint8)
    from numcodecs import Blosc
    import numcodecs
    codec = numcodecs.get_codec(meta["compressor"]) if meta.get("compressor") else None
    sep = meta.get("dimension_separator", ".")
    zi = plane // cz
    for yi in range(roi["y0"] // cy, (roi["y1"] - 1) // cy + 1):
        for xi in range(roi["x0"] // cx, (roi["x1"] - 1) // cx + 1):
            p = dirpath / sep.join(str(v) for v in (zi, yi, xi))
            if not p.is_file():
                continue
            raw = p.read_bytes()
            blk = np.frombuffer(codec.decode(raw) if codec else raw, np.uint8).reshape(cz, cy, cx)[plane - zi * cz]
            y0, x0 = yi * cy, xi * cx
            ya, yb = max(y0, roi["y0"]), min(y0 + cy, roi["y1"])
            xa, xb = max(x0, roi["x0"]), min(x0 + cx, roi["x1"])
            out[ya - roi["y0"]:yb - roi["y0"], xa - roi["x0"]:xb - roi["x0"]] = blk[ya - y0:yb - y0, xa - x0:xb - x0]
    return out


def s_score(m, ctx):
    import numpy as np
    from PIL import Image
    from argus.core import metrics
    r = m["source"]["roi"]
    plane = int(m["labels"]["label_plane"])
    lab = {}
    for name in ("ink", "supervision", "validation"):
        d = ctx["labels_dir"] / name
        meta = json.loads((d / ".zarray").read_text(encoding="utf-8"))
        lab[name] = _read_label(d, meta, plane, r)
    with Image.open(str(ctx["prediction"])) as im:
        pred = np.asarray(im, np.uint8)
    if pred.shape != (r["y1"] - r["y0"], r["x1"] - r["x0"]):
        raise StageRefusal("PREDICTION_SHAPE", "prediction is %s, the crop is %s" % (pred.shape, (r["y1"] - r["y0"], r["x1"] - r["x0"])))
    where = lab["validation"] > 0
    n = int(where.sum())
    if n == 0:
        raise StageRefusal("NO_SCORED_PIXELS", "the validation mask is empty inside the crop")
    overlap = int((where & (lab["supervision"] > 0)).sum())
    y = lab["ink"][where] > 0
    s = pred[where].astype(np.float32)
    if not y.any() or y.all():
        raise StageRefusal("DEGENERATE_LABELS", "the scored region is all ink or no ink; AUC is undefined")
    sc = metrics.score(s, y)
    ctx["metric"] = sc
    return {"metric": sc, "scored_pixels": n, "ink_prior": round(float(y.mean()), 4),
            "validation_pixels_also_in_supervision_mask": overlap,
            "ap_lift_over_prior": round(sc["ap"] / float(y.mean()), 3),
            "prediction_nonzero_fraction_in_crop": round(float((pred > 0).mean()), 4),
            "scored_where": m["labels"]["scored_where"], "label_plane": plane}


def _launch_argv(m, msha):
    return ["argus", "run", m["id"], msha]


def _packet(m, msha, checkpoint):
    plan = _plan_a2(m)
    return {"runner_rel": RUNNER_REL, "modules": list(BOUND_MODULES), "contract_sha256": msha,
            "plan_sha256": hashlib.sha256(json.dumps({"stages": [s["id"] for s in m["stages"]], "acquisition_id": plan["acquisition_id"],
                                                       "model": m["model"]["sha256"]}, sort_keys=True).encode()).hexdigest(),
            "bindings": [{"scroll": m["scroll"], "segment": m["alias"], "array_path": m["source"]["array_path"],
                          "array_shape": m["source"]["declared_zarray"]["shape"], "declared_level": m["source"]["array_path"],
                          "label_sha256": hashlib.sha256(json.dumps(m["labels"], sort_keys=True).encode()).hexdigest(),
                          "label_meta_sha256": None, "label_authority": "ink_9um public labels"}],
            "checkpoint_path": str(checkpoint), "arms": [ARM]}


def authorize(m, msha, aid, out) -> int:
    """Stage the pinned checkpoint, measure the live machine, and issue ONE single-use authorisation."""
    from argus.core import launch_authorization_v3 as LA3
    ctx = {}
    print("staging the pinned checkpoint (the only thing fetched before a launch is authorised) ...", file=out)
    try:
        d = s_acquire_model(m, ctx)
        p = _packet(m, msha, ctx["checkpoint"])
        meas = LA3.measure(runner_rel=p["runner_rel"], modules=p["modules"], contract_sha256=p["contract_sha256"],
                           plan_sha256=p["plan_sha256"], bindings=p["bindings"], checkpoint_path=p["checkpoint_path"],
                           argv=_launch_argv(m, msha))
        rec = LA3.issue(authorization_id=aid, node_id="public-run:%s" % m["id"], measured=meas,
                        development_controls=[m["id"]], permitted_arms=[ARM], ttl_hours=6, authorised_by="the person who ran --authorize")
    except (StageRefusal, LA3.V2.AuthorizationRefusal) as exc:
        print("REFUSED: %s" % exc, file=out)
        return 1
    print("authorisation %s issued (single use, 6 h, ceiling %s)" % (aid, rec["scientific_ceiling"]), file=out)
    print("  binds: this runner and modules, the manifest, the plan, the checkpoint sha256 %s, the git commit %s"
          % (d["sha256"][:16], str(rec["git_commit"])[:12]), file=out)
    print("  now: argus run %s --authorization-id %s" % (m["id"], aid), file=out)
    return 0


def run_manifest(ref, *, authorization_id=None, authorize_as=None, dry_run=False, out=sys.stdout, run_id=None) -> int:
    p = resolve_manifest(ref)
    if p is None:
        print("REFUSED: UNKNOWN_TARGET\n  no public target manifest matches %r (looked in %s)" % (ref, target_dir()), file=out)
        return 1
    try:
        m, msha = load_manifest(p)
    except StageRefusal as exc:
        print("REFUSED: %s\n  %s" % (exc.code, exc.detail), file=out)
        return 1
    if dry_run:
        return explain(m, out)
    if authorize_as:
        return authorize(m, msha, authorize_as, out)
    gaps = ledger_gaps(m)
    if gaps:
        print("REFUSED: CAPABILITY_NOT_CALLABLE\n  the capability ledger does not vouch for: %s" % ", ".join(gaps), file=out)
        return 1
    from argus.core import launch_authorization_v3 as LA3
    from argus.core import route_wiring as RW
    run_id = run_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = pathlib.Path(paths.artifact_write_root()) / "public_pipeline_runs" / ("%s-%s" % (m["id"], run_id))
    run_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"run_dir": run_dir}
    rec = {"schema": RECEIPT_SCHEMA, "run_id": run_id, "target": m["id"], "manifest_sha256": msha,
           "argv": _launch_argv(m, msha), "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "terms": m["terms"], "stages": [], "outcome": "RUNNING"}
    print("=" * 78, file=out)
    print("  argus run %s   (%s)" % (m["id"], _segment(m).public_name()), file=out)
    print("=" * 78, file=out)
    ck = _find_model(m)
    try:
        if ck is None:
            raise StageRefusal("LAUNCH_AUTHORIZATION_V3", "no authorised checkpoint is held; run `argus run %s --authorize ID` first" % m["id"])
        launched = RW.launch_v3(authorization_id, packet=_packet(m, msha, ck), receipt_dir=run_dir / "refusals",
                                argv=_launch_argv(m, msha), stage="argus run")
        rec["launch"] = {"authorization_id": authorization_id, "consumed": bool(launched.get("consumed")),
                         "ceiling": LA3.load(authorization_id).get("scientific_ceiling")}
        print("  launch: authorisation %s validated and consumed" % authorization_id, file=out)
    except (StageRefusal, RW.RouteRefusal, LA3.V2.AuthorizationRefusal) as exc:
        code = getattr(exc, "code", "LAUNCH_AUTHORIZATION_V3")
        print("REFUSED: %s\n  %s\n  fix: argus run %s --authorize ID, then --authorization-id ID" % (code, exc, m["id"]), file=out)
        rec.update(outcome="REFUSED", refused_at="launch", detail=str(exc))
        _write(rec, run_dir)
        return 1
    fns = {"identify": s_identify, "gate_target": s_gate_target, "acquire_ct": s_acquire_ct, "acquire_labels": s_acquire_labels,
           "acquire_model": s_acquire_model, "inspect": s_inspect, "prepare": s_prepare, "contract": s_contract,
           "infer": s_infer, "score": s_score}
    for st in m["stages"]:
        sid = st["id"]
        if sid == "receipt":
            continue
        t0 = time.time()
        row = {"stage": sid, "capability": st.get("capability"), "uses": st.get("uses")}
        try:
            row["detail"] = fns[sid](m, ctx)
            row["state"] = "RAN"
            print("  %-15s RAN      %5.1fs" % (sid, time.time() - t0), file=out)
        except StageRefusal as exc:
            row.update(state="REFUSED", code=exc.code, detail=exc.detail, fix=exc.fix)
            rec["stages"].append(row)
            rec.update(outcome="REFUSED", refused_at=sid)
            print("  %-15s REFUSED  %s\n      %s" % (sid, exc.code, exc.detail[:300]), file=out)
            _write(rec, run_dir)
            return 1
        row["seconds"] = round(time.time() - t0, 1)
        rec["stages"].append(row)
    return _finish(m, ctx, rec, run_dir, out)


_ENV_PROBE = ("import json,sys,torch\n"
              "import importlib.metadata as md\n"
              "print(json.dumps({'python': sys.version.split()[0], 'torch': torch.__version__, 'cuda_build': torch.version.cuda,"
              "'cuda_available': torch.cuda.is_available(), 'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
              "'vesuvius': md.version('vesuvius')}))\n")


def _environment(m) -> dict:
    from argus.core import git_state
    env = {"driver_python": sys.version.split()[0], "platform": sys.platform,
           "source_commit": git_state.head(paths.repo()) or "UNKNOWN"}
    try:
        r = _villa(["-c", _ENV_PROBE], timeout=120)
        env["villa_runtime"] = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"error": (r.stderr or "")[-200:]}
    except (StageRefusal, ValueError, IndexError, subprocess.SubprocessError) as exc:
        env["villa_runtime"] = {"error": str(exc)[:200]}
    return env


def _write(rec, run_dir):
    rec["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return receipts.write_json(rec, run_dir / "PIPELINE_RUN_RECEIPT.json")


def _finish(m, ctx, rec, run_dir, out) -> int:
    from argus.core import result_class as RC
    from argus.core import stage_lineage as SL
    sc = ctx["metric"]
    rc = RC.ResultClass(target=ctx["public_name"], target_class="LABELLED_SCROLL", exposure_basis=ctx["exposure"],
                        detector="%s (sha256 %s)" % (m["model"]["name"], m["model"]["sha256"][:12]),
                        detector_cross_scroll_qualified=False,
                        acquisition="%s um surface volume, level %s, 21-slice pooled input" % (m["source"]["level_um"], m["source"]["array_path"]),
                        metric="AUC (argus-metric-v1)", score=sc["auc"])
    rc.assert_not_discovery("a labelled region read by a released checkpoint; this proves the pipeline")
    rec["result_class"] = rc.as_dict()
    rec["stages"].append({"stage": "receipt", "uses": "argus.core.result_class, argus.core.stage_lineage", "state": "RAN"})
    rec["outcome"] = "COMPLETED"
    rec["environment"] = _environment(m)
    rec["outputs"] = {n: {"bytes": (run_dir / n).stat().st_size, "sha256": receipts.sha_file(run_dir / n)}
                      for n in ("prediction.tif", "infer.log") if (run_dir / n).is_file()}
    rec["outputs"]["prepared_input"] = {"path": "prepared.zarr", "note": "21 x 896 x 640 uint8, tagged level2-zmean4-21slice-v1; regenerable from the sealed crop"}
    rec["sealed_crop"] = {"store": str(ctx["store"]), "acquire_manifest": str(ctx["acquire_manifest"]),
                          "acquire_manifest_sha256": receipts.sha_file(ctx["acquire_manifest"])}
    rec["limits"] = [
        "a control proves the pipeline, not a discovery: the target has published labels and the checkpoint has seen its segment",
        "the scored pixels are the checkpoint's own reserved validation region, so the score is held out by region, not by scroll",
        "one crop, one checkpoint, one direction; no claim about any other scroll, detector or acquisition",
        "the crop is a public rendered surface volume; ARGUS did not trace or render it",
        "robust-MAD normalisation runs over the crop, not the whole segment, so the crop statistics differ from the checkpoint's own training-time statistics",
    ]
    path = _write(rec, run_dir)
    for stage, detail in (("raw_ct", "public surface-volume crop fetched and sealed (%s)" % ctx["store"].name),
                          ("ink_2d", "%s; AUC %.4f AP %.4f; %s" % (rc.presentation, sc["auc"], sc["ap"], "fp32 released ink_9um checkpoint"))):
        SL.record_attempt(m["scroll"], stage, "COMPLETED", capability_id=(("vesuvius.ink_detection.infer") if stage == "ink_2d" else None),
                          receipt_path=str(path), detail=detail, recorded_by={"tool": "argus run", "target": m["id"]})
    print("-" * 78, file=out)
    print("  %s" % rc.display_banner(), file=out)
    print("  AUC %.4f  AP %.4f  n=%d  positives=%d  (ink prior %.3f)" % (sc["auc"], sc["ap"], sc["n"], sc["n_positive"], sc["n_positive"] / sc["n"]), file=out)
    print("  receipt: %s" % path, file=out)
    print("  data terms: %s" % m["terms"]["ct_data"], file=out)
    return 0
