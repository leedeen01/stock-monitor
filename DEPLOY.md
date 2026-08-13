# Deploying to a UGREEN NASync (UGOS Pro)

Target: UGREEN NASync, Intel x86-64, UGOS Pro with the built-in Docker app.
Two containers — the app, and a Cloudflare tunnel — plus one bind-mounted
folder holding everything stateful.

```
/volume1/docker/stock-monitor/
├── src/     the repo, built on the NAS
└── data/    stocks.db, EDGAR cache, run logs   <- bind-mounted to /app/data
```

---

## What "public" means here

This deploys with no login. That is the chosen configuration: the watchlist is
not sensitive, and an open URL is the point of not needing a VPN client on
every device you want to check it from.

Assume the hostname is known. Cloudflare issues a TLS certificate for it, every
public certificate lands in Certificate Transparency logs, and those logs are
crawled continuously — so the name is searchable within minutes of creation and
will get probed by bots regardless of how obscure you make it. Plan around it
being found, not around it staying quiet.

Two consequences worth knowing up front rather than discovering:

**The write actions are open too.** Add Stock, Remove, Refresh Now and the
alert rule editor are all reachable by anyone who loads the page. Removing a
stock keeps its underlying history and `stocks.db` is small enough to back up,
so this is recoverable rather than destructive.

**Add Stock spawns processes.** Each submission runs up to four Python
subprocesses with timeouts up to five minutes, and unlike Refresh Now it has no
in-flight guard — `refreshNow` refuses to start if a run began in the last 30
minutes, `addStock` has no equivalent. Something looping on that form will pin
the NAS and get your SEC contact address rate-limited. Of everything here, this
is the one that costs more than an annoyance.

Neither requires a decision now. Both are fixable later from the Cloudflare
dashboard with no redeploy — see [Locking it down later](#locking-it-down-later).

---

## 1. On the NAS, once

1. **Update & Restore** → install the pending UGOS update. The Docker *Project*
   (Compose) UI only exists on recent builds.
2. **Control Panel → Time & Language** → set the timezone to Singapore, so the
   daily job fires when you expect.
3. **App Center** → install **Docker**.
4. Create the folders `docker/stock-monitor/src` and `docker/stock-monitor/data`.
5. SMB is already on, so `\\nastydeen` reaches them from Explorer.

## 2. Copy the database across

Fold the write-ahead log into the main file first, so you carry one file
instead of three inconsistent ones:

```bash
.venv/Scripts/python -c "import sqlite3; sqlite3.connect(r'data/stocks.db').execute('PRAGMA wal_checkpoint(TRUNCATE)')"
```

Copy `data/stocks.db` (~36 MB) into `\\nastydeen\docker\stock-monitor\data\`.

Skip `data/cache/` — it rebuilds itself, at the cost of one slow first run.

If you skip the database entirely the container will create an empty one and
seed the metric groups, and you start from a blank watchlist.

## 3. Copy the source across

Everything except `.venv`, `node_modules`, `.next` and `data` — the same set
`.dockerignore` excludes. Into `\\nastydeen\docker\stock-monitor\src\`.

## 4. Create the Cloudflare tunnel

1. Add your domain to Cloudflare (free plan is fine).
2. **Zero Trust → Networks → Tunnels → Create a tunnel** → type `Cloudflared`.
3. Copy the token out of the install command it shows you. That token is a
   credential — it grants inbound access to this container.
4. Add a **public hostname**: pick a subdomain, service type `HTTP`, URL
   `app:3000`. That name resolves on the compose network — do not use
   `localhost`, and do not use the NAS LAN IP.

Nothing else is needed on the Cloudflare side. Skip Access — the hostname
serves the app directly to anyone who requests it, which is the intent.

## 5. Deploy

Create `.env` next to `docker-compose.yml` in `src/`, from `.env.example`:

```
SEC_CONTACT=lmaodeen@gmail.com
TUNNEL_TOKEN=<the token from step 4>
DATA_DIR=/volume1/docker/stock-monitor/data
TZ=Asia/Singapore
DAILY_CRON=0 6 * * *
```

Then **Docker → Project → Create**, point it at
`/volume1/docker/stock-monitor/src`, and deploy. The first build runs `npm ci`
and `pip install` on the NAS and takes several minutes.

## 6. Verify

- Container `stock-monitor` reaches **healthy** (it polls its own homepage).
- Open the hostname in a private window — the grid should load straight away
  with your tickers, no prompt.
- Try it off your home network, on mobile data, to confirm the tunnel is
  actually serving rather than you reaching the NAS over the LAN.
- Next morning, check `data/logs/` for a new `daily_*.log`.

---

## Operating it

**Update after a code change.** Copy the changed files into `src/`, then
Project → rebuild. `data/` is untouched by rebuilds; that is the entire point
of the bind mount.

**Run the refresh by hand.** The Refresh Now button does this. From a shell:

```bash
docker exec stock-monitor /opt/venv/bin/python /app/ingest/daily_update.py
```

**Change the schedule.** Edit `DAILY_CRON` in `.env` and recreate the
container. The default `0 6 * * *` is 6am Singapore — the US close at 16:00 ET
lands at 04:00–05:00 local, so this sits comfortably after it.

**Back up.** `data/stocks.db` is the only irreplaceable file, and it is small.
Everything else rebuilds from EDGAR.

---

## When it breaks

| Symptom | Cause |
|---|---|
| Container never turns healthy | `stocks.db` missing or unreadable in the bind mount; check `DATA_DIR` matches the real path |
| Cloudflare shows 502 | App is not healthy yet — the tunnel waits on the healthcheck, so look at the app container's logs |
| Hostname unreachable from outside | Tunnel not connected — check the `cloudflared` container logs, and that the public hostname points at `app:3000` and not `localhost` |
| Daily job never runs | `docker exec stock-monitor crontab -l -u root` and `cat /app/data/logs/cron.log`. Cron gets a scrubbed environment; the entrypoint writes the needed vars into `/etc/cron.d/stock-monitor` |
| SEC requests return 403 | `SEC_CONTACT` is unset or empty — EDGAR rejects requests without a real contact address |
| `no such file or directory` on entrypoint.sh | CRLF line endings. `.gitattributes` forces LF; if you copied over SMB rather than cloning, re-copy |

## Locking it down later

All of these are Cloudflare dashboard changes. None needs a rebuild, a redeploy
or a code change, so none of it has to be decided now.

**Close it entirely.** Zero Trust → Access → Applications → Add an application
→ Self-hosted. Domain = your hostname, path empty so it covers everything.
Policy: Allow, include Emails → your address. Login method: One-time PIN, no
identity provider needed. Set a long session so you are not re-authenticating
constantly. Free up to 50 users.

**Keep it readable but stop the process-spawning.** A WAF rate-limiting rule on
`POST` requests — say 5 per minute per IP — leaves normal browsing untouched
while making the Add Stock loop described above pointless. Server actions all
arrive as POSTs to the page path, so one rule covers every write. Free tier
allows one rate-limiting rule.

**Reduce the blast radius instead.** A read-only public deployment with writes
behind auth is the other shape, but that one *is* a code change — the server
actions in `web/app/actions.ts` and `web/app/alert-actions.ts` would need a
guard. Ask if it becomes worth it.

## Known constraints

- Runs as root inside the container, so the bind mount needs no UID matching.
  Acceptable for a single-user app on a home NAS; worth revisiting if this ever
  gets exposed more widely.
- The image build happens on the NAS. Fine on any NASync CPU, just slow the
  first time. A registry (GHCR is free) would move the build to your PC.
- One container runs both Node and Python because the Add Stock and Refresh
  buttons spawn Python directly. Splitting them needs an API layer first.
