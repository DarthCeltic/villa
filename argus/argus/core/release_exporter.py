"""What may leave this machine, decided by an allowlist that fails closed."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import dataclasses
import re
from importlib import import_module

CONTRACT = "argus-release-exporter-v1"

PUBLIC_CORE = "PUBLIC_CORE"
PUBLIC_EVIDENCE = "PUBLIC_EVIDENCE"
PRIVATE_CAMPAIGN = "PRIVATE_CAMPAIGN"
LOCAL_DATA_POINTER = "LOCAL_DATA_POINTER"
CLASSES = (PUBLIC_CORE, PUBLIC_EVIDENCE, PRIVATE_CAMPAIGN, LOCAL_DATA_POINTER)

PUBLISHABLE = (PUBLIC_CORE, PUBLIC_EVIDENCE)

ALLOW = (
  (r"^argus/core/[a-z_]+\.py$",                 PUBLIC_CORE),
  (r"^argus/tests/test_[a-z_]+\.py$",           PUBLIC_CORE),
  (r"^argus/ui/tests/test_[a-z_]+\.py$",        PUBLIC_CORE),
  (r"^argus/service/[a-z_]+\.py$",              PUBLIC_CORE),
  (r"^argus/ui/src/.*\.(ts|tsx|css)$",          PUBLIC_CORE),
  (r"^scripts/[a-z0-9_]+\.py$",                 PUBLIC_CORE),
  (r"^pyproject\.toml$|^README\.md$|^LICEN[CS]E$", PUBLIC_CORE),
  (r"^corpus/schemas/.*\.md$",                  PUBLIC_CORE),
  (r"^artifacts/[a-z0-9_]+/(?:%s)$" % "|".join(
      n.replace(".", r"\.") for n in (
        "END_TO_END_CONTROL.json",
        "CONTROLS.json",
        "SEAL_surface_recto.json",
        "SEAL_surface_recto_verso.json",
        "DIARY_SHOTS.json",
        "UI_VERIFY.json",
        "UPSTREAM_LICENCE_REFRESH.json",
        "SBOM_RECEIPT.json",
      )),
   PUBLIC_EVIDENCE),
  (r"^corpus/findings/checks\.json$",           PUBLIC_EVIDENCE),
)

DENY = (
  (r"(?:ROUTE|CAMPAIGN|TARGET|PRIZE)_?MAP",  PRIVATE_CAMPAIGN,
   "target selection and progress. The standing instruction is that nothing about target "
   "choices or progress is published until the operator says so."),
  (r"^corpus/candidates?/|candidate_map|BLIND_HUNT",        PRIVATE_CAMPAIGN,
   "candidate maps are the campaign."),
  (r"_(?:annotations|mappings)(?:/|$|\.(?!py$|tsx?$))",       PRIVATE_CAMPAIGN,
   "the operator's own notes and editorial decisions."),
  (r"\.env$|token|credential|secret|client[_-]?id",         PRIVATE_CAMPAIGN,
   "credentials never leave, and the name is only the first check."),
  (r"\.(zarr|npy|tif|tiff|pth|ckpt|sqlite)$|/objects/",     LOCAL_DATA_POINTER,
   "bytes stay here. A pointer to them may be public; the data is governed by its own licence."),
)

SECRET_SHAPES = (
  re.compile(r"sk-[A-Za-z0-9]{16,}"),
  re.compile(r"gh[pous]_[A-Za-z0-9]{20,}"),
  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
  re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}"),
  re.compile(r"\"client_secret\"\s*:"),
  re.compile(r"AKIA[0-9A-Z]{16}"),
)


class ExportRefusal(RuntimeError):
    """Raised rather than shipping something nobody classified."""


DENY_EXCEPTIONS = {
  "argus/ui/src/theme/tokens.css":
    "CSS design tokens -- colour, spacing and type scale. Matched the credential rule on the "
    "word 'token' in its filename. Read and verified: no secret shape, and all eight "
    "occurrences are the design-token sense. Still subject to the content scan.",
  "argus/core/blind_hunt_firewall.py":
    "the sandbox that makes a BLIND_HUNT leak impossible to PERFORM rather than merely against "
    "the rules. Matched the `BLIND_HUNT` rule on its filename. 244 lines, READ and verified: no "
    "secret shape, no target identity, no candidate. A public repository without this file "
    "ships the closure without its enforcement.",
  "argus/core/secrets.py":
    "credential handling via the OS keyring, written so a value can never be read back. Matched "
    "the `secret` rule on its filename. 188 lines, READ and verified: no secret shape and no "
    "long literal assignment anywhere in it -- the module exists so that no credential is ever "
    "in a file, which is the opposite of containing one.",
}


def _user_data_verdict(path: str) -> dict | None:
    """User-data release chop."""
    try:
        user_data = import_module("argus.core.user_data")
        return user_data.classify_path(path)
    except Exception as exc:
        return {"category": "unevaluable", "rule": "USER_DATA:UNEVALUABLE:%s"
                % type(exc).__name__, "item": None}


def classify(path: str) -> dict:
    p = str(path).replace("\\", "/").lstrip("./")
    ud = _user_data_verdict(path)
    if ud is not None:
        return {"path": p, "class": PRIVATE_CAMPAIGN, "publishable": False,
                "why": "user data (%s). The operator's own material never leaves this machine; it "
                       "is chopped by path and by hash, not by content shape." % ud["category"],
                "rule": ud["rule"]}
    if p in DENY_EXCEPTIONS:
        pass
    else:
        for pat, cls, why in DENY:
            if re.search(pat, p, re.I):
                return {"path": p, "class": cls, "publishable": False, "why": why,
                        "rule": "DENY:%s" % pat}
    for pat, cls in ALLOW:
        if re.match(pat, p):
            return {"path": p, "class": cls, "publishable": cls in PUBLISHABLE,
                    "why": "explicitly allowed", "rule": "ALLOW:%s" % pat}
    return {"path": p, "class": None, "publishable": False,
            "why": "matches no allow rule. An allowlist ships what it names and withholds "
                   "everything else, which is the correct default: a blocklist ships everything "
                   "nobody thought of.",
            "rule": "UNCLASSIFIED"}


def scan_content(text: str) -> list:
    return [s.pattern for s in SECRET_SHAPES if s.search(text or "")]


def check_export(paths, *, read=None) -> dict:
    """Decide a whole export at once, and report every refusal rather than the first."""
    rows, refused = [], []
    ud_hashes = None
    for p in paths:
        r = classify(p)
        if r["publishable"] and read is not None:
            try:
                body = read(p)
                hits = scan_content(body if isinstance(body, str)
                                    else (body or b"").decode("utf-8", "replace"))
            except Exception as exc:
                body, hits = None, ["UNREADABLE:%s" % type(exc).__name__]
            if hits:
                r["publishable"] = False
                r["why"] = "content matches a secret shape: %s" % hits
                r["rule"] = "SECRET_CONTENT"
            else:
                try:
                    user_data = import_module("argus.core.user_data")
                    if ud_hashes is None:
                        ud_hashes = user_data.user_data_hashes()
                    raw = body.encode("utf-8") if isinstance(body, str) else (body or b"")
                    import hashlib
                    ds = {hashlib.sha256(raw).hexdigest(),
                          hashlib.sha256(raw.replace(user_data.CRLF, user_data.LF)).hexdigest()}
                    d = next((x for x in ds if x in ud_hashes), None)
                    if len(raw) >= user_data.MIN_HASH_BYTES and d:
                        r["publishable"] = False
                        r["why"] = "content is byte-identical to user-data item %s" % ud_hashes[d]
                        r["rule"] = "USER_DATA:CONTENT_HASH"
                except Exception as exc:
                    r["publishable"] = False
                    r["why"] = "user-data hash check could not run: %s" % type(exc).__name__
                    r["rule"] = "USER_DATA:UNEVALUABLE"
        rows.append(r)
        if not r["publishable"]:
            refused.append(r)
    return {
      "contract": CONTRACT,
      "considered": len(rows), "publishable": sum(1 for r in rows if r["publishable"]),
      "withheld": len(refused),
      "by_class": {c: sum(1 for r in rows if r["class"] == c) for c in CLASSES},
      "unclassified": [r["path"] for r in rows if r["class"] is None][:20],
      "rows": rows,
      "fail_closed": "anything unclassified is withheld. The failure direction is deliberate: "
                     "withholding something publishable is recoverable, publishing something "
                     "private is not.",
    }


def require_publishable(paths, *, read=None) -> dict:
    r = check_export(paths, read=read)
    if r["withheld"]:
        raise ExportRefusal(
          "%d of %d paths are not publishable. First few: %s"
          % (r["withheld"], r["considered"],
             [(x["path"], x["rule"]) for x in r["rows"] if not x["publishable"]][:5]))
    return r


def selftest() -> bool:
    """An allowlist that is too narrow fails quietly in the safe direction, which is still failing."""
    from argus.core import paths as _paths
    ok = []
    ok.append(("a real receipt name is publishable",
               classify("artifacts/official_route/SEAL_surface_recto_verso.json")
               ["publishable"] is True))
    ok.append(("DENY beats ALLOW",
               classify("scripts/candidate_map_builder.py")["class"] == PRIVATE_CAMPAIGN))
    ok.append(("an unnamed path is withheld",
               classify("some/unlisted/thing.py")["class"] is None))
    ok.append(("volume bytes are a pointer",
               classify(_argus_public_path('legacy', 'data/w00.zarr'))["class"] == LOCAL_DATA_POINTER))
    leak = check_export(["argus/core/metrics.py"],
                        read=lambda p: "TOKEN = 'gh" + "p_" + "A" * 30 + "'")
    ok.append(("content scanning withholds what the path rules would ship",
               leak["publishable"] == 0 and leak["rows"][0]["rule"] == "SECRET_CONTENT"))
    unread = check_export(["argus/core/metrics.py"],
                          read=lambda p: (_ for _ in ()).throw(OSError("gone")))
    ok.append(("an unreadable file is withheld rather than assumed clean",
               unread["withheld"] == 1))
    ok.append(("every allow rule is anchored at both ends",
               all(p.startswith("^") and p.endswith("$") for p, _c in ALLOW)))
    ok.append(("the UI's own acceptance tests are publishable",
               classify("argus/ui/tests/test_ui_information_architecture.py")["publishable"]
               is True))
    ok.append(("design tokens are not a credential",
               classify("argus/ui/src/theme/tokens.css")["class"] == PUBLIC_CORE))
    ok.append(("but a real credential path is STILL denied",
               classify("argus/ui/src/theme/access_token.css")["class"] == PRIVATE_CAMPAIGN))
    ok.append(("and an exception does not exempt a path from the CONTENT scan",
               check_export(["argus/ui/src/theme/tokens.css"],
                            read=lambda _p: "gh" + "p_" + "A" * 30)["publishable"] == 0))
    ok.append(("every exception names an exact path, never a pattern",
               all("*" not in k and "$" not in k for k in DENY_EXCEPTIONS)))
    ok.append(("every exception carries a reason long enough to be one",
               all(len(v) > 60 for v in DENY_EXCEPTIONS.values())))
    ok.append(("the guard modules that enforce the boundary are publishable",
               classify("argus/core/blind_hunt_firewall.py")["class"] == PUBLIC_CORE
               and classify("argus/core/secrets.py")["class"] == PUBLIC_CORE))
    ok.append(("but a candidate map is STILL denied by the same rule",
               classify("corpus/candidates/BLIND_HUNT_map.json")["class"]
               == PRIVATE_CAMPAIGN))
    ok.append(("user data under the user-data root is refused before any allow rule",
               classify(str(_paths.user_data_root("notes", "x.py", check=False)))
               ["rule"].startswith("USER_DATA")))
    ok.append(("and an actual credential file is STILL denied",
               classify("argus/core/my_secret_token.py")["class"] == PRIVATE_CAMPAIGN))
    for name, good in ok:
        print("  %-4s %s" % ("ok" if good else "FAIL", name))
    print("selftest: %d/%d passed" % (sum(1 for _, g in ok if g), len(ok)))
    return all(g for _, g in ok)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS release exporter")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    print("classes: %s" % (CLASSES,))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
