"""Self-service account requests (REQUIREMENTS.md 3.7 follow-up, added
2026-11-02). Request-then-approve, not open self-registration, per Gerry: a
prospective user submits this themselves; an `is_admin` User approves or
denies it; only on approval does the requester get a one-time emailed link to
set their own password and enroll their own TOTP. Gerry (or another admin)
never sees or types anyone else's password, unlike the existing
scripts/create_user.py CLI flow this doesn't replace (that script still works
for a one-off account Gerry wants to create directly).

See RegistrationRequest's own docstring in models.py for the data model, and
app/email.py for why mail sends go through asyncio.to_thread().
"""

import asyncio
import base64
import io
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pyotp
import qrcode
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, require_admin
from app.config import settings
from app.db import get_session
from app.email import send_email
from app.models import RegistrationRequest, RegistrationStatus, User
from app.schemas import (
    RegistrationRequestIn,
    RegistrationRequestOut,
    RegistrationSetupIn,
    RegistrationSetupOut,
    RegistrationSetupStartOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/registration", tags=["registration"])

SETUP_TOKEN_VALID_HOURS = 48
MIN_PASSWORD_LENGTH = 8
# Public self-submission is restricted to real CAP email addresses (added
# 2026-11-02 per Gerry) -- exact domain match, not a substring/endswith check
# (so "notcap.gov" or "evilcap.gov.example.com" don't slip through). Only
# /request applies this; /admin-create (admin-only) deliberately doesn't --
# that's the exception path Gerry asked for.
ALLOWED_EMAIL_DOMAINS = {"cap.gov", "capnhq.gov"}


def _email_domain_allowed(email: str) -> bool:
    domain = email.strip().lower().rsplit("@", 1)[-1] if "@" in email else ""
    return domain in ALLOWED_EMAIL_DOMAINS


def _qrcode_data_uri(otpauth_uri: str) -> str:
    img = qrcode.make(otpauth_uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


async def _approve_and_notify(req: RegistrationRequest, admin: User, session: AsyncSession) -> str:
    """Shared by approve_request and admin_create_request -- both end with the
    same "mark approved, generate a setup link, email it" steps."""
    req.status = RegistrationStatus.approved
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_user_id = admin.id
    req.setup_token = secrets.token_urlsafe(32)
    req.setup_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=SETUP_TOKEN_VALID_HOURS)
    await session.commit()

    setup_url = f"{settings.public_base_url}/complete-setup.html?token={req.setup_token}"
    if settings.public_base_url:
        await asyncio.to_thread(
            send_email,
            req.email,
            "Your AvTrack account request was approved",
            f"Hi {req.display_name},\n\n"
            f"Your AvTrack account request (CAP ID {req.cap_id}) has been approved. "
            f"Complete your account setup here (link expires in {SETUP_TOKEN_VALID_HOURS} hours):\n\n"
            f"{setup_url}\n\n"
            f"You'll set your own password there and enroll your own authenticator app.",
        )
    else:
        logger.warning("PUBLIC_BASE_URL not set -- approval email not sent for %s, link: %s", req.cap_id, setup_url)
    return setup_url


@router.post("/request")
async def submit_request(body: RegistrationRequestIn, session: AsyncSession = Depends(get_session)):
    cap_id = body.cap_id.strip()
    if not cap_id:
        raise HTTPException(422, "CAP ID is required")
    if not _email_domain_allowed(body.email):
        raise HTTPException(
            422,
            "Email must be a cap.gov or capnhq.gov address. "
            "If you don't have one, contact an admin for an exception.",
        )

    existing_user = (await session.execute(select(User).where(User.cap_id == cap_id))).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(409, "An account with this CAP ID already exists. Log in, or contact an admin.")

    # Upsert the existing pending request for this CAP ID rather than piling
    # up duplicates on resubmission -- same additive-not-spammy spirit as the
    # roster upload's own upsert (app/api/aircraft.py).
    pending = (
        await session.execute(
            select(RegistrationRequest).where(
                RegistrationRequest.cap_id == cap_id, RegistrationRequest.status == RegistrationStatus.pending
            )
        )
    ).scalar_one_or_none()
    if pending is not None:
        pending.display_name = body.display_name
        pending.email = body.email
        pending.note = body.note
    else:
        pending = RegistrationRequest(
            cap_id=cap_id, display_name=body.display_name, email=body.email, note=body.note
        )
        session.add(pending)
    await session.commit()

    if settings.admin_notification_email_list:
        review_url = f"{settings.public_base_url}/admin-requests.html" if settings.public_base_url else ""
        body_text = (
            f"New AvTrack account request:\n\n"
            f"CAP ID: {cap_id}\nName: {body.display_name}\nEmail: {body.email}\n"
            f"Note: {body.note or '(none)'}\n\n"
            f"Review: {review_url or '(set PUBLIC_BASE_URL to include a link here)'}"
        )
        for addr in settings.admin_notification_email_list:
            await asyncio.to_thread(send_email, addr, f"AvTrack access request: {cap_id}", body_text)

    return {"status": "submitted"}


@router.get("/pending", response_model=list[RegistrationRequestOut])
async def list_pending(user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(RegistrationRequest)
        .where(RegistrationRequest.status == RegistrationStatus.pending)
        .order_by(RegistrationRequest.created_at)
    )
    return result.scalars().all()


async def _get_pending_or_404(request_id: UUID, session: AsyncSession) -> RegistrationRequest:
    req = await session.get(RegistrationRequest, request_id)
    if req is None:
        raise HTTPException(404, "Request not found")
    if req.status != RegistrationStatus.pending:
        raise HTTPException(409, f"Request is already {req.status.value}, not pending")
    return req


@router.post("/{request_id}/approve")
async def approve_request(
    request_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    req = await _get_pending_or_404(request_id, session)
    setup_url = await _approve_and_notify(req, user, session)
    return {"status": "approved", "setup_url": setup_url}


@router.post("/admin-create")
async def admin_create_request(
    body: RegistrationRequestIn,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Exception path for someone without a cap.gov/capnhq.gov address (added
    2026-11-02 per Gerry: "For exceptions, I need a way to register them.").
    Admin-only, deliberately skips _email_domain_allowed() -- an admin
    explicitly vouching for the person is the exception mechanism itself.
    Immediately approved (no separate pending step) since an admin creating
    this already IS the approval; same setup-link flow as approve_request
    otherwise, so the requester still sets their own password/TOTP."""
    cap_id = body.cap_id.strip()
    if not cap_id:
        raise HTTPException(422, "CAP ID is required")

    existing_user = (await session.execute(select(User).where(User.cap_id == cap_id))).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(409, "An account with this CAP ID already exists.")

    # Reuse an existing pending request for this CAP ID if one's already there
    # (e.g. they tried to self-submit and got bounced by the domain check) --
    # same upsert spirit as submit_request, rather than leaving an orphaned
    # duplicate row behind.
    req = (
        await session.execute(
            select(RegistrationRequest).where(
                RegistrationRequest.cap_id == cap_id, RegistrationRequest.status == RegistrationStatus.pending
            )
        )
    ).scalar_one_or_none()
    if req is not None:
        req.display_name = body.display_name
        req.email = body.email
        req.note = body.note
    else:
        req = RegistrationRequest(cap_id=cap_id, display_name=body.display_name, email=body.email, note=body.note)
        session.add(req)
    await session.flush()

    setup_url = await _approve_and_notify(req, user, session)
    return {"status": "approved", "setup_url": setup_url}


@router.post("/{request_id}/deny")
async def deny_request(
    request_id: UUID,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    req = await _get_pending_or_404(request_id, session)
    req.status = RegistrationStatus.denied
    req.reviewed_at = datetime.now(timezone.utc)
    req.reviewed_by_user_id = user.id
    await session.commit()
    return {"status": "denied"}


async def _get_valid_setup_request(token: str, session: AsyncSession) -> RegistrationRequest:
    req = (
        await session.execute(select(RegistrationRequest).where(RegistrationRequest.setup_token == token))
    ).scalar_one_or_none()
    if (
        req is None
        or req.status != RegistrationStatus.approved
        or req.setup_completed_at is not None
        or req.setup_token_expires_at is None
        or req.setup_token_expires_at < datetime.now(timezone.utc)
    ):
        raise HTTPException(404, "Invalid or expired setup link")
    return req


@router.get("/setup/{token}", response_model=RegistrationSetupStartOut)
async def get_setup(token: str, session: AsyncSession = Depends(get_session)):
    req = await _get_valid_setup_request(token, session)
    return RegistrationSetupStartOut(cap_id=req.cap_id, display_name=req.display_name)


@router.post("/setup/{token}", response_model=RegistrationSetupOut)
async def complete_setup(token: str, body: RegistrationSetupIn, session: AsyncSession = Depends(get_session)):
    req = await _get_valid_setup_request(token, session)
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    existing_user = (await session.execute(select(User).where(User.cap_id == req.cap_id))).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(409, "An account with this CAP ID already exists")

    totp_secret = pyotp.random_base32()
    user = User(
        cap_id=req.cap_id,
        display_name=req.display_name,
        password_hash=hash_password(body.password),
        totp_secret=totp_secret,
    )
    session.add(user)
    req.setup_completed_at = datetime.now(timezone.utc)
    await session.commit()

    otpauth_uri = pyotp.TOTP(totp_secret).provisioning_uri(name=req.cap_id, issuer_name="AvTrack")
    return RegistrationSetupOut(otpauth_uri=otpauth_uri, qrcode_data_uri=_qrcode_data_uri(otpauth_uri))
