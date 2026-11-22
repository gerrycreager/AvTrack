"""One-time grant: sets User.is_admin=true for a given CAP ID (REQUIREMENTS.md
3.7 follow-up, added 2026-11-02, self-service account requests). Nobody starts
as an admin after the is_admin migration runs -- this is how the first one
gets set, same standalone-script-not-migration reasoning as
scripts/backfill_legacy_mission.py (needs a real, already-created User row).

Idempotent: safe to re-run against an account that's already an admin.

Usage:
    cd backend && source .venv/bin/activate
    python ../scripts/grant_admin.py <cap_id>
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User


async def main(cap_id: str) -> None:
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.cap_id == cap_id))).scalar_one_or_none()
        if user is None:
            print(f"No account with CAP ID {cap_id}.")
            return
        if user.is_admin:
            print(f"{user.display_name} (CAP ID {cap_id}) is already an admin. No change.")
            return
        user.is_admin = True
        await session.commit()
        print(f"{user.display_name} (CAP ID {cap_id}) is now an admin.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python grant_admin.py <cap_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
