#!/usr/bin/env bash
# Back up Open Brick Designer: a PostgreSQL dump AND the data volume (model files stay files, so a dump alone is
# not a full backup). Safe while the app runs: pg_dump takes a consistent snapshot.
#
#   bash scripts/backup.sh                       # writes ./backups/obd-<stamp>.dump, obd-data-<stamp>.tgz, obd-config-<stamp>.tgz
#   BACKUP_DIR=/mnt/nas/obd bash scripts/backup.sh
#
# Settings (environment): BACKUP_DIR (default ./backups), DB_CONTAINER (ldraw-nova-db), DATA_VOLUME
# (ldraw-nova-docker_data), CONFIG_VOLUME (ldraw-nova-docker_config), DB_NAME (obd), DB_ADMIN (obd_admin; reached
# through the container's local socket, so no password is needed). The config archive holds the LLM settings and API keys
# in clear text: it is written readable by you only, keep it as private as the keys themselves. BACKUP_CONFIG=0 skips it.
# Restore with scripts/restore.sh. Windows: run it from Git Bash or WSL (Docker Desktop must be running).
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'      # Git Bash must not rewrite /tmp/... arguments

DB_CONTAINER="${DB_CONTAINER:-ldraw-nova-db}"
DATA_VOLUME="${DATA_VOLUME:-ldraw-nova-docker_data}"
CONFIG_VOLUME="${CONFIG_VOLUME:-ldraw-nova-docker_config}"
BACKUP_CONFIG="${BACKUP_CONFIG:-1}"
DB_NAME="${DB_NAME:-obd}"
DB_ADMIN="${DB_ADMIN:-obd_admin}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
# Any image with sh and tar will do; this is the database image from docker-compose.yml, so nothing else is pulled.
HELPER_IMAGE="${HELPER_IMAGE:-postgres:18-alpine@sha256:898c60cb390d1085bc97bdd6a1ae968757421df394b071848b52b1c09c89d6a5}"
stamp="$(date +%Y%m%d-%H%M%S)"

mkdir -p "${BACKUP_DIR}"
host_dir="$(cd "${BACKUP_DIR}" && { pwd -W 2>/dev/null || pwd; })"      # a path Docker Desktop can mount, also from Git Bash

echo "Dumping database ${DB_NAME} from ${DB_CONTAINER} ..."
# Streamed through stdout: the container's /tmp is a tmpfs, which `docker cp` can not read. Run from bash, not PowerShell
# 5.1 (it corrupts binary redirects).
docker exec "${DB_CONTAINER}" pg_dump -U "${DB_ADMIN}" -Fc "${DB_NAME}" > "${BACKUP_DIR}/obd-${stamp}.dump.partial"
mv "${BACKUP_DIR}/obd-${stamp}.dump.partial" "${BACKUP_DIR}/obd-${stamp}.dump"

echo "Archiving the data volume ${DATA_VOLUME} (generated, builds, inventories, templates, chats) ..."
docker run --rm --user 0 -v "${DATA_VOLUME}:/d:ro" -v "${host_dir}:/b" --entrypoint sh "${HELPER_IMAGE}" \
    -c 'cd /d && tar czf "/b/obd-data-$1.tgz" $(ls -d generated builds inventories templates chats 2>/dev/null)' sh "${stamp}"

files=("${BACKUP_DIR}/obd-${stamp}.dump" "${BACKUP_DIR}/obd-data-${stamp}.tgz")
if [ "${BACKUP_CONFIG}" != 0 ]; then
    echo "Archiving the config volume ${CONFIG_VOLUME} (LLM settings and API keys; keep this file private) ..."
    docker run --rm --user 0 -v "${CONFIG_VOLUME}:/c:ro" -v "${host_dir}:/b" --entrypoint sh "${HELPER_IMAGE}" \
        -c 'umask 077 && cd /c && tar czf "/b/obd-config-$1.tgz" .' sh "${stamp}"
    chmod 600 "${BACKUP_DIR}/obd-config-${stamp}.tgz" 2>/dev/null || true
    files+=("${BACKUP_DIR}/obd-config-${stamp}.tgz")
fi

echo "Done:"
ls -l "${files[@]}"
