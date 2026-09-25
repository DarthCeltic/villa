"""Read-only First Letters / Grand Prize route boards, derived from live records."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

router = APIRouter()

ROUTES = ("FIRST_LETTERS", "GRAND_PRIZE")


_PUBLIC_KEYS = ("contract", "read_only", "readable", "why_unreadable", "boards", "controls",
                "generated_utc")


def _load() -> dict:
    from argus.core import prize_boards
    try:
        doc = prize_boards.route_boards()
    except Exception as exc:
        return {"contract": prize_boards.ROUTE_BOARDS_CONTRACT, "read_only": True,
                "readable": False, "why_unreadable": type(exc).__name__, "boards": {}}
    out = {k: doc[k] for k in _PUBLIC_KEYS if k in doc}
    out["claim_ceiling"] = "MECHANICS_ONLY: no scientific claim is made by this build"
    return out


@router.get("/api/route_boards")
async def route_boards():
    return await asyncio.to_thread(_load)


@router.get("/api/route_boards/{route}")
async def route_board(route: str):
    key = str(route or "").upper()
    if key not in ROUTES:
        raise HTTPException(status_code=404, detail="route must be one of %s" % (ROUTES,))
    doc = await asyncio.to_thread(_load)
    board = (doc.get("boards") or {}).get(key)
    return {"contract": doc.get("contract"), "read_only": True, "route": key, "board": board,
            "controls": doc.get("controls"),
            "claim_ceiling": doc.get("claim_ceiling")}
