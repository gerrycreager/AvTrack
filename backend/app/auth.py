"""Identity and session handling -- the one module that knows how a request maps
to a User (REQUIREMENTS.md open question 7, standalone accounts for Phase 1,
added 2026-10-16 per Gerry). A future CAP eServices SSO adapter replaces just
this module's session-resolution half; every mission-scoped route depends on
require_mission_member here rather than re-implementing the check, so that swap
wouldn't have to touch them.

Login is CAP ID + password + a TOTP code (MFA, per Gerry's explicit choice --
no outbound email/SMS infrastructure exists in this app to build a one-time-code
flow on instead). Sessions are DB-backed opaque tokens (app.models.UserSession),
not JWTs, so there's no secret key to manage and logout is a real DELETE. Only a
SHA-256 hash of the token is ever stored -- a DB read alone can't produce a
usable cookie value.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import MissionMembership, User, UserSession

SESSION_COOKIE_NAME = "avtrack_session"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def verify_totp(secret: str, code: str) -> bool:
    # valid_window=1: accept the previous/next 30s step too, for ordinary clock
    # skew between the server and the user's phone.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_session(user: User, session: AsyncSession) -> str:
    """Returns the raw token to set as the cookie value; only its hash is stored."""
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
        )
    )
    await session.commit()
    return token


async def invalidate_session(token: str, session: AsyncSession) -> None:
    row = (
        await session.execute(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    ).scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.commit()


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not logged in")
    row = (
        await session.execute(select(UserSession).where(UserSession.token_hash == _hash_token(token)))
    ).scalar_one_or_none()
    if row is None or row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Session expired or invalid")
    user = await session.get(User, row.user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Account disabled")
    return user


def require_mission_member(mission_id_param: str = "mission_id"):
    """Dependency factory -- checks membership for whichever path/query param
    carries the mission id (default "mission_id", matching every mission-scoped
    route's own param name). Role is never consulted here (descriptive only,
    per Gerry -- see MissionMembership's docstring in models.py); membership is
    binary: a row exists, or it doesn't."""

    async def _dep(
        request: Request,
        user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_session),
    ) -> User:
        mission_id = request.path_params.get(mission_id_param) or request.query_params.get(mission_id_param)
        if not mission_id:
            raise HTTPException(422, f"Missing {mission_id_param}")
        membership = (
            await session.execute(
                select(MissionMembership).where(
                    MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(403, "Not a member of this mission")
        return user

    return _dep


async def optional_mission_member(
    mission_id: uuid.UUID | None, request: Request, session: AsyncSession
) -> User | None:
    """Transitional helper for mission-scoping an endpoint without breaking it
    for the current, pre-login frontend (added 2026-10-16, phase 2 of the
    per-user/per-mission rollout -- see
    /home/gerry/.claude/plans/distributed-humming-sprout.md). When mission_id
    is omitted, this is a no-op (None, no auth check) and the caller should run
    its existing, unscoped query exactly as before. Once the frontend always
    sends mission_id (that plan's phase 3), every caller should become
    required and this fallback branch should be deleted -- it exists only to
    let the schema/API land ahead of the frontend without an outage in
    between."""
    if mission_id is None:
        return None
    user = await get_current_user(request, session)
    membership = (
        await session.execute(
            select(MissionMembership).where(
                MissionMembership.mission_id == mission_id, MissionMembership.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(403, "Not a member of this mission")
    return user
