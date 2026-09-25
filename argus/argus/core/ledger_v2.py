"""Command ledger v2: an append-only successor that SEALS the broken v1 chain instead of repairing it."""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import time
from pathlib import Path

SCHEMA = "argus-ledger-v2"
STATUS_SCHEMA = "argus-ledger-status-v1"
ZERO = "0" * 64

HISTORICAL_CHAIN_BROKEN = "HISTORICAL_CHAIN_BROKEN"
HISTORICAL_CHAIN_INTACT = "HISTORICAL_CHAIN_INTACT"
HISTORICAL_CHAIN_MISSING = "HISTORICAL_CHAIN_MISSING"
HISTORICAL_CHAIN_UNREADABLE = "HISTORICAL_CHAIN_UNREADABLE"

CURRENT_CHAIN_VERIFIED = "CURRENT_CHAIN_VERIFIED"
CURRENT_CHAIN_BROKEN = "CURRENT_CHAIN_BROKEN"
CURRENT_CHAIN_UNKNOWN = "CURRENT_CHAIN_UNKNOWN"

LOCK_WAIT_S = 10.0
LOCK_STALE_S = 120.0


_UNSEEN = object()
_RECLAIM_BUSY = object()


class LedgerError(Exception):
    pass


class AlreadySealed(LedgerError):
    pass


class NotSealed(LedgerError):
    pass



def _actions():
    from argus.core import actions
    return actions


def legacy_path() -> Path:
    return Path(_actions().AUDIT)


def v2_path() -> Path:
    return Path(_actions().STATE) / "ledger_v2.jsonl"


def marker_path(v2: Path | None = None) -> Path:
    v2 = Path(v2) if v2 is not None else v2_path()
    return v2.with_name(v2.stem + ".SEALED.json")


def _lock_path(v2: Path) -> Path:
    return v2.with_name(v2.stem + ".lock")



def canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode("utf-8")


def record_hash(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k != "this_hash"}
    return hashlib.sha256(canonical(body)).hexdigest()


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def file_facts(path: Path) -> dict:
    """sha256 + size + line count of the WHOLE file, from one read of its bytes."""
    data = Path(path).read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data), "line_count": data.count(b"\n")}



def v1_verify_bytes(data: bytes) -> dict:
    """The v1 verification rule, applied to bytes."""
    prev = ZERO
    n = 0
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("prev_hash") != prev:
            return {"status": "BROKEN", "at": n, "why": "prev_hash does not chain"}
        body = {k: v for k, v in rec.items() if k != "this_hash"}
        want = hashlib.sha256(
            (prev + json.dumps(body, sort_keys=True, default=str)).encode("utf-8")).hexdigest()
        if want != rec.get("this_hash"):
            return {"status": "BROKEN", "at": n,
                    "why": "record content does not match its own hash"}
        prev = rec["this_hash"]
        n += 1
    return {"status": "INTACT", "n": n, "head": prev}


def v1_forensics(data: bytes) -> dict:
    """Every link break and every self-hash failure, not just the first, with a diagnosis."""
    recs = [json.loads(l) for l in data.decode("utf-8").splitlines() if l.strip()]
    self_bad, link_breaks = [], []
    by_hash = {}
    for i, r in enumerate(recs):
        body = {k: v for k, v in r.items() if k != "this_hash"}
        want = hashlib.sha256((str(r.get("prev_hash")) + json.dumps(body, sort_keys=True,
                                                                     default=str))
                              .encode("utf-8")).hexdigest()
        if want != r.get("this_hash"):
            self_bad.append(i)
        if i > 0 and r.get("prev_hash") != recs[i - 1].get("this_hash"):
            parent = by_hash.get(r.get("prev_hash"))
            link_breaks.append({
                "at": i,
                "prev_hash_names_record": parent,
                "utc": r.get("utc"),
                "previous_record_utc": recs[i - 1].get("utc"),
                "shape": ("FORK: this record and record %d both chain off record %d"
                          % (i - 1, parent)) if parent is not None and parent == i - 2
                         else ("prev_hash names record %s" % parent),
            })
        by_hash.setdefault(r.get("this_hash"), i)
    first = link_breaks[0]["at"] if link_breaks else None
    return {
        "record_count": len(recs),
        "self_hash_failures": self_bad,
        "link_breaks": link_breaks,
        "last_attested_record": (first - 1) if first is not None else len(recs) - 1,
        "last_attested_head": (recs[first - 1]["this_hash"] if first else
                               (recs[-1]["this_hash"] if recs else ZERO)),
        "tail_this_hash": recs[-1]["this_hash"] if recs else ZERO,
        "tail_utc": recs[-1].get("utc") if recs else None,
    }



def _unlink_retry(path: Path, attempts: int = 200) -> None:
    """Delete a lock or guard file."""
    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.01)


class _Lock:
    def __init__(self, v2: Path):
        self.p = _lock_path(v2)
        self.fd = None
        self.token = hashlib.sha256(
            ("%d|%f|%s" % (os.getpid(), time.time(), self.p)).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _holder_state(held: dict) -> str:
        """Return LIVE, GONE, or UNKNOWN for the recorded process identity."""
        pid = held.get("pid")
        created = held.get("process_created")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created):
            return "UNKNOWN"
        try:
            import psutil
        except ImportError:
            return "UNKNOWN"
        try:
            process = psutil.Process(pid)
            return "LIVE" if abs(float(process.create_time()) - float(created)) < 0.5 else "GONE"
        except psutil.NoSuchProcess:
            return "GONE"
        except Exception:
            return "UNKNOWN"

    def _read_holder(self, path: Path | None = None) -> dict:
        try:
            value = json.loads((path or self.p).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _try_reclaim_dead_holder(self, held: dict):
        """True: reclaimed."""
        guard = self.p.with_name(self.p.name + ".reclaim")
        try:
            gfd = os.open(str(guard), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            try:
                age = time.time() - guard.stat().st_mtime
                if age > 30 or age < -30:
                    guard.unlink()
            except OSError:
                pass
            return _RECLAIM_BUSY
        try:
            current = self._read_holder()
            if held:
                if current.get("token") != held.get("token") or self._holder_state(current) != "GONE":
                    return False
            else:
                if current:
                    return False
                try:
                    age = time.time() - self.p.stat().st_mtime
                except OSError:
                    return True
                if 0 <= age < LOCK_STALE_S:
                    return False
            try:
                _unlink_retry(self.p, attempts=50)
            except OSError:
                return False
            return True
        finally:
            os.close(gfd)
            _unlink_retry(guard)

    def __enter__(self):
        self.p.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOCK_WAIT_S
        first_owner = _UNSEEN
        busy_since = None
        while True:
            try:
                self.fd = os.open(str(self.p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    try:
                        import psutil
                        created = round(float(psutil.Process(os.getpid()).create_time()), 3)
                    except Exception:
                        created = None
                    payload = {"pid": os.getpid(), "process_created": created,
                               "token": self.token, "taken_utc": _utc()}
                    os.write(self.fd, canonical(payload))
                    os.fsync(self.fd)
                except BaseException:
                    os.close(self.fd)
                    self.fd = None
                    _unlink_retry(self.p)
                    raise
                return self
            except (FileExistsError, PermissionError):
                if first_owner is _UNSEEN:
                    first_owner = self._read_holder().get("token")
                if time.monotonic() > deadline:
                    held = self._read_holder()
                    if held and held.get("token") != first_owner:
                        first_owner = held.get("token")
                        deadline = time.monotonic() + LOCK_WAIT_S
                        continue
                    reclaimed = self._try_reclaim_dead_holder(held)
                    if reclaimed is _RECLAIM_BUSY:
                        busy_since = busy_since or time.monotonic()
                        if time.monotonic() - busy_since > 3 * LOCK_WAIT_S:
                            raise LedgerError("ledger v2 lock %s: another process has been reclaiming it for over %d s; refusing rather than forking the chain" % (self.p, 3 * LOCK_WAIT_S))
                        time.sleep(0.02)
                        continue
                    busy_since = None
                    if reclaimed:
                        deadline = time.monotonic() + LOCK_WAIT_S
                        continue
                    now_held = self._read_holder()
                    if now_held.get("token") != held.get("token") or (held and not now_held):
                        deadline = time.monotonic() + LOCK_WAIT_S
                        continue
                    raise LedgerError("ledger v2 lock %s is held; refusing rather than "
                                      "forking the chain" % self.p)
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        held = self._read_holder()
        if held.get("token") != self.token:
            raise LedgerError("ledger v2 lock ownership changed before release; refusing to "
                              "delete another owner's lock")
        _unlink_retry(self.p)


def _fsync_append(path: Path, line: str, *, create: bool) -> None:
    flags = os.O_WRONLY | os.O_APPEND | (os.O_CREAT | os.O_EXCL if create else 0)
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)



def seal(*, old: Path | None = None, v2: Path | None = None, sealed_by: dict | None = None,
         now: str | None = None) -> dict:
    """Write the v2 genesis."""
    old = Path(old) if old is not None else legacy_path()
    v2 = Path(v2) if v2 is not None else v2_path()
    mk = marker_path(v2)
    if not old.is_file():
        raise LedgerError("nothing to seal: the v1 ledger %s does not exist" % old)
    from argus.core import actions as _A
    v1_guard = _A._lease_lock("audit_v1") if old.resolve() == Path(_A.AUDIT).resolve() else contextlib.nullcontext()
    with v1_guard, _Lock(v2):
        if v2.exists() or mk.exists():
            existing = None
            try:
                existing = json.loads(v2.read_text(encoding="utf-8").splitlines()[0])["this_hash"]
            except Exception:
                pass
            raise AlreadySealed("ledger v2 is already sealed at %s (genesis %s). A second "
                                "genesis would be a second history." % (v2, existing))
        data = old.read_bytes()
        facts = {"path": str(old).replace("\\", "/"),
                 "sha256": hashlib.sha256(data).hexdigest(),
                 "size_bytes": len(data), "line_count": data.count(b"\n")}
        chain = v1_verify_bytes(data)
        foren = v1_forensics(data)
        after = hashlib.sha256(old.read_bytes()).hexdigest()
        if after != facts["sha256"]:
            raise LedgerError("the v1 ledger changed while it was being sealed; refusing")
        broken = chain["status"] == "BROKEN"
        first = chain.get("at")
        genesis = {
            "schema": SCHEMA, "kind": "GENESIS", "seq": 0, "utc": now or _utc(),
            "prev_hash": ZERO,
            "predecessor": dict(facts, **{
                "role": "v1 command ledger, preserved byte-identical and never rewritten",
                "record_count": foren["record_count"],
                "tail_this_hash": foren["tail_this_hash"],
                "tail_utc": foren["tail_utc"],
                "verified_by": "argus.core.actions.verify_audit_chain",
                "historical_chain": chain,
                "break": ({"at": first, "why": chain.get("why"),
                          "all_link_breaks": foren["link_breaks"],
                          "self_hash_failures": foren["self_hash_failures"],
                          "diagnosis": ("every record matches its own hash; the LINKS fork. "
                                        "actions.audit() read the last line and appended "
                                        "without a lock, so concurrent submits in the same "
                                        "second chained off the same predecessor.")}
                         if broken else None),
            }),
            "attestation": {
                "historical_state": HISTORICAL_CHAIN_BROKEN if broken else HISTORICAL_CHAIN_INTACT,
                "attested_by_v1_chain": ("records 0..%d" % foren["last_attested_record"])
                                        if foren["record_count"] else "none",
                "last_attested_head": foren["last_attested_head"],
                "post_break": ("POST_BREAK_ATTESTATION_LOST_UNVERIFIED: records %d..%d of the "
                               "v1 file are not attested by any chain" %
                               (first, foren["record_count"] - 1)) if broken else None,
                "re_attested": False,
                "historical_certification": "SUPPRESSED",
                "statement": ("This genesis does NOT re-attest, repair, or rehabilitate any v1 "
                              "record. It binds the v1 file's exact bytes so that the history "
                              "cannot be altered without v2 failing. Any certification that "
                              "depended on the v1 chain remains suppressed for every record in "
                              "the v1 file. Only records appended to v2 after this genesis are "
                              "attested by v2."),
            },
            "sealed_by": sealed_by or {},
        }
        genesis["this_hash"] = record_hash(genesis)
        _fsync_append(v2, json.dumps(genesis, sort_keys=True, ensure_ascii=False) + "\n",
                      create=True)
        marker = {"schema": SCHEMA + "-seal-marker", "v2_path": str(v2).replace("\\", "/"),
                  "genesis_hash": genesis["this_hash"], "predecessor_sha256": facts["sha256"],
                  "why": "if the v2 file disappears, audit() must refuse rather than resume "
                         "writing into the sealed v1 file"}
        _fsync_append(mk, json.dumps(marker, sort_keys=True) + "\n", create=True)
    return genesis


def is_sealed(v2: Path | None = None) -> bool:
    v2 = Path(v2) if v2 is not None else v2_path()
    return v2.exists() or marker_path(v2).exists()



def append(event: dict, *, v2: Path | None = None) -> dict:
    v2 = Path(v2) if v2 is not None else v2_path()
    if not v2.is_file():
        if marker_path(v2).exists():
            raise LedgerError("ledger v2 was sealed (%s) but %s is missing; refusing to write "
                              "anywhere" % (marker_path(v2), v2))
        raise NotSealed("ledger v2 is not sealed; call argus.core.ledger_v2.seal() first")
    with _Lock(v2):
        data = v2.read_bytes()
        if not data.endswith(b"\n"):
            raise LedgerError("ledger v2 ends in a partial line; refusing to chain onto it")
        last = json.loads(data.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode("utf-8"))
        if record_hash(last) != last.get("this_hash"):
            raise LedgerError("the last v2 record does not match its own hash; refusing to "
                              "extend a chain whose head is already wrong")
        rec = {"schema": SCHEMA, "kind": "EVENT", "seq": int(last["seq"]) + 1, "utc": _utc(),
               "prev_hash": last["this_hash"], "event": event}
        rec["this_hash"] = record_hash(rec)
        _fsync_append(v2, json.dumps(rec, sort_keys=True, ensure_ascii=False, default=str) + "\n",
                      create=False)
    return rec


PREDECESSOR_APPEND_ACK = "PREDECESSOR_POST_SEAL_APPEND_ACKNOWLEDGED"


def acknowledge_predecessor_append(*, old: Path | None = None, v2: Path | None = None,
                                   acknowledged_by: dict | None = None,
                                   why: str = "") -> dict:
    """Append (to v2) an explicit acknowledgement of bytes a stale writer appended to v1."""
    old = Path(old) if old is not None else legacy_path()
    v2 = Path(v2) if v2 is not None else v2_path()
    if not v2.is_file():
        raise NotSealed("ledger v2 is not sealed")
    genesis = json.loads(v2.read_text(encoding="utf-8").splitlines()[0])
    pred = genesis["predecessor"]
    raw = old.read_bytes()
    size = pred["size_bytes"]
    if len(raw) <= size or hashlib.sha256(raw[:size]).hexdigest() != pred["sha256"]:
        raise LedgerError("refusing: the v1 file has not grown past the seal, or its sealed "
                          "prefix no longer matches genesis (that is tamper, not a stray append)")
    tail = raw[size:]
    return append({"kind": PREDECESSOR_APPEND_ACK,
                   "predecessor_sha256_now": hashlib.sha256(raw).hexdigest(),
                   "predecessor_size_bytes_now": len(raw),
                   "post_seal_bytes": len(tail), "post_seal_lines": tail.count(b"\n"),
                   "post_seal_sha256": hashlib.sha256(tail).hexdigest(),
                   "attested": False, "why": why, "acknowledged_by": acknowledged_by or {}},
                  v2=v2)



def verify(*, old: Path | None = None, v2: Path | None = None) -> dict:
    """Recompute v2 from genesis, then check genesis still binds the v1 file's exact bytes."""
    old = Path(old) if old is not None else legacy_path()
    v2 = Path(v2) if v2 is not None else v2_path()
    out = {"path": str(v2).replace("\\", "/"), "verified_by": "argus.core.ledger_v2.verify"}
    if not v2.is_file():
        return dict(out, state=CURRENT_CHAIN_UNKNOWN, reason=(
            "SEAL_MARKER_WITHOUT_LEDGER" if marker_path(v2).exists() else "NOT_SEALED"))
    try:
        data = v2.read_bytes()
    except OSError as exc:
        return dict(out, state=CURRENT_CHAIN_UNKNOWN, reason="UNREADABLE: %s" % type(exc).__name__)

    def broken(at, why, **kw):
        return dict(out, state=CURRENT_CHAIN_BROKEN, at=at, why=why, **kw)

    if data and not data.endswith(b"\n"):
        return broken(data.count(b"\n"), "trailing partial record")
    lines = data.decode("utf-8", errors="strict").split("\n")[:-1] if data else []
    if not lines:
        return broken(0, "ledger v2 exists but has no genesis")
    prev = ZERO
    genesis = None
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            return broken(i, "record is not valid JSON")
        if not isinstance(rec, dict):
            return broken(i, "record is not an object")
        if rec.get("schema") != SCHEMA:
            return broken(i, "wrong schema")
        if rec.get("seq") != i:
            return broken(i, "seq %r out of order (expected %d)" % (rec.get("seq"), i))
        if (rec.get("kind") == "GENESIS") != (i == 0):
            return broken(i, "GENESIS must be exactly record 0")
        if rec.get("prev_hash") != prev:
            return broken(i, "prev_hash does not chain")
        if record_hash(rec) != rec.get("this_hash"):
            return broken(i, "record content does not match its own hash")
        if i == 0:
            genesis = rec
        prev = rec["this_hash"]

    pred = genesis.get("predecessor") or {}
    res = dict(out, n=len(lines), head=prev, genesis_hash=genesis["this_hash"],
               sealed_utc=genesis.get("utc"), predecessor_sha256=pred.get("sha256"),
               predecessor_path=pred.get("path"))
    if not old.is_file():
        return dict(res, state=CURRENT_CHAIN_UNKNOWN, reason="PREDECESSOR_MISSING",
                    genesis_matches_predecessor=None)
    raw = old.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha == pred.get("sha256") and len(raw) == pred.get("size_bytes"):
        return dict(res, state=CURRENT_CHAIN_VERIFIED, genesis_matches_predecessor=True)
    size = pred.get("size_bytes") or 0
    prefix_intact = (len(raw) > size
                     and hashlib.sha256(raw[:size]).hexdigest() == pred.get("sha256"))
    acks = [json.loads(l).get("event") or {} for l in lines[1:]]
    acks = [a for a in acks if a.get("kind") == PREDECESSOR_APPEND_ACK]
    if prefix_intact and acks and acks[-1].get("predecessor_sha256_now") == sha \
            and acks[-1].get("predecessor_size_bytes_now") == len(raw):
        return dict(res, state=CURRENT_CHAIN_VERIFIED, genesis_matches_predecessor=False,
                    predecessor_sealed_prefix_intact=True,
                    predecessor_post_seal_bytes_acknowledged=len(raw) - size,
                    predecessor_post_seal_bytes_attested=False)
    if prefix_intact:
        return dict(res, state=CURRENT_CHAIN_UNKNOWN, reason="PREDECESSOR_APPENDED_AFTER_SEAL",
                    genesis_matches_predecessor=False, predecessor_sealed_prefix_intact=True,
                    predecessor_extra_bytes=len(raw) - size,
                    action=("a writer running pre-migration code appended to the sealed v1 "
                            "file; restart it so audit() writes to v2"))
    return broken(0, "PREDECESSOR_MODIFIED: the v1 ledger no longer hashes to what genesis bound",
                  n=len(lines), genesis_hash=genesis["this_hash"],
                  genesis_matches_predecessor=False, predecessor_sealed_prefix_intact=False,
                  predecessor_sha256_now=sha, predecessor_sha256=pred.get("sha256"))



def historical(*, old: Path | None = None) -> dict:
    old = Path(old) if old is not None else legacy_path()
    base = {"path": str(old).replace("\\", "/"),
            "verified_by": "argus.core.actions.verify_audit_chain"}
    if not old.is_file():
        return dict(base, state=HISTORICAL_CHAIN_MISSING)
    try:
        data = old.read_bytes()
        chain = v1_verify_bytes(data)
    except (OSError, ValueError) as exc:
        return dict(base, state=HISTORICAL_CHAIN_UNREADABLE, why=type(exc).__name__)
    return dict(base, chain=chain, sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data), line_count=data.count(b"\n"),
                state=HISTORICAL_CHAIN_BROKEN if chain["status"] == "BROKEN"
                else HISTORICAL_CHAIN_INTACT,
                certification="SUPPRESSED for every record in this file; the v2 seal does not "
                              "re-attest it")


def status(*, old: Path | None = None, v2: Path | None = None) -> dict:
    h = historical(old=old)
    c = verify(old=old, v2=v2)
    return {
        "schema": STATUS_SCHEMA,
        "utc": _utc(),
        "historical": h,
        "current": c,
        "certification": {
            "historical_records": "SUPPRESSED",
            "new_records": "ALLOWED" if c["state"] == CURRENT_CHAIN_VERIFIED else "SUPPRESSED",
            "rule": ("certification of records in the v1 file stays suppressed permanently; a "
                     "record appended to v2 may be certified only while the v2 chain recomputes "
                     "from genesis and genesis still matches the v1 file's bytes"),
        },
        "two_facts_not_one": ("historical and current are separate. A verified current chain "
                              "says nothing good about the broken history."),
        "read_only": True,
    }


def may_certify_new_records(*, old: Path | None = None, v2: Path | None = None) -> bool:
    return verify(old=old, v2=v2)["state"] == CURRENT_CHAIN_VERIFIED
