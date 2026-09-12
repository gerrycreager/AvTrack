# Alembic — reference for AvTrack

You don't need to know Alembic deeply to use it here day-to-day. This covers the one
workflow you'll actually run, a command cheat sheet, and the gotchas specific to this
project. Written 2026-09-13 after setting this up live, including a real bug it caught
immediately — see "A worked example" below, that's the best explanation there is.

## What Alembic actually does

`backend/app/models.py` describes the schema you *want* (in Python). The Postgres
database has whatever schema it *actually has*. Alembic's job is closing the gap
between those two, safely, with a history you can replay or reverse. Each gap-closing
step is one **migration** — a small Python file in `backend/alembic/versions/`, each
one stamped with the migration before it, forming a chain. `alembic upgrade head` walks
the database forward through that whole chain to the latest one.

Before today, this project used `Base.metadata.create_all()` at app startup instead —
fine for *creating tables that don't exist yet*, but it can never *change* a table that
already exists (add a column, widen one, add a constraint). Every schema change so far
had to be hand-patched with raw `psql`/`ALTER TABLE`, or a `DROP TABLE` + restart. That
stops now — `init_db()` in `app/db.py` no longer calls `create_all()` at all.

## The workflow you'll actually use

1. Edit `app/models.py` the way you normally would (add a column, add a table, change
   a type, add an index).
2. Generate a migration that captures the diff:
   ```
   cd backend && source .venv/bin/activate
   alembic revision --autogenerate -m "add sortie notes field"
   ```
3. **Open the generated file in `alembic/versions/` and read it.** Autogenerate is
   good but not infallible — see Gotchas below. Fix anything wrong before moving on.
4. Apply it:
   ```
   alembic upgrade head
   ```
5. Commit the migration file to git along with the models.py change that caused it —
   they're one unit of work, always commit them together.

That's it. You will almost never write a migration by hand from scratch; you'll almost
always start from `--autogenerate` and edit the result.

## Command cheat sheet

| Command | What it does |
|---|---|
| `alembic upgrade head` | Bring the DB up to the latest migration |
| `alembic downgrade -1` | Undo the most recent migration |
| `alembic current` | Show which migration the DB is currently at |
| `alembic history` | List all migrations in order |
| `alembic revision --autogenerate -m "..."` | Generate a migration from the models.py diff |
| `alembic revision -m "..."` | Generate a **blank** migration to hand-write (rare) |

All of these run from `backend/` with the venv active — same as any other project
script.

## Gotchas specific to this project

- **The DB URL comes from `.env`, not `alembic.ini`.** `alembic/env.py` overrides
  `alembic.ini`'s URL with `app.config.settings.database_url` on every run, so there's
  one source of truth. Don't bother editing the URL in `alembic.ini`, it's unused.
- **PostGIS needs help.** `Airfield.location`/`Position.location` are GeoAlchemy2
  `Geometry` columns backed by PostGIS internals (`spatial_ref_sys` and friends).
  Without `geoalchemy2.alembic_helpers` wired into `env.py` (already done), autogenerate
  would try to `DROP TABLE` PostGIS's own system tables since they're not in our
  `Base.metadata`. If you ever regenerate `env.py` from scratch, re-add the
  `render_item`/`include_object`/`process_revision_directives=writer` wiring — see the
  comments in `alembic/env.py` and https://geoalchemy-2.readthedocs.io/en/latest/alembic.html.
- **Autogenerate can misread a rename as a drop+add.** If you rename a column,
  autogenerate will usually generate `drop_column` + `add_column` instead of
  `alter_column(..., new_column_name=...)` — the first version silently discards data
  in that column. Always read the generated file; for a rename, rewrite it as an
  `alter_column` (or `op.alter_column(table, old_name, new_column_name=new_name)`) by
  hand.
- **Constraint names are deterministic now.** `app/db.py`'s `Base` has a
  `naming_convention` set (Alembic's own recommended one). This is why today's baseline
  migration could safely repair the missing foreign key — without it, Postgres
  auto-assigns constraint names that Alembic can't reliably reference for
  `downgrade()`. Don't remove this.
- **This is separate from the data-import scripts.** `scripts/import_*.py` (callsign
  CSV, NASR, OurAirports) load *rows* into an existing schema — they have nothing to do
  with Alembic, which manages the *shape* of the tables. Don't confuse "run a new NASR
  cycle" with "run a migration"; they're unrelated maintenance tasks that happen to
  both be recurring.

## A worked example (what actually happened setting this up)

The very first `alembic revision --autogenerate` run here found real drift: back when
a `VARCHAR(4)` column-width bug got fixed, the fix included `DROP TABLE airfields
CASCADE`, which also silently dropped the foreign key from `tracking_events.airfield_id`
to `airfields.id`. The table got recreated by `create_all()` on the next restart, but
`create_all()` only creates *missing tables* — it never noticed the *existing*
`tracking_events` table was now missing a constraint the model said it should have.
That gap sat there undetected until Alembic's diff caught it on the very first run.
The generated migration's `upgrade()` re-added exactly that constraint, and running
`alembic upgrade head` fixed it for real. A second `--autogenerate` afterward produced
an empty migration — proof the database and `models.py` fully agree — which is exactly
the sanity check to run any time you're unsure whether something's drifted.
