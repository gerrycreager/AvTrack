"""Outbound email (REQUIREMENTS.md 3.7 follow-up, added 2026-11-02) -- used only
by app/api/registration.py's self-service account-request flow.

Shells out to `msmtp -a gmail`, the exact same pipeline CAP WxCOP's own alert
scripts already use (same account, `capwxcop.alerts@gmail.com`, same
`/etc/msmtprc` on r815) -- confirmed live 2026-11-02: `/etc/msmtprc`'s ACL
grants `gerry` read (`user:gerry:r--`), `avtrack-backend.service` already runs
as `User=gerry`, and a real test send got a genuine `250 OK` from Gmail. This
needs ZERO new secrets anywhere -- no credential in AvTrack's own .env, no
copy, no new Gmail account. Earlier design (this file's first version) read
SMTP host/user/password from AvTrack's own .env instead; abandoned once this
turned out to already just work at the OS permission level.

Known tradeoff, not a bug: this means AvTrack's account-request emails come
from the same address used for official CAP WxCOP alerting, and AvTrack (which
has no app-level auth of its own protecting this relay) gets send-as access to
it. Accepted deliberately -- same owner/host, file permissions already allow
it. A separate Gmail account/app password is a clean escape hatch later if
that ever stops being fine.
"""

import logging
import subprocess

logger = logging.getLogger(__name__)

MSMTP_ACCOUNT = "gmail"


def send_email(to: str, subject: str, body: str) -> bool:
    """Synchronous -- call via asyncio.to_thread() from an async route, never
    awaited directly (subprocess.run blocks, and per this session's own
    event-loop-blocking lesson, app/api/airfields.py's fix, nothing should
    block the shared event loop the ADS-B poll loops also run on). Returns
    False (and logs a warning/error, doesn't raise) on any failure -- a flaky
    mail send should never be the reason a registration request or approval
    fails outright; the admin can still see/act on it from the app regardless
    of whether the notification email actually went out."""
    message = f"To: {to}\nSubject: {subject}\n\n{body}"
    try:
        result = subprocess.run(
            ["msmtp", "-a", MSMTP_ACCOUNT, to],
            input=message.encode("utf-8"),
            capture_output=True,
            timeout=20,
        )
        if result.returncode != 0:
            logger.warning(
                "msmtp exited %s sending to %s: %s", result.returncode, to, result.stderr.decode(errors="replace")
            )
            return False
        return True
    except Exception:
        logger.exception("Failed to send email to %s: %s", to, subject)
        return False
