"""Typed, fail-closed invocation boundary for the pinned Villa/VC3D tools."""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from argus.core import copy_out_in_defect_control as defect_control
from argus.core import paths

CONTRACT = "argus-villa-provider-adapter-v1"
CALLABLE_STATES = {"CALLABLE", "REAL_DATA_PROVEN"}

COPY_OUT_IN_CAPABILITY_ID = "vc_grow_seg_from_seed"
NEIGHBOR_COPY_DIRECTIONS = {"out", "in"}

CONSOLE_SCRIPT_NAMES = {
    "vesuvius.zarr_tasks": "vesuvius.zarr_tasks.exe",
    "vesuvius.compute_st": "vesuvius.compute_st.exe",
    "vesuvius.voxelize_obj": "vesuvius.voxelize_obj.exe",
    "vesuvius.predict": "vesuvius.predict.exe",
    "vesuvius.train": "vesuvius.train.exe",
    "vesuvius.blend_and_finalize": "vesuvius.blend_and_finalize.exe",
    "vesuvius.blend_logits": "vesuvius.blend_logits.exe",
    "vesuvius.finalize_outputs": "vesuvius.finalize_outputs.exe",
    "vesuvius.refine_labels": "vesuvius.refine_labels.exe",
    "vesuvius.find_patches": "vesuvius.find_patches.exe",
    "vesuvius.accept_terms": "vesuvius.accept_terms.exe",
}

COMMANDS = {
    "vc_flatten": "flatten",
    "vc_obj2tifxyz": "obj_to_tifxyz",
    "vc_tifxyz2obj": "tifxyz_to_obj",
    "vc_grow_seg_from_segments": "grow_segments",
    "vc_render_tifxyz": "render",
    "vc_project_tifxyz": "project",
    "vc_gen_normalgrids": "normalgrids",
    "vc_calc_surface_metrics": "surface_metrics",
    "vesuvius.surface_preflight": "surface_preflight",
    "vc_obj2tifxyz_legacy": "obj_to_tifxyz_legacy",
    "vc_straighten": "straighten",
    "vc_cut_windings": "cut_windings",
    "vc_objrefine": "obj_refine",
    "vc_transform_geom": "transform_geom",
    "vc_tifxyz2zarr_sparse": "tifxyz_to_zarr_sparse",
    "vc_tifxyz_selfcross": "tifxyz_selfcross",
    "vc_tifxyz_gengt": "tifxyz_gengt",
    "vc_tifxyz_inp_mask": "tifxyz_input_mask",
    "vc_add_ignore_label": "add_ignore_label",
    "vc_zarr_to_tiff": "zarr_to_tiff",
    "vc_visualize": "visualize",
    "vc_ngrids": "normalgrid_inspect",
    "vc_atlas_constraints_export": "atlas_constraints_export",
    "vc_render_video": "render_video",
    "vesuvius.zarr_tasks": "zarr_tasks",
    "vesuvius.compute_st": "compute_st",
    "vesuvius.voxelize_obj": "voxelize_obj",
    "vesuvius.predict": "predict",
    "vesuvius.blend_logits": "blend_logits",
    "vesuvius.blend_and_finalize": "blend_and_finalize",
    "vesuvius.finalize_outputs": "finalize_outputs",
    "vesuvius.refine_labels": "refine_labels",
    "vesuvius.train": "train",
    "vesuvius.find_patches": "find_patches",
    "vc_diffuse_winding": "diffuse_winding",
    "vc_tifxyz_winding": "winding_field",
    "vc_lasagna_maxflow_graph": "lasagna_maxflow_graph",
    "vc_atlas_inspect": "atlas_inspect",
    "vc_atlas_pred_snap_rebuild": "atlas_pred_snap_rebuild",
    "vc_tifxyz": "tifxyz_transform",
    "vc_seg_add_overlap": "seg_add_overlap",
    "vc_merge_patch": "merge_patch",
    "vc_merge_tifxyz": "merge_tifxyz",
    "vc_fiber_trace_metric": "fiber_trace_metric",
    "vc_grow_seg_from_seed": "neighbor_copy",
}


class ProviderRefusal(RuntimeError):
    pass


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                       default=str).encode("utf-8")).hexdigest()


def _ledger() -> dict:
    source = Path(__file__).resolve().parents[1] / "capabilities.json"
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProviderRefusal("capability ledger is unreadable: %s" % exc) from None
    if not isinstance(doc, dict) or not isinstance(doc.get("capabilities"), list):
        raise ProviderRefusal("capability ledger has no capabilities list")
    return doc


def _entry(capability_id: str) -> dict:
    if capability_id not in COMMANDS:
        raise ProviderRefusal("%r is not an ARGUS-adapted Villa command; supported: %s" %
                              (capability_id, sorted(COMMANDS)))
    rows = [r for r in _ledger()["capabilities"]
            if isinstance(r, dict) and r.get("capability_id") == capability_id]
    if len(rows) != 1:
        raise ProviderRefusal("capability %r is not uniquely registered" % capability_id)
    row = rows[0]
    if row.get("state") not in CALLABLE_STATES:
        raise ProviderRefusal("capability %s is %s, not callable" %
                              (capability_id, row.get("state")))
    runtime = str(row.get("runtime") or "").split(" (", 1)[0].strip().replace("<ARGUS_RUNTIME_ROOT>", paths.runtimes().as_posix())
    entrypoint = str(row.get("entrypoint") or "").strip()
    if not runtime or not entrypoint:
        raise ProviderRefusal("capability %s has no runtime and entrypoint" % capability_id)
    runtime_path = Path(runtime)
    if ":" in entrypoint and runtime_path.is_dir():
        script_name = CONSOLE_SCRIPT_NAMES.get(capability_id)
        if script_name is None:
            script_name = entrypoint.split(":", 1)[0] + ".exe"
        executable = runtime_path / "Scripts" / script_name
    elif runtime_path.is_file():
        executable = runtime_path
    else:
        executable = (runtime_path if runtime_path.suffix.lower() == ".exe" else
                      runtime_path / (entrypoint if entrypoint.lower().endswith(".exe")
                                      else entrypoint + ".exe"))
    if not executable.is_file():
        raise ProviderRefusal("capability %s executable is absent: %s" %
                              (capability_id, executable))
    return dict(row, executable=str(executable.resolve()))


_CONFINE_TO_SERVED_ROOTS: contextvars.ContextVar = contextvars.ContextVar("argus_provider_plan_confined", default=False)


@contextlib.contextmanager
def confined_to_served_roots():
    """Inside this block every path the adapter resolves must lie in ARGUS's declared roots."""
    token = _CONFINE_TO_SERVED_ROOTS.set(True)
    try:
        yield
    finally:
        _CONFINE_TO_SERVED_ROOTS.reset(token)


def _path(value, label: str, *, must_exist: bool) -> Path:
    if value in (None, ""):
        raise ProviderRefusal("%s is required" % label)
    if paths.is_remote_path(value):
        raise ProviderRefusal("%s may not be a network (UNC) path" % label)
    if paths.looks_like_url(value):
        raise ProviderRefusal("%s must be a local path, not a URL" % label)
    if not paths.plain_local_path(value):
        raise ProviderRefusal("%s is not a plain local path" % label)
    if _CONFINE_TO_SERVED_ROOTS.get() and paths.outside_served_roots(**{label: value}):
        raise ProviderRefusal("%s is outside ARGUS's declared roots; this read-only route only plans inside them" % label)
    try:
        p = Path(str(value)).resolve()
    except (OSError, ValueError, RuntimeError):
        raise ProviderRefusal("%s is not a usable path" % label) from None
    if must_exist and not p.exists():
        raise ProviderRefusal("%s does not exist" % label)
    if p == p.parent:
        raise ProviderRefusal("%s may not be a filesystem root" % label)
    return p


def _options(kind: str, options: dict | None) -> dict:
    opts = dict(options or {})
    for name, value in opts.items():
        if isinstance(value, str) and paths.is_remote_path(value):
            raise ProviderRefusal("option %s may not be a network (UNC) path" % name)
    allowed = {
        "flatten": {"iterations", "downsample", "lscm_only", "no_scale", "uv_only"},
        "obj_to_tifxyz": {"stretch_factor", "mesh_units", "uv_metric", "uv_to_obj",
                           "uv_downsample", "grid_cap", "uuid", "tifxyz_source"},
        "tifxyz_to_obj": {"normalize_uv", "align_grid", "keep", "clean", "inpaint"},
        "grow_segments": {"volume_path", "params_path", "src_segment", "seed_coords",
                           "max_width", "sweep", "sweep_ranges", "sweep_strategy",
                           "sweep_max_runs", "sweep_max_gen", "sweep_max_width",
                           "sweep_max_height", "sweep_min_area", "sweep_seed",
                           "sweep_cutout_cm", "sweep_metric_tolerance",
                           "sweep_max_distance_samples"},
        "render": {"volume_path", "scale", "group_idx", "output_format", "cache_gb",
                    "prefetch_remote", "remote_url", "log_path", "timeout_m", "num_slices",
                    "slice_step", "accum", "accum_type", "crop_x", "crop_y", "crop_width",
                    "crop_height", "auto_crop", "affine", "affine_invert", "affine_transform",
                    "invert_affine", "scale_segmentation", "rotate", "flip", "flip_normals",
                    "zarr_compressor", "zarr_compression_level", "zarr_separator", "quick_tif",
                    "flatten", "flatten_iterations", "flatten_downsample", "alpha_min",
                    "alpha_max", "alpha_opacity", "alpha_cutoff", "bl_extinction",
                    "bl_emission", "bl_ambient", "iso_cutoff", "composite_start",
                    "composite_end", "composite_collapse", "num_parts", "part_id",
                    "merge_tiff_parts", "pyramid", "resume", "pre", "voxel_size", "voxel_unit"},
        "project": {"umbilicus_path", "patch_dir", "direction", "z_min", "z_max",
                    "max_distance", "min_distance", "dedup_radius", "same_wrap_tolerance",
                    "spacing", "neighbor_max_delta", "neighbor_max_farther_delta",
                    "ray_neighborhood_radius", "ray_neighborhood_min_samples",
                    "ray_neighborhood_max_delta", "average_direct_hits", "smooth_radius",
                    "smooth_iters", "index_stride", "self_hit_ignore_radius",
                    "self_hit_epsilon", "bbox_padding", "snap_to_patch"},
        "normalgrids": {"level", "spiral_step", "grid_step", "direction", "num_parts",
                        "part_id", "sparse_volume", "chunk_budget_mib", "io_threads",
                        "preview_every", "verify_grid_save", "debug_per_slice", "metrics_json"},
        "surface_metrics": {"surface_path", "winding_path", "z_min", "z_max"},
        "surface_preflight": {"volume_path", "array_key", "margin", "max_samples",
                              "minimum_support_fraction", "support_threshold"},
        "obj_to_tifxyz_legacy": {"step_size", "uuid"},
        "straighten": {"unbend", "overlap_pairs", "orthogonalize", "trim", "unbend_smooth_cols",
                       "unbend_min_rows", "threshold", "min_du", "max_dv", "stride",
                       "trim_max_edge", "seed"},
        "cut_windings": {"start_winding"},
        "obj_refine": {"volume_path", "params_path"},
        "transform_geom": {"affine_path", "invert", "scale_before_affine", "scale_after_affine"},
        "tifxyz_to_zarr_sparse": {"shape_z", "shape_y", "shape_x", "reference_zarr", "z_min",
                                   "z_max", "chunk_size", "source_segment", "source_mesh",
                                   "spool_memory_mb", "spool_dir", "source_group", "raster_mode",
                                   "metrics_json", "keep_spool", "overwrite"},
        "tifxyz_selfcross": {"collection_path", "exclude", "maxedge", "cell", "threads",
                              "max_collection_points", "fail_on_crossing"},
        "tifxyz_gengt": {"winding_path", "num_collections"},
        "tifxyz_input_mask": {"mask_path"},
        "add_ignore_label": {"mode", "ignore_value", "alpha", "chunk_alpha", "n_angle_bins",
                              "shrink_factor", "compute_level", "output_level", "workers",
                              "z_min", "z_max"},
        "zarr_to_tiff": {"level", "compression"},
        "visualize": {"volpkg_path", "volume_id", "segment_id", "overlap_source", "stride",
                      "filter"},
        "normalgrid_inspect": {"surf_path", "crop", "fit_normals", "vis_ply", "vis_surf",
                               "vis_normals", "output_zarr", "step", "metrics_json",
                               "align_normals", "output_kind"},
        "atlas_constraints_export": {"project_path", "lasagna_manifest", "fiber_path",
                                      "link_table", "line_max_step",
                                      "cross_target", "cross_tolerance", "cross_z_threshold",
                                      "close_min_signed_winding", "close_max_signed_winding",
                                      "close_atlas_winding_threshold", "greedy_beam_width",
                                      "no_cycle_close", "no_lines", "no_cross"},
        "render_video": {"segment_ids"},
        "zarr_tasks": {"task", "num_workers", "level", "threshold", "erase_blank", "num_levels"},
        "compute_st": {"mode", "sigma", "structure_tensor_only", "smooth_components", "volume",
                        "patch_size", "overlap", "step_size", "batch_size", "chunk_size",
                        "swap_eigenvectors", "no_ome_out", "ome_downsample", "ome_scale",
                        "confidence_metric", "keep_eigen", "gpus", "parts_per_gpu",
                        "zarr_compressor", "zarr_compression_level", "num_workers"},
        "voxelize_obj": {"spatial_shape", "transform", "transform_axis_order", "transform_invert",
                          "num_workers", "recursive", "chunk_size", "label_dtype", "format"},
        "predict": {"model_path", "input_anon", "input_format", "tta_type", "disable_tta",
                    "num_parts", "part_id", "overlap", "batch_size", "patch_size", "save_softmax",
                    "normalization", "device", "num_workers", "writer_workers", "verbose",
                    "skip_empty_patches", "no_skip_empty_patches", "zarr_compressor",
                    "zarr_compression_level", "scroll_id", "segment_id", "energy", "resolution",
                    "read_retries", "max_patches", "bbox"},
        "blend_logits": {"sigma_scale", "chunk_size", "num_workers", "compression_level", "quiet",
                          "num_parts", "part_id"},
        "blend_and_finalize": {"sigma_scale", "chunk_size", "num_workers", "compression_level", "quiet",
                                "num_parts", "part_id", "mode", "threshold", "threshold_value"},
        "finalize_outputs": {"mode", "threshold", "threshold_value", "chunk_size", "num_workers",
                              "quiet", "num_parts", "part_id"},
        "refine_labels": {"dilation_distance", "ridge_threshold", "num_workers"},
        "train": {"config_path", "format", "val_dir", "checkpoint_path", "load_weights_only",
                   "rebuild_from_ckpt_config", "intensity_properties_json", "skip_image_checks",
                   "batch_size", "patch_size", "loss", "train_split", "seed", "no_skip_intensity_sampling",
                   "no_spatial", "rotation_axes", "model_name", "nonlin", "se", "se_reduction_ratio",
                   "pool_type", "max_epoch", "max_steps_per_epoch", "max_val_steps_per_epoch",
                   "full_epoch", "early_stopping_patience", "val_every_n", "gpus", "optimizer",
                   "gradient_accumulation", "grad_clip", "amp_dtype", "no_amp", "scheduler",
                   "warmup_steps", "trainer", "ssl_warmup", "labeled_ratio", "num_labeled",
                   "labeled_batch_size", "pretrained_checkpoint", "verbose"},
        "find_patches": {"force"},
        "diffuse_winding": {"volume_path", "dataset", "mode", "iterations", "ray_step_dist",
                             "mask_path", "winding", "collection", "box_size", "debug",
                             "dampening", "starting_diameter", "end_diameter", "spiral_step",
                             "revolutions", "no_optimized_spiral"},
        "atlas_inspect": {"project_path"},
        "winding_field": {"crop_x", "crop_y", "crop_width", "crop_height"},
        "atlas_pred_snap_rebuild": {"project_path", "lasagna_manifest", "fiber",
                                     "debug_images_dir", "dry_run", "overwrite_manual",
                                     "verbose", "trace_samples"},
        "tifxyz_transform": {"rotate", "resample", "smooth", "interpolation"},
        "seg_add_overlap": {"source_path", "workers", "point_stride"},
        "merge_patch": {"child_path", "border_cells", "blend_cells", "ransac_iters",
                         "ransac_min_thresh", "ransac_max_thresh", "ransac_mad_k",
                         "ransac_seed", "anchor_cap", "idw_k"},
        "merge_tifxyz": {"paths_dir", "ref", "obj2tifxyz", "ransac_iters",
                          "ransac_min_thresh", "ransac_max_thresh", "ransac_mad_k",
                          "ransac_seed", "ransac_autogate", "ransac_autogate_scale_k",
                          "ransac_autogate_shift_frac", "ransac_min_shift", "ransac_scale_min",
                          "ransac_scale_max", "anchor_cap", "strip_cols"},
        "fiber_trace_metric": {"fiber_json", "normal_manifest", "remote_cache_dir",
                                "voxel_size_um", "inference_scaledown_power", "step_voxels",
                                "cone_angle_degrees", "cone_angle_step_degrees", "cone_grid_size",
                                "beam_width", "beam_prune_distance_voxels", "beam_lookahead_steps",
                                "lookahead_parent_cap", "lookahead_retry_parent_cap",
                                "exhaustive_lookahead", "threads", "smoothness_weight",
                                "smoothness_normal_weight", "smoothness_tangent_weight",
                                "smoothness_free_angle_degrees", "cumulative_smoothness_steps",
                                "cumulative_smoothness_tangent_weight", "max_step_factor",
                                "error_threshold_base_voxels", "cache_gib", "quiet"},
    }[kind]
    unknown = sorted(set(opts) - allowed)
    if unknown:
        raise ProviderRefusal("unknown %s option(s): %s" % (kind, unknown))
    required = {
        "grow_segments": ("volume_path", "params_path"),
        "render": ("volume_path", "scale", "group_idx"),
        "project": ("umbilicus_path", "patch_dir"),
        "surface_metrics": ("surface_path", "winding_path"),
        "surface_preflight": ("volume_path",),
        "obj_refine": ("volume_path", "params_path"),
        "transform_geom": ("affine_path",),
        "tifxyz_to_zarr_sparse": (),
        "tifxyz_selfcross": (),
        "tifxyz_gengt": ("winding_path",),
        "tifxyz_input_mask": ("mask_path",),
        "visualize": ("volpkg_path", "volume_id", "segment_id", "overlap_source"),
        "normalgrid_inspect": ("output_kind",),
        "render_video": ("segment_ids",),
        "zarr_tasks": (),
        "compute_st": (),
        "voxelize_obj": ("spatial_shape",),
        "predict": ("model_path",),
        "train": ("config_path",),
        "diffuse_winding": ("volume_path",),
        "atlas_pred_snap_rebuild": ("project_path",),
        "seg_add_overlap": ("source_path",),
        "merge_patch": ("child_path",),
        "merge_tifxyz": ("paths_dir",),
        "fiber_trace_metric": ("fiber_json", "normal_manifest"),
    }.get(kind, ())
    missing = [key for key in required if opts.get(key) in (None, "")]
    if missing:
        raise ProviderRefusal("%s option(s) are required: %s" % (kind, missing))
    if kind == "render_video":
        segment_ids = opts["segment_ids"]
        if isinstance(segment_ids, str):
            segment_ids = [segment_ids]
        if not isinstance(segment_ids, (list, tuple)) or not segment_ids:
            raise ProviderRefusal("segment_ids must contain at least one segment id")
        if any(not isinstance(value, str) or not value.strip() for value in segment_ids):
            raise ProviderRefusal("segment_ids must contain non-empty strings")
        opts["segment_ids"] = list(segment_ids)
    if kind == "voxelize_obj":
        shape = opts["spatial_shape"]
        if isinstance(shape, str):
            shape = shape.replace(",", " ").split()
        if not isinstance(shape, (list, tuple)) or len(shape) != 3:
            raise ProviderRefusal("spatial_shape must contain three positive integers")
        try:
            shape = [int(value) for value in shape]
        except (TypeError, ValueError):
            raise ProviderRefusal("spatial_shape must contain three positive integers") from None
        if any(value <= 0 for value in shape):
            raise ProviderRefusal("spatial_shape must contain three positive integers")
        opts["spatial_shape"] = shape
    if kind == "predict":
        if opts.get("input_format", "zarr") not in {"zarr", "volume"}:
            raise ProviderRefusal("input_format must be zarr or volume")
        if opts.get("tta_type", "mirroring") not in {"mirroring", "rotation"}:
            raise ProviderRefusal("tta_type must be mirroring or rotation")
        if opts.get("device", "cuda") not in {"cuda", "cpu"}:
            raise ProviderRefusal("device must be cuda or cpu")
        if "patch_size" in opts and opts["patch_size"] not in (None, ""):
            patch_size = str(opts["patch_size"]).replace(" ", "")
            try:
                dims = [int(value) for value in patch_size.split(",")]
            except (TypeError, ValueError):
                raise ProviderRefusal("patch_size must be comma-separated positive integers") from None
            if len(dims) not in {2, 3} or any(value <= 0 for value in dims):
                raise ProviderRefusal("patch_size must contain two or three positive integers")
            opts["patch_size"] = ",".join(str(value) for value in dims)
        if "bbox" in opts and opts["bbox"] not in (None, ""):
            pieces = str(opts["bbox"]).split(",")
            if len(pieces) != 3 or any(":" not in piece for piece in pieces):
                raise ProviderRefusal("bbox must be z0:z1,y0:y1,x0:x1")
    if kind in {"blend_logits", "blend_and_finalize", "finalize_outputs"}:
        if opts.get("mode", "binary") not in {"binary", "multiclass"}:
            raise ProviderRefusal("mode must be binary or multiclass")
        if "threshold_value" in opts:
            if not opts.get("threshold"):
                raise ProviderRefusal("threshold_value requires threshold")
            try:
                threshold_value = float(opts["threshold_value"])
            except (TypeError, ValueError):
                raise ProviderRefusal("threshold_value must be numeric") from None
            if not 0.0 < threshold_value < 1.0:
                raise ProviderRefusal("threshold_value must be between 0 and 1")
            if opts.get("mode", "binary") == "multiclass":
                raise ProviderRefusal("threshold_value is not applicable in multiclass mode")
            opts["threshold_value"] = threshold_value
    if kind == "refine_labels":
        for key in ("dilation_distance", "ridge_threshold"):
            if key in opts:
                try:
                    value = float(opts[key])
                except (TypeError, ValueError):
                    raise ProviderRefusal("%s must be numeric" % key) from None
                if not math.isfinite(value) or value < 0:
                    raise ProviderRefusal("%s must be finite and non-negative" % key)
                opts[key] = value
    if kind == "render":
        output_format = str(opts.get("output_format", "tif")).lower()
        if output_format not in {"tif", "zarr"}:
            raise ProviderRefusal("output_format must be tif or zarr")
        opts["output_format"] = output_format
    if kind == "normalgrid_inspect":
        output_kind = str(opts["output_kind"]).lower()
        if output_kind not in {"vis_ply", "vis_surf", "vis_normals", "output_zarr", "metrics_json"}:
            raise ProviderRefusal("output_kind must be vis_ply, vis_surf, vis_normals, output_zarr or metrics_json")
        opts["output_kind"] = output_kind
        if "crop" in opts:
            crop = opts["crop"]
            if isinstance(crop, str):
                crop = crop.split(",")
            if not isinstance(crop, (list, tuple)) or len(crop) != 6:
                raise ProviderRefusal("crop must contain six integer bounds")
            try:
                crop = [int(value) for value in crop]
            except (TypeError, ValueError):
                raise ProviderRefusal("crop must contain six integer bounds") from None
            opts["crop"] = crop
    for key in ("params_path", "sweep_ranges", "umbilicus_path", "patch_dir", "surface_path",
                "winding_path", "affine_path", "mask_path", "reference_zarr", "spool_dir",
                "volpkg_path", "surf_path", "project_path", "lasagna_manifest", "fiber_path",
                "fiber_json", "normal_manifest", "remote_cache_dir",
                "transform", "model_path", "config_path", "checkpoint_path", "intensity_properties_json",
                "pretrained_checkpoint", "val_dir"):
        if key in opts:
            opts[key] = str(_path(opts[key], key, must_exist=True))
    for key in ("source_path", "child_path", "paths_dir", "obj2tifxyz"):
        if key in opts:
            opts[key] = str(_path(opts[key], key, must_exist=True))
    if "debug_images_dir" in opts:
        opts["debug_images_dir"] = str(_path(opts["debug_images_dir"], "debug_images_dir", must_exist=False))
    if "volume_path" in opts:
        value = str(opts["volume_path"])
        if kind == "surface_preflight" and paths.clean_object_url(value):
            opts["volume_path"] = value
        else:
            opts["volume_path"] = str(_path(value, "volume_path", must_exist=True))
    integer_options = {
        "iterations": 1, "downsample": 1, "group_idx": 0, "num_slices": 1,
        "crop_x": 0, "crop_y": 0, "crop_width": 1, "crop_height": 1,
        "timeout_m": 0, "rotate": 0, "flip": -1,
        "flatten_iterations": 1, "flatten_downsample": 1, "alpha_min": 0,
        "alpha_max": 0, "alpha_opacity": 0, "alpha_cutoff": 0, "iso_cutoff": 0,
        "composite_start": -1, "composite_end": -1, "num_parts": 1, "part_id": 0,
        "pyramid": 0,
        "zarr_compression_level": -1, "sweep_max_runs": 0, "sweep_max_gen": -1,
        "sweep_max_width": -1, "sweep_max_height": -1, "sweep_seed": 0,
        "sweep_max_distance_samples": 1,
        "max_samples": 1,
        "z_min": -1, "z_max": -1, "num_parts": 1, "part_id": 0,
        "level": 0, "grid_step": 1, "sparse_volume": 1, "chunk_budget_mib": 1,
        "io_threads": 0, "preview_every": 0, "max_distance": 0, "min_distance": 0,
        "dedup_radius": 0, "spacing": 1, "neighbor_max_delta": 0,
        "neighbor_max_farther_delta": 0, "ray_neighborhood_radius": 0,
        "ray_neighborhood_min_samples": 1, "ray_neighborhood_max_delta": 0,
        "smooth_radius": 0, "smooth_iters": 0, "index_stride": 1,
        "self_hit_ignore_radius": 0, "bbox_padding": 0,
        "overlap_pairs": 0, "unbend_min_rows": 1, "min_du": 0, "max_dv": 0,
        "stride": 0, "trim_max_edge": 0, "seed": 0, "start_winding": 0,
        "shape_z": 1, "shape_y": 1, "shape_x": 1, "z_min": -1, "z_max": -1,
        "chunk_size": 1, "spool_memory_mb": 0, "source_group": 0, "exclude": 0,
        "maxedge": 0, "cell": 1, "threads": 0, "max_collection_points": 1,
        "num_collections": 1, "ignore_value": 1, "chunk_alpha": 0, "n_angle_bins": 1,
        "compute_level": 0, "output_level": 0, "workers": 0, "level": 0,
        "unbend_smooth_cols": 1,
        "greedy_beam_width": 1,
        "point_stride": 1, "border_cells": 1, "blend_cells": 1, "ransac_iters": 1,
        "ransac_seed": 0, "anchor_cap": 0, "idw_k": 1, "strip_cols": 0,
        "smooth": 1,
        "inference_scaledown_power": 0, "cone_grid_size": 1, "beam_width": 1,
        "beam_lookahead_steps": 1, "lookahead_parent_cap": 0,
        "lookahead_retry_parent_cap": 0, "threads": 0, "cumulative_smoothness_steps": 1,
    }
    for key, minimum in integer_options.items():
        if key not in opts:
            continue
        value = opts[key]
        if key == "pyramid" and isinstance(value, bool):
            value = int(value)
        if isinstance(value, bool):
            raise ProviderRefusal("%s must be an integer" % key)
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ProviderRefusal("%s must be an integer" % key) from None
        if value < minimum:
            raise ProviderRefusal("%s must be >= %s" % (key, minimum))
        opts[key] = value
    float_options = {
        "stretch_factor": 0.0, "mesh_units": 0.0, "uv_to_obj": 0.0,
        "uv_downsample": 1.0, "keep": 0.0, "clean": 0.0, "scale": 0.0,
        "cache_gb": 0.0, "slice_step": 0.0, "accum": 0.0,
        "scale_segmentation": 0.0, "voxel_size": 0.0,
        "bl_extinction": 0.0, "bl_emission": 0.0, "bl_ambient": 0.0,
        "max_width": 0.0, "sweep_min_area": 0.0, "sweep_cutout_cm": 0.0,
        "sweep_metric_tolerance": 0.0,
        "spiral_step": 0.0, "self_hit_epsilon": 0.0,
        "margin": 0.0, "minimum_support_fraction": 0.0,
        "support_threshold": 0.0,
        "step_size": 0.0, "threshold": 0.0, "scale_before_affine": 0.0,
        "scale_after_affine": 0.0, "alpha": 0.0, "shrink_factor": 0.0,
        "line_max_step": 0.0, "cross_target": 0.0, "cross_tolerance": 0.0,
        "cross_z_threshold": 0.0, "close_min_signed_winding": -1.0,
        "close_max_signed_winding": -1.0, "close_atlas_winding_threshold": 0.0,
        "resample": 0.0, "ransac_min_thresh": 0.0,
        "ransac_max_thresh": 0.0, "ransac_mad_k": 0.0,
        "ransac_autogate_scale_k": 0.0, "ransac_autogate_shift_frac": 0.0,
        "ransac_min_shift": 0.0, "ransac_scale_min": 0.0, "ransac_scale_max": 0.0,
        "voxel_size_um": 0.0, "step_voxels": 0.0, "cone_angle_degrees": 0.0,
        "cone_angle_step_degrees": 0.0, "beam_prune_distance_voxels": 0.0,
        "smoothness_weight": 0.0, "smoothness_normal_weight": 0.0,
        "smoothness_tangent_weight": 0.0, "smoothness_free_angle_degrees": 0.0,
        "cumulative_smoothness_tangent_weight": 0.0, "max_step_factor": 0.0,
        "error_threshold_base_voxels": 0.0, "cache_gib": 0.0,
    }
    for key, minimum in float_options.items():
        if key not in opts:
            continue
        value = opts[key]
        if isinstance(value, bool) and not (key == "threshold" and kind in {"blend_and_finalize", "finalize_outputs"}):
            raise ProviderRefusal("%s must be numeric" % key)
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ProviderRefusal("%s must be numeric" % key) from None
        if not math.isfinite(value):
            raise ProviderRefusal("%s must be finite" % key)
        if value < minimum:
            raise ProviderRefusal("%s must be >= %s" % (key, minimum))
        opts[key] = value
    if "rotate" in opts and opts["rotate"] not in {0, 90, 180, 270}:
        raise ProviderRefusal("rotate must be one of 0, 90, 180 or 270")
    if "flip" in opts and opts["flip"] not in {-1, 0, 1, 2}:
        raise ProviderRefusal("flip must be -1, 0, 1 or 2")
    if "keep" in opts and not 1 <= opts["keep"] <= 100:
        raise ProviderRefusal("keep must be between 1 and 100")
    if kind == "project" and "direction" in opts and opts["direction"] not in {"in", "out"}:
        raise ProviderRefusal("direction must be in or out")
    if kind == "tifxyz_transform" and "interpolation" in opts and opts["interpolation"] not in {
        "nearest", "bilinear", "cubic", "lanczos"}:
        raise ProviderRefusal("interpolation must be nearest, bilinear, cubic or lanczos")
    if kind == "tifxyz_transform" and not any(key in opts for key in ("rotate", "resample", "smooth")):
        raise ProviderRefusal("tifxyz_transform requires rotate, resample or smooth")
    if kind in {"merge_patch", "merge_tifxyz"}:
        if "ransac_min_thresh" in opts and "ransac_max_thresh" in opts:
            if opts["ransac_max_thresh"] > 0 and opts["ransac_max_thresh"] < opts["ransac_min_thresh"]:
                raise ProviderRefusal("ransac_max_thresh must be >= ransac_min_thresh")
    if kind == "merge_tifxyz" and "paths_dir" not in opts:
        raise ProviderRefusal("paths_dir is required so the global merge can be staged safely")
    if kind == "fiber_trace_metric":
        if opts.get("inference_scaledown_power", 2) > 30:
            raise ProviderRefusal("inference_scaledown_power must be <= 30")
        if opts.get("cone_angle_step_degrees", 5.0) < 0:
            raise ProviderRefusal("cone_angle_step_degrees must be non-negative")
        if opts.get("max_step_factor", 3.0) <= 0:
            raise ProviderRefusal("max_step_factor must be positive")
    if kind == "normalgrids" and "direction" in opts and opts["direction"] not in {"all", "xy", "xz", "yz"}:
        raise ProviderRefusal("direction must be all, xy, xz or yz")
    for key in ("alpha_min", "alpha_max", "alpha_opacity"):
        if key in opts and not 0 <= opts[key] <= 255:
            raise ProviderRefusal("%s must be between 0 and 255" % key)
    if "alpha_cutoff" in opts and not 0 <= opts["alpha_cutoff"] <= 10000:
        raise ProviderRefusal("alpha_cutoff must be between 0 and 10000")
    if "scale_segmentation" in opts and opts["scale_segmentation"] <= 0:
        raise ProviderRefusal("scale_segmentation must be > 0")
    if "voxel_size" in opts and opts["voxel_size"] <= 0:
        raise ProviderRefusal("voxel_size must be > 0")
    if "minimum_support_fraction" in opts and opts["minimum_support_fraction"] > 1:
        raise ProviderRefusal("minimum_support_fraction must be between 0 and 1")
    return opts


def _argv(kind: str, executable: str, input_path: Path, output_path: Path, opts: dict) -> list[str]:
    if kind == "flatten":
        out = [executable, "--input", str(input_path), "--output", str(output_path)]
        if "iterations" in opts:
            out += ["--iterations", str(int(opts["iterations"]))]
        if "downsample" in opts:
            out += ["--downsample", str(int(opts["downsample"]))]
        for flag in ("lscm_only", "no_scale", "uv_only"):
            if opts.get(flag):
                out.append("--" + flag.replace("_", "-"))
        return out
    if kind == "obj_to_tifxyz":
        out = [executable, str(input_path), str(output_path)]
        if "stretch_factor" in opts or "mesh_units" in opts:
            out.append(str(float(opts.get("stretch_factor", 1.0))))
            out.append(str(float(opts.get("mesh_units", 1.0))))
        if opts.get("uv_metric", True):
            out.append("--uv-metric")
        for key, flag in (("uv_to_obj", "--uv-to-obj"), ("uv_downsample", "--uv-downsample"),
                          ("grid_cap", "--grid-cap"), ("uuid", "--uuid"),
                          ("tifxyz_source", "--tifxyz-source")):
            if key in opts:
                out.append("%s=%s" % (flag, opts[key]))
        return out
    if kind == "obj_to_tifxyz_legacy":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("step_size", None), ("uuid", "--uuid")):
            if key not in opts:
                continue
            if flag is None:
                out.append(str(opts[key]))
            else:
                out.append("%s=%s" % (flag, opts[key]))
        return out
    if kind == "straighten":
        out = [executable, "--input", str(input_path), "--output", str(output_path)]
        for key, flag in (("unbend_smooth_cols", "--unbend-smooth-cols"),
                          ("unbend_min_rows", "--unbend-min-rows"),
                          ("threshold", "--threshold"), ("min_du", "--min-du"),
                          ("max_dv", "--max-dv"), ("stride", "--stride"),
                          ("trim_max_edge", "--trim-max-edge"), ("seed", "--seed"),
                          ("overlap_pairs", "--overlap-pairs")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("unbend", "--unbend"), ("orthogonalize", "--orthogonalize"),
                          ("trim", "--trim")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "cut_windings":
        out = [executable, "--input", str(input_path), "--output", str(output_path)]
        if "start_winding" in opts:
            out += ["--start-winding", str(opts["start_winding"])]
        return out
    if kind == "obj_refine":
        return [executable, opts["volume_path"], str(input_path), str(output_path), opts["params_path"]]
    if kind == "transform_geom":
        out = [executable, "--input", str(input_path), "--output", str(output_path),
               "--affine", opts["affine_path"]]
        for key, flag in (("scale_before_affine", "--scale-before-affine"),
                          ("scale_after_affine", "--scale-after-affine")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("invert"):
            out.append("--invert")
        return out
    if kind == "project":
        out = [executable, "-s", str(input_path), "-u", opts["umbilicus_path"],
               "-p", opts["patch_dir"], "-o", str(output_path)]
        for key, flag in (("direction", "--direction"), ("z_min", "--z-min"),
                          ("z_max", "--z-max"), ("max_distance", "--max-distance"),
                          ("min_distance", "--min-distance"), ("dedup_radius", "--dedup-radius"),
                          ("same_wrap_tolerance", "--same-wrap-tolerance"),
                          ("spacing", "--spacing"), ("neighbor_max_delta", "--neighbor-max-delta"),
                          ("neighbor_max_farther_delta", "--neighbor-max-farther-delta"),
                          ("ray_neighborhood_radius", "--ray-neighborhood-radius"),
                          ("ray_neighborhood_min_samples", "--ray-neighborhood-min-samples"),
                          ("ray_neighborhood_max_delta", "--ray-neighborhood-max-delta"),
                          ("smooth_radius", "--smooth-radius"), ("smooth_iters", "--smooth-iters"),
                          ("index_stride", "--index-stride"),
                          ("self_hit_ignore_radius", "--self-hit-ignore-radius"),
                          ("self_hit_epsilon", "--self-hit-epsilon"),
                          ("bbox_padding", "--bbox-padding")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("average_direct_hits"):
            out.append("--ray-neighborhood-average-direct-hits")
        if opts.get("snap_to_patch"):
            out.append("--snap-to-patch")
        return out
    if kind == "normalgrids":
        out = [executable, "generate", "-i", str(input_path), "-o", str(output_path)]
        for key, flag in (("level", "--level"), ("spiral_step", "--spiral-step"),
                          ("grid_step", "--grid-step"), ("direction", "--direction"),
                          ("num_parts", "--num-parts"), ("part_id", "--part-id"),
                          ("sparse_volume", "--sparse-volume"),
                          ("chunk_budget_mib", "--chunk-budget-mib"),
                          ("io_threads", "--io-threads"), ("preview_every", "--preview-every"),
                          ("metrics_json", "--metrics-json")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("verify_grid_save", "--verify-grid-save"),
                          ("debug_per_slice", "--debug-per-slice")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "surface_metrics":
        out = [executable, "--collection", str(input_path), "--surface", opts["surface_path"],
               "--winding", opts["winding_path"], "--output", str(output_path)]
        for key, flag in (("z_min", "--z_min"), ("z_max", "--z_max")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "tifxyz_to_zarr_sparse":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("shape_z", "--shape-z"), ("shape_y", "--shape-y"),
                          ("shape_x", "--shape-x"), ("reference_zarr", "--reference-zarr"),
                          ("z_min", "--z-min"), ("z_max", "--z-max"),
                          ("chunk_size", "--chunk-size"), ("source_segment", "--source-segment"),
                          ("source_mesh", "--source-mesh"), ("spool_memory_mb", "--spool-memory-mb"),
                          ("spool_dir", "--spool-dir"), ("source_group", "--source-group"),
                          ("raster_mode", "--raster-mode"), ("metrics_json", "--metrics-json")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("keep_spool", "--keep-spool"), ("overwrite", "--overwrite")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "tifxyz_selfcross":
        out = [executable, "--surface", str(input_path), "--output", str(output_path)]
        for key, flag in (("collection_path", "--collection"), ("exclude", "--exclude"),
                          ("maxedge", "--maxedge"), ("cell", "--cell"),
                          ("threads", "--threads"),
                          ("max_collection_points", "--max-collection-points")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("fail_on_crossing"):
            out.append("--fail-on-crossing")
        return out
    if kind == "tifxyz_gengt":
        out = [executable, "--input", str(input_path), "--winding", opts["winding_path"],
               "--output", str(output_path)]
        if "num_collections" in opts:
            out += ["--num-collections", str(opts["num_collections"])]
        return out
    if kind == "tifxyz_input_mask":
        return [executable, str(input_path), opts["mask_path"], str(output_path)]
    if kind == "add_ignore_label":
        out = [executable, "--input", str(input_path), "--output", str(output_path)]
        for key, flag in (("mode", "--mode"), ("ignore_value", "--ignore-value"),
                          ("alpha", "--alpha"), ("chunk_alpha", "--chunk-alpha"),
                          ("n_angle_bins", "--n-angle-bins"), ("shrink_factor", "--shrink-factor"),
                          ("compute_level", "--compute-level"), ("output_level", "--output-level"),
                          ("workers", "--workers"), ("z_min", "--z-min"), ("z_max", "--z-max")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "zarr_to_tiff":
        out = [executable, "--input", str(input_path), "--output", str(output_path)]
        for key, flag in (("level", "--level"), ("compression", "--compression")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "visualize":
        out = [executable, "--volpkg-path", opts["volpkg_path"], "--volume-id",
               opts["volume_id"], "--segment-id", opts["segment_id"], "--overlap-source",
               opts["overlap_source"], "--output", str(output_path)]
        for key, flag in (("stride", "--stride"), ("filter", "--filter")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "atlas_constraints_export":
        out = [executable, str(input_path)]
        for key, flag in (("project_path", None), ("lasagna_manifest", "--lasagna-manifest"),
                          ("fiber_path", "--fiber-path")):
            if key not in opts:
                continue
            if flag is None:
                out.append(str(opts[key]))
            else:
                out += [flag, str(opts[key])]
        out += ["--output", str(output_path)]
        for key, flag in (
            ("line_max_step", "--line-max-step"), ("cross_target", "--cross-target"),
            ("cross_tolerance", "--cross-tolerance"), ("cross_z_threshold", "--cross-z-threshold"),
            ("close_min_signed_winding", "--close-min-signed-winding"),
            ("close_max_signed_winding", "--close-max-signed-winding"),
            ("close_atlas_winding_threshold", "--close-atlas-winding-threshold"),
            ("greedy_beam_width", "--greedy-beam-width")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("link_table", "--link-table"), ("no_cycle_close", "--no-cycle-close"),
                          ("no_lines", "--no-lines"), ("no_cross", "--no-cross")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "render_video":
        return [executable, str(input_path), str(output_path), *opts["segment_ids"]]
    if kind == "zarr_tasks":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("task", "--task"), ("num_workers", "--num-workers"),
                          ("level", "--level"), ("threshold", "--threshold"),
                          ("num_levels", "--num-levels")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("erase_blank"):
            out.append("--erase-blank")
        return out
    if kind == "compute_st":
        out = [executable, "--input_dir", str(input_path), "--output_dir", str(output_path)]
        for key, flag in (("mode", "--mode"), ("sigma", "--sigma"), ("volume", "--volume"),
                          ("patch_size", "--patch_size"), ("overlap", "--overlap"),
                          ("step_size", "--step_size"), ("batch_size", "--batch_size"),
                          ("chunk_size", "--chunk-size"), ("ome_downsample", "--ome-downsample"),
                          ("ome_scale", "--ome-scale"), ("confidence_metric", "--confidence-metric"),
                          ("gpus", "--gpus"), ("parts_per_gpu", "--parts-per-gpu"),
                          ("zarr_compressor", "--zarr-compressor"),
                          ("zarr_compression_level", "--zarr-compression-level"),
                          ("num_workers", "--num-workers")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("structure_tensor_only", "--structure-tensor-only"),
                          ("smooth_components", "--smooth-components"),
                          ("swap_eigenvectors", "--swap-eigenvectors"),
                          ("no_ome_out", "--no-ome-out"), ("keep_eigen", "--keep-eigen")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "voxelize_obj":
        out = [executable, str(input_path), "--spatial-shape",
               *[str(value) for value in opts["spatial_shape"]], "--output_path", str(output_path)]
        for key, flag in (("transform", "--transform"), ("transform_axis_order", "--transform-axis-order"),
                          ("num_workers", "--num_workers"), ("chunk_size", "--chunk_size"),
                          ("label_dtype", "--label-dtype"), ("format", "--format")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("transform_invert", "--transform-invert"), ("recursive", "--recursive")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "predict":
        out = [executable, "--model-path", opts["model_path"], "--input-dir", str(input_path),
               "--output-dir", str(output_path)]
        for key, flag in (("input_format", "--input-format"), ("tta_type", "--tta-type"),
                          ("num_parts", "--num-parts"), ("part_id", "--part-id"),
                          ("overlap", "--overlap"), ("batch_size", "--batch-size"),
                          ("patch_size", "--patch-size"), ("normalization", "--normalization"),
                          ("device", "--device"), ("num_workers", "--num-workers"),
                          ("writer_workers", "--writer-workers"),
                          ("zarr_compressor", "--zarr-compressor"),
                          ("zarr_compression_level", "--zarr-compression-level"),
                          ("scroll_id", "--scroll-id"), ("segment_id", "--segment-id"),
                          ("energy", "--energy"), ("resolution", "--resolution"),
                          ("read_retries", "--read-retries"), ("max_patches", "--max-patches"),
                          ("bbox", "--bbox")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("input_anon", "--input-anon"), ("disable_tta", "--disable-tta"),
                          ("save_softmax", "--save-softmax"), ("verbose", "--verbose"),
                          ("no_skip_empty_patches", "--no-skip-empty-patches")):
            if opts.get(key):
                out.append(flag)
        if "skip_empty_patches" in opts and not opts["skip_empty_patches"]:
            out.append("--no-skip-empty-patches")
        return out
    if kind == "blend_logits":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("sigma_scale", "--sigma-scale"), ("chunk_size", "--chunk-size"),
                          ("num_workers", "--num-workers"), ("compression_level", "--compression-level"),
                          ("num_parts", "--num-parts"), ("part_id", "--part-id")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("quiet"):
            out.append("--quiet")
        return out
    if kind == "blend_and_finalize":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("sigma_scale", "--sigma-scale"), ("chunk_size", "--chunk-size"),
                          ("num_workers", "--num-workers"), ("compression_level", "--compression-level"),
                          ("num_parts", "--num-parts"), ("part_id", "--part-id"), ("mode", "--mode"),
                          ("threshold_value", "--threshold-value")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("quiet", "--quiet"), ("threshold", "--threshold")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "finalize_outputs":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("mode", "--mode"), ("threshold_value", "--threshold-value"),
                          ("chunk_size", "--chunk-size"), ("num_workers", "--num-workers"),
                          ("num_parts", "--num-parts"), ("part_id", "--part-id")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("quiet", "--quiet"), ("threshold", "--threshold")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "refine_labels":
        out = [executable, str(input_path), str(output_path)]
        for key, flag in (("dilation_distance", "--dilation_distance"),
                          ("ridge_threshold", "--ridge_threshold"),
                          ("num_workers", "--num_workers")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "train":
        out = [executable, "--input", str(input_path), "--config", opts["config_path"],
               "--output", str(output_path)]
        for key, flag in (
            ("format", "--format"), ("val_dir", "--val-dir"), ("checkpoint_path", "--checkpoint"),
            ("intensity_properties_json", "--intensity-properties-json"),
            ("batch_size", "--batch-size"), ("patch_size", "--patch-size"), ("loss", "--loss"),
            ("train_split", "--train-split"), ("seed", "--seed"), ("rotation_axes", "--rotation-axes"),
            ("model_name", "--model-name"), ("nonlin", "--nonlin"),
            ("se_reduction_ratio", "--se-reduction-ratio"), ("pool_type", "--pool-type"),
            ("max_epoch", "--max-epoch"), ("max_steps_per_epoch", "--max-steps-per-epoch"),
            ("max_val_steps_per_epoch", "--max-val-steps-per-epoch"),
            ("early_stopping_patience", "--early-stopping-patience"), ("val_every_n", "--val-every-n"),
            ("gpus", "--gpus"), ("optimizer", "--optimizer"),
            ("gradient_accumulation", "--grad-accum"), ("grad_clip", "--grad-clip"),
            ("amp_dtype", "--amp-dtype"), ("scheduler", "--scheduler"),
            ("warmup_steps", "--warmup-steps"), ("trainer", "--trainer"),
            ("ssl_warmup", "--ssl-warmup"), ("labeled_ratio", "--labeled-ratio"),
            ("num_labeled", "--num-labeled"), ("labeled_batch_size", "--labeled-batch-size"),
            ("pretrained_checkpoint", "--pretrained_checkpoint")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("load_weights_only", "--load-weights-only"),
                          ("rebuild_from_ckpt_config", "--rebuild-from-ckpt-config"),
                          ("skip_image_checks", "--skip-image-checks"),
                          ("no_skip_intensity_sampling", "--no-skip-intensity-sampling"),
                          ("no_spatial", "--no-spatial"), ("se", "--se"), ("full_epoch", "--full-epoch"),
                          ("no_amp", "--no-amp"), ("verbose", "--verbose")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "find_patches":
        out = [executable, "--config", str(input_path), "--cache-dir", str(output_path)]
        if opts.get("force"):
            out.append("--force")
        return out
    if kind == "diffuse_winding":
        out = [executable, "--points", str(input_path), "--volume", opts["volume_path"],
               "--output", str(output_path)]
        for key, flag in (("dataset", "--dataset"), ("mode", "--mode"),
                          ("iterations", "--iterations"), ("ray_step_dist", "--ray-step-dist"),
                          ("mask_path", "--mask"), ("winding", "--winding"),
                          ("collection", "--collection"), ("box_size", "--box-size"),
                          ("dampening", "--dampening"), ("starting_diameter", "--starting-diameter"),
                          ("end_diameter", "--end-diameter"), ("spiral_step", "--spiral-step"),
                          ("revolutions", "--revolutions")):
            if key in opts:
                value = opts[key]
                if key == "box_size" and isinstance(value, (list, tuple)):
                    value = " ".join(str(part) for part in value)
                out += [flag, str(value)]
        for key, flag in (("debug", "--debug"), ("no_optimized_spiral", "--no-optimized-spiral")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "normalgrid_inspect":
        flag = {
            "vis_ply": "--vis-ply", "vis_surf": "--vis-surf",
            "vis_normals": "--vis-normals", "output_zarr": "--output-zarr",
            "metrics_json": "--metrics-json",
        }[opts["output_kind"]]
        out = [executable, "-i", str(input_path), flag, str(output_path)]
        for key, argflag in (("surf_path", "--surf"), ("step", "--step")):
            if key in opts:
                out += [argflag, str(opts[key])]
        if "crop" in opts:
            out += ["--crop"] + [str(value) for value in opts["crop"]]
        for key, argflag in (("fit_normals", "--fit-normals"),
                             ("align_normals", "--align-normals")):
            if opts.get(key):
                out.append(argflag)
        return out
    if kind == "surface_preflight":
        out = [executable, "--surface", str(input_path), "--volume", opts["volume_path"],
               "--output", str(output_path)]
        for key, flag in (("array_key", "--array-key"), ("margin", "--margin"),
                          ("max_samples", "--max-samples"),
                          ("minimum_support_fraction", "--minimum-support-fraction"),
                          ("support_threshold", "--support-threshold")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "atlas_inspect":
        out = [executable, str(input_path)]
        if "project_path" in opts:
            out.append(opts["project_path"])
        return out
    if kind == "winding_field":
        crop_keys = ("crop_x", "crop_y", "crop_width", "crop_height")
        present = [k for k in crop_keys if k in opts]
        if present and len(present) != len(crop_keys):
            raise ProviderRefusal(
                "winding_field crop requires all four of %s or none of them; got only %s" %
                (crop_keys, present))
        out = [executable, str(input_path)]
        if present:
            out += [str(int(opts[k])) for k in crop_keys]
        return out
    if kind == "atlas_pred_snap_rebuild":
        out = [executable, str(input_path), opts["project_path"]]
        for key, flag in (("lasagna_manifest", "--lasagna-manifest"), ("fiber", "--fiber"),
                          ("debug_images_dir", "--debug-images-dir")):
            if key in opts:
                out.append("%s=%s" % (flag, opts[key]))
        for key, flag in (("dry_run", "--dry-run"), ("overwrite_manual", "--overwrite-manual"),
                          ("verbose", "--verbose"), ("trace_samples", "--trace-samples")):
            if opts.get(key):
                out.append(flag)
        return out
    if kind == "tifxyz_transform":
        out = [executable, str(input_path)]
        for key, flag in (("rotate", "--rotate"), ("resample", "--resample"),
                          ("smooth", "--smooth"), ("interpolation", "--interpolation")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "seg_add_overlap":
        out = [executable, "--target", str(input_path), "--source", opts["source_path"]]
        for key, flag in (("workers", "--workers"), ("point_stride", "--point-stride")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "merge_patch":
        out = [executable, "--parent", str(input_path), "--child", opts["child_path"]]
        for key, flag in (("border_cells", "--border-cells"), ("blend_cells", "--blend-cells"),
                          ("ransac_iters", "--ransac-iters"),
                          ("ransac_min_thresh", "--ransac-min-thresh"),
                          ("ransac_max_thresh", "--ransac-max-thresh"),
                          ("ransac_mad_k", "--ransac-mad-k"), ("ransac_seed", "--ransac-seed"),
                          ("anchor_cap", "--anchor-cap"), ("idw_k", "--idw-k")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "merge_tifxyz":
        out = [executable, "--merge", str(input_path), "--paths-dir", opts["paths_dir"]]
        for key, flag in (("ref", "--ref"), ("obj2tifxyz", "--obj2tifxyz"),
                          ("ransac_iters", "--ransac-iters"),
                          ("ransac_min_thresh", "--ransac-min-thresh"),
                          ("ransac_max_thresh", "--ransac-max-thresh"),
                          ("ransac_mad_k", "--ransac-mad-k"), ("ransac_seed", "--ransac-seed"),
                          ("ransac_autogate", "--ransac-autogate"),
                          ("ransac_autogate_scale_k", "--ransac-autogate-scale-k"),
                          ("ransac_autogate_shift_frac", "--ransac-autogate-shift-frac"),
                          ("ransac_min_shift", "--ransac-min-shift"),
                          ("ransac_scale_min", "--ransac-scale-min"),
                          ("ransac_scale_max", "--ransac-scale-max"),
                          ("anchor_cap", "--anchor-cap"), ("strip_cols", "--strip-cols")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "fiber_trace_metric":
        out = [executable, str(input_path), opts["fiber_json"],
               "--normal-manifest", opts["normal_manifest"]]
        for key, flag in (
            ("remote_cache_dir", "--remote-cache-dir"),
            ("voxel_size_um", "--voxel-size-um"),
            ("inference_scaledown_power", "--inference-scaledown-power"),
            ("step_voxels", "--step-voxels"),
            ("cone_angle_degrees", "--cone-angle-degrees"),
            ("cone_angle_step_degrees", "--cone-angle-step-degrees"),
            ("cone_grid_size", "--cone-grid-size"),
            ("beam_width", "--beam-width"),
            ("beam_prune_distance_voxels", "--beam-prune-distance-voxels"),
            ("beam_lookahead_steps", "--beam-lookahead-steps"),
            ("lookahead_parent_cap", "--lookahead-parent-cap"),
            ("lookahead_retry_parent_cap", "--lookahead-retry-parent-cap"),
            ("threads", "--threads"),
            ("smoothness_weight", "--smoothness-weight"),
            ("smoothness_normal_weight", "--smoothness-normal-weight"),
            ("smoothness_tangent_weight", "--smoothness-tangent-weight"),
            ("smoothness_free_angle_degrees", "--smoothness-free-angle-degrees"),
            ("cumulative_smoothness_steps", "--cumulative-smoothness-steps"),
            ("cumulative_smoothness_tangent_weight", "--cumulative-smoothness-tangent-weight"),
            ("max_step_factor", "--max-step-factor"),
            ("error_threshold_base_voxels", "--error-threshold-base-voxels"),
            ("cache_gib", "--cache-gib")):
            if key in opts:
                out += [flag, str(opts[key])]
        if opts.get("exhaustive_lookahead"):
            out.append("--exhaustive-lookahead")
        if opts.get("quiet"):
            out.append("--quiet")
        return out
    if kind == "grow_segments":
        out = [executable, "-v", opts["volume_path"], "-s", str(input_path),
               "-t", str(output_path), "-p", opts["params_path"]]
        for key, flag in (("src_segment", "--src-segment"), ("seed_coords", "--seed-coords"),
                          ("max_width", "--max-width"), ("sweep", "--sweep"),
                          ("sweep_ranges", "--sweep-ranges"),
                          ("sweep_strategy", "--sweep-strategy"),
                          ("sweep_max_runs", "--sweep-max-runs"),
                          ("sweep_max_gen", "--sweep-max-gen"),
                          ("sweep_max_width", "--sweep-max-width"),
                          ("sweep_max_height", "--sweep-max-height"),
                          ("sweep_min_area", "--sweep-min-area"),
                          ("sweep_seed", "--sweep-seed"),
                          ("sweep_cutout_cm", "--sweep-cutout-cm"),
                          ("sweep_metric_tolerance", "--sweep-metric-tolerance"),
                          ("sweep_max_distance_samples", "--sweep-max-distance-samples")):
            if key in opts:
                out += [flag, str(opts[key])]
        return out
    if kind == "render":
        out = [executable, "-v", opts["volume_path"], "--scale", str(opts["scale"]),
               "-g", str(opts["group_idx"]), "-s", str(input_path)]
        out += [("--zarr-output" if opts["output_format"] == "zarr" else "--tif-output"),
                str(output_path)]
        for key, flag in (("cache_gb", "--cache-gb"), ("remote_url", "--remote-url"),
                          ("log_path", "--log-path"), ("timeout_m", "--timeout"),
                          ("num_slices", "--num-slices"), ("slice_step", "--slice-step"),
                          ("accum", "--accum"), ("accum_type", "--accum-type"),
                          ("crop_x", "--crop-x"), ("crop_y", "--crop-y"),
                          ("crop_width", "--crop-width"), ("crop_height", "--crop-height"),
                          ("affine", "--affine"), ("affine_invert", "--affine-invert"),
                          ("affine_transform", "--affine-transform"),
                          ("invert_affine", "--invert-affine"),
                          ("scale_segmentation", "--scale-segmentation"),
                          ("rotate", "--rotate"), ("flip", "--flip"),
                          ("zarr_compressor", "--zarr-compressor"),
                          ("zarr_compression_level", "--zarr-compression-level"),
                          ("zarr_separator", "--zarr-separator"),
                          ("flatten_iterations", "--flatten-iterations"),
                          ("flatten_downsample", "--flatten-downsample"),
                          ("alpha_min", "--alpha-min"), ("alpha_max", "--alpha-max"),
                          ("alpha_opacity", "--alpha-opacity"), ("alpha_cutoff", "--alpha-cutoff"),
                          ("bl_extinction", "--bl-extinction"), ("bl_emission", "--bl-emission"),
                          ("bl_ambient", "--bl-ambient"), ("iso_cutoff", "--iso-cutoff"),
                          ("composite_start", "--composite-start"),
                          ("composite_end", "--composite-end"), ("num_parts", "--num-parts"),
                          ("part_id", "--part-id"), ("pyramid", "--pyramid"),
                          ("voxel_size", "--voxel-size"), ("voxel_unit", "--voxel-unit")):
            if key in opts:
                out += [flag, str(opts[key])]
        for key, flag in (("prefetch_remote", "--prefetch-remote"),
                          ("auto_crop", "--auto-crop"), ("flip_normals", "--flip-normals"),
                          ("quick_tif", "--quick-tif"), ("flatten", "--flatten"),
                          ("composite_collapse", "--composite-collapse"),
                          ("merge_tiff_parts", "--merge-tiff-parts"),
                          ("resume", "--resume"), ("pre", "--pre")):
            if opts.get(key):
                out.append(flag)
        return out
    out = [executable, str(input_path), str(output_path)]
    for key, flag in (("normalize_uv", "--normalize-uv"), ("align_grid", "--align-grid"),
                      ("inpaint", "--inpaint")):
        if opts.get(key):
            out.append(flag)
    if "keep" in opts:
        out.append("--keep=%s" % float(opts["keep"]))
    if "clean" in opts:
        out.append("--clean%s" % ("=" + str(float(opts["clean"]))
                                  if opts["clean"] is not True else ""))
    return out


MAX_TREE_FILES = 20000
MAX_TREE_BYTES = 8 << 30


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _tree_manifest(path: Path, bounded: bool = True) -> list[dict]:
    """Every file under `path` (or `path` itself, if it is one file), as {path, sha256, bytes} rows -- the exact citation shape `argus.core.evidence_package._output_hashes` walks for (it recurses any..."""
    if path.is_file():
        return [{"path": path.name, "sha256": _file_sha256(path), "bytes": path.stat().st_size}]
    children = []
    total = 0
    for p in path.rglob("*"):
        try:
            if not p.is_file() or p.is_symlink():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        children.append((p, size))
        total += size
        if bounded and len(children) > MAX_TREE_FILES:
            raise ProviderRefusal("the input holds more than %d files; name a smaller directory" % MAX_TREE_FILES)
        if bounded and total > MAX_TREE_BYTES:
            raise ProviderRefusal("the input holds more than %d bytes; name a smaller directory" % MAX_TREE_BYTES)
    rows = []
    for child, size in sorted(children):
        rows.append({"path": str(child.relative_to(path)).replace("\\", "/"), "sha256": _file_sha256(child), "bytes": size})
    return rows


def _tree_fingerprint(path: Path) -> str:
    return _sha(_tree_manifest(path))


def _coerce_metric_value(value: str):
    """Keep native metric values typed while retaining non-numeric annotations."""
    value = value.strip()
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer() and "." not in value and "e" not in value.lower():
        return int(number)
    return number


def _parse_fiber_metric_stdout(stdout: str) -> list[dict]:
    """Parse only the stable final native_trace2cp_* records, preserving raw stdout separately."""
    records = []
    for raw_line in (stdout or "").replace("\r", "\n").splitlines():
        line = raw_line.strip()
        if not line.startswith("native_trace2cp_"):
            continue
        tokens = line.split()
        fields = {}
        for token in tokens[1:]:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            fields[key] = _coerce_metric_value(value)
        records.append({"event": tokens[0], "fields": fields})
    return records


def _materialize_fiber_metric_report(the_plan: dict, result, output: Path) -> None:
    """Turn the native stdout-only metric into an explicit, immutable ARGUS artifact."""
    records = _parse_fiber_metric_stdout(getattr(result, "stdout", "") or "")
    if not any(record["event"] == "native_trace2cp_fiber" for record in records):
        raise ProviderRefusal(
            "vc_fiber_trace_metric returned zero without its stable native_trace2cp_fiber record"
        )
    if output.exists():
        raise ProviderRefusal("provider output already exists while materialising metric report")
    report = {
        "schema": "argus-villa-fiber-trace-metric-v1",
        "contract": CONTRACT,
        "capability_id": the_plan["capability_id"],
        "plan_sha256": the_plan["plan_sha256"],
        "upstream_revision": the_plan.get("upstream_revision"),
        "source_binding": the_plan["source_binding"],
        "inputs": {
            "fiber_manifest": the_plan["input_path"],
            **(the_plan.get("supporting_inputs") or {}),
        },
        "input_fingerprints": {
            "fiber_manifest": the_plan["input_fingerprint"],
            **(the_plan.get("supporting_input_fingerprints") or {}),
        },
        "options": the_plan.get("options") or {},
        "native_returncode": getattr(result, "returncode", None),
        "records": records,
        "native_stdout": getattr(result, "stdout", "") or "",
        "native_stderr": getattr(result, "stderr", "") or "",
        "scientific_boundary": (
            "native trace metrics are operational diagnostics; they do not qualify a fiber, "
            "detector, geometry, or reading"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_tifxyz_for_distortion(directory: Path) -> tuple:
    """x, y, z (float64) and meta.json's declared scale for one TIFXYZ output directory."""
    import tifffile
    x = tifffile.imread(str(directory / "x.tif")).astype("float64")
    y = tifffile.imread(str(directory / "y.tif")).astype("float64")
    z = tifffile.imread(str(directory / "z.tif")).astype("float64")
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    scale = meta.get("scale")
    if not (isinstance(scale, (list, tuple)) and len(scale) >= 2):
        raise ValueError("meta.json has no two-element scale")
    return x, y, z, (float(scale[0]), float(scale[1]))


def _flatten_distortion_report(output: Path) -> dict:
    """Distortion of a REAL `vc_flatten` output, or an honest reason none is available."""
    from argus.core import surface_metric
    if not output.is_dir():
        return {"status": "UNAVAILABLE", "why": "flatten output is not a directory"}
    try:
        x, y, z, scale = _read_tifxyz_for_distortion(output)
    except Exception as exc:
        return {"status": "UNAVAILABLE",
                "why": "%s: %s" % (type(exc).__name__, exc)}
    try:
        return surface_metric.flatten_distortion_summary(x, y, z, scale)
    except Exception as exc:
        return {"status": "UNAVAILABLE",
                "why": "distortion computation failed: %s: %s" % (type(exc).__name__, exc)}


STAGED_CAPABILITIES = frozenset({
    "vc_atlas_inspect", "vc_atlas_pred_snap_rebuild", "vc_tifxyz",
    "vc_seg_add_overlap", "vc_merge_patch", "vc_merge_tifxyz",
    "vc_tifxyz_winding",
})


def _copy_artifact(source: Path, output: Path) -> None:
    """Materialise one staged artifact at the caller's declared immutable output path."""
    if output.exists():
        raise ProviderRefusal("provider output already exists while materialising staged result")
    output.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, output)
    else:
        shutil.copy2(source, output)


def _copy_directory_contents(source: Path, output: Path) -> None:
    """Copy a directory's generated contents without copying its temporary directory name."""
    if output.exists():
        raise ProviderRefusal("provider output already exists while materialising staged result")
    output.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = output / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)


def _staged_run(the_plan: dict, *, timeout_s: int):
    """Run an upstream in-place/implicit-output tool on a disposable copy."""
    capability = the_plan["capability_id"]
    source = _path(the_plan["input_path"], "input_path", must_exist=True)
    output = _path(the_plan["output_path"], "output_path", must_exist=False)
    options = the_plan.get("options") or {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="argus-provider-", dir=str(output.parent)) as temp_name:
        temp = Path(temp_name)
        staged_input = temp / source.name
        if source.is_dir():
            shutil.copytree(source, staged_input)
        else:
            shutil.copy2(source, staged_input)
        argv = list(the_plan["argv"])
        if capability in {"vc_atlas_inspect", "vc_atlas_pred_snap_rebuild", "vc_tifxyz",
                          "vc_seg_add_overlap", "vc_merge_patch", "vc_tifxyz_winding"}:
            if capability == "vc_atlas_inspect":
                argv[1] = str(staged_input)
                run_cwd = temp
            elif capability == "vc_atlas_pred_snap_rebuild":
                argv[1] = str(staged_input)
                if "--debug-images-dir=" in " ".join(argv):
                    argv = [a if not a.startswith("--debug-images-dir=") else
                            "--debug-images-dir=" + str(temp / "debug") for a in argv]
                run_cwd = temp
            elif capability == "vc_tifxyz":
                argv[1] = str(staged_input)
                run_cwd = temp
            elif capability == "vc_tifxyz_winding":
                argv[1] = str(staged_input)
                run_cwd = temp
            elif capability == "vc_seg_add_overlap":
                argv[argv.index("--target") + 1] = str(staged_input)
                run_cwd = temp
            else:
                argv[argv.index("--parent") + 1] = str(staged_input)
                run_cwd = temp
        else:
            paths_dir = _path(options["paths_dir"], "paths_dir", must_exist=True)
            staged_paths = temp / paths_dir.name
            shutil.copytree(paths_dir, staged_paths)
            argv[argv.index("--paths-dir") + 1] = str(staged_paths)
            run_cwd = temp

        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s,
                                shell=False, cwd=str(run_cwd))
        if result.returncode != 0:
            return result

        if capability == "vc_atlas_inspect":
            _copy_directory_contents(temp, output)
        elif capability == "vc_atlas_pred_snap_rebuild":
            _copy_artifact(staged_input, output)
        elif capability == "vc_tifxyz":
            candidates = [p for p in temp.iterdir() if p != staged_input and p.name != "debug"]
            if len(candidates) != 1:
                raise ProviderRefusal("vc_tifxyz did not produce exactly one transformed artifact")
            _copy_artifact(candidates[0], output)
        elif capability == "vc_tifxyz_winding":
            candidates = [p for p in temp.iterdir() if p != staged_input]
            if not candidates:
                raise ProviderRefusal("vc_tifxyz_winding produced no winding-field artifacts")
            output.mkdir(parents=True, exist_ok=True)
            for child in candidates:
                shutil.copy2(child, output / child.name)
        elif capability in {"vc_seg_add_overlap", "vc_merge_patch"}:
            _copy_artifact(staged_input, output)
        else:
            candidates = [p for p in staged_paths.iterdir()
                          if p.name.lower().endswith("_merged") or "_merged_v" in p.name]
            if len(candidates) != 1:
                raise ProviderRefusal("vc_merge_tifxyz did not produce exactly one merged artifact")
            _copy_artifact(candidates[0], output)
        return result


def plan(*, capability_id: str, input_path, output_path, source_binding: dict,
         options: dict | None = None) -> dict:
    """Pure command plan."""
    if capability_id == COPY_OUT_IN_CAPABILITY_ID:
        raise ProviderRefusal(
            "%s is the classical Copy Out/In tool; use plan_copy_out_in()/run_copy_out_in(), "
            "not plan()/run()" % capability_id)
    row = _entry(capability_id)
    kind = COMMANDS[capability_id]
    source = _path(input_path, "input_path", must_exist=True)
    output = _path(output_path, "output_path", must_exist=False)
    if source == output:
        raise ProviderRefusal("provider output may not overwrite its input")
    if output.exists():
        raise ProviderRefusal("provider output already exists; choose a new immutable output path")
    if not isinstance(source_binding, dict):
        raise ProviderRefusal("source_binding is required and must be an object")
    missing = [key for key in ("physical_scroll", "volume_id", "acquisition_id")
               if source_binding.get(key) in (None, "")]
    if missing:
        raise ProviderRefusal("source_binding is missing %s" % missing)
    opts = _options(kind, options)
    supporting_inputs = {}
    if kind == "fiber_trace_metric":
        output_suffix = output.suffix.lower()
        if output_suffix != ".json":
            raise ProviderRefusal("fiber_trace_metric output_path must end in .json")
        supporting_inputs = {
            "fiber_json": opts["fiber_json"],
            "normal_manifest": opts["normal_manifest"],
        }
    argv = _argv(kind, row["executable"], source, output, opts)
    body = {
        "schema": "argus-villa-provider-plan-v1", "contract": CONTRACT,
        "capability_id": capability_id, "kind": kind, "upstream_revision": row.get("upstream_commit"),
        "executable": row["executable"], "input_path": str(source), "output_path": str(output),
        "input_fingerprint": _tree_fingerprint(source), "source_binding": dict(source_binding),
        "options": opts, "argv": argv,
        "controls": ["immutable upstream ledger entry", "typed argv", "input/output separation",
                      "source binding", "nonzero exit refusal", "output existence check"],
        "scientific_boundary": "successful invocation is an operational artifact, not a qualified geometry or reading",
    }
    if supporting_inputs:
        body["supporting_inputs"] = supporting_inputs
        body["supporting_input_fingerprints"] = {
            key: _tree_fingerprint(Path(value)) for key, value in supporting_inputs.items()
        }
        body["controls"].extend(["multi-input identity binding", "native stdout retained verbatim",
                                  "stable metric records required before output admission"])
    if capability_id in STAGED_CAPABILITIES:
        body["staging"] = {
            "mode": "copy_on_write",
            "source_preserved": True,
            "native_output_boundary": "explicit ARGUS output_path",
            "implicit_outputs_admitted": capability_id in {"vc_atlas_inspect", "vc_tifxyz",
                                                             "vc_merge_tifxyz",
                                                             "vc_tifxyz_winding"},
        }
        body["controls"].extend(["disposable staging copy", "no native in-place source mutation",
                                  "explicit materialization after zero exit"])
    body["plan_sha256"] = _sha(body)
    return body


def run(the_plan: dict, *, runner=None, receipt_path=None, timeout_s: int = 7200) -> dict:
    """Run one approved plan and write a differential receipt, without shell interpretation."""
    if not isinstance(the_plan, dict) or the_plan.get("schema") != "argus-villa-provider-plan-v1":
        raise ProviderRefusal("an ARGUS Villa provider plan is required")
    expected = _sha({k: v for k, v in the_plan.items() if k != "plan_sha256"})
    if expected != the_plan.get("plan_sha256"):
        raise ProviderRefusal("provider plan hash does not re-derive")
    source = _path(the_plan.get("input_path"), "input_path", must_exist=True)
    output = _path(the_plan.get("output_path"), "output_path", must_exist=False)
    if output.exists() or source == output:
        raise ProviderRefusal("provider output path is no longer unused")
    if _tree_fingerprint(source) != the_plan.get("input_fingerprint"):
        raise ProviderRefusal("input changed after planning; re-plan before invocation")
    for key, expected in (the_plan.get("supporting_input_fingerprints") or {}).items():
        path_value = (the_plan.get("supporting_inputs") or {}).get(key)
        supporting = _path(path_value, key, must_exist=True)
        if _tree_fingerprint(supporting) != expected:
            raise ProviderRefusal("%s changed after planning; re-plan before invocation" % key)
    if receipt_path is None:
        receipt_path = paths.science_data("provider_runs", the_plan["plan_sha256"] + ".json")
    rp = paths.assert_writable(receipt_path)
    if rp.exists():
        raise ProviderRefusal("provider receipt already exists; receipts are append-only")

    def write_receipt(receipt: dict) -> str:
        rp.parent.mkdir(parents=True, exist_ok=True)
        if rp.exists():
            raise ProviderRefusal("provider receipt already exists; receipts are append-only")
        rp.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n",
                      encoding="utf-8")
        return str(rp)

    fn = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True,
                                                 timeout=timeout_s, shell=False))
    started = time.time()
    try:
        if runner is None and the_plan["capability_id"] in STAGED_CAPABILITIES:
            result = _staged_run(the_plan, timeout_s=timeout_s)
        else:
            result = fn(list(the_plan["argv"]))
    except Exception as exc:
        receipt = {"schema": "argus-villa-provider-receipt-v1", "contract": CONTRACT,
                   "plan_sha256": the_plan["plan_sha256"],
                   "capability_id": the_plan["capability_id"],
                   "upstream_revision": the_plan.get("upstream_revision"),
                   "argv": list(the_plan["argv"]), "source_binding": the_plan["source_binding"],
                   "input_fingerprint": the_plan["input_fingerprint"],
                   "supporting_inputs": the_plan.get("supporting_inputs", {}),
                   "supporting_input_fingerprints": the_plan.get("supporting_input_fingerprints", {}),
                   "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                   "duration_s": round(time.time() - started, 3), "status": "REFUSED",
                   "error": "%s: %s" % (type(exc).__name__, exc),
                   "output_path": str(output), "output_exists": output.exists(),
                   "scientific_boundary": the_plan["scientific_boundary"]}
        write_receipt(receipt)
        raise ProviderRefusal("provider process failed: %s: %s; receipt: %s"
                              % (type(exc).__name__, exc, rp)) from None
    receipt = {
        "schema": "argus-villa-provider-receipt-v1", "contract": CONTRACT,
        "plan_sha256": the_plan["plan_sha256"], "capability_id": the_plan["capability_id"],
        "upstream_revision": the_plan.get("upstream_revision"), "argv": list(the_plan["argv"]),
        "source_binding": the_plan["source_binding"], "input_fingerprint": the_plan["input_fingerprint"],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "duration_s": round(time.time() - started, 3), "returncode": getattr(result, "returncode", None),
        "stdout_tail": (getattr(result, "stdout", "") or "")[-2000:],
        "stderr_tail": (getattr(result, "stderr", "") or "")[-2000:],
        "output_path": str(output), "output_exists": output.exists(),
        "supporting_inputs": the_plan.get("supporting_inputs", {}),
        "supporting_input_fingerprints": the_plan.get("supporting_input_fingerprints", {}),
        "scientific_boundary": the_plan["scientific_boundary"],
        "physical_scroll": the_plan["source_binding"].get("physical_scroll"),
        "acquisition_id": the_plan["source_binding"].get("acquisition_id"),
        "provider": the_plan["capability_id"],
        "provider_revision": the_plan.get("upstream_revision"),
    }
    if receipt["returncode"] != 0:
        receipt["status"] = "REFUSED"
        write_receipt(receipt)
        raise ProviderRefusal("provider returned %s; output was not admitted; receipt: %s"
                              % (receipt["returncode"], rp))
    if the_plan["capability_id"] == "vc_fiber_trace_metric":
        try:
            _materialize_fiber_metric_report(the_plan, result, output)
        except Exception as exc:
            receipt["status"] = "REFUSED"
            receipt["error"] = "%s: %s" % (type(exc).__name__, exc)
            receipt["output_exists"] = output.exists()
            write_receipt(receipt)
            raise ProviderRefusal("fiber metric output was not admitted: %s; receipt: %s"
                                  % (exc, rp)) from None
    if not output.exists():
        receipt["status"] = "REFUSED"
        write_receipt(receipt)
        raise ProviderRefusal("provider returned zero but produced no declared output; receipt: %s"
                              % rp)
    if the_plan["capability_id"] == "vc_flatten":
        receipt["flatten_distortion"] = _flatten_distortion_report(output)
    output_files = _tree_manifest(output, bounded=False)
    receipt["output_fingerprint"] = _sha(output_files)
    receipt["outputs"] = {"path": str(output), "files": output_files}
    receipt["status"] = "OK"
    write_receipt(receipt)
    return {"status": "OK", "receipt": receipt, "receipt_path": str(rp)}



_COPY_OUT_IN_PASS1_ALLOWED = {"neighbor_max_distance", "neighbor_min_clearance", "neighbor_fill",
                              "neighbor_interp_window", "generations", "neighbor_spike_window"}
_COPY_OUT_IN_PASS2_ALLOWED = {"resume_local_opt_step", "resume_local_opt_radius",
                              "resume_local_max_iters", "resume_local_dense_qr"}
_COPY_OUT_IN_BOOL_KEYS = {"neighbor_fill", "resume_local_dense_qr"}


def _require_review(review) -> dict:
    """Refuse an invented \"reviewed\" status: require an explicit, attributable attestation."""
    if not isinstance(review, dict):
        raise ProviderRefusal("review is required and must be an object")
    missing = [key for key in ("reviewer", "reviewed_utc", "finding")
               if not str(review.get(key) or "").strip()]
    if missing:
        raise ProviderRefusal(
            "review is missing %s; Copy Out/In requires an explicit, attributable review of the "
            "source wrap (who reviewed it, when, and what they found) before it may be planned"
            % missing)
    return dict(review)


def _validate_copy_out_in_options(kind: str, opts: dict, allowed: set) -> dict:
    opts = dict(opts or {})
    unknown = sorted(set(opts) - allowed)
    if unknown:
        raise ProviderRefusal("unknown %s option(s): %s" % (kind, unknown))
    for key, value in list(opts.items()):
        if key in _COPY_OUT_IN_BOOL_KEYS:
            if not isinstance(value, bool):
                raise ProviderRefusal("%s must be a boolean" % key)
            continue
        if isinstance(value, bool):
            raise ProviderRefusal("%s must be numeric" % key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ProviderRefusal("%s must be numeric" % key) from None
        if not math.isfinite(number) or number < 0:
            raise ProviderRefusal("%s must be finite and non-negative" % key)
        opts[key] = number
    return opts


def plan_copy_out_in(*, source_tifxyz_path, volume_path, normal_grid_path, direction: str,
                     output_dir, source_binding: dict, review: dict,
                     pass1_options: dict | None = None,
                     pass2_options: dict | None = None) -> dict:
    """Typed plan for the classical (non-neural) VC3D Copy Out/In tool."""
    row = _entry(COPY_OUT_IN_CAPABILITY_ID)
    if direction not in NEIGHBOR_COPY_DIRECTIONS:
        raise ProviderRefusal("direction must be 'out' or 'in'")
    source = _path(source_tifxyz_path, "source_tifxyz_path", must_exist=True)
    for name in ("x.tif", "y.tif", "z.tif", "meta.json"):
        if not (source / name).is_file():
            raise ProviderRefusal(
                "source_tifxyz_path is missing %s; not a well-formed TIFXYZ wrap" % name)
    _volume_str = str(volume_path)
    if _volume_str.lower().startswith(("s3://", "http://", "https://")):
        volume = _volume_str
    else:
        volume = _path(volume_path, "volume_path", must_exist=True)
    normal_grid = _path(normal_grid_path, "normal_grid_path", must_exist=True)
    out_dir = _path(output_dir, "output_dir", must_exist=False)
    if out_dir == source:
        raise ProviderRefusal("output_dir may not equal the source wrap's own path")
    try:
        out_dir.relative_to(source)
    except ValueError:
        pass
    else:
        raise ProviderRefusal("output_dir may not be nested inside the source wrap")
    try:
        source.relative_to(out_dir)
    except ValueError:
        pass
    else:
        raise ProviderRefusal("output_dir may not contain the source wrap")
    if not isinstance(source_binding, dict):
        raise ProviderRefusal("source_binding is required and must be an object")
    missing = [key for key in ("physical_scroll", "volume_id", "acquisition_id")
               if source_binding.get(key) in (None, "")]
    if missing:
        raise ProviderRefusal("source_binding is missing %s" % missing)
    review = _require_review(review)

    p1 = _validate_copy_out_in_options("pass1", pass1_options, _COPY_OUT_IN_PASS1_ALLOWED)
    p2 = _validate_copy_out_in_options("pass2", pass2_options, _COPY_OUT_IN_PASS2_ALLOWED)

    pass1_params = {
        "mode": "gen_neighbor",
        "normal_grid_path": str(normal_grid),
        "neighbor_dir": direction,
        "neighbor_max_distance": p1.get("neighbor_max_distance", 40),
        "neighbor_min_clearance": p1.get("neighbor_min_clearance", 4),
        "neighbor_fill": bool(p1.get("neighbor_fill", True)),
        "neighbor_interp_window": p1.get("neighbor_interp_window", 5),
        "generations": p1.get("generations", 100),
        "neighbor_spike_window": p1.get("neighbor_spike_window", 5),
    }
    pass2_params = {
        "normal_grid_path": str(normal_grid),
        "max_gen": 1,
        "generations": 1,
        "resume_local_opt_step": p2.get("resume_local_opt_step", 20),
        "resume_local_opt_radius": p2.get("resume_local_opt_radius", 40),
        "resume_local_max_iters": p2.get("resume_local_max_iters", 1000),
        "resume_local_dense_qr": bool(p2.get("resume_local_dense_qr", False)),
    }
    directory_prefix = "neighbor_out_" if direction == "out" else "neighbor_in_"
    body = {
        "schema": "argus-villa-copy-out-in-plan-v1", "contract": CONTRACT,
        "capability_id": COPY_OUT_IN_CAPABILITY_ID, "kind": "neighbor_copy",
        "upstream_revision": row.get("upstream_commit"), "executable": row["executable"],
        "direction": direction, "directory_prefix": directory_prefix,
        "source_path": str(source), "volume_path": str(volume),
        "normal_grid_path": str(normal_grid), "output_dir": str(out_dir),
        "source_binding": dict(source_binding), "review": review,
        "source_fingerprint": _tree_fingerprint(source),
        "pass1_params": pass1_params, "pass2_params": pass2_params,
        "controls": [
            "immutable upstream ledger entry (shares vc_grow_seg_from_seed's ledger gate)",
            "explicit attributable review of the source wrap required before planning",
            "source binding",
            "output_dir refused if it equals, nests in, or contains the source path",
            "source fingerprint re-checked unchanged after both native passes",
            "candidate materialised at a separate, natively-named new directory only",
            "genuine geometric propagation-error detector run against source vs candidate",
        ],
        "scientific_boundary": (
            "a successful Copy Out/In run is an operational candidate mesh, not a qualified "
            "reading; a clean propagation report does not certify the candidate defect-free, only "
            "that no local roughness outlier in it co-locates with one already flagged in the "
            "reviewed source"
        ),
    }
    body["plan_sha256"] = _sha(body)
    return body


def _copy_out_in_propagation_report(source_dir: Path, candidate_dir: Path) -> dict:
    """Real geometric propagation-error check between one finished source/candidate pair."""
    sx, sy, sz, _ = _read_tifxyz_for_distortion(source_dir)
    cx, cy, cz, _ = _read_tifxyz_for_distortion(candidate_dir)
    report = defect_control.detect_propagated_defects((sx, sy, sz), (cx, cy, cz))
    report["status"] = "MEASURED"
    return report


def run_copy_out_in(the_plan: dict, *, runner=None, receipt_path=None,
                    timeout_s: int = 7200) -> dict:
    """Run one approved Copy Out/In plan (both native passes) and write a receipt."""
    if not isinstance(the_plan, dict) or the_plan.get("schema") != "argus-villa-copy-out-in-plan-v1":
        raise ProviderRefusal("an ARGUS Copy Out/In plan is required")
    expected = _sha({k: v for k, v in the_plan.items() if k != "plan_sha256"})
    if expected != the_plan.get("plan_sha256"):
        raise ProviderRefusal("copy-out-in plan hash does not re-derive")
    source = _path(the_plan["source_path"], "source_path", must_exist=True)
    out_dir = _path(the_plan["output_dir"], "output_dir", must_exist=False)
    if _tree_fingerprint(source) != the_plan["source_fingerprint"]:
        raise ProviderRefusal("source wrap changed after planning; re-plan before invocation")
    row = _entry(the_plan["capability_id"])
    executable = row["executable"]

    out_dir.mkdir(parents=True, exist_ok=True)
    baseline = {p.name for p in out_dir.iterdir() if p.is_dir()}

    if receipt_path is None:
        receipt_path = paths.science_data(
            "provider_runs", the_plan["plan_sha256"] + "_copy_out_in.json")
    rp = paths.assert_writable(receipt_path)
    if rp.exists():
        raise ProviderRefusal("copy-out-in receipt already exists; receipts are append-only")

    def write_receipt(receipt: dict) -> str:
        rp.parent.mkdir(parents=True, exist_ok=True)
        if rp.exists():
            raise ProviderRefusal("copy-out-in receipt already exists; receipts are append-only")
        rp.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str) + "\n",
                      encoding="utf-8")
        return str(rp)

    fn = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True,
                                                 timeout=timeout_s, shell=False))
    started = time.time()

    def base_receipt() -> dict:
        return {
            "schema": "argus-villa-copy-out-in-receipt-v1", "contract": CONTRACT,
            "plan_sha256": the_plan["plan_sha256"], "capability_id": the_plan["capability_id"],
            "direction": the_plan["direction"], "source_binding": the_plan["source_binding"],
            "review": the_plan["review"], "source_path": str(source),
            "source_fingerprint_before": the_plan["source_fingerprint"],
            "output_dir": str(out_dir),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            "scientific_boundary": the_plan["scientific_boundary"],
        }

    short = the_plan["plan_sha256"][:12]
    pass1_params_path = out_dir / (".argus_copy_out_in_pass1_%s.json" % short)
    pass1_params_path.write_text(json.dumps(the_plan["pass1_params"], indent=2), encoding="utf-8")
    pass1_argv = [executable, "-v", the_plan["volume_path"], "-p", str(pass1_params_path),
                  "--resume", str(source), "-t", str(out_dir), "--resume-opt", "skip"]

    try:
        pass1_result = fn(pass1_argv)
    except Exception as exc:
        receipt = base_receipt()
        receipt.update({"stage": "pass1", "argv_pass1": pass1_argv, "status": "REFUSED",
                        "error": "%s: %s" % (type(exc).__name__, exc),
                        "duration_s": round(time.time() - started, 3)})
        write_receipt(receipt)
        raise ProviderRefusal("copy-out-in pass 1 failed: %s: %s; receipt: %s"
                              % (type(exc).__name__, exc, rp)) from None

    if _tree_fingerprint(source) != the_plan["source_fingerprint"]:
        receipt = base_receipt()
        receipt.update({
            "stage": "pass1", "argv_pass1": pass1_argv,
            "returncode_pass1": getattr(pass1_result, "returncode", None),
            "status": "REFUSED",
            "error": "source wrap was mutated by pass 1; refusing regardless of exit code (known "
                     "vc_grow_seg_from_seed data-loss defect class)",
            "source_still_exists": source.exists(),
            "source_fingerprint_after_pass1": (_tree_fingerprint(source)
                                               if source.exists() else None),
            "duration_s": round(time.time() - started, 3),
        })
        write_receipt(receipt)
        raise ProviderRefusal("Copy Out/In mutated its source wrap in pass 1; refused and "
                              "receipted: %s" % rp)

    if getattr(pass1_result, "returncode", None) != 0:
        receipt = base_receipt()
        receipt.update({
            "stage": "pass1", "argv_pass1": pass1_argv,
            "returncode_pass1": getattr(pass1_result, "returncode", None),
            "stdout_pass1_tail": (getattr(pass1_result, "stdout", "") or "")[-2000:],
            "stderr_pass1_tail": (getattr(pass1_result, "stderr", "") or "")[-2000:],
            "status": "REFUSED", "duration_s": round(time.time() - started, 3),
        })
        write_receipt(receipt)
        raise ProviderRefusal("copy-out-in pass 1 returned %s; receipt: %s"
                              % (receipt["returncode_pass1"], rp))

    current = {p.name for p in out_dir.iterdir() if p.is_dir()}
    new_dirs = sorted(name for name in (current - baseline)
                      if name.startswith(the_plan["directory_prefix"]))
    if len(new_dirs) != 1:
        receipt = base_receipt()
        receipt.update({
            "stage": "pass1", "argv_pass1": pass1_argv,
            "returncode_pass1": getattr(pass1_result, "returncode", None),
            "new_directories": sorted(current - baseline), "status": "REFUSED",
            "error": "pass 1 did not create exactly one new %s* directory"
                     % the_plan["directory_prefix"],
            "duration_s": round(time.time() - started, 3),
        })
        write_receipt(receipt)
        raise ProviderRefusal("copy-out-in pass 1 candidate output is ambiguous; receipt: %s" % rp)

    candidate = out_dir / new_dirs[0]

    pass2_params_path = out_dir / (".argus_copy_out_in_pass2_%s.json" % short)
    pass2_params_path.write_text(json.dumps(the_plan["pass2_params"], indent=2), encoding="utf-8")
    pass2_argv = [executable, "-v", the_plan["volume_path"], "-p", str(pass2_params_path),
                  "--resume", str(candidate), "-t", str(out_dir), "--resume-opt", "local"]

    try:
        pass2_result = fn(pass2_argv)
    except Exception as exc:
        receipt = base_receipt()
        receipt.update({"stage": "pass2", "argv_pass1": pass1_argv, "argv_pass2": pass2_argv,
                        "candidate_path": str(candidate), "status": "REFUSED",
                        "error": "%s: %s" % (type(exc).__name__, exc),
                        "duration_s": round(time.time() - started, 3)})
        write_receipt(receipt)
        raise ProviderRefusal("copy-out-in pass 2 failed: %s: %s; receipt: %s"
                              % (type(exc).__name__, exc, rp)) from None

    if _tree_fingerprint(source) != the_plan["source_fingerprint"]:
        receipt = base_receipt()
        receipt.update({
            "stage": "pass2", "argv_pass1": pass1_argv, "argv_pass2": pass2_argv,
            "candidate_path": str(candidate), "status": "REFUSED",
            "error": "source wrap was mutated by pass 2; refusing regardless of exit code",
            "source_still_exists": source.exists(),
            "source_fingerprint_after_pass2": (_tree_fingerprint(source)
                                               if source.exists() else None),
            "duration_s": round(time.time() - started, 3),
        })
        write_receipt(receipt)
        raise ProviderRefusal("Copy Out/In mutated its source wrap in pass 2; refused and "
                              "receipted: %s" % rp)

    if getattr(pass2_result, "returncode", None) != 0:
        receipt = base_receipt()
        receipt.update({
            "stage": "pass2", "argv_pass1": pass1_argv, "argv_pass2": pass2_argv,
            "candidate_path": str(candidate),
            "returncode_pass2": getattr(pass2_result, "returncode", None),
            "stdout_pass2_tail": (getattr(pass2_result, "stdout", "") or "")[-2000:],
            "stderr_pass2_tail": (getattr(pass2_result, "stderr", "") or "")[-2000:],
            "status": "REFUSED", "duration_s": round(time.time() - started, 3),
        })
        write_receipt(receipt)
        raise ProviderRefusal("copy-out-in pass 2 returned %s; receipt: %s"
                              % (receipt["returncode_pass2"], rp))

    receipt = base_receipt()
    receipt.update({
        "stage": "complete", "argv_pass1": pass1_argv, "argv_pass2": pass2_argv,
        "returncode_pass1": getattr(pass1_result, "returncode", None),
        "returncode_pass2": getattr(pass2_result, "returncode", None),
        "candidate_path": str(candidate),
        "source_fingerprint_after": _tree_fingerprint(source),
        "candidate_fingerprint": _tree_fingerprint(candidate),
        "duration_s": round(time.time() - started, 3),
    })
    try:
        receipt["propagation_report"] = _copy_out_in_propagation_report(source, candidate)
    except Exception as exc:
        receipt["propagation_report"] = {"status": "UNAVAILABLE",
                                         "why": "%s: %s" % (type(exc).__name__, exc)}
    receipt["status"] = "OK"
    write_receipt(receipt)
    return {"status": "OK", "candidate_path": str(candidate), "receipt": receipt,
           "receipt_path": str(rp)}
