#!/bin/sh
#
# Pull the newest published image and restart only if it actually changed.
#
# Run hourly from UGOS Control Panel -> Task Scheduler. Safe to run as often as
# you like: when the registry has nothing new, this compares two image IDs and
# exits without touching the running container.
#
#   chmod +x /volume1/docker/stock-monitor/src/scripts/autoupdate.sh
#
set -eu

SRC="${STOCK_MONITOR_SRC:-/volume1/docker/stock-monitor/src}"
IMAGE="ghcr.io/leedeen01/stock-monitor:latest"

cd "$SRC" || {
    echo "autoupdate: no checkout at $SRC" >&2
    exit 1
}

id_of() {
    docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo none
}

before=$(id_of)

# Compose config lives in git, so refresh it first — a change to the compose
# file or the entrypoint arrives that way rather than inside the image. .env is
# gitignored, so local secrets survive this.
git pull --quiet origin main || echo "autoupdate: git pull failed, continuing with image check" >&2

docker compose pull --quiet

after=$(id_of)

if [ "$before" = "$after" ]; then
    # Compose may still need applying if the yml changed without a new image.
    docker compose up -d --quiet-pull >/dev/null
    exit 0
fi

echo "autoupdate: new image $(echo "$after" | cut -c8-19), restarting"
docker compose up -d

# Superseded images accumulate fast on a NAS with a modest system volume.
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

echo "autoupdate: done"
