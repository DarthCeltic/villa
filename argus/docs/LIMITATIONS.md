# Limitations

Read this before drawing any conclusion from ARGUS output. `PUBLIC_CLAIMS_AND_LIMITS.md` at the
repository root states what ARGUS claims and does not claim in one place.

## Claim boundary

- **This release ships no detector and makes no scientific claim.** No output may be presented as a
  reading.
- **It does not read unread scrolls.**
- **A rendered candidate is not a reading.** An image that looks like it contains letters is not
  evidence of text.
- **Every export packet is `never_published`.** ARGUS prepares packets for human review. It does not
  publish, upload or submit anything, and a packet is not a claim.

## What this release does not include

- No CT data, ink labels or model weights. Their licences do not allow redistribution; fetch them
  from their sources under their own terms.
- No scientific results of any kind.
- No prize submission packaging, and no claim of any prize result.
- Some interface panels are not part of the public release. The UI shows a short note where they
  would appear.
- No GPU inside the Docker containers. GPU stages need a native install.

## What has not been verified

- The native install path is verified on Windows 11 with Python 3.11 and Node 24. The Docker image
  builds the interface with Node 20 and pins Python 3.11; continuous integration builds and tests the
  interface with Node 22 and runs the test kit on Python 3.11, 3.12 and 3.14. The test kit was run before
  release on Windows (Python 3.11 and 3.14) and on Linux (Ubuntu under WSL, Python 3.12); one test that
  probes processes through Windows PowerShell is skipped on other platforms. A Linux install has not been
  walked through by hand (only the test kit was run there), and macOS has not been tried.
- The Docker stack was built and run from a clean checkout by the maintainers on Windows with Docker
  Desktop. The test kit checks its files (`tests/test_docker_runtime_contract.py`) but does not build
  the image.
- `argus run <manifest>` is tested offline here. The one real run over public data was made by the
  maintainers on one machine (a GTX 1660 Ti, fp32); it needs Villa's runtime, network access and a GPU, and
  it is a control (`docs/PUBLIC_RUN.md`). The governed `argus run --target` driver is not shipped.
- The renderer-parity test compares this release's sampling, tiling and resampling with plain-loop
  references on synthetic data. No external renderer is compared here.

## Known gaps

- Source comments and long docstrings were removed from this public build. Each module keeps a
  one-sentence summary.
- The public test kit shows that controls are wired correctly on synthetic inputs. It cannot show that a
  detector works on a scroll.
