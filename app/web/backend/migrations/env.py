"""Alembic environment: online migrations only (no offline SQL mode), on the app's own engine."""
from alembic import context

from db import engine as engine_module
from db.models import metadata


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name == "alembic_version")


def run() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:                                      # `alembic revision --autogenerate` from a shell
        engine = engine_module.get_engine()
        if engine is None:
            raise SystemExit("No database: set LDRAW_NOVA_DATABASE_URL (or provide the password file).")
        with engine.connect() as connection:
            configure_and_run(connection)
            connection.commit()
    else:
        configure_and_run(connection)


def configure_and_run(connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, compare_type=True, compare_server_default=True,
                      include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not supported: run against a database.")
run()
