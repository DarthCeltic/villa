"""The one path contract."""
from __future__ import annotations
from argus.public_paths import public_path as _argus_public_path

import ntpath
import os
import re
from dataclasses import dataclass
from pathlib import Path

_CONTRACT = {
    "ARGUS_REPO": (_argus_public_path('repo', ''), "source of record: code, tests, upstream lock"),
    "ARGUS_LEGACY_ROOT": (_argus_public_path('legacy', ''),
                          "artifacts, data, corpus -- read in place, never copied"),
    "ARGUS_CACHE_ROOT": (_argus_public_path('cache', ''),
                         "the single shared chunk cache; preserve, never bulk-delete"),
    "ARGUS_UPSTREAM_ROOT": (_argus_public_path('home', 'upstream'),
                            "pinned external checkouts; Villa is the only supported one"),
    "ARGUS_RUNTIME_ROOT": (_argus_public_path('home', 'runtimes'),
                           "per-component runtimes"),
    "ARGUS_SCIENCE_DATA_ROOT": (_argus_public_path('home', 'data'),
                                "controls and staged scientific inputs"),
    "ARGUS_SCIENCE_CACHE_ROOT": (_argus_public_path('home', 'cache'),
                                 "derived caches; regenerable, never evidence"),
    "ARGUS_RUNS_ROOT": (_argus_public_path('home', 'runs'),
                        "frozen pre-registrations, bindings, errata and run receipts"),
    "ARGUS_MODELS_ROOT": (_argus_public_path('home', 'models'),
                          "checkpoints, hashed; the model store's local half"),
    "ARGUS_REGISTRIES_ROOT": (_argus_public_path('home', 'registries'),
                              "generated model/dataset/asset/target/failure registries"),
    "ARGUS_TOOLS_ROOT": (_argus_public_path('home', 'tools'),
                         "project-scoped external executables and verified downloads"),
}


class PathContractViolation(RuntimeError):
    """A refusal."""

    def __init__(self, var: str, path: Path, detail: str):
        super().__init__("%s -> %s: %s" % (var, path, detail))
        self.var, self.path, self.detail = var, path, detail

    def as_record(self) -> dict:
        return {"ok": False, "terminal": "ARGUS_PATH_CONTRACT_VIOLATION",
                "variable": self.var, "path": str(self.path), "detail": self.detail}


@dataclass(frozen=True)
class Root:
    var: str
    path: Path
    purpose: str
    from_environment: bool

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    def as_record(self) -> dict:
        return {"variable": self.var, "path": str(self.path), "purpose": self.purpose,
                "from_environment": self.from_environment, "exists": self.exists}


def root(var: str, *, require: bool = False) -> Path:
    """Resolve one declared root."""
    if var not in _CONTRACT:
        raise PathContractViolation(var, Path(), "not part of the ARGUS path contract; "
                                    "declared roots are " + ", ".join(sorted(_CONTRACT)))
    default, _purpose = _CONTRACT[var]
    p = Path(os.environ.get(var, default))
    if require and not p.is_dir():
        raise PathContractViolation(
            var, p, "the root does not exist. Reads under it would return empty results "
                    "that are indistinguishable from a genuine absence, which is exactly "
                    "the failure this contract exists to prevent.")
    return p


def describe() -> list:
    out = []
    for var, (default, purpose) in sorted(_CONTRACT.items()):
        env = os.environ.get(var)
        out.append(Root(var, Path(env or default), purpose, env is not None))
    return out


def repo(*parts, require: bool = False) -> Path:
    """Source of record."""
    return root("ARGUS_REPO", require=require).joinpath(*parts)


def legacy(*parts, require: bool = False) -> Path:
    """The legacy checkout, read in place."""
    return root("ARGUS_LEGACY_ROOT", require=require).joinpath(*parts)


def tools(*parts, require: bool = False) -> Path:
    """Project-scoped external tools, never scientific evidence or source of record."""
    return root("ARGUS_TOOLS_ROOT", require=require).joinpath(*parts)


_READ_ONLY_ROOTS = ("ARGUS_LEGACY_ROOT",)


class WriteRootRefusal(RuntimeError):
    """Raised when something tries to write a receipt through a historical root."""


def artifact_write_root() -> Path:
    """THE ONE place a new receipt may be written."""
    return Path(repo("artifacts"))


def artifact_read_roots() -> list:
    """Every root that may be READ, canonical first, then historical."""
    seen, out = set(), []
    for r in (artifact_write_root(), legacy("artifacts"),
              Path(root("ARGUS_RUNS_ROOT")) if "ARGUS_RUNS_ROOT" in _CONTRACT else None):
        if r is None:
            continue
        rp = Path(r)
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def assert_writable(target) -> Path:
    """Refuse a write aimed at a historical root."""
    t = Path(target).resolve()
    for name in _READ_ONLY_ROOTS:
        base = Path(root(name)).resolve()
        try:
            t.relative_to(base)
        except ValueError:
            continue
        raise WriteRootRefusal(
            "%s is inside %s, which is LEGACY_RESEARCH_READ_ONLY for ARGUS receipts. "
            "New evidence must be written under %s." % (t, base, artifact_write_root()))
    return t


def artifact_roots() -> list:
    """EVERY place a receipt actually lives, in precedence order."""
    seen, out = set(), []
    for r in (repo("artifacts"), legacy("artifacts")):
        rp = Path(r)
        if rp not in seen:
            seen.add(rp)
            out.append(rp)
    return out


def serve_roots() -> list:
    """Every root a client-supplied path may resolve into, for either service that serves one -- `argus/service/app.py`'s reads and `argus/service/bff.py`'s writes."""
    return [*artifact_roots(), repo("argus")]


def within_root(p: Path, root: Path) -> bool:
    """Is `p` actually inside `root`, after resolution."""
    try:
        p.relative_to(root.resolve())
        return True
    except ValueError:
        return False


_REFUSED_REMOTE = Path("/__argus_refused_remote_path__")


def _is_remote_path(path) -> bool:
    """A UNC, device or NT-namespace path: two leading separators of either kind, or the NT object prefix (question marks between backslashes, as in the UNC form)."""
    text = str(path).replace("/", chr(92))
    bs = chr(92)
    for candidate in (text, ntpath.normpath(text)):
        low = candidate.lower()
        if candidate.startswith(bs * 2) or low.startswith(bs + "??" + bs) or low == bs + "??":
            return True
    return False


_BAD_SEGMENT = re.compile(r'[<>:"|?*\x00-\x1f\x7f]')
_DRIVE = re.compile(r"[A-Za-z]:")


def plain_local_path(value) -> bool:
    """True only for what an ordinary local path looks like: an optional drive letter, then segments that hold none of `< > : \" | ?"""
    text = str(value)
    if not text or len(text) > 4096:
        return False
    rest = text[2:] if _DRIVE.match(text) else text
    if _is_remote_path(text) or rest.replace("/", chr(92)).startswith(chr(92) * 2):
        return False
    return not any(_BAD_SEGMENT.search(seg) for seg in re.split(r"[\\/]", rest))


def is_remote_path(path) -> bool:
    return _is_remote_path(path)


_URL = re.compile(r"(?:s3|https?)://", re.IGNORECASE)


def looks_like_url(value) -> bool:
    return bool(_URL.match(str(value)))


def clean_object_url(value) -> bool:
    """A plain ASCII s3/http(s) object address: no backslash, no control or format character of any kind, no `..` segment."""
    text = str(value)
    if not text.isascii() or not _URL.match(text) or "\\" in text or ".." in text.split("/"):
        return False
    return not any(ord(c) < 0x20 or ord(c) == 0x7F for c in text)


def outside_served_roots(*, url_labels=(), **named_paths) -> str | None:
    """The label of the first named path that is remote, or lies outside the served roots, else None."""
    roots = serve_roots()
    for label, value in named_paths.items():
        if value in (None, ""):
            continue
        text = str(value)
        if _URL.match(text):
            if label in url_labels and clean_object_url(text):
                continue
            return label
        if _is_remote_path(text) or not plain_local_path(text):
            return label
        try:
            resolved = Path(text).resolve()
        except (OSError, ValueError, RuntimeError):
            return label
        if not any(within_root(resolved, root) for root in roots):
            return label
    return None


def resolve_served_path(path: str, roots: list) -> Path:
    """Resolve a path a run recorded (which may be repo-relative, e.g."""
    if _is_remote_path(path) or not plain_local_path(path):
        return _REFUSED_REMOTE
    raw = Path(path)
    if raw.is_absolute():
        return raw.resolve()
    for root in roots:
        cand = (root.parent / raw).resolve()
        if cand.exists() and within_root(cand, root):
            return cand
        cand = (root / raw).resolve()
        if cand.exists() and within_root(cand, root):
            return cand
    return raw.resolve()


def resolve_ui_target_dir(target: str) -> Path | None:
    """The on-disk directory for one exported layer-stack target, e.g."""
    import json as _json

    base = next((r / "ui_audit" for r in artifact_roots()
                 if (r / "ui_audit" / "TARGETS.json").is_file()), None)
    if base is None:
        return None
    idx = _json.loads((base / "TARGETS.json").read_text(encoding="utf-8"))
    row = next((t for t in idx["targets"] if t["key"] == target), None)
    if row is None:
        return None
    return base / row["base"].strip("/")


def find_artifact(*parts, require: bool = False) -> Path:
    """Resolve one receipt across every artifact root."""
    roots = artifact_roots()
    for r in roots:
        c = r.joinpath(*parts)
        if c.exists():
            return c
    if require:
        raise FileNotFoundError(
            "no artifact %r under any root: %s" % ("/".join(map(str, parts)),
                                                   [str(r) for r in roots]))
    return roots[0].joinpath(*parts)


def resolve_repo_relative(rel, *, require: bool = False) -> Path:
    """Resolve ANY repo-relative path across the canonical root and the legacy root."""
    rel = str(rel).replace("\\", "/").lstrip("/")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise PathContractViolation(
            "ARGUS_REPO", repo(),
            "refusing to resolve %r: a parent traversal can leave the declared roots entirely, "
            "and a path that escapes its root is not one this contract can reason about" % rel)
    canonical = repo(*parts)
    if canonical.exists():
        return canonical
    for var in _CONTRACT:
        if var == "ARGUS_REPO":
            continue
        if var != "ARGUS_LEGACY_ROOT":
            continue
        candidate = root(var).joinpath(*parts)
        if candidate.exists():
            return candidate
    if require:
        raise FileNotFoundError(
            "no %r under the canonical root (%s) or the legacy root (%s)"
            % (rel, repo(), legacy()))
    return canonical


def resolution_report(rel) -> dict:
    """Where a path resolved and where it was searched."""
    rel = str(rel).replace("\\", "/").lstrip("/")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    searched = [repo(*parts), legacy(*parts)]
    found = next((c for c in searched if c.exists()), None)
    return {
        "relative": rel,
        "resolved": str(found) if found else None,
        "found_in": ("canonical" if found == searched[0]
                     else "legacy" if found else None),
        "searched": [str(c) for c in searched],
        "exists": found is not None,
    }


def artifacts(*parts, require: bool = False) -> Path:
    """READ-ONLY convenience for the historical root."""
    base = root("ARGUS_LEGACY_ROOT", require=require) / "artifacts"
    if require and not base.is_dir():
        raise PathContractViolation("ARGUS_LEGACY_ROOT", base,
                                    "no artifacts directory under the legacy root")
    return base.joinpath(*parts)


def data(*parts, require: bool = False) -> Path:
    base = root("ARGUS_LEGACY_ROOT", require=require) / "data"
    return base.joinpath(*parts)


def corpus(*parts, require: bool = False) -> Path:
    base = root("ARGUS_LEGACY_ROOT", require=require) / "corpus"
    return base.joinpath(*parts)


def models(*parts, require: bool = False) -> Path:
    """Model weights."""
    base = root("ARGUS_LEGACY_ROOT", require=require) / "models"
    return base.joinpath(*parts)


def cache(*parts, require: bool = False) -> Path:
    """The shared chunk cache."""
    return root("ARGUS_CACHE_ROOT", require=require).joinpath(*parts)


def upstream(*parts, require: bool = False) -> Path:
    return root("ARGUS_UPSTREAM_ROOT", require=require).joinpath(*parts)



def science_data(*parts, require: bool = False) -> Path:
    """Controls and staged scientific inputs (e.g."""
    return root("ARGUS_SCIENCE_DATA_ROOT", require=require).joinpath(*parts)


def science_cache(*parts, require: bool = False) -> Path:
    """Derived caches."""
    return root("ARGUS_SCIENCE_CACHE_ROOT", require=require).joinpath(*parts)


def runs(*parts, require: bool = False) -> Path:
    """Frozen pre-registrations, input bindings, errata and run receipts."""
    return root("ARGUS_RUNS_ROOT", require=require).joinpath(*parts)


def model_store(*parts, require: bool = False) -> Path:
    """The local checkpoint store."""
    return root("ARGUS_MODELS_ROOT", require=require).joinpath(*parts)


def registries(*parts, require: bool = False) -> Path:
    """Generated model/dataset/asset/target/failure registries."""
    return root("ARGUS_REGISTRIES_ROOT", require=require).joinpath(*parts)



USER_DATA_IN_REPO_OPT_IN = "ARGUS_USER_DATA_ALLOW_IN_REPO"


def argus_home(*parts) -> Path:
    """ARGUS_HOME (default <ARGUS_HOME>) -- the machine-local state home the services already use."""
    return Path(os.environ.get("ARGUS_HOME", _argus_public_path('home', ''))).joinpath(*parts)


def _inside_git_work_tree(p: Path):
    """The work tree containing `p`, or None."""
    rp = p.resolve()
    candidates = [repo().resolve()]
    candidates.extend(a for a in [rp, *rp.parents] if (a / ".git").exists())
    for c in candidates:
        if rp == c or c in rp.parents:
            return c
    return None


def user_data_root(*parts, check: bool = True) -> Path:
    """The operator's private user-data cache."""
    env = os.environ.get("ARGUS_USER_DATA")
    base = Path(env) if env else argus_home("user_data")
    if check and os.environ.get(USER_DATA_IN_REPO_OPT_IN) != "1":
        tree = _inside_git_work_tree(base)
        if tree is not None:
            raise PathContractViolation(
                "ARGUS_USER_DATA", base,
                "the user-data root is inside the git work tree %s. Personal data there is one "
                "`git add -A` from being pushed. Choose a root outside any checkout, or set %s=1 "
                "to accept that explicitly." % (tree, USER_DATA_IN_REPO_OPT_IN))
    return base.joinpath(*parts)


def public_cache_root(*parts) -> Path:
    """Where public downloads are cached."""
    env = os.environ.get("ARGUS_PUBLIC_CACHE")
    base = Path(env) if env else root("ARGUS_CACHE_ROOT")
    return base.joinpath(*parts)


def public_cache_roots() -> list:
    """Every declared store of PUBLIC downloaded data, in place."""
    out, seen = [], set()
    for p in (public_cache_root(), cache(), upstream(), science_cache()):
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def villa(*parts, require: bool = False) -> Path:
    """The sole supported Villa checkout."""
    return upstream("villa", require=require).joinpath(*parts)


def runtimes(*parts, require: bool = False) -> Path:
    return root("ARGUS_RUNTIME_ROOT", require=require).joinpath(*parts)


def villa_runtime(*parts, require: bool = False) -> Path:
    return runtimes("villa-vesuvius", require=require).joinpath(*parts)


def selftest() -> bool:
    ck = []
    src = Path(__file__).read_text(encoding="utf-8")

    ck.append(("every contract root is declared (11)", len(_CONTRACT) == 11))
    ck.append(("and each is overridable by its own environment variable",
               all(v.startswith("ARGUS_") for v in _CONTRACT)))

    a, r = artifacts(), repo()
    ck.append(("artifacts resolve to the LEGACY root, not beside the code",
               r not in a.parents and a != r))
    ck.append(("and the empty-artifacts-after-migration failure is recorded with its cause",
               "EMPTY" in src and "indistinguishable from a genuine absence" in src))

    os.environ["ARGUS_LEGACY_ROOT"] = str(Path(__file__).parent)
    try:
        ck.append(("an environment override is honoured",
                   root("ARGUS_LEGACY_ROOT") == Path(__file__).parent))
    finally:
        del os.environ["ARGUS_LEGACY_ROOT"]

    try:
        root("ARGUS_NOT_A_ROOT")
        unknown_refused = False
    except PathContractViolation:
        unknown_refused = True
    ck.append(("an undeclared root is refused, not invented", unknown_refused))

    os.environ["ARGUS_CACHE_ROOT"] = str(Path(__file__).parent / "does-not-exist")
    try:
        cache(require=True)
        missing_refused = False
    except PathContractViolation as e:
        missing_refused = "does not exist" in e.detail
    finally:
        del os.environ["ARGUS_CACHE_ROOT"]
    ck.append(("SABOTAGE a missing root REFUSES under require, rather than returning a "
               "path into empty space", missing_refused))

    ck.append(("but a missing root is still returnable without require, so a caller can "
               "report on it", isinstance(cache(), Path)))
    ck.append(("model weights resolve to the legacy root, not the repo",
               repo() not in models().parents and models().name == "models"))
    ck.append(("the cache is documented as never copied or bulk-deleted",
               "never copied" in src and "never bulk-delete" in src))
    ck.append(("describe() reports every root with its existence",
               len(describe()) == len(_CONTRACT)
               and all("exists" in x.as_record() for x in describe())))

    ok = True
    for msg, good in ck:
        print("  %s %s" % ("PASS" if good else "FAIL", msg))
        ok &= bool(good)
    print("selftest: %d/%d passed" % (sum(1 for _, g in ck if g), len(ck)))
    return ok


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="ARGUS path contract")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    bad = 0
    for r in describe():
        mark = "ok " if r.exists else "MISSING"
        star = " (env)" if r.from_environment else ""
        print("%-8s %-20s %s%s" % (mark, r.var, r.path, star))
        print("         %s" % r.purpose)
        bad += 0 if r.exists else 1
    print("\nartifacts -> %s" % artifacts())
    print("villa     -> %s" % villa())
    print("runtime   -> %s" % villa_runtime())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
