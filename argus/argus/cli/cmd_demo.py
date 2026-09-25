"""`argus demo` -- the whole shape of a run, on data that cannot mean anything."""
from __future__ import annotations

import sys

SEED = 20260910
SIZE = 96

STROKES = (
  ((10, 12), (10, 34)), ((10, 34), (34, 34)), ((22, 12), (22, 34)),
  ((10, 48), (34, 48)), ((22, 48), (22, 70)),
  ((10, 80), (34, 80)), ((10, 80), (10, 92)), ((34, 80), (34, 92)),
  ((50, 12), (74, 12)), ((50, 12), (50, 34)), ((62, 12), (62, 30)),
  ((50, 48), (74, 48)), ((50, 48), (50, 70)), ((74, 48), (74, 70)),
)


def fixture(size: int = SIZE, seed: int = SEED, contrast: float = 0.45):
    """A synthetic surface with known ink, and the noise that makes it non-trivial."""
    import numpy as np
    rng = np.random.default_rng(seed)
    truth = np.zeros((size, size), dtype=bool)
    for (r0, c0), (r1, c1) in STROKES:
        if r0 == r1:
            truth[r0, min(c0, c1):max(c0, c1) + 1] = True
        else:
            truth[min(r0, r1):max(r0, r1) + 1, c0] = True
    truth[:, size - 1] = False
    surface = rng.normal(0.5, 0.18, size=(size, size))
    surface += truth * contrast
    return surface.astype("float32"), truth


def detect(surface):
    """A 3x3 mean filter."""
    import numpy as np
    padded = np.pad(surface, 1, mode="edge")
    acc = np.zeros_like(surface, dtype="float64")
    for dr in (0, 1, 2):
        for dc in (0, 1, 2):
            acc += padded[dr:dr + surface.shape[0], dc:dc + surface.shape[1]]
    return acc / 9.0


def render_ascii(prob, truth, out) -> None:
    import numpy as np
    hi = float(np.quantile(prob, 0.965))
    print("  predicted (#) against the fixture's own ink (.) -- synthetic, not a scroll:",
          file=out)
    for r in range(0, prob.shape[0], 2):
        row = []
        for c in range(0, prob.shape[1], 1):
            if prob[r, c] >= hi:
                row.append("#")
            elif truth[r, c]:
                row.append(".")
            else:
                row.append(" ")
        print("    " + "".join(row), file=out)


def run(argv, out=sys.stdout) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="argus demo", description=__doc__.splitlines()[0])
    ap.add_argument("--no-render", action="store_true", help="skip the ASCII picture")
    ap.add_argument("--out", default=None,
                    help="write a receipt of this demonstration to a path")
    ns = ap.parse_args(argv)

    try:
        import numpy
    except ImportError:
        print("REFUSED: numpy is not installed, and the demo is pure numpy by design.",
              file=out)
        print("  fix: uv pip install numpy", file=out)
        return 2

    from argus.core import metrics, result_class

    surface, truth = fixture()
    prob = detect(surface)
    scored = metrics.score(prob, truth)

    rc = result_class.ResultClass(
        target="argus-cli synthetic fixture (seed %d, %dx%d)" % (SEED, SIZE, SIZE),
        target_class="SYNTHETIC",
        exposure_basis="EXPOSURE_UNKNOWN",
        detector="argus-cli demo 3x3 box filter (not a model)",
        detector_cross_scroll_qualified=False,
        acquisition="synthetic; no scanner, no scroll, no acquisition class",
        metric="AUC (%s)" % metrics.RULE_ID, score=scored["auc"])

    print("=" * 78, file=out)
    print("  " + rc.display_banner(), file=out)
    print("=" * 78, file=out)
    print("  offline: nothing was downloaded. no GPU was used. no model was loaded.", file=out)
    print("  fixture : %s" % rc.target, file=out)
    print("  detector: %s" % rc.detector, file=out)
    print("  metric  : %s = %.4f   (AP %.4f over %d pixels, %d positive)"
          % (metrics.RULE_ID, scored["auc"], scored["ap"], scored["n"],
             scored["n_positive"]), file=out)
    if not ns.no_render:
        render_ascii(prob, truth, out)

    summary = ("this is a DEMONSTRATION_ONLY result on a synthetic fixture; it establishes "
               "that the plumbing runs and nothing whatsoever about any scroll")
    rc.assert_not_discovery(summary)
    print(file=out)
    print("  %s" % summary, file=out)
    for line in rc.not_established():
        print("    not established: %s" % line, file=out)
    print("  next: argus doctor   (what this machine can and cannot do)", file=out)

    if ns.out:
        from argus.core import paths, receipts
        p = paths.assert_writable(ns.out)
        receipts.write_json({"schema": "argus-cli-demo-v1", "result": rc.as_dict(),
                             "metric": scored, "seed": SEED, "size": SIZE,
                             "offline": True, "gpu_used": False}, p)
        print("  receipt: %s" % p, file=out)
    return 0
