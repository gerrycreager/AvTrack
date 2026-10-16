"""One-time data backfill: attaches everything that predates Mission as a real
entity (the live OperationsArea, the active Aircraft roster, existing
Sorties/GisLayers) to a single "Legacy" Mission, so nothing Gerry was already
using goes missing or breaks now that the app is mission-scoped (REQUIREMENTS.md
3.7, per-user/per-mission work, added 2026-10-16).

Deliberately a standalone script, not an Alembic migration -- per ALEMBIC.md,
"this is separate from the data-import scripts... don't confuse 'run a new NASR
cycle' with 'run a migration'." It also can't be a migration for a structural
reason: it needs a real User row to own the Mission, and that user has to
create their own account first (Claude doesn't set a human's password) -- schema
migrations shouldn't depend on that kind of runtime/operational ordering.

Idempotent: safe to re-run. Finds-or-creates a Mission named "Legacy" owned by
the given CAP ID, then only touches rows that are still unscoped (mission_id
IS NULL, or not yet linked in MissionAircraft) -- a second run is a no-op.

Usage:
    cd backend && source .venv/bin/activate
    python ../scripts/backfill_legacy_mission.py <owner_cap_id>
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Aircraft, GisLayer, Mission, MissionAircraft, MissionMembership, OperationsArea, Sortie, User

LEGACY_MISSION_NAME = "Legacy"


async def main(owner_cap_id: str) -> None:
    async with SessionLocal() as session:
        owner = (await session.execute(select(User).where(User.cap_id == owner_cap_id))).scalar_one_or_none()
        if owner is None:
            print(f"No account exists for CAP ID {owner_cap_id!r} -- create it first with scripts/create_user.py.")
            return

        mission = (
            await session.execute(
                select(Mission).where(Mission.name == LEGACY_MISSION_NAME, Mission.created_by == owner.id)
            )
        ).scalar_one_or_none()
        if mission is None:
            mission = Mission(name=LEGACY_MISSION_NAME, created_by=owner.id)
            session.add(mission)
            await session.flush()
            print(f"Created Legacy mission {mission.id}")
        else:
            print(f"Reusing existing Legacy mission {mission.id}")

        membership = (
            await session.execute(
                select(MissionMembership).where(
                    MissionMembership.mission_id == mission.id, MissionMembership.user_id == owner.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            session.add(MissionMembership(mission_id=mission.id, user_id=owner.id))
            print(f"Added {owner.display_name} as a member")

        orphan_aos = (await session.execute(select(OperationsArea).where(OperationsArea.mission_id.is_(None)))).scalars().all()
        for ao in orphan_aos:
            ao.mission_id = mission.id
        print(f"Attached {len(orphan_aos)} orphan OperationsArea row(s)")

        already_linked = {
            m.aircraft_id
            for m in (
                await session.execute(select(MissionAircraft).where(MissionAircraft.mission_id == mission.id))
            ).scalars()
        }
        active_aircraft = (await session.execute(select(Aircraft).where(Aircraft.active.is_(True)))).scalars().all()
        linked_count = 0
        for aircraft in active_aircraft:
            if aircraft.id not in already_linked:
                session.add(MissionAircraft(mission_id=mission.id, aircraft_id=aircraft.id))
                linked_count += 1
        print(f"Linked {linked_count} active aircraft (of {len(active_aircraft)} total active) into the mission roster")

        orphan_sorties = (await session.execute(select(Sortie).where(Sortie.mission_id.is_(None)))).scalars().all()
        for sortie in orphan_sorties:
            sortie.mission_id = mission.id
        print(f"Attached {len(orphan_sorties)} orphan Sortie row(s)")

        orphan_layers = (await session.execute(select(GisLayer).where(GisLayer.mission_id.is_(None)))).scalars().all()
        for layer in orphan_layers:
            layer.mission_id = mission.id
        print(f"Attached {len(orphan_layers)} orphan GisLayer row(s)")

        await session.commit()
        print(f"\nDone. Legacy mission id: {mission.id}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: backfill_legacy_mission.py <owner_cap_id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
