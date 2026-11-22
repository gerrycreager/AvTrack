# AvTrack — what it is and why it exists

CAP flies more than 80% of daily USAF Air Tasking Orders, almost entirely
with volunteer-owned Cessna 172/182/206-class aircraft. Every CAP powered
aircraft carries ADS-B In/Out. AvTrack turns that into fleet-wide position
tracking for mission staff, without waiting on a long-term direct FAA feed.

## The problem

During a real mission, an Air Operations Branch Director needs to know
where CAP aircraft actually are, right now — who's airborne, who's on the
ground, whether an aircraft is where its sortie says it should be, and
whether an unregistered or unexpected aircraft has wandered into the
operations area. That picture didn't exist in one place before; it lived
in radio calls, a paper/whiteboard ops board, and whatever a staff member
could pull up on a generic flight tracker that doesn't know what CAP or
PARD callsigns mean.

## What it does

- **Live position tracking**, pulled from multiple ADS-B sources
  (FlightAware, ADSB.lol, ADSB.fi, OpenSky) so coverage doesn't depend on
  any single provider, normalized to one data model regardless of source.
- **CAP/PARD-aware identification**: knows that a PARD tasking is often the
  same physical aircraft under a different transponder callsign, not a
  different aircraft, and tracks tail-to-callsign mappings accordingly.
- **Unregistered-aircraft alerting**: if a CAP- or PARD-shaped callsign
  shows up inside the current Area of Operations without being in the
  roster, staff get flagged and can register it with one click — no form,
  no separate workflow.
- **A real Air Ops Status Board**: the same sortie-tasked view an AOBD wall
  board already uses, built from actual Sortie records, not just "whatever
  is currently transmitting."
- **Mission and sortie lifecycle**: missions, mission membership, sorties
  with waypoints and PIREPs, WMIRS sortie import so data doesn't have to be
  typed in twice.
- **Self-service registration with MFA**: CAP ID + password + a TOTP
  authenticator code, admin-approved registration requests with email
  notification — no shared logins, no Gerry-in-the-loop password resets.
- **The same real FAA charts as WxCOP**: VFR sectional and NASR aviation
  layers as map basemaps, reusing WxCOP's self-hosted chart pipeline rather
  than a generic basemap or a second copy of the same data.

## Who uses it, and how it fits together

Air Ops Branch Directors and mission staff use the live map and Ops Board
during an actual mission. Aircrew and wing staff use the self-service
registration flow to get their aircraft/tail into the roster ahead of time.
It's a separate application from WxCOP (different stack, different host
process, different team of one concern) but shares weather context from
WxCOP's API and its map chart tiles, rather than re-deriving either.

## Where this is headed

Currently **Phase 1**: a single-deployable web app, built so its core data
model (aircraft, positions, sorties, events) carries forward unchanged.
**Phase 2** is lightweight native desktop clients talking to a cloud-hosted,
CAP IT-operated server over a pub/sub channel, scaling to 50+ concurrently
tracked aircraft for large training events — using this same schema, not a
rewrite. See `REQUIREMENTS.md` for the full, detailed spec and build history
if you need more depth than this page gives.
