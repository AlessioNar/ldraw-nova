#!/bin/sh
# The opt-in `backup` compose profile: a dump a day (newest 7 kept), plus a weekly one on Sundays (newest 4 kept),
# written to /backups (the volume ldraw-nova-docker_backups). Dumps contain no API keys (those stay in /config).
set -eu
export PGPASSWORD="$(cat /run/secrets/db_password)" PGHOST=db PGUSER=obd_app PGDATABASE=obd
while :; do
    day="$(date +%F)"
    if pg_dump -Fc -f "/backups/obd-$day.dump.partial"; then
        mv "/backups/obd-$day.dump.partial" "/backups/obd-$day.dump"
        [ "$(date +%u)" = 7 ] && cp "/backups/obd-$day.dump" "/backups/obd-weekly-$day.dump"
        ls -1t /backups/obd-2*.dump 2>/dev/null | tail -n +8 | xargs -r rm -f
        ls -1t /backups/obd-weekly-*.dump 2>/dev/null | tail -n +5 | xargs -r rm -f
        echo "backup $day done"
    else
        rm -f "/backups/obd-$day.dump.partial"; echo "backup $day FAILED" >&2
    fi
    sleep 86400
done
