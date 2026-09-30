#!/usr/bin/env python3
"""Explicit local database commands. NOTHING here runs on normal app startup.

  python scripts/db.py status                 where am I connected, which migration, how much data, is Redis up
  python scripts/db.py create                 create the PostgreSQL database if it does not exist (SQLite: nothing to do)
  python scripts/db.py migrate                apply pending Alembic migrations (safe: never drops data)
  python scripts/db.py seed                   add the demo accounts + demo project, only into an EMPTY database
  python scripts/db.py reset --yes [--confirm NAME] [--seed] [--with-uploads]
                                              DESTRUCTIVE: erase every table and re-create the schema. Local databases only.
"""
import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import OperationalError, ProgrammingError  # noqa: E402

from app.config import settings  # noqa: E402

LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1", "host.docker.internal"}


def url():
    return make_url(settings.database_url)


def describe() -> str:
    u = url()
    if u.get_backend_name() == "sqlite":
        return f"SQLite file {u.database}"
    return f"PostgreSQL at {u.host or 'localhost'}:{u.port or 5432}/{u.database}"          # never prints the password


def fail_unreachable(exc: Exception) -> None:
    """Explain WHICH problem it is: server down, wrong password, or the database not existing yet."""
    msg = str(getattr(exc, "orig", exc)).lower()
    print(f"\n✗ Cannot use the database ({describe()}).", file=sys.stderr)
    if "does not exist" in msg and "database" in msg:
        print("  The server is running, but this database has not been created yet.", file=sys.stderr)
        print("  → Create it:  npm run db:create        (with Docker, `npm run db:up` creates it for you)", file=sys.stderr)
    elif "password authentication failed" in msg or "authentication failed" in msg:
        print("  The server rejected the user name or password.", file=sys.stderr)
        print("  → Compare DATABASE_URL in .env.local with the real database user/password.", file=sys.stderr)
        print("  → Docker: the password is fixed when the volume is first created. Start fresh with `docker compose down -v` (erases the Docker database), then `npm run db:up`.", file=sys.stderr)
    else:
        print(f"  Reason: {type(exc).__name__} (the server is not reachable at that address/port).", file=sys.stderr)
        print("  → Start PostgreSQL:  npm run db:up   (Docker)   or start your local PostgreSQL service.", file=sys.stderr)
        print("  → Check host and port in DATABASE_URL (.env.local).", file=sys.stderr)
    sys.exit(2)


def alembic_cfg() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def revisions(conn) -> tuple[str | None, str]:
    head = ScriptDirectory.from_config(alembic_cfg()).get_current_head()
    return MigrationContext.configure(conn).get_current_revision(), head or "?"


def redis_state() -> str:
    if not settings.redis_url:
        return "not configured (in-memory limits/caches)"
    try:
        import redis
        redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).ping()
        return "ok"
    except Exception as exc:
        return f"UNAVAILABLE ({type(exc).__name__}): start it with `npm run db:up`; the app still works with in-memory fallbacks"


def cmd_status(_a) -> int:
    print(f"Environment : {settings.env}")
    print(f"Database    : {describe()}")
    code = 0
    try:
        eng = create_engine(settings.database_url)
        with eng.connect() as c:
            cur, head = revisions(c)
            state = "up to date" if cur == head else "PENDING: run `npm run db:migrate`"
            print(f"Schema      : revision {cur or 'none'} (latest {head}): {state}")
            if cur != head:
                code = 3
            if cur:
                counts = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar() for t in ("users", "projects", "batches")}
                print("Data        : " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        eng.dispose()
    except OperationalError as exc:
        fail_unreachable(exc)
    print(f"Redis       : {redis_state()}")
    print(f"AI provider : {settings.ai_provider}" + ("" if settings.ai_provider == "none" else f" (model {settings.ai_model or 'default'}, key {'set' if settings.ai_api_key or settings.ai_provider == 'local' else 'MISSING'})"))
    return code


def cmd_create(_a) -> int:
    u = url()
    if u.get_backend_name() != "postgresql":
        print("SQLite creates its file automatically: nothing to do.")
        return 0
    name = u.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        sys.exit(f"Refusing: database name {name!r} must be letters, digits and underscores only.")
    try:
        eng = create_engine(u.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            if c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first():
                print(f"✓ Database '{name}' already exists.")
            else:
                c.execute(text(f'CREATE DATABASE "{name}" ENCODING \'UTF8\' TEMPLATE template0'))
                print(f"✓ Created database '{name}' (UTF-8).")
        eng.dispose()
    except ProgrammingError as exc:
        if "permission denied" in str(exc.orig).lower():
            sys.exit(f"\n✗ The database user '{u.username}' is not allowed to create databases.\n"
                     f"  → Create it yourself as a PostgreSQL superuser:  createdb -E UTF8 -T template0 {name}\n"
                     f"  → or grant the right:  psql -c \"ALTER ROLE {u.username} CREATEDB\"   then run this again.\n"
                     "  → (Docker: `npm run db:up` creates the database automatically.)")
        raise
    except OperationalError as exc:
        fail_unreachable(exc)
    return 0


def sync_knowledge() -> None:
    """The verified IS-code notes are mirrored into the database when the API starts. After a reset/migrate while the API is already running,
    re-mirror them so the assistant is not left with an empty knowledge base until the next restart. (DB only, no network.)"""
    from app.ai import rag
    from app.database import SessionLocal
    with SessionLocal() as db:
        rag.sync_knowledge(db)


def cmd_migrate(a) -> int:
    try:
        eng = create_engine(settings.database_url)
        with eng.connect() as c:
            cur, head = revisions(c)
        eng.dispose()
    except OperationalError as exc:
        fail_unreachable(exc)
    if cur == head:
        if not getattr(a, "quiet", False):
            print(f"✓ Schema already up to date (revision {cur}).")
        return 0
    print(f"Applying migrations: {cur or 'empty database'} → {head} on {describe()} (non-destructive)")
    command.upgrade(alembic_cfg(), "head")
    sync_knowledge()
    print("✓ Migrations applied; verified IS-code notes indexed.")
    return 0


def cmd_seed(_a) -> int:
    if settings.env == "production":
        sys.exit("Refusing to seed demo data when ENV=production.")
    from sqlalchemy import func, select
    from app.database import SessionLocal
    from app.models import User
    from app.services.seed import seed_demo
    try:
        with SessionLocal() as db:
            if (db.scalar(select(func.count(User.id))) or 0) > 0:
                sys.exit("Refusing: this database already has users. Demo data is only added to an EMPTY database.\n"
                         "  To start over on purpose:  npm run db:reset")
            info = seed_demo(db)
    except OperationalError as exc:
        fail_unreachable(exc)
    print("✓ Demo data created.")
    print("  Sign in with:  admin@buildguard.demo   password: Demo@1234   (well-known demo credentials, local use only)")
    print(f"  {info}")
    return 0


def cmd_reset(a) -> int:
    u = url()
    if settings.env == "production":
        sys.exit("Refusing: ENV=production. This tool never erases a production database.")
    if u.get_backend_name() != "sqlite" and u.host not in LOCAL_HOSTS:
        sys.exit(f"Refusing: the database host is '{u.host}', which is not this machine. Reset only works on localhost.")
    if not a.yes:
        sys.exit("Refusing: reset erases ALL data. Re-run with --yes if you really mean it.")
    name = u.database or ""
    confirm = a.confirm
    if confirm is None:
        if not sys.stdin.isatty():
            sys.exit("Refusing: no terminal to confirm on. Pass --confirm <database name>.")
        confirm = input(f'This will DELETE ALL DATA in "{name}" ({describe()}).\nType the database name to continue: ').strip()
    if confirm != name and confirm != Path(name).name:
        sys.exit("Aborted: the name did not match. Nothing was changed.")
    try:
        if u.get_backend_name() == "sqlite":
            Path(name).unlink(missing_ok=True)
        else:
            eng = create_engine(settings.database_url, isolation_level="AUTOCOMMIT")
            with eng.connect() as c:
                c.execute(text("DROP SCHEMA public CASCADE"))
                c.execute(text("CREATE SCHEMA public"))
            eng.dispose()
    except OperationalError as exc:
        fail_unreachable(exc)
    print("✓ All tables removed.")
    if a.with_uploads:
        up = Path(settings.upload_dir)
        removed = 0
        for f in up.glob("**/*"):
            if f.is_file() and f.name != ".gitkeep":
                f.unlink()
                removed += 1
        print(f"✓ Removed {removed} uploaded file(s).")
    cmd_migrate(a)
    if a.seed:
        cmd_seed(a)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("create").set_defaults(fn=cmd_create)
    m = sub.add_parser("migrate")
    m.add_argument("--quiet", action="store_true")
    m.set_defaults(fn=cmd_migrate)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    r = sub.add_parser("reset")
    r.add_argument("--yes", action="store_true", help="required: acknowledge that all data will be erased")
    r.add_argument("--confirm", help="the database name (skips the interactive prompt)")
    r.add_argument("--seed", action="store_true", help="add demo data after resetting")
    r.add_argument("--with-uploads", action="store_true", help="also delete files in UPLOAD_DIR")
    r.set_defaults(fn=cmd_reset, quiet=False)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
