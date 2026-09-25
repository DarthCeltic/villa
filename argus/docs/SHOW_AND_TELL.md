# Show-and-tell post (draft copy)

> Draft text for a community show-and-tell. It has not been posted.

---

**ARGUS: an evidence-first instrument for Herculaneum scroll CT (show-and-tell, not a result)**

Hi all. We've been building ARGUS, a local tool for Herculaneum CT work. Every number it shows comes
from a receipt, and every refusal gives a reason. We're sharing the code for feedback.

The claim boundary:
- **This release ships no detector and makes no scientific claim.** It does not read unread scrolls.
- **A rendered candidate is not a reading.** Nothing here claims ink, readability or a prize result.
- **Every export packet is `never_published`.** ARGUS prepares evidence for review and never publishes
  anything itself.

What you can run in five minutes (Windows verified by hand; see the README):
- `Start-ARGUS.cmd` (or `argus start`): the whole stack in Docker, on loopback only, no Python or Node
  setup. `argus status` and `argus stop` do what they say.
- `argus demo`: a synthetic fixture run end to end, offline. It is labelled DEMONSTRATION_ONLY, and
  the command refuses to print its own summary if that summary is edited into a claim.
- `argus doctor`: checks the machine and install. Every check it cannot run is reported as UNKNOWN,
  never as a pass.
- The UI (Home scroll archive, Explore, Workbench, Evidence, Review, System) on a read-only local
  service. A fresh install has no data, so each room explains what is missing.
- `python scripts/run_portable_ci.py`: a public test kit of null and sabotage controls (zero input,
  shuffled labels, phase and depth sabotage), renderer parity, identity, lineage and packet integrity
  checks, on synthetic data, so you can point the same controls at your own data or provider.

Design choices we'd like feedback on:
- Three separate axes: available, operationally verified, scientifically admissible. A route can
  be available and verified and still be inadmissible.
- A result class on every score, so a control result always carries "proves the pipeline, not a
  discovery".
- One AUC/AP implementation, and every score records its rule id.
- Four licence permissions per component (run, bundle, redistribute, submit). UNDECLARED counts as a
  refusal. No CT data, labels or weights are redistributed.

**Related upstream work: topology.** Villa PR #1884 (CVasilopoulos,
<https://github.com/ScrollPrize/villa/pull/1884>) adds `vc_tifxyz_topology`, a report-only census of tifxyz
surfaces that names the islands, holes, tears and folds that downstream tools silently drop or fill. We think
that is the right first step, and ARGUS does not reimplement it. ARGUS treats a mesh the way it treats any
input: qualify it, refuse when it is unsafe, and never modify the source. We are preparing a small, separate
write-up of complementary diagnostics (per-vertex angle defect, crease-network localization and conservative
clean-chart extraction) with a public, reproducible case study. Until that evidence is published, please read
this post as a tool release, not as a topology result.

Thanks to the Vesuvius Challenge team and everyone who published scans, tools and methods,
especially the ScrollPrize/villa tooling. We'd welcome
feedback on where our refusals are too strict, or not strict enough.

DarthCeltic and clexious, Cinder Covenant: <https://github.com/Cinder-Covenant/ARGUS>
