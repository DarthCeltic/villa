# Adding a row to the evidence-product board

The board at `/leaderboard` is a small, honest index of reproducible work -- it is not a
prize ranking, and adding a row is not a submission of anything.

1. Read `SCHEMA.json`. Every field it marks required must be present; `kind` must be one of
   its three enum values (`SCIENTIFIC RESULT` requires the result to actually meet the
   cross-scroll bar in `argus.core.scroll_generalization` -- do not use it speculatively).
2. Append one object to `argus/ui/src/data/evidenceProducts.json` matching the schema. That
   file, not this directory, is the single copy the board reads -- `SCHEMA.json` and this
   note live under `docs/public/` because that is where a reader looks for the rules, not
   because the data is duplicated here. `evidence_url` must point at something a reader can
   actually open and check -- a receipt, a ledger entry, a doc with real numbers in it. A
   claim with nothing behind it does not belong here.
3. Open a PR. It is reviewed like any other change: the `result` and `detail` fields are
   checked against `evidence_url` before merge, not taken on trust.

The board renders `evidenceProducts.json` directly
(`argus/ui/src/screens/Leaderboard.tsx`); there is no separate database or submission form,
so accuracy is a property of the one file, not of any of the UI code that renders it.
