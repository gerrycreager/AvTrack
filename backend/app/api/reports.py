"""PDF reports (REQUIREMENTS.md 3.3/3.6, added 2026-09-13) -- per Gerry: at the end
of an operating period (missions can span days, several operating periods per day),
a PDF of the "effective communications log" -- every logged entry, grouped by the
time it was actually RECEIVED (not the reported/effective event time, which can be
manually backdated -- see app/api/sorties.py's HHMM time entry), with several
entries received at the same moment grouped under one timestamp, and including any
comments.

"Received" time = TrackingEvent.created_at (when the row was actually written to the
database) -- distinct from event_time_utc/effective_time (the reported/backdated
time of the actual event, which is what's shown per-entry alongside it). This
distinction already existed in the schema before this report was built; nothing new
had to be added just to capture it.

"Operating period" is not a stored/named concept here -- the report takes an
arbitrary start/end time range at generation time, matching how an Incident
Commander actually declares operating period boundaries operationally (a real-time
decision), not something software should presume to know in advance.
"""

from datetime import datetime, timezone
from io import BytesIO

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.events import _effective_time
from app.db import get_session
from app.models import Aircraft, SortieWaypoint, TrackingEvent

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/comms-log")
async def comms_log_pdf(
    start: datetime = Query(..., description="Operating period start, UTC ISO-8601"),
    end: datetime = Query(..., description="Operating period end, UTC ISO-8601"),
    session: AsyncSession = Depends(get_session),
):
    # A naive datetime (no "Z"/offset in the query string) silently produced an
    # empty report instead of an error -- found live 2026-09-13 testing with curl.
    # Dangerous failure mode for an accountability document (looks like "nothing
    # happened this period" rather than "your input was ambiguous"), so naive
    # input is explicitly treated as UTC rather than left to whatever asyncpg/
    # Postgres does with it by default.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    result = await session.execute(
        select(TrackingEvent, Aircraft.tail_number, Aircraft.callsign)
        .join(Aircraft, Aircraft.id == TrackingEvent.aircraft_id)
        .where(TrackingEvent.created_at >= start, TrackingEvent.created_at <= end)
        .order_by(TrackingEvent.created_at)
    )
    rows = result.all()

    event_ids = [event.id for event, _, _ in rows]
    waypoints_by_event_id = {}
    if event_ids:
        wp_result = await session.execute(select(SortieWaypoint).where(SortieWaypoint.event_id.in_(event_ids)))
        waypoints_by_event_id = {w.event_id: w for w in wp_result.scalars()}

    # Group consecutive entries sharing the same received minute -- "allowing
    # several [to be] entered at once to be noted within the same timestamp"
    # (Gerry) -- printed once per group in the PDF rather than repeating the
    # timestamp on every line, matching real paper comms-log convention.
    groups: list[tuple[datetime, list[dict]]] = []
    for event, tail_number, callsign in rows:
        received = event.created_at.replace(second=0, microsecond=0)
        waypoint = waypoints_by_event_id.get(event.id)
        entry = {
            "aircraft": callsign or tail_number,
            "event_type": event.event_type.value.replace("_", " ").upper(),
            "effective_time": _effective_time(event),
            "comments": waypoint.comments if waypoint else None,
        }
        if groups and groups[-1][0] == received:
            groups[-1][1].append(entry)
        else:
            groups.append((received, [entry]))

    pdf_bytes = _render_pdf(start, end, groups)
    filename = f"avtrack-comms-log-{start:%Y%m%d%H%M}-{end:%Y%m%d%H%M}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _render_pdf(start: datetime, end: datetime, groups: list[tuple[datetime, list[dict]]]) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    story = [
        Paragraph("AvTrack Communications Log", styles["Title"]),
        Paragraph(
            f"Period: {start:%Y-%m-%d %H:%M}Z &ndash; {end:%Y-%m-%d %H:%M}Z "
            f"&nbsp;&nbsp; Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z",
            styles["Normal"],
        ),
        Spacer(1, 0.2 * inch),
    ]

    if not groups:
        story.append(Paragraph("No entries received in this period.", styles["Normal"]))

    for received, entries in groups:
        story.append(Paragraph(f"<b>{received:%Y-%m-%d %H:%M}Z received</b>", styles["Heading4"]))
        table_data = [["Aircraft", "Entry", "Effective Time", "Comments"]]
        for e in entries:
            table_data.append([e["aircraft"], e["event_type"], f"{e['effective_time']:%H:%M}Z", e["comments"] or ""])
        table = Table(table_data, colWidths=[1.1 * inch, 1.3 * inch, 1.0 * inch, 3.1 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b6cb0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(Spacer(1, 0.15 * inch))

    doc.build(story)
    return buffer.getvalue()
