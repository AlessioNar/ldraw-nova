# Migrations (Alembic)

Revisions: `0001` the initial schema, `0002` (phase 2) `build_draft.extra` and `proposal.chat_id` without a foreign key. The reference-SQL test compares `0001` only.

`alembic upgrade head` runs when the app starts (`db/migrate.py`, under a PostgreSQL advisory lock). Models are in `db/models.py`; the first revision is equivalent to `docs/plans/active/postgres/sql/0001_init.sql` (a test compares the two).

Changing the schema, from `app/web/backend` against a throwaway database:

```
export LDRAW_NOVA_DATABASE_URL=postgresql://postgres:test@localhost:5432/scratch   # a scratch database
python -m db.migrate                                  # bring it to the current head
alembic revision --autogenerate -m "what changed"     # then READ the file and fix it by hand
python -m db.migrate && pytest tests/test_database.py -k "migrations_agree or reference"
```

Autogenerate is a draft: check that it did not drop and recreate a column instead of renaming it, that data-moving steps are written as SQL, that constraint names follow `db/models.py`'s naming convention, and that `downgrade()` undoes the change. A migration that rewrites data should say so in its docstring (the owner takes a backup first: `scripts/backup.sh`).
