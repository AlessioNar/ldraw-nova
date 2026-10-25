#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh: the database dump and (if given or found next to it) the data archive.
#
#   bash scripts/restore.sh [-y] backups/obd-20261024-101500.dump [backups/obd-data-20261024-101500.tgz]
#
# Without the second argument the data archive with the same stamp next to the dump is used if it exists; pass
# `none` to restore the database only. This REPLACES the database. The data archive is unpacked over /data (files
# in the backup overwrite the same names; other files stay). If obd-config-STAMP.tgz sits next to the dump, the LLM
# settings and API keys in it are unpacked over /config the same way (CONFIG_VOLUME). The app container is stopped during the restore and
# started again afterwards if it was running. Without -y you are asked to confirm.
# Settings (environment): DB_CONTAINER (ldraw-nova-db), APP_CONTAINER (ldraw-nova-app; empty = leave it alone),
# DATA_VOLUME (ldraw-nova-docker_data), DB_NAME (obd), DB_ADMIN (obd_admin), DB_OWNER (obd_app).
set -euo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'

DB_CONTAINER="${DB_CONTAINER:-ldraw-nova-db}"
APP_CONTAINER="${APP_CONTAINER-ldraw-nova-app}"
DATA_VOLUME="${DATA_VOLUME:-ldraw-nova-docker_data}"
CONFIG_VOLUME="${CONFIG_VOLUME:-ldraw-nova-docker_config}"
DB_NAME="${DB_NAME:-obd}"
DB_ADMIN="${DB_ADMIN:-obd_admin}"
DB_OWNER="${DB_OWNER:-obd_app}"
HELPER_IMAGE="${HELPER_IMAGE:-postgres:18-alpine@sha256:898c60cb390d1085bc97bdd6a1ae968757421df394b071848b52b1c09c89d6a5}"

yes=0
if [ "${1:-}" = "-y" ]; then yes=1; shift; fi
dump="${1:-}"
[ -n "${dump}" ] && [ -f "${dump}" ] || { echo "usage: restore.sh [-y] <obd-STAMP.dump> [<obd-data-STAMP.tgz>|none]" >&2; exit 2; }
data="${2:-}"
if [ -z "${data}" ]; then
    candidate="$(dirname "${dump}")/$(basename "${dump}" .dump | sed 's/^obd-/obd-data-/').tgz"
    [ -f "${candidate}" ] && data="${candidate}" || data="none"
fi
[ "${data}" = "none" ] || [ -f "${data}" ] || { echo "no such data archive: ${data}" >&2; exit 2; }
config=""
if [ "${data}" != "none" ]; then
    candidate="$(dirname "${data}")/$(basename "${data}" .tgz | sed 's/^obd-data-/obd-config-/').tgz"
    [ -f "${candidate}" ] && config="${candidate}"
fi

echo "This replaces database '${DB_NAME}' in ${DB_CONTAINER} with ${dump}"
[ "${data}" = "none" ] && echo "and leaves the data volume alone." || echo "and unpacks ${data} over volume ${DATA_VOLUME}."
[ -z "${config}" ] || echo "It also unpacks ${config} (settings and API keys) over volume ${CONFIG_VOLUME}."
if [ "${yes}" != 1 ]; then
    read -r -p "Continue? [y/N] " answer
    [ "${answer}" = "y" ] || { echo "Aborted."; exit 1; }
fi

was_running=0
if [ -n "${APP_CONTAINER}" ] && [ "$(docker inspect -f '{{.State.Running}}' "${APP_CONTAINER}" 2>/dev/null || echo false)" = true ]; then
    was_running=1
    echo "Stopping ${APP_CONTAINER} ..."
    docker stop "${APP_CONTAINER}" >/dev/null
fi

echo "Restoring the database ..."
docker exec "${DB_CONTAINER}" sh -ec "
    dropdb -U '${DB_ADMIN}' --if-exists --force '${DB_NAME}'
    createdb -U '${DB_ADMIN}' -O '${DB_OWNER}' '${DB_NAME}'"
docker exec -i "${DB_CONTAINER}" pg_restore -U "${DB_ADMIN}" -d "${DB_NAME}" --no-owner --role="${DB_OWNER}" < "${dump}"

if [ "${data}" != "none" ]; then
    echo "Restoring the data volume ..."
    data_dir="$(cd "$(dirname "${data}")" && { pwd -W 2>/dev/null || pwd; })"
    docker run --rm --user 0 -v "${DATA_VOLUME}:/d" -v "${data_dir}:/b:ro" --entrypoint sh "${HELPER_IMAGE}" \
        -c 'tar xzf "/b/$1" -C /d' sh "$(basename "${data}")"
fi

if [ -n "${config}" ]; then
    echo "Restoring the config volume ..."
    config_dir="$(cd "$(dirname "${config}")" && { pwd -W 2>/dev/null || pwd; })"
    docker run --rm --user 0 -v "${CONFIG_VOLUME}:/c" -v "${config_dir}:/b:ro" --entrypoint sh "${HELPER_IMAGE}" \
        -c 'tar xzf "/b/$1" -C /c' sh "$(basename "${config}")"
fi

if [ "${was_running}" = 1 ]; then
    echo "Starting ${APP_CONTAINER} ..."
    docker start "${APP_CONTAINER}" >/dev/null
fi
echo "Restore finished."
