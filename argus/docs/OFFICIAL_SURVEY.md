# The public official survey, and `argus identify`

ARGUS decides what a scroll volume or segment *is* against one public file,
`argus/public_official_survey.json`. It lists every official volume of every scroll in the
Vesuvius Challenge open-data bucket: the acquisition (scan) id, the volume id, pitch, energy, the
OME-Zarr store and its level-0 array shape, with the source URL and check time on every row.

Re-run it whenever upstream changes (metadata only, no voxels are read):

    python scripts/refresh_official_survey.py           # fetch, rewrite the survey and the registries derived from it
    python scripts/refresh_official_survey.py --check   # exit 1 if upstream differs from the committed survey

## Terms

* **scan id**: the acquisition timestamp (the catalogue's `scans`). One scroll can have several.
* **volume id**: the reconstruction timestamp naming a store (the catalogue's `volumes`). The prize
  page, and every tifxyz named `<segment>-on-<volume id>-<pitch>um`, refer to a *volume*.
  One scan can have several volumes; one scroll several scans, at different resolutions.

An earlier record treated PHerc0125's scan id `20250720091415` as a "superseded volume id". The
catalogue shows it is the scan the current volume `20250821151825` was reconstructed from; the gate
now says "that is a scan id" instead of "superseded".

## One source of truth

The survey is the source. `argus/public_target_registry.json`, the prize roles and eligible volume
in `corpus/scrolls/registry.json`, and (in a private checkout) the target receipt are *derived* from
it by the same script, so PHerc0800 has one eligible volume (`20250521135224`, the one the prize
page lists) in every registry. The registry freshness check (30 days) is satisfied by re-running
the refresh, not by editing a date.

## First Letters is 22, not 23

The prize page lists 22 First Letters volumes and 13 Grand Prize volumes. Earlier ARGUS records
carried 23 because PHerc1447 was still on the First Letters list; the page now says the First
Letters set excludes scrolls where letters have been found. PHerc1447 remains on the Grand Prize
list, so neither set is a subset of the other (12 scrolls are on both). Historical receipts that
say "23" describe the list as it was when they were written.

## `argus identify PATH_OR_URL`

Infers the scroll, volume and scan from evidence in the data, in this order: `volume_source.txt` or
the store URL, the `-on-<volume id>-<pitch>um` segment name, the zarr array header, then whether the
segment's extent fits inside the candidate volume. It prints the identity with a confidence level
(HIGH needs two agreeing pieces of evidence, MEDIUM one) and every piece of evidence used, then the
route (`scroll_status`): which of the sixteen stages are available, blocked or not reached, and the
next action. It refuses, with reasons, when a volume id is unknown or is really a scan id, when
evidence contradicts (wrong pitch, an extent that does not fit, a folder naming another scroll),
when the shape fits several volumes, or when nothing names a volume. It never guesses.

`import.segment` uses the same identification; operator attestation (`attested_by` and a reason) is
the fallback only when the data itself cannot settle it, and never outvotes contradicting data.
