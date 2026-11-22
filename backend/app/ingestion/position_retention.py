"""Hard retention policy for raw Position rows (REQUIREMENTS.md 3.5, added
2026-10-19). Per Gerry: a hard cutoff, 30 days initially, "could decrease to 14
days" -- no carve-outs (e.g. for a missing-aircraft incident) were asked for, so
this deletes unconditionally past the cutoff, same as stated. `Position` had no
pruning at all before this and was already 23,129 rows / 19MB after ~6 weeks with
only adsb.lol polling -- this stops unbounded growth before it's a real problem.

A plain bulk `DELETE ... WHERE ts_utc < cutoff` is safe here: confirmed live
(grep) that nothing else has a foreign key into positions.id, so there's no
cascade/orphan concern to design around.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from app.config import settings
from app.db import SessionLocal
from app.models import Position

logger = logging.getLogger(__name__)


async def prune_once() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.position_retention_days)
    async with SessionLocal() as session:
        result = await session.execute(delete(Position).where(Position.ts_utc < cutoff))
        await session.commit()
        return result.rowcount or 0


async def run_position_retention_loop(interval_seconds: int | None = None) -> None:
    interval = interval_seconds if interval_seconds is not None else settings.position_retention_check_interval_seconds
    while True:
        try:
            deleted = await prune_once()
            if deleted:
                logger.info(
                    "Position retention: pruned %d row(s) older than %d days", deleted, settings.position_retention_days
                )
        except Exception:
            logger.exception("Position retention pass failed")
        await asyncio.sleep(interval)
