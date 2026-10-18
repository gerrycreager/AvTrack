"""Mission: the real, first-class scoping entity for a common operating picture
shared by IC/Ops/AOBD/COMMS (REQUIREMENTS.md, added 2026-10-16 per Gerry: "those
groups need to be able to see a particular mission(s)"). Any logged-in user can
create a mission and add members by CAP ID -- per Gerry, "Any of those four
areas can set up a Mission and enter sortie information." Role is descriptive
only, never checked for authorization -- see MissionMembership's docstring in
models.py, and app/auth.py's require_mission_member for the actual check."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import list_user_missions
from app.auth import get_current_user, require_mission_member
from app.db import get_session
from app.models import Mission, MissionMembership, User
from app.schemas import MissionCreateIn, MissionJoinIn, MissionMemberIn, MissionMemberOut, MissionOut

router = APIRouter(prefix="/api/missions", tags=["missions"])

# Capped, recency-sorted default list for the browse/join combobox's empty-query
# state (item 4, 2026-10-18 plan) -- keeps a long tail of stale training/
# persistent missions from dominating an empty-query browse.
BROWSE_DEFAULT_LIMIT = 20


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
async def list_mine(
    include_closed: bool = Query(False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Missions this user belongs to. `include_closed` backs the frontend's
    explicit "Show closed missions" checkbox (item 5, 2026-10-18 plan) --
    defaults to open-only everywhere else. Shares its membership-loop logic
    with GET /api/auth/me via app.api.auth.list_user_missions, rather than
    each re-implementing it (the two used to be independent copies)."""
    return await list_user_missions(user, session, include_closed=include_closed)


@router.get("", response_model=list[MissionOut])
async def browse_missions(
    q: str | None = Query(None, description="Case-insensitive substring match against name or mission_number"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Browse/search OPEN missions -- any logged-in user, deliberately NOT
    gated by membership (item 4, 2026-10-18 plan: that's the whole point of
    this route, so a user can find and self-join a mission they're not on
    yet). This is a new discoverability surface (mission names/numbers become
    visible to any logged-in user) -- confirmed acceptable by Gerry, since
    self-service add-by-CAP-ID already has an equally low bar today.

    With `q` omitted: a capped, recency-sorted default list (so a long tail of
    stale training/persistent missions doesn't dominate an empty-query
    browse). With `q` given: substring match against name OR mission_number
    across ALL open missions, uncapped, so typing an exact/partial mission
    number always finds it even if it's not recent."""
    stmt = select(Mission).where(Mission.closed_at.is_(None))
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Mission.name.ilike(pattern), Mission.mission_number.ilike(pattern)))
        stmt = stmt.order_by(Mission.created_at.desc())
    else:
        stmt = stmt.order_by(Mission.created_at.desc()).limit(BROWSE_DEFAULT_LIMIT)
    missions = (await session.execute(stmt)).scalars().all()
    # The caller's own role, if they happen to already be a member -- but this
    # route is explicitly reachable by non-members too, so None is the common case.
    return [_to_mission_out(m, None) for m in missions]


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


@router.delete("/{mission_id}/members/me")
async def leave_mission(
    mission_id: UUID,
    user: User = Depends(require_mission_member()),
    session: AsyncSession = Depends(get_session),
):
    """Leave a mission -- deletes only the CALLER's own MissionMembership row;
    the mission and every other member's row is untouched (item 1, 2026-10-18
    plan). Distinct from Close below -- per Gerry these are two separate
    actions. No auto-close when the last member leaves, matching this app's
    existing permissive binary-auth style: an orphaned-but-open mission is
    recoverable via self-service join (browse_missions/join_mission below),
    not lost."""
    membership = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if membership is not None:
        await session.delete(membership)
        await session.commit()
    return {"status": "ok"}


@router.post("/{mission_id}/close", response_model=MissionOut)
async def close_mission(
    mission_id: UUID,
    user: User = Depends(require_mission_member()),
    session: AsyncSession = Depends(get_session),
):
    """Sets Mission.closed_at = now(). Idempotent -- closing an already-closed
    mission is a no-op, matching DiscoveredCallsign.dismissed_at's existing
    idempotency convention. Historical sortie/aircraft data stays fully
    intact and queryable; closing only affects whether the mission shows up
    in the picker by default (item 2, 2026-10-18 plan)."""
    mission = await session.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(404, "Mission not found")
    if mission.closed_at is None:
        mission.closed_at = datetime.now(timezone.utc)
        await session.commit()
    membership = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    return _to_mission_out(mission, membership.role if membership else None)


@router.post("/{mission_id}/reopen", response_model=MissionOut)
async def reopen_mission(
    mission_id: UUID,
    user: User = Depends(require_mission_member()),
    session: AsyncSession = Depends(get_session),
):
    """Sets Mission.closed_at = None -- exactly symmetric with close above,
    and equally idempotent."""
    mission = await session.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(404, "Mission not found")
    if mission.closed_at is not None:
        mission.closed_at = None
        await session.commit()
    membership = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    return _to_mission_out(mission, membership.role if membership else None)


@router.post("/{mission_id}/join", response_model=MissionOut)
async def join_mission(
    mission_id: UUID,
    body: MissionJoinIn | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Self-service join (item 4, 2026-10-18 plan) -- deliberately NOT gated
    by require_mission_member, since the whole point is letting a non-member
    in. Upsert-or-noop against MissionMembership, same pattern as add_member
    above. 404s on a missing or already-closed mission."""
    mission = await session.get(Mission, mission_id)
    if mission is None or mission.closed_at is not None:
        raise HTTPException(404, "Mission not found")
    role = body.role if body is not None else None
    existing = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(MissionMembership(mission_id=mission_id, user_id=user.id, role=role))
        await session.commit()
        effective_role = role
    else:
        effective_role = existing.role
    return _to_mission_out(mission, effective_role)
