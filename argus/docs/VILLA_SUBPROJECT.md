# ARGUS as a Villa subproject

This page applies when this tree is checked out as the `argus/` directory of the ScrollPrize/villa
monorepo. The tree is the same one that ships as a standalone repository; only the placement differs
(the public-export tool of the private build repository, run with `--subdir argus`, produces this layout,
and the README header and the NOTICE section are the only files it changes). Every path in this tree is
relative to the tree, so nothing needs editing: run tools from `argus/`, or use the forms below from the
monorepo root.

## Verify the manifest

```
cd argus
python tools/verify_manifest.py
```

It checks that every file listed in `RELEASE_MANIFEST.json` exists with its sha256, that nothing tracked is
unlisted, and that `tree_sha256` equals the recipe below. To recompute the tree hash by hand:

```
python - <<'PY'
import hashlib, json
doc = json.load(open("RELEASE_MANIFEST.json"))
rows = sorted(doc["files"], key=lambda r: r["path"])
text = "".join("%s\0%s\n" % (r["path"], r["sha256"]) for r in rows)
print(hashlib.sha256(text.encode("utf-8")).hexdigest(), doc["tree_sha256"])
PY
```

Any edit to any file (including this directory's README) changes that file's sha256 and therefore the
tree hash. A mirror is refreshed by regenerating the whole tree with the exporter, never by hand edits.

## Environment with uv

`uv.lock` is resolved from `pyproject.toml`, whose `[tool.uv] constraint-dependencies` equals the pins in
`runtime/requirements-portable-ci.txt` (a public test keeps the two equal). From `argus/`:

```
uv sync --locked --extra service --extra volume --extra test
uv run --no-sync python scripts/run_portable_ci.py
```

The locked profile has no torch. The optional `surface-model` extra resolves torch and nnunetv2 without a pin.
To regenerate after editing `pyproject.toml`: `uv lock`.

## Docker from the monorepo root

The `Dockerfile` and `docker-compose.yml` use contexts relative to `argus/`. From the monorepo root:

```
docker build -f argus/Dockerfile -t argus argus
docker compose -f argus/docker-compose.yml up --build
```

The trailing `argus` of the first command is the build context (the equivalent of `.` inside `argus/`);
compose resolves `.` against the directory of the compose file, so the second form needs no change.

## Python imports

`argus/` contains a package also named `argus`. From the monorepo root, `import argus` resolves to the outer
directory as a namespace package. Install with `pip install -e ./argus` (or `uv sync` inside `argus/`), and
run scripts and tests with `argus/` as the working directory. `conftest.py` and the test files locate the tree
from their own path, so the suite runs unchanged in either layout.

## Continuous integration

GitHub reads workflows only from the monorepo's root `.github/workflows/`. The workflow inside this tree
(`.github/workflows/argus-portable-validation.yml`) therefore does not run in the monorepo. The root-level
equivalent is proposed in `docs/villa/argus-ci.yml` (path filter `argus/**`, `uv`, `working-directory: argus`,
Python 3.11/3.12/3.14 and Node 22), with a proposed CODEOWNERS line in `docs/villa/CODEOWNERS.snippet`.
Both are reference text for the maintainers, not active files.
