"""One-off admin script to provision a standalone AvTrack account (CAP ID +
password + TOTP MFA) -- no self-service signup exists (REQUIREMENTS.md open
question 7, added 2026-10-16 per Gerry). Mirrors scripts/import_callsign_tails.py's
"SSH in and run this" pattern rather than a UI signup/reset flow this app has no
email/SMS infrastructure to build.

Usage:
    cd backend && source .venv/bin/activate
    python ../scripts/create_user.py <cap_id> "<display name>"

Prints a scannable QR code (terminal ASCII art) plus the raw secret and
otpauth:// URI for manual entry, so the new user can enroll in an authenticator
app (Google Authenticator, Authy, 1Password, etc.) on the spot.
"""

import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import pyotp
import qrcode
from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import User


async def main(cap_id: str, display_name: str) -> None:
    async with SessionLocal() as session:
        existing = (await session.execute(select(User).where(User.cap_id == cap_id))).scalar_one_or_none()
        if existing is not None:
            print(f"A user with CAP ID {cap_id} already exists.")
            return

        password = getpass.getpass("New password: ")
        confirm = getpass.getpass("Confirm password: ")
        if not password or password != confirm:
            print("Passwords were empty or did not match.")
            return

        totp_secret = pyotp.random_base32()
        user = User(
            cap_id=cap_id,
            display_name=display_name,
            password_hash=hash_password(password),
            totp_secret=totp_secret,
        )
        session.add(user)
        await session.commit()

        uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(name=cap_id, issuer_name="AvTrack")
        print(f"\nAccount created for {display_name} (CAP ID {cap_id}).")
        print("\nScan this into an authenticator app (Google Authenticator, Authy, 1Password, etc.):\n")
        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.print_ascii(invert=True)
        print(f"\nOr enter this secret manually: {totp_secret}")
        print(f"Setup URI: {uri}\n")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: create_user.py <cap_id> <display_name>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
