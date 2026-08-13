import "server-only";

import path from "node:path";

/**
 * Where the Python side lives, for the server actions that shell out to it.
 *
 * Both layouts put the Next root and the ingest tree side by side under one
 * parent, so `cwd/..` resolves correctly in either:
 *
 *   dev        C:\...\stock-monitor\{web, ingest, .venv, data}
 *   container  /app/{web, ingest, data}, venv at /opt/venv
 */
const PROJECT_ROOT = path.join(process.cwd(), "..");

export const INGEST_DIR = path.join(PROJECT_ROOT, "ingest");

/**
 * Windows venvs put binaries in `Scripts/`, POSIX in `bin/`, and the
 * executable is named differently too. The container sets
 * STOCK_MONITOR_PYTHON and never reaches this fallback — the fallback is only
 * what makes `npm run dev` work without configuration on either machine.
 */
const isWindows = process.platform === "win32";

export const PYTHON =
  process.env.STOCK_MONITOR_PYTHON ??
  path.join(
    PROJECT_ROOT,
    ".venv",
    isWindows ? "Scripts" : "bin",
    isWindows ? "python.exe" : "python",
  );
