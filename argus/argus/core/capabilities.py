"""The capability ledger: discovered upstream surface, and how far each one actually got."""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

STATES = ("DISCOVERED", "PINNED", "RUNTIME_READY", "CALLABLE", "REAL_DATA_PROVEN",
          "ARGUS_WIRED", "UI_OPERABLE", "REGRESSION_LOCKED")
BLOCKED = "BLOCKED_EXACT"
INTEGRATED = "REGRESSION_LOCKED"

EVIDENCE_REQUIRED = {
    "DISCOVERED": ("upstream_path",),
    "PINNED": ("upstream_commit",),
    "RUNTIME_READY": ("runtime",),
    "CALLABLE": ("entrypoint", "invocation_receipt"),
    "REAL_DATA_PROVEN": ("real_data_fixture", "parity_evidence"),
    "ARGUS_WIRED": ("argus_adapter",),
    "UI_OPERABLE": ("ui_route",),
    "REGRESSION_LOCKED": ("regression_test",),
}

STAGES = ("catalog", "segmentation", "geometry", "flattening", "render", "ink",
          "postprocess", "candidate", "review", "text", "evidence")


class CapabilityError(Exception):
    pass


@dataclasses.dataclass
class Capability:
    capability_id: str
    stage: str
    upstream_repo: str | None = None
    upstream_path: str | None = None
    upstream_commit: str | None = None
    licence: str | None = None
    input_formats: tuple = ()
    output_formats: tuple = ()
    runtime: str | None = None
    hardware: str | None = None
    entrypoint: str | None = None
    invocation_receipt: str | None = None
    argus_adapter: str | None = None
    ui_route: str | None = None
    real_data_fixture: str | None = None
    parity_evidence: str | None = None
    regression_test: str | None = None
    state: str = "DISCOVERED"
    blocker: str | None = None
    notes: str | None = None

    def __post_init__(self):
        if self.state != BLOCKED and self.state not in STATES:
            raise CapabilityError("%s: unknown state %r" % (self.capability_id, self.state))
        if self.stage not in STAGES:
            raise CapabilityError("%s: unknown stage %r" % (self.capability_id, self.stage))
        if self.state == BLOCKED and not self.blocker:
            raise CapabilityError(
                "%s: BLOCKED_EXACT without a blocker. The whole point of that state is that "
                "the blocker is named exactly; an unnamed blocker is just a shrug."
                % self.capability_id)

    @property
    def is_integrated(self) -> bool:
        return self.state == INTEGRATED

    def missing_evidence(self) -> list:
        """Every rung up to and including the claimed one must have its evidence."""
        if self.state == BLOCKED:
            return []
        out = []
        for rung in STATES[:STATES.index(self.state) + 1]:
            for field in EVIDENCE_REQUIRED[rung]:
                if not getattr(self, field, None):
                    out.append({"rung": rung, "missing_field": field})
        return out

    def validate(self) -> None:
        miss = self.missing_evidence()
        if miss:
            raise CapabilityError(
                "%s claims %s but is missing %s. A rung may not be skipped: claiming a state "
                "without the evidence for every rung below it is how a capability map comes "
                "to read as complete while nothing runs."
                % (self.capability_id, self.state,
                   ", ".join("%s/%s" % (m["rung"], m["missing_field"]) for m in miss)))


class CapabilityLedger:
    def __init__(self, capabilities=()):
        self.caps = {c.capability_id: c for c in capabilities}

    def add(self, c: Capability) -> None:
        if c.capability_id in self.caps:
            raise CapabilityError("duplicate capability %r" % c.capability_id)
        self.caps[c.capability_id] = c

    def validate_all(self) -> list:
        errs = []
        for c in self.caps.values():
            try:
                c.validate()
            except CapabilityError as e:
                errs.append(str(e))
        return errs

    def integrated(self) -> list:
        return sorted(c.capability_id for c in self.caps.values() if c.is_integrated)

    def by_state(self) -> dict:
        out = {}
        for c in self.caps.values():
            out.setdefault(c.state, []).append(c.capability_id)
        return {k: sorted(v) for k, v in sorted(out.items())}

    def blocked(self) -> list:
        return sorted(({"id": c.capability_id, "blocker": c.blocker}
                       for c in self.caps.values() if c.state == BLOCKED),
                      key=lambda d: d["id"])


    def guard_undeclared(self, discovered_ids) -> list:
        """A new upstream entrypoint that has no record at all."""
        return sorted(set(discovered_ids) - set(self.caps))

    def guard_stale_pin(self, upstream_commit: str) -> list:
        """A record pinned to a commit other than the tested one."""
        return sorted(c.capability_id for c in self.caps.values()
                      if c.upstream_commit and c.upstream_commit != upstream_commit)

    def guard_vanished_from_ui(self, live_routes) -> list:
        """Something that claimed UI reachability and no longer has a route."""
        live = set(live_routes)
        return sorted(c.capability_id for c in self.caps.values()
                      if c.ui_route and c.state in ("UI_OPERABLE", INTEGRATED)
                      and c.ui_route not in live)

    def summary(self) -> dict:
        return {"total": len(self.caps), "by_state": self.by_state(),
                "integrated": self.integrated(),
                "integrated_count": len(self.integrated()),
                "blocked": self.blocked(),
                "definition_of_integrated": (
                    "REGRESSION_LOCKED only. A capability is integrated when it can be "
                    "selected in ARGUS, run on real scroll data, its output seen, the run "
                    "reproduced, and a test fails when upstream changes it. Anything less "
                    "is a decision, not an integration.")}



_SCRIPTS = re.compile(r"\[project\.scripts\](.*?)(?:\n\[|\Z)", re.S)
_ENTRY = re.compile(r'^\s*"?([A-Za-z0-9_.\-]+)"?\s*=\s*"([^"]+)"', re.M)
_EXE = re.compile(r"add_executable\(\s*([A-Za-z0-9_]+)")

_NOT_A_CAPABILITY = re.compile(r"^(test_|bench_|sanitizer_)")


def discover_python_entrypoints(root: Path) -> dict:
    """Console scripts declared by any pyproject under root."""
    out = {}
    for p in sorted(root.rglob("pyproject.toml")):
        try:
            s = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _SCRIPTS.search(s)
        if not m:
            continue
        for name, target in _ENTRY.findall(m.group(1)):
            out[name] = {"kind": "python_console_script", "target": target,
                         "declared_in": str(p.relative_to(root)).replace("\\", "/")}
    return out


def discover_cmake_targets(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("CMakeLists.txt")):
        try:
            s = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for t in _EXE.findall(s):
            if _NOT_A_CAPABILITY.match(t):
                continue
            out[t] = {"kind": "cmake_executable",
                      "declared_in": str(p.relative_to(root)).replace("\\", "/")}
    return out


def discover(root: Path) -> dict:
    d = {}
    d.update(discover_python_entrypoints(root))
    d.update(discover_cmake_targets(root))
    return d


def load(path: Path) -> CapabilityLedger:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return CapabilityLedger([Capability(**c) for c in raw["capabilities"]])


def dump(ledger: CapabilityLedger, path: Path, upstream_commit: str) -> None:
    body = {"schema": "argus-capability-ledger-v1",
            "upstream_commit": upstream_commit,
            "definition_of_integrated": ledger.summary()["definition_of_integrated"],
            "capabilities": [dataclasses.asdict(c)
                             for c in sorted(ledger.caps.values(),
                                             key=lambda c: (c.stage, c.capability_id))]}
    Path(path).write_text(json.dumps(body, indent=1), encoding="utf-8")
