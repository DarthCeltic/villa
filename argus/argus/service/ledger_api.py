"""Read-only ledger status: the historical v1 chain and the current v2 chain, as two facts."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/ledger/status")
async def ledger_status():
    from argus.core import ledger_v2

    def _load():
        try:
            return dict(ledger_v2.status(), readable=True, why_unreadable=None)
        except Exception as exc:
            return {"schema": ledger_v2.STATUS_SCHEMA, "readable": False,
                    "why_unreadable": type(exc).__name__,
                    "historical": {"state": ledger_v2.HISTORICAL_CHAIN_UNREADABLE},
                    "current": {"state": ledger_v2.CURRENT_CHAIN_UNKNOWN,
                                "reason": "status could not be computed"},
                    "certification": {"historical_records": "SUPPRESSED",
                                      "new_records": "SUPPRESSED"},
                    "read_only": True}

    return await asyncio.to_thread(_load)
