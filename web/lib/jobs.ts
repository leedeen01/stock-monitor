import "server-only";

import { spawn } from "node:child_process";

import { db } from "@/lib/db";
import { INGEST_DIR, PYTHON } from "@/lib/paths";

/**
 * Background work, started and forgotten.
 *
 * Long jobs used to be awaited inside a server action, which held the action
 * pending for minutes. Next queues client navigations behind an in-flight
 * action, so a refresh froze the whole page. Everything long now goes through
 * here: insert a row, spawn detached, return in milliseconds, let the browser
 * poll /api/jobs.
 */

export type JobKind = "refresh" | "add" | "ibkr";

/** A job still 'running' after this lost its process; see ingest/jobs.py. */
export const STALE_JOB_MINUTES = 45;

export function expireStaleJobs(): void {
  const cutoff = new Date(Date.now() - STALE_JOB_MINUTES * 60_000).toISOString();
  db()
    .prepare(
      `UPDATE jobs
          SET status = 'error', finished_at = ?, step = NULL,
              detail = COALESCE(detail, 'process vanished before reporting a result')
        WHERE status = 'running' AND started_at < ?`,
    )
    .run(new Date().toISOString(), cutoff);
}

/**
 * Is a job of this kind already in flight?
 *
 * `userId` scopes the question. Omitting it asks globally, which is right for
 * a refresh — that rewrites ratios_daily for every ticker, so two at once
 * would fight regardless of who started them. It is wrong for an add: without
 * the scope, one account adding NVDA blocks every other account from adding
 * NVDA, which is not a conflict at all since watchlists are per-user.
 */
export function jobRunning(
  kind: JobKind,
  options: { userId?: number; target?: string | null } = {},
): boolean {
  const clauses = ["kind = ?", "status = 'running'"];
  const args: (string | number)[] = [kind];

  if (options.target != null) {
    clauses.push("target = ?");
    args.push(options.target);
  }
  if (options.userId != null) {
    clauses.push("user_id = ?");
    args.push(options.userId);
  }

  return Boolean(
    db()
      .prepare(`SELECT id FROM jobs WHERE ${clauses.join(" AND ")}`)
      .get(...args),
  );
}

/**
 * Insert the job row, then spawn. That order is deliberate — if Python created
 * the row, the first poll would land before it existed and the UI would decide
 * nothing was running.
 *
 * `env` carries anything the child needs that must not appear in argv, which
 * is visible in the process list. The IBKR token goes this way.
 */
export function startJob(options: {
  script: string;
  args: string[];
  kind: JobKind;
  /** Whoever started it. /api/jobs will only report a job to its owner. */
  userId: number;
  target?: string | null;
  firstStep: string;
  env?: Record<string, string>;
}): number {
  const info = db()
    .prepare(
      "INSERT INTO jobs (kind, target, user_id, status, step, started_at) " +
        "VALUES (?, ?, ?, 'running', ?, ?)",
    )
    .run(
      options.kind,
      options.target ?? null,
      options.userId,
      options.firstStep,
      new Date().toISOString(),
    );

  const jobId = Number(info.lastInsertRowid);

  const child = spawn(
    PYTHON,
    [options.script, ...options.args, "--job-id", String(jobId)],
    {
      cwd: INGEST_DIR,
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env: { ...process.env, ...(options.env ?? {}) },
    },
  );
  // Let it outlive this request.
  child.unref();

  return jobId;
}
