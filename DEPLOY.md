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

## 3. Get the source onto the NAS

The application arrives as a prebuilt image from GHCR, but the NAS still needs
`docker-compose.yml`, `.env` and `scripts/`.

**UGOS ships no `git`**, so download the tarball instead. The repo is public, so
no credentials:

```bash
cd /volume1/docker/stock-monitor
curl -L https://github.com/leedeen01/stock-monitor/archive/refs/heads/main.tar.gz -o main.tar.gz
mkdir -p src && tar xzf main.tar.gz -C src --strip-components=1 && rm main.tar.gz
```

`--strip-components=1` drops the `stock-monitor-main/` wrapper directory the
archive carries. If `curl` is missing, `wget -O main.tar.gz <url>` does the same.

Two alternatives, neither necessary:

- **Real git, via Docker** — `docker run --rm -v /volume1/docker/stock-monitor:/w
  -w /w alpine/git clone https://github.com/leedeen01/stock-monitor.git src`.
  Worth it only if you want to make local commits on the NAS.
- **`sudo apt install git`** — UGOS is Debian-based so this may work, but it is
  not a supported surface and firmware updates can revert it.

Without SSH: download the ZIP from GitHub and copy the contents into
`\\nastydeen\docker\stock-monitor\src\`.

## 4. Set up Cloudflare

### A. Get a domain into Cloudflare

A named tunnel needs a zone in your account. The app lives on a subdomain like
`stocks.yourdomain.com`, but the zone has to exist.

- **Register through Cloudflare** — Domain Registration → Register Domains.
  Wholesale pricing, no markup, ~$10–12/yr for a `.com`, and it arrives already
  configured.
- **Register elsewhere** (Porkbun, Namecheap — a `.xyz` can be a couple of
  dollars), then **Add a domain** in Cloudflare and change the nameservers at
  your registrar.

Wait for the zone to read **Active** before step C. A hostname created against
a pending zone looks correct and simply fails to resolve.

### B. Create the tunnel

1. Main dashboard → **Networking → Tunnels**
   (`dash.cloudflare.com/?to=/:account/tunnels`).
2. **Create a tunnel** → connector type **Cloudflared**.
3. Name it — `nastydeen`.
4. Copy **only the token** from the install command it shows: the long string
   after `--token`. Do not run the command; compose already runs cloudflared.

> Tunnel management moved into the main dashboard on 2026-02-20. Older guides
> say *Zero Trust → Networks → Tunnels*; that section is now called
> **Connectors** and is for access policies and private networks. A plain
> public hostname needs no Zero Trust onboarding.

### C. Add the public hostname

| Field | Value |
|---|---|
| Subdomain | `stocks`, or anything you like |
| Domain | your zone |
| Path | *leave empty* |
| Service type | `HTTP` — not HTTPS; the hop is inside Docker |
| URL | `app:3000` |

`app` is the compose service name, resolved by Docker's internal DNS.
`localhost` would point cloudflared at itself and return 502; the NAS LAN IP
fails too, because the app publishes no ports.

Saving creates the DNS record automatically. If offered the option to protect
the hostname with Access, decline — that is the login gate, and this deployment
is intentionally public.

### D. Hand the token to Docker

It goes in `.env` as `TUNNEL_TOKEN` (next step). The tunnel reads **Down** until
`cloudflared` starts — expected before deploying, not a fault.

**The token is a credential.** Anyone holding it can run a connector for your
tunnel. `.env` is gitignored; never put it in the compose file or a commit.

## 5. Deploy

Create `.env` next to `docker-compose.yml` in `src/`, from `.env.example`:

```
SEC_CONTACT=lmaodeen@gmail.com
TUNNEL_TOKEN=<the token from step 4>
DATA_DIR=/volume1/docker/stock-monitor/data
TZ=Asia/Singapore
DAILY_CRON=0 6 * * *
AUTH_PASSWORD=<pick one>
AUTH_SECRET=<openssl rand -hex 32>
```

`AUTH_*` gates the private views only — the watchlist, alerts and deep dives
stay public. Leave them unset and login is disabled and `/portfolio` stays
shut: it fails closed rather than open. Changing `AUTH_SECRET` signs out every
existing session, which is also how you revoke one.

### Package visibility — already handled

The image is built by GitHub Actions and published to
`ghcr.io/leedeen01/stock-monitor`. GHCR packages are often private by default,
which would make the NAS fail with `denied` on pull — but this one came out
public, verified by fetching its manifest anonymously. **The NAS needs no
`docker login`.**

If that ever changes: GitHub → your profile → **Packages** → `stock-monitor` →
**Package settings** → **Change visibility** → Public. Or keep it private and
run `docker login ghcr.io` on the NAS with a token carrying `read:packages`.

### Pull and start

```bash
cd /volume1/docker/stock-monitor/src
docker compose pull
docker compose up -d
```

Or **Docker → Project → Create** pointed at
`/volume1/docker/stock-monitor/src`. Startup is now a download rather than a
build — under a minute instead of several.

## 6. Verify

- Container `stock-monitor` reaches **healthy** (it polls its own homepage).
- Open the hostname in a private window — the grid should load straight away
  with your tickers, no prompt.
- Try it off your home network, on mobile data, to confirm the tunnel is
  actually serving rather than you reaching the NAS over the LAN.
- Next morning, check `data/logs/` for a new `daily_*.log`.

---

## Operating it

**Update after a code change.** Push to `main`. GitHub Actions builds and
publishes the image; the NAS picks it up on its next check. `data/` is never
touched by an update — that is the entire point of the bind mount.

To take an update immediately:

```bash
cd /volume1/docker/stock-monitor/src && docker compose pull && docker compose up -d
```

To take it automatically, register `scripts/autoupdate.sh` as an hourly task
under **Control Panel → Task Scheduler**. It compares image IDs and only
restarts when the registry actually has something new, so running it often
costs nothing. Avoid scheduling it at 06:00, when the daily refresh is running.

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
| `docker compose pull` says `denied` | The GHCR package is still private. Set it public in Package settings, or `docker login ghcr.io` with a `read:packages` token |
| Pushed to main but nothing changed | The workflow skips doc-only commits (`paths-ignore`). Check the Actions tab; run it manually with **Run workflow** if needed |
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
- The image is built by GitHub Actions for `linux/amd64` only, which every
  NASync model is. Moving to different hardware means adding `linux/arm64` to
  the `platforms` list in the workflow.
- One container runs both Node and Python because the Add Stock and Refresh
  buttons spawn Python directly. Splitting them needs an API layer first.
