# syntax=docker/dockerfile:1
#
# One image, two runtimes.
#
# The tidy-looking design is a web container and a separate ingest container,
# but the Add Stock and Refresh Now buttons spawn Python directly
# (web/app/actions.ts), so the web process needs the interpreter and the whole
# ingest/ tree regardless. Splitting them would mean building an IPC layer to
# gain nothing.
#
# SQLite forces the same conclusion from the other direction: WAL mode uses a
# shared-memory file, so every reader and writer has to sit on one real local
# filesystem. Two containers on one bind mount is fine. Two hosts is not.
#
# Target is a UGREEN NASync (Intel x86-64), so no ARM cross-build to worry about.

# --------------------------------------------------------------- web build --
FROM node:22-bookworm-slim AS web-build

WORKDIR /build

# better-sqlite3 ships prebuilt binaries for common ABIs but falls back to
# compiling from source. Give node-gyp what it needs rather than discovering
# the gap at build time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
# Every page is force-dynamic, so the build never opens the database — which
# matters, because there is no database in the build context.
RUN npm run build


# ------------------------------------------------------------ python build --
# Same base as the runtime stage on purpose. A venv records an absolute path to
# its interpreter, so building it against a different image would produce a
# venv whose python does not exist where it lands.
FROM node:22-bookworm-slim AS py-build

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 python3-venv build-essential ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /tmp/requirements.txt


# ----------------------------------------------------------------- runtime --
FROM node:22-bookworm-slim AS runtime

# python3 for the pipeline, cron for the daily job, tini to reap the Python
# processes that server actions spawn — without an init, those become zombies.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 cron tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=py-build /opt/venv /opt/venv

WORKDIR /app

# Layout matters. actions.ts derives PROJECT_ROOT as cwd/.., and config.py
# derives DATA_DIR as ingest/../data, so /app/web + /app/ingest + /app/data is
# not a preference — it is what both sides already assume.
COPY ingest/ /app/ingest/

# Next standalone output: a traced dependency tree, ~50 MB against the 376 MB
# of node_modules it replaces. static/ and public/ are excluded from the trace
# and have to be copied alongside it.
COPY --from=web-build /build/.next/standalone /app/web/
COPY --from=web-build /build/.next/static     /app/web/.next/static
COPY --from=web-build /build/public           /app/web/public

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Fail here rather than on the first page load. serverExternalPackages keeps
# better-sqlite3 out of the bundle, so it has to have survived the trace into
# standalone/node_modules with its .node binary intact.
RUN node -e "require('/app/web/node_modules/better-sqlite3'); console.log('better-sqlite3 binding ok')" \
 && /opt/venv/bin/python -c "import yfinance, lxml; print('python deps ok')"

# HOSTNAME matters: the standalone server binds localhost by default, which the
# cloudflared sidecar cannot reach across the compose network.
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    STOCK_MONITOR_DB=/app/data/stocks.db \
    STOCK_MONITOR_PYTHON=/opt/venv/bin/python \
    DAILY_CRON="0 6 * * *"

# Bind-mounted from the NAS. Declared so a run without the mount still starts
# instead of failing on a missing directory.
VOLUME ["/app/data"]

EXPOSE 3000

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
