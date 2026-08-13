import "server-only";

import Database from "better-sqlite3";
import path from "node:path";

// The ingest pipeline writes with WAL enabled, so reads here never block a
// running backfill.
const DB_PATH =
  process.env.STOCK_MONITOR_DB ??
  path.join(process.cwd(), "..", "data", "stocks.db");

let instance: Database.Database | null = null;

export function db(): Database.Database {
  if (!instance) {
    instance = new Database(DB_PATH, { readonly: false, fileMustExist: true });
    instance.pragma("journal_mode = WAL");
  }
  return instance;
}
