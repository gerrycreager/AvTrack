"""Minimal FIXM/NAS message parser for FDPS "Track Message" data (REQUIREMENTS.md 3.5).

Written against real captured FDPS messages (2026-09-13, via the official JumpStart
client run from r815 -- see deploy/avtrack-fdps-consumer.service), NOT the generic
FIXM 3.0 spec -- FAA wraps FIXM in its own `faa.aero/nas/3.0` envelope
(MessageCollection/FlightMessageType/NasFlightType etc.) that a spec-only
implementation wouldn't anticipate. Deliberately namespace-agnostic (matches by
stripped local tag name) since exact namespace prefixes are an XML serialization
detail, not something to depend on.

Known unit differences from the FlightAware integration (app/adsb/flightaware.py) --
easy to get wrong by assuming they match:
- `<altitude uom="FEET">43000.0</altitude>` is already plain feet, NOT FlightAware's
  hundreds-of-feet-per-unit quirk. Confirmed against real samples (e.g. 43000.0 for a
  jet at FL430, 1100.0 for a GA aircraft on approach -- both plausible only as
  direct feet).
- No native heading field; derived from `trackVelocity` x/y (KNOTS, ENU-style
  east/north components) via atan2 -- unverified against a known ground-truth
  heading, worth double-checking once real CAP track data is flowing.
"""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FixmPosition:
    callsign: str
    ts_utc: datetime
    latitude: float
    longitude: float
    altitude_ft: float | None
    ground_speed_kt: float | None
    heading_deg: float | None


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _children(el: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name]


def parse_fixm_positions(xml_text: str) -> list[FixmPosition]:
    root = ET.fromstring(xml_text)
    out: list[FixmPosition] = []

    for message in _children(root, "message"):
        flight = _child(message, "flight")
        if flight is None:
            continue

        flight_id_el = _child(flight, "flightIdentification")
        callsign = flight_id_el.attrib.get("aircraftIdentification") if flight_id_el is not None else None
        if not callsign:
            continue

        en_route = _child(flight, "enRoute")
        if en_route is None:
            continue
        outer_pos = _child(en_route, "position")  # NasAircraftPositionType
        if outer_pos is None:
            continue

        pos_time_str = outer_pos.attrib.get("positionTime")
        if not pos_time_str:
            continue
        ts_utc = datetime.fromisoformat(pos_time_str.replace("Z", "+00:00")).astimezone(timezone.utc)

        inner_pos = _child(outer_pos, "position")  # LocationPointType, same tag name, different type
        location_el = _child(inner_pos, "location") if inner_pos is not None else None
        pos_text_el = _child(location_el, "pos") if location_el is not None else None
        if pos_text_el is None or not pos_text_el.text:
            continue
        try:
            lat_str, lon_str = pos_text_el.text.split()
            latitude, longitude = float(lat_str), float(lon_str)
        except ValueError:
            continue

        altitude_el = _child(outer_pos, "altitude")
        altitude_ft = float(altitude_el.text) if altitude_el is not None and altitude_el.text else None

        ground_speed_kt = None
        speed_el = _child(outer_pos, "actualSpeed")
        if speed_el is not None:
            surveillance_el = _child(speed_el, "surveillance")
            if surveillance_el is not None and surveillance_el.text:
                ground_speed_kt = float(surveillance_el.text)

        heading_deg = None
        vel_el = _child(outer_pos, "trackVelocity")
        if vel_el is not None:
            x_el, y_el = _child(vel_el, "x"), _child(vel_el, "y")
            if x_el is not None and y_el is not None and x_el.text and y_el.text:
                vx, vy = float(x_el.text), float(y_el.text)
                heading_deg = math.degrees(math.atan2(vx, vy)) % 360

        out.append(
            FixmPosition(
                callsign=callsign,
                ts_utc=ts_utc,
                latitude=latitude,
                longitude=longitude,
                altitude_ft=altitude_ft,
                ground_speed_kt=ground_speed_kt,
                heading_deg=heading_deg,
            )
        )
    return out
