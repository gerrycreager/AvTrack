"""Callsign -> wing display hint (mission-lifecycle plan, item 6, added
2026-10-18 -- /home/gerry/.claude/plans/distributed-humming-sprout.md). Pure,
sourced lookup from `data/callsign_wing_lookup.csv`, Gerry's authoritative
52-row table (Wing ID 01-52 -> assigned wing; 50-52 are Alaska/Hawaii/Puerto
Rico, appended after the 49 alphabetical states).

Per Gerry, CAP aircraft numbers are wing-prefixed "usually but not always" --
this is DISPLAY-ONLY, a small badge/tooltip next to a callsign on
discovered-candidate rows and roster rows. It must NEVER be used for any
authorization or data-integrity decision; nothing here gates approve/dismiss
or any write path.

Wings 01-09's pattern is "CAP<N>xx" (a single leading digit, 3 digits total,
e.g. CAP199); wings 10-52's pattern is "CAP<NN>xx" (two leading digits, 4+
digits total, e.g. CAP1099) -- see the CSV's own "Aircraft callsign pattern"
column. That's a real ambiguity for a 3-vs-4-digit suffix, not a bug: a
3-digit suffix is read as a single-digit wing prefix, a 4-or-more-digit
suffix as a two-digit wing prefix. Anything else (e.g. a bare 1 or 2 digit
callsign) returns None rather than guessing.
"""

import csv
from pathlib import Path

from app.integrations.adsb_discovery import CALLSIGN_RE

# backend/app/integrations/wing_lookup.py -> repo root -> data/
_CSV_PATH = Path(__file__).resolve().parents[3] / "data" / "callsign_wing_lookup.csv"


def _load_wing_table(csv_path: Path = _CSV_PATH) -> dict[str, str]:
    """wing id ("01".."52") -> assigned wing name. Returns an empty table
    (not an error) if the CSV is missing -- this is a display hint, not a
    required dependency; its absence should degrade to "no badge shown," not
    break aircraft listing/discovery."""
    table: dict[str, str] = {}
    if not csv_path.exists():
        return table
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            wing_id = (row.get("Wing ID") or "").strip()
            wing_name = (row.get("Assigned wing") or "").strip()
            if wing_id and wing_name:
                table[wing_id.zfill(2)] = wing_name
    return table


WING_BY_ID = _load_wing_table()


def wing_hint_for_callsign(callsign: str | None) -> str | None:
    """Best-effort wing name for a CAP/PARD-shaped callsign, by its leading
    wing-prefix digits -- see module docstring for the 3-vs-4-digit rule.
    None for anything that doesn't match the usual CAP<N>/PARD<N> shape, or
    whose leading digits aren't a known wing id. Display-only."""
    if not callsign:
        return None
    callsign = callsign.strip()
    m = CALLSIGN_RE.match(callsign)
    if not m:
        return None
    digits = callsign.upper()[len(m.group(1)):].strip()
    if not digits.isdigit():
        return None
    if len(digits) == 3:
        return WING_BY_ID.get(digits[0].zfill(2))
    if len(digits) >= 4:
        return WING_BY_ID.get(digits[:2])
    return None
