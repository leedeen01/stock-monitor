#!/bin/sh
#
# Starts the daily cron job, then hands the container over to the Next server.
set -eu

DATA_DIR=/app/data
DB_PATH="${STOCK_MONITOR_DB:-$DATA_DIR/stocks.db}"
VENV_PY="${STOCK_MONITOR_PYTHON:-/opt/venv/bin/python}"

mkdir -p "$DATA_DIR/logs" "$DATA_DIR/cache"

# db.ts opens with fileMustExist, so an empty bind mount would turn into a 500
# on every page rather than anything diagnosable. Seed a schema instead: that
# makes a from-scratch deploy work, and a deploy where stocks.db was copied in
# skips this entirely.
if [ ! -f "$DB_PATH" ]; then
    echo "entrypoint: no database at $DB_PATH - creating an empty one"
    echo "entrypoint: (if you meant to bring existing data, stop here and copy"
    echo "entrypoint:  stocks.db into the host folder mapped to /app/data)"
    cd /app/ingest
    "$VENV_PY" db.py
    "$VENV_PY" groups.py || echo "entrypoint: group seeding failed, continuing"
fi

# Cron runs jobs with a near-empty environment — it does not inherit the
# container's. Anything daily_update.py needs has to be written into the
# crontab explicitly, or the job runs against the wrong database and quietly
# gets 403'd by the SEC for a missing contact address.
#
# /etc/cron.d entries carry a user column that user crontabs do not.
cat > /etc/cron.d/stock-monitor <<EOF
SHELL=/bin/sh
PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin
TZ=${TZ:-UTC}
STOCK_MONITOR_DB=${DB_PATH}
STOCK_MONITOR_PYTHON=${VENV_PY}
SEC_CONTACT=${SEC_CONTACT:-}
${DAILY_CRON:-0 6 * * *} root cd /app/ingest && ${VENV_PY} daily_update.py >> ${DATA_DIR}/logs/cron.log 2>&1
EOF
chmod 0644 /etc/cron.d/stock-monitor

echo "entrypoint: daily refresh scheduled at '${DAILY_CRON:-0 6 * * *}' (${TZ:-UTC})"
cron

cd /app/web
echo "entrypoint: starting Next on ${HOSTNAME:-0.0.0.0}:${PORT:-3000}"
exec node server.js
