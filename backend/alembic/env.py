import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# `alembic` is a console-script entry point, not a `python script.py` invocation --
# unlike our scripts/*.py files, the backend dir isn't automatically on sys.path here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import all models so they register on Base.metadata before autogenerate runs.
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import *  # noqa: E402, F401, F403 -- import side effect, not for names

# GeoAlchemy2 support: without these, autogenerate will (a) mishandle Geometry column
# comparisons and (b) try to DROP PostGIS's own internal tables (spatial_ref_sys etc.)
# since they're not in our metadata. See
# https://geoalchemy-2.readthedocs.io/en/latest/alembic.html
from geoalchemy2.alembic_helpers import include_object, render_item, writer  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Single source of truth for the DB URL is app/config.py (.env), not a second copy in
# alembic.ini -- avoids the two drifting apart.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
        include_object=include_object,
        process_revision_directives=writer,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_item=render_item,
        include_object=include_object,
        process_revision_directives=writer,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
