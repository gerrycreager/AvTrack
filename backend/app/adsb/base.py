"""Provider-agnostic ADS-B ingestion interface (REQUIREMENTS.md 3.5).

Any provider (FlightAware today, a second provider for redundancy later, eventually
a direct FAA feed) implements `ADSBProvider`. Nothing outside this package should
import a provider-specific client directly -- the ingestion loop and API only ever
see `AircraftPosition`/`ADSBProvider`.

IMPORTANT (REQUIREMENTS.md 3.3/3.5): a provider's notion of a "flight" (start/end,
continuity) is not trusted anywhere in this codebase. Providers report positions;
AvTrack derives its own sortie/event boundaries from the raw position stream. Do not
add a `get_flights()`-style method that returns provider-defined flight objects.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AircraftPosition:
    tail_number: str
    callsign: str | None
    ts_utc: datetime
    latitude: float
    longitude: float
    altitude_ft: float | None
    ground_speed_kt: float | None
    heading_deg: float | None
    raw: dict


class ADSBProvider(ABC):
    name: str

    @abstractmethod
    async def get_positions(self, tail_numbers: list[str]) -> list[AircraftPosition]:
        """Return the latest known position for each requested tail number that the
        provider currently has data for. Silently omit tails with no current data --
        callers must not assume one result per requested tail (this is itself part of
        the "ADS-B coverage isn't uniform" reality, REQUIREMENTS.md 3.5)."""
        raise NotImplementedError
