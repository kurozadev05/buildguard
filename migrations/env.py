from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registers tables)
from app.config import settings
from app.database import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    _url = settings.db_url
    engine = create_engine(_url, poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=_url.startswith("sqlite"), compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
