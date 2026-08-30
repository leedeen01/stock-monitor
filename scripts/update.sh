#!/bin/sh
#
# Pull the newest published image and restart. Run by hand when you have just
# pushed something and want it live now.
#
# The scheduled counterpart is autoupdate.sh, which runs hourly, refreshes the
# source tree too, and stays quiet when nothing has changed. This one is
# deliberately loud: it reports whether the image actually moved, waits for the
# container to come back healthy, and surfaces any migrations that ran, because
# those are the things worth seeing after a manual update.
#
#   chmod +x /volume1/docker/stock-monitor/src/scripts/update.sh
#
set -eu

SRC="${STOCK_MONITOR_SRC:-/volume1/docker/stock-monitor/src}"
IMAGE="ghcr.io/leedeen01/stock-monitor:latest"
# compose addresses the service, docker addresses the container. They are
# not the same name here, and mixing them fails with "no such service".
SERVICE="app"
CONTAINER="stock-monitor"

cd "$SRC" 2>/dev/null || {
    echo "update: nothing at $SRC" >&2
    exit 1
}

docker info >/dev/null 2>&1 || {
    echo "update: cannot reach the Docker daemon." >&2
    echo "        Run as a user in the docker group, or prefix with sudo." >&2
    exit 1
}

short() { echo "$1" | sed 's/^sha256://' | cut -c1-12; }
id_of() { docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo none; }

before=$(id_of)
echo "update: currently running $(short "$before")"

echo "update: pulling..."
docker compose pull

after=$(id_of)
if [ "$before" = "$after" ]; then
    echo "update: already on the newest image, nothing to pull"
else
    echo "update: new image $(short "$after")"
fi

# Always run: the compose file or .env may have changed even when the image
# has not, and `up -d` is a no-op when genuinely nothing differs.
docker compose up -d

# A container that starts is not the same as one that works. Migrations run at
# boot, so a bad one shows up here rather than at the next page load.
printf 'update: waiting for health'
i=0
while [ "$i" -lt 60 ]; do
    state=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
    case "$state" in
        healthy)   echo " - healthy"; break ;;
        unhealthy) echo " - UNHEALTHY"; docker compose logs --tail 40 "$SERVICE"; exit 1 ;;
        missing)   echo " - container not found"; exit 1 ;;
        *)         printf '.'; sleep 2; i=$((i + 1)) ;;
    esac
done
[ "$i" -lt 60 ] || { echo " - timed out"; docker compose logs --tail 40 "$SERVICE"; exit 1; }

# The reason to read the log after an update is almost always a migration.
migrations=$(docker compose logs --tail 200 "$SERVICE" 2>/dev/null | grep -i "migration" || true)
if [ -n "$migrations" ]; then
    echo
    echo "update: migrations this boot"
    echo "$migrations" | sed 's/^/  /'
fi

echo
echo "update: done - https://stockwatch.leedeen.dev/"
