"""Mission: the real, first-class scoping entity for a common operating picture
shared by IC/Ops/AOBD/COMMS (REQUIREMENTS.md, added 2026-10-16 per Gerry: "those
groups need to be able to see a particular mission(s)"). Any logged-in user can
create a mission and add members by CAP ID -- per Gerry, "Any of those four
areas can set up a Mission and enter sortie information." Role is descriptive
only, never checked for authorization -- see MissionMembership's docstring in
models.py, and app/auth.py's require_mission_member for the actual check."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_mission_member
from app.db import get_session
from app.models import Mission, MissionMembership, User
from app.schemas import MissionCreateIn, MissionMemberIn, MissionMemberOut, MissionOut

router = APIRouter(prefix="/api/missions", tags=["missions"])


def _to_mission_out(mission: Mission, role: str | None) -> MissionOut:
    return MissionOut(
        id=mission.id,
        name=mission.name,
        mission_number=mission.mission_number,
        created_by=mission.created_by,
        created_at=mission.created_at,
        closed_at=mission.closed_at,
        role=role,
    )


@router.get("/mine", response_model=list[MissionOut])
async def list_mine(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    memberships = (
        await session.execute(select(MissionMembership).where(MissionMembership.user_id == user.id))
    ).scalars().all()
    out = []
    for m in memberships:
        mission = await session.get(Mission, m.mission_id)
        if mission is not None:
            out.append(_to_mission_out(mission, m.role))
    return out


@router.post("", response_model=MissionOut)
async def create_mission(
    body: MissionCreateIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    mission = Mission(name=body.name, mission_number=body.mission_number, created_by=user.id)
    session.add(mission)
    await session.flush()
    session.add(MissionMembership(mission_id=mission.id, user_id=user.id, role=body.role))
    await session.commit()
    return _to_mission_out(mission, body.role)


@router.get("/{mission_id}/members", response_model=list[MissionMemberOut])
async def list_members(
    mission_id: UUID, user: User = Depends(require_mission_member()), session: AsyncSession = Depends(get_session)
):
    rows = (
        await session.execute(select(MissionMembership).where(MissionMembership.mission_id == mission_id))
    ).scalars().all()
    out = []
    for r in rows:
        member = await session.get(User, r.user_id)
        if member is not None:
            out.append(MissionMemberOut(cap_id=member.cap_id, display_name=member.display_name, role=r.role))
    return out


@router.post("/{mission_id}/members", response_model=list[MissionMemberOut])
async def add_member(
    mission_id: UUID,
    body: MissionMemberIn,
    user: User = Depends(require_mission_member()),
    session: AsyncSession = Depends(get_session),
):
    """Any existing member can add another -- per Gerry, no role restricts who
    can do this. 404 if that CAP ID has no account yet (no invite-send flow,
    matching the no-self-service-signup decision in app/api/auth.py)."""
    target = (await session.execute(select(User).where(User.cap_id == body.cap_id))).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, f"No account for CAP ID {body.cap_id!r} yet")
    existing = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == target.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(MissionMembership(mission_id=mission_id, user_id=target.id, role=body.role))
    else:
        existing.role = body.role
    await session.commit()
    return await list_members(mission_id, user, session)
