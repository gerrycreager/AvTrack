"""Same-origin proxy to WxCOP (REQUIREMENTS.md 3.3a) for the airfield layer's
Flight Impacts and Station Plot display modes. The frontend never calls WxCOP
cross-origin and never has to branch on HTTP status -- both routes always return
200 with an `available` flag, since a down WxCOP/data2 is the expected, graceful
case right now (data2 confirmed down 2026-10-14, intermittently back up/down
during a software update as of the same day) rather than an error condition."""

from fastapi import APIRouter, Query

from app.integrations import wxcop_client

router = APIRouter(prefix="/api/weather", tags=["weather"])


@router.get("/impacts")
async def get_impacts(
    bounds: str = Query(..., description="west,south,east,north (WGS84)"),
    hour: int = Query(1, description="forecast hour offset, passed through to WxCOP"),
    op: str = Query("vfr", description="operation type, passed through to WxCOP"),
):
    data = await wxcop_client.get_flight_impacts(bounds, hour=hour, op=op)
    if data is None:
        return {"available": False}
    return {"available": True, "airports": data.get("airports", [])}


@router.get("/metar")
async def get_metar(
    bounds: str = Query(..., description="west,south,east,north (WGS84)"),
):
    data = await wxcop_client.get_metar(bounds)
    if data is None:
        return {"available": False}
    return {"available": True, "metars": data.get("metars", [])}


@router.get("/station/{station_id}")
async def get_station(station_id: str):
    """METAR history + TAF for one station -- REQUIREMENTS.md 3.3a, "click an
    airfield to see its METAR and TAF, like EWMC." Mirrors EWMC's own
    showStationPopup click behavior (full detail, used outside Station Plot
    mode where the METAR isn't already drawn on the marker itself)."""
    data = await wxcop_client.get_station_weather(station_id)
    if data is None:
        return {"available": False}
    return {
        "available": True,
        "station": data.get("station"),
        "metars": data.get("metars", []),
        "taf": data.get("taf"),
        "kq_association": data.get("kq_association"),
        "metar_note": data.get("metar_note"),
    }


@router.get("/taf-nearest")
async def get_taf_nearest(
    lat: float = Query(...),
    lon: float = Query(...),
    max_nm: float = Query(150),
):
    """Nearest currently-valid TAF to a point -- REQUIREMENTS.md 3.3a. Mirrors
    EWMC's showNearestTafPopup, used for the Station Plot click (the METAR is
    already visible on the plot itself; only TAF needs fetching there)."""
    data = await wxcop_client.get_nearest_taf(lat, lon, max_nm=max_nm)
    if data is None:
        return {"available": False}
    if "error" in data:
        return {"available": True, "found": False, "error": data["error"]}
    return {"available": True, "found": True, **data}
