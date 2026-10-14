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
