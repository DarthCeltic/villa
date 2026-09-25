# End-to-end workflow

The claim boundary applies at every step: this release ships no detector and makes no scientific
claim, and it does not read unread scrolls. A rendered candidate is not a reading. Every export
packet is `never_published`.

## 0. Or start the whole stack with Docker

On Windows double-click `Start-ARGUS.cmd`; anywhere, run `argus start` (then `argus status`, and
`argus stop` when you are done). The interface opens at `http://127.0.0.1:8792`. The containers have
no GPU, so GPU stages need the native install below. `QUICKSTART.md` has the details.

## 1. Install and check the machine

```powershell
.venv\Scripts\python -m pip install -e ".[service,test]"
.venv\Scripts\python -m argus doctor
```

`doctor` lists every check with a fix. On a new machine the external components show as FAIL until
you accept their licences, and the upstream pin shows as FAIL until the pinned checkout exists.

## 2. See the whole shape offline

```powershell
.venv\Scripts\python -m argus demo --out demo_receipt.json
```

This generates a synthetic surface with synthetic truth, runs a box filter over it, scores the
output with `argus-metric-v1`, and prints the result class banner. With `--out` it also writes a
receipt: the result class, the metric with its rule id, the seed, and `offline: true`.

## 3. Acquire components, one licence at a time

```powershell
.venv\Scripts\python -m argus setup --plan                 # what would be fetched, and its licence
.venv\Scripts\python -m argus setup --accept <component>   # record an acceptance, then fetch
```

CT volumes are released under CC BY-NC 4.0. You can fetch and use them, but ARGUS never bundles or
redistributes them, and this repository contains no scan data. A component with no declared licence
is refused by `setup` until you accept it explicitly.

## 4. Start the service and the UI

```powershell
.venv\Scripts\python -m argus.serve observe --port 8787
cd argus\ui ; npm ci ; npm run dev
```

Open `http://127.0.0.1:5173`. Then:

1. **Home**: pick a collection, then a scroll. The inspect panel shows prize eligibility, local
   data, and the furthest stage reached. Without data it says UNKNOWN.
2. **Explore**: scroll detail, declared acquisitions (never guessed from a file name), and what is
   held locally.
3. **Workbench**: with a surface volume present, step through depth at fixed contrast, flip the
   orientation, and copy the printed call that reproduces the image on screen.
4. **Evidence**: each image and receipt with its result class and provenance.
5. **Review**: the qualification state of a result, and what it would need.
6. **System**: capabilities on their three axes, boundaries, licences and credits.

## 5. Run on real data

```powershell
.venv\Scripts\python -m argus run --help
```

`argus run pherc0139-w016-ink9um-control --dry-run` explains, stage by stage, the bounded public control this release ships; `docs/PUBLIC_RUN.md` has the commands and the limits. `run` refuses a target it cannot name unambiguously, a machine that cannot finish the job, an input
it has not verified, and a volume outside the model's declared provenance policy. A refusal names
its reason and the smallest action that would clear it.

## 6. Check a result before you trust it

Run the controls that matter for your data: `python scripts/run_portable_ci.py --list` shows them by
the question each answers, and `docs/TEST_KIT.md` says how to point them at your own data or
provider. A model or data package has to declare what `MODEL_PACKAGE_BOUNDARY.md` lists before ARGUS
will accept it.

## 7. Reproduce

See `docs/REPRODUCTION.md`.
