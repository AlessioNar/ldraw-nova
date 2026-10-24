#!/usr/bin/env bash
# Starts one Xvfb for the lifetime of the container (instead of spinning one
# up per `leocad` call), then execs whatever the container's main process is
# — a shell, a Python worker script, `sleep infinity`, or a direct
# `leocad ...` invocation.
#
# DISPLAY comes from the image ENV (see Dockerfile) so that `docker exec`
# shells, which skip this script, point at the same Xvfb.
set -euo pipefail

export DISPLAY="${DISPLAY:-:99}"

# --- Keep agent-written code away from the app's own API ----------------------
# The API has no login and agent code runs in the same network namespace, so a
# prompt-injected script could call it (change a model's endpoint, read or delete
# chats). Reject TCP from the `agent` user to the app port on every local address,
# and to the Docker host (a published port on the host leads back to this API).
# Needs CAP_NET_ADMIN, held only for this step: the server is started below with
# NET_ADMIN removed from its bounding set. If the rules cannot be applied the app
# still starts, warns here, and /api/environment reports "off" (shown in Settings).
APP_PORT="${LDRAW_NOVA_APP_PORT:-8000}"
AGENT_USER="${LDRAW_NOVA_AGENT_USER:-agent}"
ISOLATION_FILE="${LDRAW_NOVA_ISOLATION_FILE:-/run/ldraw-nova-isolation}"

host_addresses() {
    getent ahostsv4 host.docker.internal 2>/dev/null | awk '{print $1}' | sort -u
    # the default gateway (the Docker host on a bridge network)
    local g
    g="$(awk 'NR>1 && $2=="00000000" {print $3; exit}' /proc/net/route 2>/dev/null || true)"
    if [ -n "${g}" ]; then printf '%s\n' "$((0x${g:6:2})).$((0x${g:4:2})).$((0x${g:2:2})).$((0x${g:0:2}))"; fi
    return 0
}

apply_isolation() {
    id -u "${AGENT_USER}" >/dev/null 2>&1 || { echo "no ${AGENT_USER} user"; return 1; }
    command -v iptables >/dev/null || { echo "iptables is not installed"; return 1; }
    local rule=(OUTPUT -m owner --uid-owner "${AGENT_USER}" -p tcp --dport "${APP_PORT}" -m addrtype --dst-type LOCAL -j REJECT --reject-with tcp-reset)
    iptables -C "${rule[@]}" 2>/dev/null || iptables -I "${rule[@]}" 2>/dev/null \
        || { echo "iptables refused the rule (is NET_ADMIN granted?)"; return 1; }
    local note="API port ${APP_PORT} blocked for ${AGENT_USER}"
    if command -v ip6tables >/dev/null && ! { ip6tables -C "${rule[@]}" 2>/dev/null || ip6tables -I "${rule[@]}" 2>/dev/null; }; then
        if [ -e /proc/net/if_inet6 ] && grep -q '^00000000000000000000000000000001' /proc/net/if_inet6; then
            echo "ip6tables refused the rule while IPv6 is enabled"; return 1
        fi
    fi
    if [ "${LDRAW_NOVA_SANDBOX_ALLOW_HOST:-0}" != "1" ]; then
        local addr
        for addr in $(host_addresses); do
            case "${addr}" in 127.*|0.0.0.0) continue;; esac
            iptables -C OUTPUT -m owner --uid-owner "${AGENT_USER}" -p tcp -d "${addr}" -j REJECT --reject-with tcp-reset 2>/dev/null \
                || iptables -I OUTPUT -m owner --uid-owner "${AGENT_USER}" -p tcp -d "${addr}" -j REJECT --reject-with tcp-reset 2>/dev/null \
                || { echo "iptables refused the Docker host rule"; return 1; }
            note="${note}; Docker host ${addr} blocked"
        done
    fi
    echo "${note}"
}

mkdir -p "$(dirname "${ISOLATION_FILE}")"
if reason="$(apply_isolation)"; then
    echo "on ${reason}" > "${ISOLATION_FILE}"
    echo "Sandbox isolation: on (${reason})"
else
    echo "off ${reason}" > "${ISOLATION_FILE}"
    echo "WARNING: Sandbox isolation is OFF (${reason}). Agent code can reach the app's own API; run with cap_add: [NET_ADMIN] to enable it." >&2
fi

# A stopped-then-restarted container keeps its filesystem, including the
# previous Xvfb's lock/socket, which would make the new Xvfb refuse to start.
DISPLAY_NUM="${DISPLAY#:}"
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}"

Xvfb "${DISPLAY}" -screen 0 1920x1080x24 &
XVFB_PID=$!

cleanup() {
    kill "${XVFB_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for the X server to actually be ready before handing off.
for _ in $(seq 1 50); do
    if xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done

# The server (and everything it starts) can no longer change the firewall.
if command -v setpriv >/dev/null && setpriv --bounding-set=-net_admin true 2>/dev/null; then
    exec setpriv --bounding-set=-net_admin "$@"
fi
exec "$@"
