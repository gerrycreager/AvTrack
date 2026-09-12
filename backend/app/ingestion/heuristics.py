"""Sortie/event detection from the raw position stream.

Deliberately NOT implemented yet -- this is the R&D item from REQUIREMENTS.md 3.3/3.5.
Two problems to solve here, in priority order (per the 2026-09 FlightAware
mis-stitching observation in REQUIREMENTS.md 3.3, sortie segmentation is the harder
and more foundational of the two):

1. Sortie segmentation: derive engine-start/takeoff/landing/engine-stop boundaries
   from `Position` rows for one aircraft, independent of anything the ADS-B provider
   calls a "flight". Primary signal: sustained ground speed ~0 at/near a known
   Airfield for more than a threshold duration.
2. Touch-and-go vs. full-stop discrimination within an already-bounded sortie -- see
   the proposed first-draft heuristic (runway proximity, AGL/ground-speed profile,
   20-90s discriminator window) in REQUIREMENTS.md open question 3. IMPORTANT: that
   design was derived independently from public ADS-B semantics and the MDPI
   go-around-detection paper -- do not consult US Patent 12,304,653 when implementing
   this; get CAP legal/IP review before this ships beyond internal prototyping.

`detect_events` is a placeholder so the ingestion loop has a call site to wire this
into once designed; it currently returns nothing.
"""

from app.adsb.base import AircraftPosition
from app.models import TrackingEvent


def detect_events(tail_number: str, recent_positions: list[AircraftPosition]) -> list[TrackingEvent]:
    return []
