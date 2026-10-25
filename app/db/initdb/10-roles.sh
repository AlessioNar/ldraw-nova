#!/bin/sh
# Runs once, when the database directory is first created (docker-entrypoint-initdb.d), as the postgres user.
# Creates the role the app connects as. It owns the database `obd` (so migrations can create tables) but is not a
# superuser and can not create roles or databases. The bootstrap superuser obd_admin is for the local socket only
# (docker exec: backups, restore): its network logins are rejected below.
set -eu
password="$(cat /run/secrets/db_password)"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --set=app_password="$password" <<'SQL'
CREATE ROLE obd_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER DATABASE obd OWNER TO obd_app;
REVOKE ALL ON DATABASE obd FROM PUBLIC;
SQL
sed -i '1i host all obd_admin all reject' "$PGDATA/pg_hba.conf"
