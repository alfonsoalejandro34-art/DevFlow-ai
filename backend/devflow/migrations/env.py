"""Alembic environment. The connection is supplied by ``Database.migrate``; running the
``alembic`` CLI directly falls back to the configured URL."""

from __future__ import annotations

from alembic import context
from devflow.database.models import Base
from sqlalchemy import Connection, create_engine

config = context.config
target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        from devflow.config import get_settings

        url = get_settings().sqlalchemy_url
    engine = create_engine(url)
    with engine.begin() as conn:
        _run(conn)
