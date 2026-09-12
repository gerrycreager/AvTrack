"""AvTrack SWIM/STDDS relay -- runs on r815 alongside the JumpStart consumer service
(avtrack-stdds-consumer.service, see deploy/avtrack-stdds-consumer.service).

STDDS delivers TAIS (Terminal Automation Information System) terminal/TRACON-local
radar track + flight plan data, one <ns2:TATrackAndFlightPlan> message per facility
report, containing multiple <record> blocks (one per tracked aircraft in that
facility's airspace at that instant). This is a structurally different product from
FDPS (see fdps_relay.py) -- callsign lives in <flightPlan><acid>...</acid>, not an
XML attribute, and there is no per-flight message; a single message covers many
aircraft in one facility (<src>) at once.

Real sample captured 2026-09-12 (~7MB in under a minute from a single facility feed)
had <src>BNA</src> (Nashville) -- NOT the U90/Tucson facility seen in an earlier
one-off STDDS sample, meaning this subscription is not locked to a single facility.

Same cheap text-level pre-filter strategy as fdps_relay.py: no XML parsing here, just
look for a CAP-shaped <acid> value, and forward the whole message (which may contain
other aircraft too -- cheap to let cosp1's real parser sort out which <record> blocks
matter) to cosp1 for real parsing.

Does NOT try to resume from a saved offset across restarts (yet) -- starts reading
from the current end of the file, i.e. tail -f semantics.
"""

import os
import re
import time
import urllib.request
import urllib.error

LOG_FILE = "/home/gerry/CAP/AvTrack/jumpstart-stdds/log/messages.log"
INGEST_URL = "http://192.168.0.57:8000/api/swim/ingest"
CAP_PATTERN = re.compile(r"<acid>(CAP\d+)</acid>")
SRC_PATTERN = re.compile(r"<src>([A-Z0-9]+)</src>")
MESSAGE_START = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><ns2:TATrackAndFlightPlan'
MESSAGE_END = "</ns2:TATrackAndFlightPlan>"

# Coverage-detection phase (2026-09-12): cosp1's /api/swim/ingest endpoint only knows
# how to parse FIXM/NAS XML (from the FDPS relay), not TAIS -- forwarding TAIS
# messages there would just generate harmless-but-noisy parse-failure log spam with no
# benefit. So for now, just detect and log CAP matches locally; once STDDS is
# confirmed to actually carry CAP traffic, add a TAIS parser (parallel to
# app/adsb/fixm.py) and switch this back to forwarding for real ingestion.
FORWARD_ENABLED = False


def forward(message: str) -> None:
    data = message.encode("utf-8")
    req = urllib.request.Request(INGEST_URL, data=data, headers={"Content-Type": "application/xml"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"forward failed: {e}")


def _open_at_end():
    f = open(LOG_FILE, "r", encoding="utf-8", errors="replace")
    f.seek(0, 2)  # tail -f semantics, see module docstring
    return f, os.fstat(f.fileno()).st_ino


def tail_and_relay() -> None:
    # Same log-rotation-blindness fix as fdps_relay.py: compare the currently-open
    # file's inode against the path's current inode on every idle poll, reopen from
    # the start when they differ.
    buffer = ""
    forwarded = 0
    scanned = 0
    f, current_ino = _open_at_end()
    while True:
        try:
            path_ino = os.stat(LOG_FILE).st_ino
        except FileNotFoundError:
            path_ino = current_ino
        if path_ino != current_ino:
            f.close()
            f = open(LOG_FILE, "r", encoding="utf-8", errors="replace")
            current_ino = os.fstat(f.fileno()).st_ino
            buffer = ""
            print("log file rotated -- reopened")

        chunk = f.read(1 << 20)
        if not chunk:
            time.sleep(0.5)
            continue
        buffer += chunk
        while True:
            start = buffer.find(MESSAGE_START)
            if start == -1:
                buffer = ""
                break
            end = buffer.find(MESSAGE_END, start)
            if end == -1:
                buffer = buffer[start:]
                break
            end += len(MESSAGE_END)
            message = buffer[start:end]
            buffer = buffer[end:]
            scanned += 1
            matches = CAP_PATTERN.findall(message)
            if matches:
                src_m = SRC_PATTERN.search(message)
                src = src_m.group(1) if src_m else "?"
                print(f"MATCH src={src} acid={matches}")
                if FORWARD_ENABLED:
                    forward(message)
                forwarded += 1
            if scanned % 500 == 0:
                print(f"scanned {scanned}, forwarded {forwarded}")


if __name__ == "__main__":
    tail_and_relay()
