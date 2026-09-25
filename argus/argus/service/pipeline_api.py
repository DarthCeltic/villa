"""Read-only endpoint for the single cross-stage ARGUS pipeline plan."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/pipeline/plan")
async def pipeline_plan(scroll: str | None = None):
    from argus.core import pipeline_plan

    return await asyncio.to_thread(pipeline_plan.build, scroll)


@router.get("/api/material/readiness")
async def material_readiness(scroll: str):
    from argus.core import material_readiness as readiness

    return await asyncio.to_thread(readiness.build, scroll)


@router.get("/api/villa/science")
async def villa_science():
    from argus.core import villa_science_registry

    return await asyncio.to_thread(villa_science_registry.inventory)


@router.get("/api/science/candidates")
async def science_candidates(scroll: str | None = None):
    from argus.core import science_candidates as candidates

    return await asyncio.to_thread(candidates.inventory, scroll)
