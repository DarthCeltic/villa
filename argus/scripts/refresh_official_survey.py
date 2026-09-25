"""Refresh the PUBLIC official acquisition survey (argus/public_official_survey.json)."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
BUCKET = "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/"
CATALOGUE_URL = BUCKET + "metadata.min.json"
PRIZE_URL = "https://scrollprize.org/prizes"
SURVEY_PATH = REPO / "argus" / "public_official_survey.json"
PUBLIC_REGISTRY_PATH = REPO / "argus" / "public_target_registry.json"
CORPUS_REGISTRY_PATH = REPO / "corpus" / "scrolls" / "registry.json"
OVERLAY_REGISTRY_PATH = REPO / "docs" / "release" / "public_export" / "overlay" / "corpus" / "scrolls" / "registry.json"
V2_PATH = REPO / "artifacts" / "scrollprize_crawl" / "OFFICIAL_ELIGIBLE_TARGETS_V2.json"
SCHEMA = "argus-public-official-survey-v1"
PRIZES = ("FIRST_LETTERS", "GRAND_PRIZE_2027")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "argus-refresh-official-survey/1",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
    return body


def _dump(doc) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _write(path: pathlib.Path, doc, indent=2, ensure_ascii=False) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(doc, indent=indent, ensure_ascii=ensure_ascii) + "\n")


def parse_prize_page(html: str) -> dict:
    """{'GRAND_PRIZE_2027': {scroll: volume}, 'FIRST_LETTERS': {...}} from the page's two 'Eligible scroll volumes ( N )' lists, in page order (Grand Prize first)."""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&#x27;|&amp;", "'", text)
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"Eligible scroll volumes\s*\(\s*(\d+)\s*\)", text)
    if len(parts) != 5:
        raise SystemExit("prize page: expected exactly two 'Eligible scroll volumes ( N )' lists, "
                         "found %d; the page layout changed" % ((len(parts) - 1) // 2))
    out = {}
    for name, n, body in (("GRAND_PRIZE_2027", parts[1], parts[2]),
                          ("FIRST_LETTERS", parts[3], parts[4])):
        rows = {}
        for m in re.finditer(r"(PHerc\w+?)\s*[^\w\s]\s*(\d{14})", body[:4000]):
            rows.setdefault(m.group(1), m.group(2))
            if len(rows) == int(n):
                break
        if len(rows) != int(n):
            raise SystemExit("prize page: %s declares %s rows, parsed %d" % (name, n, len(rows)))
        out[name] = rows
    return out


def _https_root(url) -> str:
    """The catalogue names an access root as s3://vesuvius-challenge-open-data or an https host."""
    url = (url or "").rstrip("/")
    if url.startswith("s3://vesuvius-challenge-open-data") or not url:
        return BUCKET
    return url + "/"


def _fetch_zarray(store_url: str) -> dict | None:
    try:
        z = json.loads(_get(store_url + "0/.zarray").decode("utf-8"))
        return {"path": "0", "shape": z["shape"], "chunks": z["chunks"], "dtype": z["dtype"]}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return None


def build(catalogue: bytes, prize_html: bytes | None, checked_at: str, *, zarray=True,
          prize_source=None) -> dict:
    cat = json.loads(catalogue.decode("utf-8"))
    prize = parse_prize_page(prize_html.decode("utf-8", "replace")) if prize_html else None
    cat_src = {"url": CATALOGUE_URL, "sha256": _sha(catalogue), "bytes": len(catalogue),
               "checked_at": checked_at}
    samples, jobs = {}, []
    for name, s in sorted((cat.get("samples") or {}).items()):
        scans = {}
        for sid, sc in sorted((s.get("scans") or {}).items()):
            props = sc.get("properties") or {}
            scans[sid] = {"long_id": sc.get("long_id"),
                          "pixel_size_um": props.get("pixel_size_um"),
                          "energy_kev": props.get("energy_keV")}
        vols = {}
        for vid, v in sorted((s.get("volumes") or {}).items()):
            store, root = None, None
            for d in v.get("data") or []:
                if d.get("type") == "ome-zarr":
                    for o in d.get("origins") or []:
                        if store is None:
                            store = o.get("path")
                            roots = o.get("access_roots") or [{}]
                            root = _https_root(roots[0].get("url"))
            if not store:
                continue
            store = store.rstrip("/") + "/"
            props = v.get("properties") or {}
            base = pathlib.PurePosixPath(store.rstrip("/")).name[:-len(".zarr")]
            row = {"scan_id": v.get("scan_id"), "pixel_size_um": props.get("pixel_size_um"),
                   "energy_kev": props.get("energy_keV"), "store": store,
                   "long_id": base[:-len("-masked")] if base.endswith("-masked") else base,
                   "license": (props.get("license") or {}).get("name"),
                   "prizes": [], "source_url": root + store, "checked_at": checked_at}
            vols[vid] = row
            jobs.append((name, vid, row))
        samples[name] = {"type": (s.get("sample", {}).get("properties") or {}).get("type"),
                         "scans": scans, "volumes": vols,
                         "segments_listed": len(s.get("segments") or {}),
                         "source_url": CATALOGUE_URL, "checked_at": checked_at}
    if zarray:
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            for (name, vid, row), arr in zip(jobs, ex.map(lambda j: _fetch_zarray(j[2]["source_url"]), jobs)):
                row["array"] = arr
    prize_sets = {}
    if prize:
        for pname, mapping in prize.items():
            for scroll, vid in mapping.items():
                row = ((samples.get(scroll) or {}).get("volumes") or {}).get(vid)
                if row is None:
                    raise SystemExit("prize page names %s volume %s, which the catalogue does not "
                                     "list for that scroll; refusing to write a survey that "
                                     "disagrees with itself" % (scroll, vid))
                row["prizes"] = sorted(set(row["prizes"]) | {pname})
            prize_sets[pname] = {"count": len(mapping), "scrolls": sorted(mapping),
                                 "volume_ids": dict(sorted(mapping.items()))}
    doc = {"schema": SCHEMA, "checked_at": checked_at,
           "generated_by": "scripts/refresh_official_survey.py",
           "read_only": "metadata objects only (catalogue JSON, level-0 .zarray, the prize page); "
                        "no chunk and no voxel is ever requested",
           "identity_terms": {
               "scan_id": "the acquisition (CT scan) timestamp; the catalogue's `scans` keys",
               "volume_id": "the reconstruction timestamp naming an OME-Zarr store; the catalogue's "
                            "`volumes` keys, and what the prize page and every tifxyz "
                            "`<segment>-on-<volume_id>-<pitch>um` name refers to. One scan can have "
                            "several volumes and one scroll several scans."},
           "sources": {"catalogue": cat_src,
                       "prize_page": ({"url": PRIZE_URL, "sha256": _sha(prize_html),
                                       "checked_at": checked_at} if prize_html else None)},
           "prize_sets": prize_sets,
           "prize_set_notes": {
               "FIRST_LETTERS": "22 eligible volumes. The set is not a superset of the Grand Prize "
                                "set: the prize page says it excludes scrolls where letters have "
                                "now been found, and PHerc1447 is on the Grand Prize list but no "
                                "longer on this one. Earlier ARGUS records carried 23 (they still "
                                "listed PHerc1447 in First Letters).",
               "GRAND_PRIZE_2027": "13 eligible volumes."},
           "samples": samples}
    if prize_source:
        doc["sources"]["prize_page"]["read_from"] = prize_source
    return doc



def _first_volume_pitch(survey, scroll, vid):
    v = survey["samples"][scroll]["volumes"][vid]
    return v["pixel_size_um"], v["energy_kev"]


def reconcile_public_registry(survey: dict) -> dict:
    sets = survey["prize_sets"]
    targets = []
    for scroll in sorted({s for p in sets.values() for s in p["scrolls"]}):
        prizes = [p for p in PRIZES if scroll in sets[p]["scrolls"]]
        vid = next(sets[p]["volume_ids"][scroll] for p in PRIZES if scroll in sets[p]["scrolls"])
        pitch, kev = _first_volume_pitch(survey, scroll, vid)
        targets.append({"scroll": scroll, "scan_id": vid, "prizes": prizes,
                        "pitch_um": pitch, "energy_kev": int(kev) if float(kev).is_integer() else kev})
    old = json.loads(PUBLIC_REGISTRY_PATH.read_text(encoding="utf-8")) if PUBLIC_REGISTRY_PATH.is_file() else {}
    doc = {"schema": "argus-public-target-registry-v1", "id": "ARGUS-PUBLIC-TARGET-REGISTRY-V1",
           "derived_from": {"file": "argus/public_official_survey.json",
                            "generator": "scripts/refresh_official_survey.py",
                            "checked_at": survey["checked_at"],
                            "note": "derived; the survey is the source of truth. `scan_id` on a "
                                    "target row is the official VOLUME id the prize page lists."},
           "source": {"url": PRIZE_URL, "sha256": survey["sources"]["prize_page"]["sha256"],
                      "read_only": True},
           "sets": {p: {"count": sets[p]["count"], "scrolls": sets[p]["scrolls"]} for p in PRIZES},
           "special_lanes": old.get("special_lanes") or {"PARIS4_TITLE": ["PHercParis4"]},
           "targets": targets}
    doc["sets"]["rule"] = ("Different prizes, different eligibility. Never merge them into one "
                           "eligible list; neither is a subset of the other.")
    return doc


def _reconcile_object(o: dict, survey: dict) -> dict:
    sets = survey["prize_sets"]
    name = o["object_id"]
    gp = sets["GRAND_PRIZE_2027"]["volume_ids"].get(name)
    fl = name in sets["FIRST_LETTERS"]["scrolls"]
    roles = [r for r in o.get("roles") or []
             if r not in ("grand_prize_2027_target", "first_letters_2027_target")]
    if gp:
        roles.append("grand_prize_2027_target")
    if fl:
        roles.append("first_letters_2027_target")
    o["roles"] = roles
    if gp:
        v = survey["samples"][name]["volumes"][gp]
        block = dict(o.get("grand_prize_2027") or {})
        block.update(eligible=True, volume_id=v["long_id"], voxel_um=v["pixel_size_um"],
                     energy_kev=v["energy_kev"])
        o["grand_prize_2027"] = block
    else:
        o["grand_prize_2027"] = {"eligible": False}
    return o


def reconcile_corpus_registry(survey: dict, text: str) -> str:
    """Line-surgical: the registry keeps one compact object per line, and only the derived fields (captured_at, source check times, prize roles, grand_prize_2027) are touched."""
    at = survey["checked_at"]
    out = []
    for line in text.split(chr(10)):
        if line.startswith('  "captured_at"'):
            line = '  "captured_at": "%s",' % at
        elif '"source_id"' in line and '"checked_at"' in line:
            line = re.sub(r'"checked_at": "[^"]*"', '"checked_at": "%s"' % at, line)
        elif line.lstrip().startswith('{"object_id"'):
            tail = "," if line.rstrip().endswith(",") else ""
            o = _reconcile_object(json.loads(line.strip().rstrip(",")), survey)
            line = "    " + json.dumps(o, ensure_ascii=False, separators=(",", ":")) + tail
        out.append(line)
    return chr(10).join(out)


def reconcile_pretty_registry(survey: dict, reg: dict) -> dict:
    """The same derivation for the public overlay copy, which is pretty-printed."""
    reg["captured_at"] = survey["checked_at"]
    for src in reg.get("sources") or []:
        if src.get("source_id") in ("official_s3_index", "official_prizes_2027"):
            src["checked_at"] = survey["checked_at"]
    for o in reg.get("objects") or []:
        _reconcile_object(o, survey)
    return reg


def reconcile_v2(survey: dict, v2: dict) -> dict:
    sets = survey["prize_sets"]
    fl = sets["FIRST_LETTERS"]["scrolls"]
    v2["sets"]["FIRST_LETTERS"] = {"count": len(fl), "scrolls": fl}
    v2["sets"]["GRAND_PRIZE_2027"] = {"count": sets["GRAND_PRIZE_2027"]["count"],
                                      "scrolls": sets["GRAND_PRIZE_2027"]["scrolls"]}
    for t in v2["targets"]:
        t["prizes"] = [p for p in PRIZES if t["scroll"] in sets[p]["scrolls"]]
    v2["targets"] = [t for t in v2["targets"] if t["prizes"]]
    v2["reconciled"] = {"from": "argus/public_official_survey.json",
                        "checked_at": survey["checked_at"],
                        "note": "prize membership re-derived from the survey; First Letters is "
                                "%d (was 23 while PHerc1447 was still listed)" % len(fl)}
    return v2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalogue", help="read this saved catalogue instead of fetching it")
    ap.add_argument("--prize-html", help="read this saved prize page instead of fetching it")
    ap.add_argument("--checked-at", help="UTC time to record (default: now)")
    ap.add_argument("--no-zarray", action="store_true", help="do not fetch array headers")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if the survey on disk differs")
    ap.add_argument("--no-reconcile", action="store_true", help="write only the survey")
    a = ap.parse_args(argv)
    checked_at = a.checked_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cat = pathlib.Path(a.catalogue).read_bytes() if a.catalogue else _get(CATALOGUE_URL)
    if cat[:2] == b"\x1f\x8b":
        cat = gzip.decompress(cat)
    html = pathlib.Path(a.prize_html).read_bytes() if a.prize_html else _get(PRIZE_URL)
    survey = build(cat, html, checked_at, zarray=not a.no_zarray)
    if a.no_zarray and SURVEY_PATH.is_file():
        old = json.loads(SURVEY_PATH.read_text(encoding="utf-8"))
        for n, s in survey["samples"].items():
            for vid, row in s["volumes"].items():
                row["array"] = (((old.get("samples") or {}).get(n) or {}).get("volumes") or {}).get(vid, {}).get("array")
    text = _dump(survey)
    if a.check:
        cur = SURVEY_PATH.read_text(encoding="utf-8") if SURVEY_PATH.is_file() else ""
        strip = lambda t: re.sub(r'"checked_at": "[^"]*"', '"checked_at": ""', t)
        page_re = r'("prize_page": \{[^}]*?"sha256": ")([0-9a-f]{64})'
        page_sha = lambda t: [m[1] for m in re.findall(page_re, t, flags=re.S)]
        norm = lambda t: re.sub(page_re, lambda m: m.group(1), strip(t), flags=re.S)
        drift = norm(cur) != norm(text)
        if not drift and page_sha(cur) != page_sha(text):
            print("INFO: the prize page's own sha256 drifted (%s -> %s); every survey row is unchanged"
                  % ((page_sha(cur) or ["?"])[0][:12], (page_sha(text) or ["?"])[0][:12]))
        print("survey %s" % ("DIFFERS from upstream" if drift else "matches upstream"))
        return 1 if drift else 0
    SURVEY_PATH.write_text(text, encoding="utf-8", newline="\n")
    print("wrote %s (%d samples, %d volumes)" % (SURVEY_PATH.relative_to(REPO), len(survey["samples"]),
                                                  sum(len(s["volumes"]) for s in survey["samples"].values())))
    if not a.no_reconcile:
        _write(PUBLIC_REGISTRY_PATH, reconcile_public_registry(survey))
        if CORPUS_REGISTRY_PATH.is_file():
            raw = CORPUS_REGISTRY_PATH.read_text(encoding="utf-8")
            with open(CORPUS_REGISTRY_PATH, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(reconcile_corpus_registry(survey, raw))
        if OVERLAY_REGISTRY_PATH.is_file():
            _write(OVERLAY_REGISTRY_PATH, reconcile_pretty_registry(
                survey, json.loads(OVERLAY_REGISTRY_PATH.read_text(encoding="utf-8"))))
        if V2_PATH.is_file():
            _write(V2_PATH, reconcile_v2(survey, json.loads(V2_PATH.read_text(encoding="utf-8"))),
               indent=1, ensure_ascii=True)
        print("reconciled the derived registries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
