from collections.abc import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

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


async def init_db() -> None:
    """Bootstraps the postgis extension only. Table/constraint management now lives in
    Alembic migrations (backend/alembic/) -- see backend/ALEMBIC.md. This intentionally
    no longer calls Base.metadata.create_all(); `alembic upgrade head` owns that now."""
    async with engine.begin() as conn:
        from sqlalchemy import text

        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
