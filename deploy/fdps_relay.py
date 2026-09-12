"""AvTrack SWIM/FDPS relay -- runs on r815 alongside the JumpStart consumer service
(avtrack-fdps-consumer.service, see deploy/avtrack-fdps-consumer.service).

The consumer writes raw FIXM/NAS XML messages to a local rotating log file
(./jumpstart/log/messages.log). FDPS's national volume is huge (~350k lines / ~17k
messages per 20s observed 2026-09-13) and AvTrack only cares about a small set of CAP
tail numbers, so this script tails that log, does a CHEAP TEXT-LEVEL pre-filter (no
XML parsing here -- keep this relay genuinely lightweight per Gerry's direction, since
it runs on r815 which is already resource-constrained), and forwards only messages
that look CAP-relevant to cosp1's backend for real parsing.

Filter heuristic: a `"CAP<digits>"`-shaped substring, matching the
aircraftIdentification="CAP1203"-style attribute seen in real captured FDPS messages
-- cheap, no XML parsing needed to make the forward/discard decision.

Message boundary: each JMS message logged by the consumer is one complete
<?xml ...?><ns5:MessageCollection>...</ns5:MessageCollection> block (log4j2 pattern is
`%msg%n`, so exactly one trailing newline per message, but the message body itself
contains many internal newlines from pretty-printing).

Does NOT try to resume from a saved offset across restarts (yet) -- starts reading
from the current end of the file, i.e. tail -f semantics. A relay restart can miss
whatever the consumer wrote during the gap; acceptable for a first version given FDPS
re-sends fresh track updates every ~1 min anyway.
"""

import os
import re
import time
import urllib.request
import urllib.error

LOG_FILE = "/home/gerry/CAP/AvTrack/jumpstart/log/messages.log"
INGEST_URL = "http://192.168.0.57:8000/api/swim/ingest"
CAP_PATTERN = re.compile(r'"CAP\d+"')
MESSAGE_START = '<?xml version="1.0" encoding="UTF-8"?><ns5:MessageCollection'
MESSAGE_END = "</ns5:MessageCollection>"


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
    # Real bug found live 2026-09-12, first deployment: the log4j RollingFile
    # appender rotates messages.log at 100MB (see avtrack-fdps-consumer.service /
    # log4j2.properties), which at FDPS's volume can happen every 1-3 minutes. A
    # plain open() + read-loop keeps its file descriptor pointed at the OLD inode
    # after rotation (the path now refers to a fresh, mostly-empty file) -- reads
    # just silently stop producing new data forever, with no error. Detected because
    # the relay had scanned+forwarded nothing several minutes after startup, right
    # after a rotation had actually occurred (confirmed via `ls -la log/`). Fixed by
    # comparing the currently-open file's inode against the path's current inode on
    # every idle poll, and reopening (from the start, since a fresh rotation means a
    # new near-empty file) when they differ.
    buffer = ""
    forwarded = 0
    scanned = 0
    f, current_ino = _open_at_end()
    while True:
        try:
            path_ino = os.stat(LOG_FILE).st_ino
        except FileNotFoundError:
            path_ino = current_ino  # transient during rotation; try again next loop
        if path_ino != current_ino:
            f.close()
            f = open(LOG_FILE, "r", encoding="utf-8", errors="replace")  # start from 0 -- it's a fresh file
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
                buffer = ""  # no message start in buffer at all -- drop garbage
                break
            end = buffer.find(MESSAGE_END, start)
            if end == -1:
                buffer = buffer[start:]  # incomplete message, wait for more data
                break
            end += len(MESSAGE_END)
            message = buffer[start:end]
            buffer = buffer[end:]
            scanned += 1
            if CAP_PATTERN.search(message):
                forward(message)
                forwarded += 1
            if scanned % 5000 == 0:
                print(f"scanned {scanned}, forwarded {forwarded}")


if __name__ == "__main__":
    tail_and_relay()
