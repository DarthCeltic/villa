# Model and data package boundary

This page says what a model or data package has to declare before ARGUS will register it, plan with it,
run it, or let an operator promote it, and what ARGUS never ships. Every requirement below is enforced by
code in this repository, and each item names the module that enforces it. Nothing here is a wish list: an
item that is missing does not produce a warning, it produces a refusal or a named blocker.

ARGUS has no single "accept" button. A package moves through separate, recorded steps (registered,
plannable, runnable through a governed action, promoted by an operator), and each step asks for more.
Declaring everything below makes a package reviewable, not qualified.

## What ARGUS never ships

| Never in this repository or its Docker image | Why | Where it comes from instead |
|---|---|---|
| Model weights and checkpoints | Their terms are separate from the code's, and some carry no declared licence at all (`UNDECLARED`, refused for bundling, redistribution and submission) | Fetched by the person running ARGUS from the publisher, after accepting the licence (`argus setup`), and pinned by hash |
| CT volumes and other restricted data | Non-commercial terms (CC BY-NC 4.0 for the public volumes): fetch and use, never redistribute | The data publisher, fetched at run time |
| Ink labels | Publicly fetchable is not the same as redistributable | The label publisher; ARGUS records where each source lives and how to list it |
| Unpublished results of any kind | They are not part of this release, and nothing in it claims them | Not available |
| Credentials, tokens, machine paths | Never belong in a package or a receipt | The person running ARGUS |

The Apache-2.0 licence covers ARGUS source code only (`LICENSE`, `NOTICE`, `SBOM.md`). It does not reach
any of the rows above.

## What a package must declare

### 1. Identity and immutable revision

- A provider row carries `id`, `stage`, `role`, an immutable `revision`, and a `pin` block:
  `{revision, kind, components, upstream_source, note}` (`argus/core/provider_registry.py`, `_record` and `_pin`).
  A branch name, `latest` or a mutable tag is not a revision.
- The licence resolver states the same rule as five separately required fields: **source**, **revision**,
  **hash**, **licence**, **redistribute** (`argus/core/licence_resolver.py`). A path is not a source: the same
  path on two machines is two artefacts.
- Installable components are pinned by digest or version. A component pinned only by a mutable tag is
  `TAG_ONLY_DIGEST_NOT_RESOLVED` and is refused for scientific use, however healthy it looks
  (`argus/core/install_tiers.py`, the pin rule).

### 2. Hash-pinned checkpoint identity

- A checkpoint is identified by its full lowercase 64-character `sha256`. `argus/core/model_promotion.py`
  refuses anything shorter or not lowercase hexadecimal.
- Weights that will be scored carry an id, a `sha256` and a non-empty `trained_on_scrolls` tuple
  (`argus/core/scroll_generalization.py`, `WeightIdentity`). An empty training manifest is refused: "undeclared"
  is not the same as "clean".
- A qualification that must be blind freezes the reasoner checkpoint and its exemplar library, both by full
  `sha256`, before the holdout is opened, and proves afterwards that neither changed
  (`argus/core/checkpoint_lifecycle.py`, `freeze_for_blind_qualification` and `assert_still_frozen`).
- Bytes are stored once, addressed by their `sha256`. An object with a recorded source and revision may be
  evicted and re-fetched; an object produced locally is never evicted (`argus/core/content_store.py`).

### 3. Licence

- Every component has an entry with a `kind` (`CODE`, `MODEL_WEIGHTS`, `DATA`, `TOOL`, `LABELS`), a `family`
  (`PERMISSIVE`, `COPYLEFT`, `NONCOMMERCIAL`, `UNDECLARED`, `PROPRIETARY`), an SPDX id, a source, and non-empty
  `evidence` for the claim. An entry without evidence is refused at construction
  (`argus/core/licence_registry.py`).
- Four permissions are answered separately and never merged: run locally, bundle in an installer,
  redistribute, and enter a prize submission. `UNDECLARED` is a refusal in every column that needs a grant.
  Weights are part of the method, so an undeclared checkpoint can never enter a submission.
- A provider row links its licence with `licence_id`, which must start with `licence_registry:` or
  `licence_resolver:`, or be `UNKNOWN` (`_uniform` in the provider registry).
- To advance a model to a qualifying rung the licence evidence must state `verified: true` and
  `admissible: true` (`argus/core/model_promotion.py`).

### 4. Provenance of the inputs

- The identity of a source and the identity of a job are kept apart. `store_id` (a function of the canonical
  URL) says which array a directory mirrors; `acquisition_id` binds the canonical URL, the array path, the
  region, the ordered key list and the configuration hash, so a resumed fetch either proves it is the same job
  or is a different one (`argus/core/acquisition_identity.py`).
- A model must have a declared policy for the volumes it can consume. A model with no declared policy is
  refused, not defaulted, and an unresolved source is not treated as clean
  (`argus/core/compression_provenance.py`, checked by `argus run`).
- Labels come with their source: name, URI, access class, content kinds (human labels are distinct from model
  predictions), scrolls, pitch, the date it was verified, and the command that lists it. A source without a
  listing command is refused (`argus/core/label_sources.py`). Pseudo-labels are never ground truth
  (`argus/core/pseudolabel_controls.py`, `assert_not_ground_truth`).

### 5. Input representation

- A provider row declares `input_contract`, `output_contract` and `coordinate_schema`, so a run states what
  the model was given and in which frame.
- Pixel pitch is resolved from evidence in a fixed order (OME transform, segment metadata, source identity,
  pyramid relation, mesh geometry), never from a file name (`argus/core/pitch.py`).
- A volume declares its physical frame: physical scroll, volume id, frame id, axes order, units, voxel size,
  pyramid level, origin convention and source tool; two products are comparable only if they share a frame or
  a declared transform (`argus/core/physical_frame_contract.py`, `PhysicalFrame`).
- A surface declares its physical scroll, segment, surface and CT volume ids, the normal orientation, which
  face it is and how that was established, pitch, energy, pyramid level, source licence, whether
  redistribution is permitted, its parent geometry, the command that generated it and its hashes; every
  clause is reported together (`argus/core/surface_contract.py`, `SurfaceDeclaration`).
- Depth is composited at fixed physical offsets. The sheet-normal sign is a decision with a status; until it
  is confirmed both orientations are shown and neither is called the interior
  (`argus/core/depth_composite.py`, `argus/core/signed_normal.py`).

### 6. Exposure and lineage

- Model exposure is declared per channel: `raw_volume_pretraining`, `supervised_label`, `pseudo_label`,
  `teacher_prediction`, `fine_tuning`. Each channel is `EXPOSED`, `CLEAN` or `UNKNOWN`. Every channel starts
  `UNKNOWN`, `UNKNOWN` is never treated as `CLEAN`, and a `CLEAN` or `EXPOSED` verdict must carry the evidence
  that was checked. An unknown channel makes eligibility `INDETERMINATE` (`argus/core/exposure.py`).
- Operator exposure (what the operator has looked at) and asset availability (what exists locally) are kept
  apart from model exposure, because collapsing them discards clean scrolls or keeps contaminated ones.
- Lineage is two graphs, not one: the training graph and the evidence graph, judged by edge type. An unknown
  edge type is refused rather than ignored, and separation between training and evidence regions is `UNPROVEN`
  until a buffer with every term declared shows it (`argus/core/lineage.py`).
- To advance a model to a qualifying rung the exposure evidence must state `verified: true` with no
  `unknown_channels` (`argus/core/model_promotion.py`).

### 7. Sabotage controls the package has to survive

ARGUS asks for controls, not for a score. The public test kit (`docs/TEST_KIT.md`) contains them, and they
are written so a user can run the same controls against their own provider:

- zero input, shuffled labels, phase and depth sabotage (`tests/test_public_null_controls.py`);
- channel swap and mirror-orientation equivariance, teacher-student collapse, and train/evaluation leakage
  (`argus/core/pseudolabel_controls.py`);
- a cross-scroll gate that needs at least three distinct scrolls, each strictly above the floor, and never
  passes on one scroll (`cross_scroll_gate`);
- a leave-one-scroll-out weight gate: exposure is checked before any score is read, at least three held-out
  scrolls are required, the worst scroll decides (never a mean), and the bootstrap seed is frozen
  (`argus/core/scroll_generalization.py`);
- renderer parity, tile-seam and geometry checks (`tests/test_public_renderer_parity.py`,
  `argus/core/surface_consistency.py`, `argus/core/topology_metric.py`).

For the qualifying rungs, `argus/core/model_promotion.py` requires an evaluation that reports `status: PASS`
with `holdout_frozen: true`, and the highest rung additionally requires `cross_scroll_controls: true`.

### 8. Hardware, claim ceiling and role

- `hardware_profile.state` is one of `MEASURED`, `ASSUMED_NOT_MEASURED`, `DECLARED_NOT_MEASURED`,
  `NOT_MEASURED`. VRAM and host RAM are numbers or `null`, never guesses. `argus/core/install_tiers.py`
  estimates activation memory, plans a torch-level VRAM cap so an over-budget job fails instead of silently
  spilling into system RAM, and detects that spill from samples.
- `claim_ceiling` is a token from `CLAIM_CEILINGS` (`NO_INK_CLAIM`, `NO_QUALIFIED_DETECTOR`, `APPARATUS_ONLY`,
  `MECHANICS_ONLY`, `DIAGNOSTIC_ONLY`, `CANDIDATE_ONLY`, `OPERATIONAL_ONLY`, `HUMAN_GATED`). A row in the ink
  family may only carry a ceiling that refuses a detector claim.
- `evidence_role` may never be `INDEPENDENT_EVIDENCE` or `GROUND_TRUTH`: a provider row is never independent
  evidence. `promotion` is always `operator_only`.
- A row that cannot run says exactly what blocks it: `lifecycle_state` is one of `CATALOG_ONLY`, `PLAN_ONLY`,
  `GATED_EXECUTABLE`, `EXECUTABLE`, `BLOCKED`, `RESEARCH_ONLY`, and every state other than the two executable
  ones must carry a blocker of kind `DATA_UNAVAILABLE`, `CODE_MISSING`, `LICENCE`, `EXPOSURE`, `RUNTIME` or
  `NOT_AUTHORIZED`. A row that runs cannot carry one.

## What the promotion packet looks like

`model.qualify` advances one model by at most one rung, and only with an evidence artefact that exists on
disk. For the qualifying rungs it validates this packet (`argus/core/model_promotion.py`). The three evidence
entries may be inline objects or paths to JSON files, and their hashes are recorded in the receipt:

```json
{
  "checkpoint_id": "example-checkpoint",
  "checkpoint_sha256": "<64 lowercase hexadecimal characters>",
  "checkpoint_class": "CP-REASONER-GENERAL",
  "source_scroll_id": "PHercExample",
  "dest_scroll_id": "PHercExample",
  "artifact_kind": "LEARNED_PARAMETERS",
  "dest_is_general": false,
  "exposure": {"verified": true, "unknown_channels": []},
  "licence": {"verified": true, "admissible": true},
  "evaluation": {"status": "PASS", "holdout_frozen": true, "cross_scroll_controls": true}
}
```

The receipt is `PROMOTION_PACKET_VERIFIED`, and it says in its own words that the current pin and any
scientific qualification remain operator-controlled. Verifying a packet does not change which model ARGUS
uses and does not qualify anything.

## What ARGUS does with a package after that

- **Three checkpoint classes.** `CP-GEOMETRY-GENERAL`, `CP-PERCEPTION-GENERAL` and `CP-REASONER-GENERAL`.
  Geometry and perception have no promotion path from a per-scroll branch to a general checkpoint; the
  reasoner class does, and only through quarantine (`argus/core/checkpoint_lifecycle.py`).
- **One direction for physical evidence.** A scroll's meshes, surfaces, coordinate maps, renderings and
  corrections persist under that scroll's identity and flow up into that scroll's own evidence packet. They
  never move sideways into another scroll's branch.
- **Learned state does not cross scrolls.** Learned parameters, optimiser state, normalisation statistics,
  thresholds, pseudo-labels and unreviewed exemplars may not move between scrolls below the reasoning layer,
  and may not move from a scroll into a general checkpoint except by quarantine and promotion.
- **Adaptation stays exploratory.** An adaptation is `EXPLORATORY` and cannot be cited as qualifying until a
  quarantined improvement has passed the multi-scroll suite on at least two distinct scrolls
  (`MIN_SCROLLS_FOR_PROMOTION`) and been promoted.
- **Everything ends as a packet.** Results are exported as packets that carry their limitations and are
  `never_published`; a reader can verify that the bytes are the ones exported (`argus/core/publication_packet.py`).

## Checklist

1. Weights, data and labels stay outside the repository, fetched from their source under their terms.
2. Source, an immutable revision, a full `sha256`, an SPDX licence with evidence, and a redistribution
   answer, for every component.
3. Input contract, output contract, coordinate schema, pixel pitch with its evidence, face and frame.
4. Per-channel exposure with evidence, the training lineage, and the trained-on scroll list.
5. The sabotage controls above, run on your own data with the public test kit, with the arrays and tile ids
   saved before any summary is computed.
6. A hardware profile that is measured, or says that it is not.
7. A claim ceiling that refuses a detector claim, and no independent-evidence or ground-truth role.
