# Contributing to ARGUS

Thank you for looking. ARGUS is maintained by DarthCeltic and clexious at
<https://github.com/Cinder-Covenant/ARGUS>.

## Before you open a pull request

1. Install with `pip install -e ".[service,test]"` and run the test kit: `python scripts/run_portable_ci.py`
   (or `python -m pytest`, which collects `argus/tests` and `tests`). Both are offline and need no
   private data. `docs/TEST_KIT.md` says what each group checks.
2. For UI changes: `cd argus/ui && npm ci && npm run build && npm run test:unit`. The Docker image builds
   the interface with Node 20 and continuous integration with Node 22; either works.
3. For container changes: `docker compose config` must still parse, `tests/test_docker_runtime_contract.py`
   must still pass, and no port may be published on anything but `127.0.0.1`.
4. Keep the claim boundary intact. A change must not present any output as ink, a reading, readability,
   qualification or a prize result. If a change adds a new kind of result, it needs a result class
   (`argus/core/result_class.py`) and a receipt.
5. One metric. Use `argus.core.metrics`; do not add another AUC or AP implementation.
6. A new control or gate ships with a test that can fail: plant the defect it is meant to catch, and
   require it to be caught.
7. No machine-specific paths. Resolve locations from `ARGUS_HOME` and the roots in
   `config/argus.example.env`.
8. No credentials, tokens or `.env` files in commits.
9. Do not add data, labels, model weights or unpublished results to the repository. Their licences or
   their status do not allow it (see `MODEL_PACKAGE_BOUNDARY.md`).
10. Commit messages describe the change and its reason. They carry no co-author or tool trailers.

## Reporting a problem

Open an issue with the command you ran, the full output (remove any local paths you do not want to
share), your OS, Python and Node versions, whether you used Docker, and the output of
`python -m argus doctor` (or `argus status` for the container stack).

## Licence of contributions

Contributions are accepted under the repository licence (Apache-2.0, see `LICENSE`).
