#!/bin/sh
#
# Pull the newest published image and restart only if it actually changed.
#
# Run hourly from UGOS Control Panel -> Task Scheduler. Safe to run as often as
# you like: when the registry has nothing new this compares two image IDs and
# exits without touching the running container.
#
#   chmod +x /volume1/docker/stock-monitor/src/scripts/autoupdate.sh
#
# UGOS ships no git, so the source refresh downloads a tarball rather than
# pulling. That only matters for docker-compose.yml and these scripts — the
# application itself arrives as a prebuilt image from GHCR.
#
set -eu

SRC="${STOCK_MONITOR_SRC:-/volume1/docker/stock-monitor/src}"
IMAGE="ghcr.io/leedeen01/stock-monitor:latest"
TARBALL="https://github.com/leedeen01/stock-monitor/archive/refs/heads/main.tar.gz"
TMP="/tmp/stock-monitor-src.tgz"

cd "$SRC" || {
    echo "autoupdate: nothing at $SRC" >&2
    exit 1
}

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        return 1
    fi
}

id_of() {
    docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null || echo none
}

before=$(id_of)

# Refresh compose and scripts. .env is not in the archive, so local config
# survives — which is also why every setting belongs in .env rather than in
# an edited compose file, since that would be overwritten here.
if fetch "$TARBALL" "$TMP"; then
    tar xzf "$TMP" -C "$SRC" --strip-components=1
    rm -f "$TMP"
else
    echo "autoupdate: source refresh failed, continuing with the image check" >&2
fi

docker compose pull --quiet

after=$(id_of)

if [ "$before" = "$after" ]; then
    # No new image, but the compose file may have moved underneath us.
    docker compose up -d >/dev/null
    exit 0
fi

echo "autoupdate: new image $(echo "$after" | cut -c8-19), restarting"
docker compose up -d

# Superseded images accumulate fast on a modest system volume.
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true

echo "autoupdate: done"
