import asyncio
import logging
from collections.abc import AsyncGenerator

from sqlalchemy import MetaData, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Deterministic constraint names (Alembic's recommended convention:
# https://alembic.sqlalchemy.org/en/latest/naming.html) -- without this, every
# unnamed constraint gets a Postgres-assigned name Alembic can't reliably reference in
# downgrade()s, and autogenerate diffs get noisy trying to guess at them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# Startup DB-connection retry (added 2026-11-11, after a real incident: a power
# failure took down both r815 and data2; once r815 came back up first, the
# backend's very first DB connection attempt here failed with a transient
# asyncio.CancelledError out of asyncpg/uvloop's own connect path, which isn't
# caught by a plain `except Exception` -- CancelledError has inherited from
# BaseException, not Exception, since Python 3.8, specifically so broad except
# clauses don't accidentally swallow task cancellation. Previously, any failure
# here crashed the whole process; systemd's Restart=always then just
# re-launched it from scratch every ~60s, burning ~32 minutes before
# cross-host connectivity to data2 happened to settle. Retrying in-process
# instead avoids tearing down and respawning the whole app on every attempt,
# and uses a real backoff instead of a flat interval.
DB_CONNECT_MAX_ATTEMPTS = 12
DB_CONNECT_BACKOFF_SECONDS = (2, 4, 8, 15, 30)  # last value repeats for remaining attempts


async def init_db() -> None:
    """Bootstraps the postgis extension only. Table/constraint management now lives in
    Alembic migrations (backend/alembic/) -- see backend/ALEMBIC.md. This intentionally
    no longer calls Base.metadata.create_all(); `alembic upgrade head` owns that now."""
    for attempt in range(1, DB_CONNECT_MAX_ATTEMPTS + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            return
        except (OSError, asyncio.TimeoutError, asyncio.CancelledError, OperationalError) as exc:
            if attempt == DB_CONNECT_MAX_ATTEMPTS:
                logger.error("Database still unreachable after %d attempts, giving up: %s", attempt, exc)
                raise
            delay = DB_CONNECT_BACKOFF_SECONDS[min(attempt - 1, len(DB_CONNECT_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "Database not reachable yet (attempt %d/%d): %s -- retrying in %ds",
                attempt,
                DB_CONNECT_MAX_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
