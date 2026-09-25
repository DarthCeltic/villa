"""Immutable intake map for newly released Villa-community science candidates.

Public build: the rows carry public identities only (repository, revision, pinned hashes, declared
contracts, licence). Per-scroll exposure analysis and local run notes are not part of the public
release; every selected scroll reads as not analysed, which never counts as clean."""
from __future__ import annotations

from copy import deepcopy

from argus.core import content_store, official_models, scroll_ids

SCHEMA = "argus-science-candidates-v1"

_FIBER_INK_4CLASS = official_models.by_id("scrollprize/fiber_ink_4class_selfdistill")

_CANDIDATES = (
    {
        "id": "hecate_24um", "stage": "ink_3d", "role": "surface_conditioned_3d_localizer",
        "revision": "9cb86e500e944b11a06a7020403cde5dffb5bcb2",
        "lifecycle": "CANDIDATE_PACKAGE_AVAILABLE", "source": "hf://scrollprize/hecate",
        "artifact": {"file": "hecate_2.4um.pth", "bytes": 522656918,
                     "sha256": "fa2de3827ebd247ff2d33df1b6f899ea79d5269036cf16765997c62f0e9988c7"},
        "input_contract": "uint8 ZYX surface-conditioned render, 64x256x256 patches, pre-sampled at 2.4 um; divide by 200 without clipping",
        "output_contract": "2D attention-combined probability map plus 3D middle-sheet ink probability volume",
        "coordinate_schema": "surface-render ZYX with acquisition pitch and source mesh identity",
        "license": "MIT package; underlying CT/annotation data lineage remains separately governed",
        "controls": ["exact pitch", "source mesh identity", "checkpoint hash", "weak-supervision exposure manifest", "held-out validation"],
        "detail": "Public runnable package exists; ARGUS plans it and never runs it. Promotion needs every declared control.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": ["2.4um"], "available": False,
    },
    {
        "id": "hecate_96um", "stage": "ink_3d", "role": "surface_conditioned_3d_localizer",
        "revision": "9cb86e500e944b11a06a7020403cde5dffb5bcb2",
        "lifecycle": "CANDIDATE_PACKAGE_AVAILABLE", "source": "hf://scrollprize/hecate",
        "artifact": {"file": "hecate_9.6um.pth", "bytes": 522576086,
                     "sha256": "809f4f10f7cb7afa19b4bee0f7d2ab31edd7e11b664f9f210cc9c17f22fcfe5d"},
        "input_contract": "uint8 ZYX surface-conditioned render, 16x64x64 patches, pre-sampled at 9.6 um; divide by 255 without clipping",
        "output_contract": "2D attention-combined probability map plus 3D middle-sheet ink probability volume",
        "coordinate_schema": "surface-render ZYX with acquisition pitch and source mesh identity",
        "license": "MIT package; underlying CT/annotation data lineage remains separately governed",
        "controls": ["exact pitch", "source mesh identity", "checkpoint hash", "weak-supervision exposure manifest", "held-out validation"],
        "detail": "Public runnable package exists; ARGUS plans it and never runs it. Promotion needs every declared control.",
        "evidence": "upstream model card and repository (see source)",
        "scientific_status": {"state": "NOT_QUALIFIED"},
        "native_families": ["9.6um"], "available": False,
    },
    {
        "id": "ink_3d_dino_guided", "stage": "ink_3d", "role": "raw_ct_3d_ink_candidate",
        "revision": "73a79525466037432191284dfa237baf830c49ec",
        "lifecycle": "CANDIDATE_APPARATUS_ONLY", "source": "hf://scrollprize/ink_3d_dino_guided",
        "artifact": {"file": "ckpt_78k_fullsup.pth", "bytes": 1703138075,
                     "sha256": "5a148c2c1bb730bfa683f2b3e3cdfc2000424003605e42300b527ff90118b303"},
        "input_contract": "a tifxyz segment directory (x.tif, y.tif, z.tif plus volume_source.txt naming the exact "
                          "eligible volume); the pinned infer_full3d_tifxyz CLI reads the raw scroll chunks itself and "
                          "feeds the model single-channel 256-cubed patches with percentile min-max normalization "
                          "(planned by argus.core.native_3d_provider, never run there)",
        "output_contract": "single-channel per-voxel ink logits/probability volume; no default z projection",
        "coordinate_schema": "inferred ZYX depth-first until code-level axis verification; pitch belongs to acquisition",
        "license": "MIT model claim; underlying tomographic data CC BY-NC 4.0",
        "controls": ["axis verification", "strict ink-head load", "fixed seed", "exposure closure", "independent clean control"],
        "detail": "Public checkpoint; ARGUS plans it and never runs it. Promotion needs every declared control.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": ["approximately 2.4um"], "available": False,
    },
    {
        "id": "hercunet_v0", "stage": "surface_prediction", "role": "iterative_surface_refiner",
        "revision": "5cbc6f42df3da301310679e9a7c76a5f1f682897",
        "code_revision": "100b828fd88a66cf6e799d119a086b8f5c1897a1",
        "drift_watch": {
            "repo": "jimmylomro/hercUNet",
            "stub_if": [":construction: coming soon"],
            "implemented_if": ["Stage 3 — Iterative full-volume inference",
                               "hercunet.infer", "implemented"],
            "current_lifecycle_is_stub": False,
        },
        "lifecycle": "IMPLEMENTED_UPSTREAM_NOT_YET_ARGUS_QUALIFIED",
        "provider_kind": "SURFACE_SHEET_SEGMENTATION",
        "is_ink_provider": False,
        "promotion_state": "CANDIDATE_ONLY",
        "promoted": False,
        "prize_target_eligible": False,
        "architecture": {
            "input_channels": 8, "channel_layout": "[CT, prev, orientation x6]",
            "refinement_iterations": 4, "tta": False,
            "evidence": "the upstream README's declared input layout",
        },
        "scientific_status": {
            "state": "NOT_QUALIFIED",
            "argus_qualification": "NOT_QUALIFIED",
        },
        "source": "hf://jimmylomro/hercunet-v0",
        "artifact": {"file": "fold_0/checkpoint_best.pth", "sha256": None},
        "input_contract": "CT plus previous surface candidates, as an 8-channel [CT, prev, orientation x6] "
                          "network per the upstream README.",
        "output_contract": "refined surface prediction (iterative, N double-buffered Jacobi passes; "
                           "final pass is the deliverable)",
        "coordinate_schema": "nnU-Net dataset geometry; network built stock from the model's own "
                             "plans.json via get_network_from_plans",
        "license": "MIT code; model/corpus point to MIT but lack standalone artifact grants",
        "controls": ["strict load against the declared input stem", "training exposure closure",
                     "held-out merge controls"],
        "detail": ("hercunet.train and hercunet.infer are implemented upstream. This is NOT a qualification or "
                   "generalization claim: ARGUS has not run or qualified this checkpoint. It remains a "
                   "CANDIDATE_ONLY surface/sheet provider: not promoted, never an ink provider, and not for any "
                   "prize target."),
        "evidence": "upstream model card and repository README (see source)",
        "native_families": [], "available": False,
    },
    {
        "id": "copy_displacement_latest", "stage": "mesh_tracing", "role": "learned_surface_propagation",
        "revision": "4da532350f40d84347991731fc25124fc07afbc1",
        "lifecycle": "LEGAL_AND_EXPOSURE_BLOCKED", "source": "hf://scrollprize/copy_displacement_latest",
        "artifact": {"file": "copy_displacement_latest.pth", "sha256": None},
        "input_contract": "existing TIFXYZ conditioning surface, source CT volume and checkpoint",
        "output_contract": "displaced adjacent-wrap TIFXYZ surface proposal",
        "coordinate_schema": "conditioning mesh and CT volume coordinates",
        "license": "NONE DECLARED for checkpoint",
        "controls": ["explicit licence grant", "training exposure manifest", "identity sabotage", "mesh differential"],
        "detail": "Useful capability shape, but the checkpoint has no licence and no documented training exposure.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": ["4.8um trained; card reports 2.4-9.6um"], "available": False,
    },
    {
        "id": "unmerge_cli", "stage": "topology_repair", "role": "merge_split_proposal",
        "revision": "179ebe7d352b8601805c9079491f67241e1c7137",
        "lifecycle": "ADOPT_CANDIDATE_UNVERIFIED", "source": "github://Jinhojeong/vesuvius-unmerge",
        "artifact": {"file": "source", "sha256": None},
        "input_contract": "merged binary mask plus click or candidate points",
        "output_contract": "Sheet_A.obj and Sheet_B.obj cut proposals",
        "coordinate_schema": "input mask volume coordinates",
        "license": "MIT", "controls": ["minimal reproduction", "merge-pair score", "human cut acceptance"],
        "detail": "Real licensed cutter for the underlying failure mode; not the unverified literal name 'free cutter'.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": [], "available": False,
    },
    {
        "id": "surface_geometry_diagnostic", "stage": "topology_repair", "role": "merge_failure_metric",
        "revision": "34011216020f5bdb3070f5693f0e2721616e1c28",
        "lifecycle": "ADOPT_CANDIDATE_UNVERIFIED", "source": "github://Jinhojeong/vesuvius-surface-geometry-diagnostic",
        "artifact": {"file": "source", "sha256": None},
        "input_contract": "surface patches and labelled merge controls",
        "output_contract": "geometry diagnostic and merge-failure measurements",
        "coordinate_schema": "source surface/volume coordinates",
        "license": "MIT", "controls": ["minimal reproduction", "200-patch external control", "uncertainty interval"],
        "detail": "Licensed diagnostic candidate for merger errors; no ARGUS reproduction exists yet.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": [], "available": False,
    },
    {
        "id": "fiber_ink_4class_selfdistill", "stage": "fiber_ink_segmentation",
        "role": "four_class_self_distilled_segmenter",
        "revision": _FIBER_INK_4CLASS.revision,
        "lifecycle": "CANDIDATE_NOT_ATTEMPTED",
        "source": "hf://" + _FIBER_INK_4CLASS.repo_id,
        "official_model": _FIBER_INK_4CLASS.repo_id,
        "semantic_state": _FIBER_INK_4CLASS.semantic_state,
        "artifact": {"file": _FIBER_INK_4CLASS.weight_file, "bytes": _FIBER_INK_4CLASS.weight_bytes,
                     "sha256": _FIBER_INK_4CLASS.weight_sha256},
        "input_contract": "villa vesuvius.predict --model_type train_py bounded ROI input",
        "output_contract": "four-class fiber/ink segmentation (task_heads.labels)",
        "coordinate_schema": "villa NetworkFromConfig shared_encoder/decoder geometry",
        "license": _FIBER_INK_4CLASS.licence,
        "controls": ["architecture-config match to the checkpoint's producing revision",
                     "strict state_dict load", "training exposure closure",
                     "independent held-out control"],
        "detail": "Public checkpoint registered as a candidate row; ARGUS plans nothing for it and never runs it.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": ["2.4um (PHercParis4 scan)"], "available": False,
    },
    {
        "id": "dinovol_v2_ps8_supcon3class_step362500", "stage": "representation_learning",
        "role": "supcon_fiber_embedding_backbone",
        "revision": None,
        "lifecycle": "CANDIDATE_NOT_ATTEMPTED",
        "source": "hf://scrollprize/dinovol_v2_ps8_supcon3class_step362500",
        "artifact": {"file": None, "bytes": None, "sha256": None},
        "input_contract": "ps8 volumetric crops, per the upstream model card",
        "output_contract": "dense patch embeddings plus a contrastive projection head; no ink/fiber class head of its own",
        "coordinate_schema": "per the upstream model card; not bound locally",
        "license": "NOT YET DETERMINED -- read the licence from the repository before use",
        "controls": ["download and hash the checkpoint", "strict-load verification receipt",
                     "training exposure closure", "independent held-out control"],
        "detail": "Public checkpoint registered as a candidate row; not downloaded or attempted by the public build.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": [], "available": False,
    },
    {
        "id": "icdar2023_grk_papyri_baseline", "stage": "reading",
        "role": "papyrus_character_detection_recognition",
        "revision": "zenodo:10.5281/zenodo.13825619",
        "lifecycle": "CANDIDATE_PACKAGE_AVAILABLE",
        "source": "https://zenodo.org/records/13825619 (dataset); "
                  "https://lme.tf.fau.de/competitions/2023-competition-on-detection-and-recognition-of-greek-letters-on-papyri/ "
                  "(baseline detector + trained model, FAU Pattern Recognition Lab)",
        "artifact": {"file": "ICDAR2023_Competition_on_Detection_and_Recognition_of_Greek_Letters_on_Papyri_Dataset.zip",
                     "bytes": 423934038, "sha256": None},
        "input_contract": "photographic JPG scans of real, physically-opened Greek papyrus fragments (Homer's "
                          "Iliad texts; 150 images / 108 texts), fed patch-wise (non-overlapping crops, not the "
                          "whole page downscaled) into a PyTorch object-detection baseline -- NOT the CT-recovered "
                          "ink-probability renders argus.core.reading_board actually carries",
        "output_contract": "per-character bounding box plus a Greek-letter class and a preservation-quality tag "
                           "(bt-1..bt-4); a detection+classification result, not a line-level transcription string",
        "coordinate_schema": "2D pixel bounding boxes in the source photograph's own frame; no declared "
                             "relationship to any CT/mesh/surface coordinate system",
        "license": "CC BY-NC 4.0 for the dataset (Zenodo DOI 10.5281/zenodo.13825619, live-checked 2026-09-17); "
                  "the baseline TRAINED MODEL weights carry no explicit license statement anywhere on the "
                  "competition page (checked 2026-09-17) -- treat as NONE DECLARED for the checkpoint itself "
                  "until its own archive contents are read",
        "controls": ["an explicit license grant for the trained checkpoint itself, not just the dataset",
                     "a real domain-shift validation: photographic ink-on-fiber vs CT-recovered/ML-inferred "
                     "ink-probability renders -- unvalidated in either direction, not assumed transferable",
                     "reading_board pixel-evidence plumbing: reading_board cells (argus/core/reading_board.py) "
                     "carry only extent, a scalar ink_prob, and string CT references -- no image content -- so no "
                     "real accepted glyph can reach any vision classifier, this one included, without new "
                     "rendering-export code that does not exist yet",
                     "a real accepted TRANSCRIPTION to evaluate against, which does not exist anywhere in ARGUS "
                     "today (no ink detector is qualified and reading-layer calibration has not started)"],
        "detail": "Real, published, checkably-licensed (dataset) Greek-papyrus HTR/character-recognition system, "
                  "namely the ICDAR 2023 Competition on Detection and "
                  "Recognition of Greek Letters on Papyri (FAU Pattern Recognition Lab), 194 real photographed "
                  "Homer's-Iliad papyrus fragments with character-level bounding-box ground truth, plus a real "
                  "downloadable PyTorch baseline detector and trained model. The competition's actual winning "
                  "submission (D-Scribes YOLOv8/DeiT/SimCLR ensemble, CC BY-NC-SA 4.0 per its own arXiv listing "
                  "2401.12513, mAP 42.2% recognition / 51.4% detection) publishes its paper and its predictions "
                  "(Figshare) but not its trained weights -- no checkpoint for it was found anywhere, so only the "
                  "FAU baseline is a genuinely acquirable artifact, and even it is served through an "
                  "interactive FAUbox share link rather than a plain file URL. Not wired or run: every image in "
                  "this dataset is a photograph of a real, physically opened fragment (visible carbon ink on "
                  "papyrus fiber under normal light) -- a different imaging modality from anything "
                  "argus.core.reading_board would ever carry, which is an ML-inferred ink-probability render "
                  "sampled off a 3D mesh recovered from unopened, carbonized CT data. Applying a "
                  "photographically-trained classifier to that render without a dedicated domain-shift study "
                  "would be exactly the unvalidated substitution this project's contract forbids. Independently, "
                  "reading_board's own cell schema carries no pixel content at all, and no accepted TRANSCRIPTION "
                  "exists in ARGUS today regardless -- three real, independent gaps; closing any one does not "
                  "close the others.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": [], "available": False,
    },
    {
        "id": "fiber_dinoguided_2class_step010000", "stage": "fiber_segmentation",
        "role": "dino_guided_two_class_target_generator",
        "revision": None,
        "lifecycle": "CANDIDATE_NOT_ATTEMPTED",
        "source": "hf://scrollprize/fiber_dinoguided_2class_step010000",
        "artifact": {"file": None, "bytes": None, "sha256": None},
        "input_contract": "PHerc Paris 4, 2.4um scan, per the card's own Trained-on row "
                          "(s3://vesuvius-challenge-open-data/PHercParis4/volumes/"
                          "20260411134726-2.400um-0.2m-78keV-masked.zarr)",
        "output_contract": "two-class fibre segmentation target, guided by frozen DINO embeddings -- exact head "
                           "architecture unread; no config.json has been fetched locally yet",
        "coordinate_schema": "not yet bound locally; pitch/axes must come from the acquisition once fetched, never "
                             "assumed from the model",
        "license": "NOT YET DETERMINED -- no licence tag or file has been read from this repo",
        "controls": ["download and hash the checkpoint", "fetch and read config.json / README.md in full for "
                     "architecture, in/out channels and axes", "strict-load verification receipt",
                     "exposure closure beyond the single named scroll", "independent held-out control"],
        "detail": "Public checkpoint registered as a candidate row; not downloaded or attempted by the public build.",
        "evidence": "upstream model card and repository (see source)",
        "native_families": ["2.4um (PHercParis4 scan)"], "available": False,
    },
)


def _exposure(candidate_id: str, scroll: str | None) -> str:
    """Per-scroll exposure analysis is not part of the public release; never reads as clean."""
    if not scroll:
        return "NOT_EVALUATED"
    return "UNKNOWN_NOT_ANALYSED"


def inventory(scroll: str | None = None) -> dict:
    canonical = None
    if scroll:
        try:
            canonical = scroll_ids.resolve(scroll)
        except KeyError as exc:
            return {"schema": SCHEMA, "selected_scroll": scroll, "canonical_scroll": None,
                    "state": "IDENTITY_CONFLICT", "candidates": [], "why": str(exc)}
    rows = deepcopy(list(_CANDIDATES))
    for row in rows:
        artifact_sha = (row.get("artifact") or {}).get("sha256")
        row["held_locally"] = bool(artifact_sha and content_store.has(artifact_sha))
        row["available"] = row["held_locally"]
        row["selected_scroll_exposure"] = _exposure(row["id"], canonical)
        row["eligible_for_automatic_routing"] = False
        row["promotion"] = "operator_only"
        row["promotion_gate"] = "all declared controls plus an independent real-data receipt"
    return {
        "schema": SCHEMA, "read_only": True, "selected_scroll": scroll,
        "canonical_scroll": canonical, "state": "CANDIDATES_REQUIRE_CONTROLS",
        "candidates": rows,
        "counts": {
            "total": len(rows),
            "package_available": sum(row["lifecycle"] == "CANDIDATE_PACKAGE_AVAILABLE" for row in rows),
            "apparatus_only": sum("APPARATUS_ONLY" in row["lifecycle"] for row in rows),
            "blocked": sum("BLOCKED" in row["lifecycle"] or "STUB" in row["lifecycle"] for row in rows),
            "adopt_candidates": sum(row["lifecycle"] == "ADOPT_CANDIDATE_UNVERIFIED" for row in rows),
            "installed_not_loadable": sum(
                row["lifecycle"] == "INSTALLED_BUT_NOT_LOADABLE_BY_THE_PINNED_RUNTIME" for row in rows),
            "not_attempted": sum(row["lifecycle"] == "CANDIDATE_NOT_ATTEMPTED" for row in rows),
        },
        "claim_ceiling": "candidate identity and readiness only; no model is qualified and no candidate ran",
    }
