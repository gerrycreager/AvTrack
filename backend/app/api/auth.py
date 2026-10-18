"""Login/logout/me -- CAP ID + password + TOTP (REQUIREMENTS.md open question 7,
standalone accounts for Phase 1, added 2026-10-16 per Gerry). See app/auth.py
for the session mechanics this wraps.

Not yet required on every other route (that lands with the frontend login gate,
per the per-user/per-mission plan's rollout order) -- this router alone is safe
to ship ahead of that without changing Gerry's current anonymous usage."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    SESSION_COOKIE_NAME,
    create_session,
    get_current_user,
    hash_password,
    invalidate_session,
    verify_password,
    verify_totp,
)
from app.config import settings
from app.db import get_session
from app.models import Mission, MissionMembership, User
from app.schemas import ChangePasswordIn, LoginIn, MeOut, MissionOut, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def list_user_missions(
    user: User, session: AsyncSession, include_closed: bool = False
) -> list[MissionOut]:
    """All missions `user` belongs to, each annotated with their OWN role on
    that mission. Factored out 2026-10-18 (mission-lifecycle plan,
    /home/gerry/.claude/plans/distributed-humming-sprout.md) -- `me()` below and
    `list_mine()` (app/api/missions.py) used to each independently re-implement
    this exact "every MissionMembership row for this user, joined to its
    Mission" loop; now both call this one helper instead. `include_closed`
    defaults to False everywhere except the frontend's explicit "show closed
    missions" toggle, per Gerry -- closing a mission only affects whether it
    shows up in a picker by default, never its underlying data."""
    memberships = (
        await session.execute(select(MissionMembership).where(MissionMembership.user_id == user.id))
    ).scalars().all()
    out = []
    for m in memberships:
        mission = await session.get(Mission, m.mission_id)
        if mission is None:
            continue
        if not include_closed and mission.closed_at is not None:
            continue
        out.append(
            MissionOut(
                id=mission.id,
                name=mission.name,
                mission_number=mission.mission_number,
                created_by=mission.created_by,
                created_at=mission.created_at,
                closed_at=mission.closed_at,
                role=m.role,
            )
        )
    return out


@router.post("/login")
async def login(body: LoginIn, response: Response, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(User.cap_id == body.cap_id))).scalar_one_or_none()
    # Deliberately identical failure for "no such CAP ID" / "wrong password" /
    # "wrong TOTP code" -- no "password was right, just need the code" leak.
    if (
        user is None
        or not user.active
        or not verify_password(body.password, user.password_hash)
        or not verify_totp(user.totp_secret, body.totp_code)
    ):
        raise HTTPException(401, "Invalid CAP ID, password, or authenticator code")
    token = await create_session(user, session)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
    )
    return {"status": "ok"}


@router.post("/logout")
async def logout(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await invalidate_session(token, session)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}


@router.get("/me", response_model=MeOut)
async def me(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    missions = await list_user_missions(user, session)
    return MeOut(user=UserOut.model_validate(user), missions=missions)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)
):
    if not verify_password(body.current_password, user.password_hash) or not verify_totp(
        user.totp_secret, body.totp_code
    ):
        raise HTTPException(401, "Current password or authenticator code is incorrect")
    user.password_hash = hash_password(body.new_password)
    await session.commit()
    return {"status": "ok"}
