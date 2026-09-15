"""PDF reports (REQUIREMENTS.md 3.3/3.6, added 2026-09-13) -- per Gerry: at the end
of an operating period (missions can span days, several operating periods per day),
a PDF of the "effective communications log" -- every logged entry, grouped by the
time it was actually RECEIVED (not the reported/effective event time, which can be
manually backdated -- see app/api/sorties.py's HHMM time entry), with several
entries received at the same moment grouped under one timestamp, and including any
comments.

Mission #/Sortie # (added 2026-09-14 per Gerry: "we need to add the mission number
at the top and the sortie to each entry") resolve through TrackingEvent.sortie_id
(added the same day for an unrelated reason -- app/models.py) -- an event with no
open sortie at the time it was logged (e.g. a raw NOW-button entry with nothing
started) shows "--" for its sortie rather than being excluded, since the comms log
is a record of everything received, not just sortie-scoped traffic. The report
spans an arbitrary time range, not a single mission, so it's possible for more than
one mission number to appear in one report (e.g. two aircraft on different missions
during the same operating period) -- the header lists every distinct mission number
actually present rather than assuming there's exactly one.

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
from app.models import Aircraft, Sortie, SortieWaypoint, TrackingEvent

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
        select(TrackingEvent, Aircraft.tail_number, Aircraft.callsign, Sortie.sortie_number, Sortie.mission_number)
        .join(Aircraft, Aircraft.id == TrackingEvent.aircraft_id)
        .outerjoin(Sortie, Sortie.id == TrackingEvent.sortie_id)
        .where(TrackingEvent.created_at >= start, TrackingEvent.created_at <= end)
        .order_by(TrackingEvent.created_at)
    )
    rows = result.all()

    event_ids = [event.id for event, _, _, _, _ in rows]
    waypoints_by_event_id = {}
    if event_ids:
        wp_result = await session.execute(select(SortieWaypoint).where(SortieWaypoint.event_id.in_(event_ids)))
        waypoints_by_event_id = {w.event_id: w for w in wp_result.scalars()}

    # Group consecutive entries sharing the same received minute -- "allowing
    # several [to be] entered at once to be noted within the same timestamp"
    # (Gerry) -- printed once per group in the PDF rather than repeating the
    # timestamp on every line, matching real paper comms-log convention.
    groups: list[tuple[datetime, list[dict]]] = []
    mission_numbers: set[str] = set()
    for event, tail_number, callsign, sortie_number, mission_number in rows:
        received = event.created_at.replace(second=0, microsecond=0)
        waypoint = waypoints_by_event_id.get(event.id)
        if mission_number:
            mission_numbers.add(mission_number)
        entry = {
            "aircraft": callsign or tail_number,
            "sortie_number": sortie_number,
            "event_type": event.event_type.value.replace("_", " ").upper(),
            "effective_time": _effective_time(event),
            "comments": waypoint.comments if waypoint else None,
        }
        if groups and groups[-1][0] == received:
            groups[-1][1].append(entry)
        else:
            groups.append((received, [entry]))

    pdf_bytes = _render_pdf(start, end, groups, sorted(mission_numbers))
    filename = f"avtrack-comms-log-{start:%Y%m%d%H%M}-{end:%Y%m%d%H%M}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _render_pdf(
    start: datetime, end: datetime, groups: list[tuple[datetime, list[dict]]], mission_numbers: list[str]
) -> bytes:
    buffer = BytesIO()
    # topMargin widened to leave room for the running per-page header drawn below --
    # a plain Paragraph in `story` only renders once (page 1), so on a multi-page
    # log this would disappear after the first page. Per Gerry: "the entry can be
    # a single line under the header on the top of each page," then "Mission Number
    # and Operating Period can be at the top of each sheet" -- both the mission
    # line and the period/generated line are now drawn per-page, not just page 1.
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=1.15 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    # More than one mission number showing up in the same report is a real,
    # expected case (the report is an arbitrary time range, not a single mission --
    # see module docstring), not an error -- list every one present rather than
    # picking one.
    if len(mission_numbers) == 1:
        mission_line = f"Mission #: {mission_numbers[0]}"
    elif mission_numbers:
        mission_line = f"Missions: {', '.join(mission_numbers)}"
    else:
        mission_line = "Mission #: none recorded"
    period_line = (
        f"Period: {start:%Y-%m-%d %H:%M}Z - {end:%Y-%m-%d %H:%M}Z    "
        f"Generated: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z"
    )

    def draw_running_header(canvas, doc):
        canvas.saveState()
        page_width = letter[0]
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(0.6 * inch, letter[1] - 0.5 * inch, "AvTrack Communications Log")
        canvas.setFont("Helvetica", 10)
        canvas.drawString(0.6 * inch, letter[1] - 0.7 * inch, mission_line)
        canvas.drawRightString(page_width - 0.6 * inch, letter[1] - 0.7 * inch, f"Page {canvas.getPageNumber()}")
        canvas.setFont("Helvetica", 9)
        canvas.drawString(0.6 * inch, letter[1] - 0.87 * inch, period_line)
        canvas.restoreState()

    story = []

    if not groups:
        story.append(Paragraph("No entries received in this period.", styles["Normal"]))
    else:
        # One continuous table for the whole report (2026-09-15, replacing a
        # separate Heading4 + its own table-with-header per received-minute
        # group) -- per Gerry: "these don't have to have all the separation...
        # the current format with the heading row for each one is overkill."
        # Date-Time Group is still only printed on the first row of a group,
        # not every row, preserving the original paper-comms-log convention
        # ("allowing several entered at once to be noted within the same
        # timestamp") without the heavyweight per-group table/heading pair.
        # `repeatRows=1` is reportlab's own mechanism for repeating the single
        # header row on every page this table spans (needed now that it's one
        # long table instead of many short ones).
        table_data = [["Date-Time Group", "Callsign", "Sortie #", "Entry", "Effective Time", "Comments"]]
        for received, entries in groups:
            for i, e in enumerate(entries):
                table_data.append(
                    [
                        f"{received:%Y-%m-%d %H:%M}Z" if i == 0 else "",
                        e["aircraft"],
                        str(e["sortie_number"]) if e["sortie_number"] is not None else "--",
                        e["event_type"],
                        f"{e['effective_time']:%H:%M}Z",
                        e["comments"] or "",
                    ]
                )
        table = Table(
            table_data,
            colWidths=[1.1 * inch, 0.9 * inch, 0.6 * inch, 1.0 * inch, 0.8 * inch, 2.1 * inch],
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b6cb0")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.grey),  # thin per-row line, not a full grid
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)

    doc.build(story, onFirstPage=draw_running_header, onLaterPages=draw_running_header)
    return buffer.getvalue()
