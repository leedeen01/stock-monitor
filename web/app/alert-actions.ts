"use server";

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { refresh } from "next/cache";

import { db } from "@/lib/db";
import { requireAction } from "@/lib/guard";
import { BY_KEY } from "@/lib/metrics";
import { INGEST_DIR, PYTHON } from "@/lib/paths";

const run = promisify(execFile);

const CONDITIONS = new Set([
  "percentile_below",
  "percentile_above",
  "value_below",
  "value_above",
  "new_filing",
]);
const PRIMARY = "__primary__";

export type ActionResult = { ok: boolean; message: string };

export async function acknowledgeAlert(id: number): Promise<ActionResult> {
  const user = await requireAction();

  if (!Number.isInteger(id)) return { ok: false, message: "Invalid alert." };
  db().prepare("UPDATE alert_events SET acknowledged = 1 WHERE id = ? AND user_id = ?")
    .run(id, user.id);
  refresh();
  return { ok: true, message: "Dismissed." };
}

export async function acknowledgeAll(): Promise<ActionResult> {
  const user = await requireAction();

  const info = db()
    .prepare("UPDATE alert_events SET acknowledged = 1 WHERE acknowledged = 0 AND user_id = ?")
    .run(user.id);
  refresh();
  return { ok: true, message: `Dismissed ${info.changes} alert(s).` };
}

export async function toggleRule(id: number, enabled: boolean): Promise<ActionResult> {
  const user = await requireAction();

  if (!Number.isInteger(id)) return { ok: false, message: "Invalid rule." };
  db()
    .prepare("UPDATE alert_rules SET enabled = ? WHERE id = ? AND user_id = ?")
    .run(enabled ? 1 : 0, id, user.id);
  refresh();
  return { ok: true, message: enabled ? "Rule enabled." : "Rule paused." };
}

export async function deleteRule(id: number): Promise<ActionResult> {
  const user = await requireAction();

  if (!Number.isInteger(id)) return { ok: false, message: "Invalid rule." };
  // Events cascade with the rule — keeping orphaned alerts whose rule no longer
  // exists would leave entries nothing can explain.
  db().prepare("DELETE FROM alert_rules WHERE id = ? AND user_id = ?").run(id, user.id);
  refresh();
  return { ok: true, message: "Rule deleted." };
}

export async function createRule(
  _prev: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const user = await requireAction();

  const name = String(formData.get("name") ?? "").trim();
  const scope = String(formData.get("scope") ?? "all");
  const scopeRefRaw = String(formData.get("scopeRef") ?? "").trim();
  const metricKey = String(formData.get("metricKey") ?? "");
  const condition = String(formData.get("condition") ?? "");
  const thresholdRaw = String(formData.get("threshold") ?? "").trim();

  if (!name) return { ok: false, message: "Give the rule a name." };
  if (!["all", "group", "ticker"].includes(scope)) {
    return { ok: false, message: "Invalid scope." };
  }
  if (scope !== "all" && !scopeRefRaw) {
    return { ok: false, message: "Pick what the rule applies to." };
  }
  if (!CONDITIONS.has(condition)) {
    return { ok: false, message: "Invalid condition." };
  }
  // metric_key becomes a SQL column name downstream, so it must come from the
  // registry — never trust it from the form.
  if (metricKey !== PRIMARY && !BY_KEY.has(metricKey)) {
    return { ok: false, message: "Unknown metric." };
  }

  let threshold: number | null = null;
  if (condition !== "new_filing") {
    threshold = Number(thresholdRaw);
    if (!Number.isFinite(threshold)) {
      return { ok: false, message: "Threshold must be a number." };
    }
    if (condition.startsWith("percentile") && (threshold < 0 || threshold > 100)) {
      return { ok: false, message: "Percentile must be between 0 and 100." };
    }
  }

  const conn = db();
  if (conn.prepare("SELECT 1 FROM alert_rules WHERE name = ? AND user_id = ?").get(name, user.id)) {
    return { ok: false, message: `A rule called "${name}" already exists.` };
  }

  conn
    .prepare(
      `INSERT INTO alert_rules
         (user_id, name, scope, scope_ref, metric_key, condition, threshold, enabled, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)`,
    )
    .run(
      user.id,
      name,
      scope,
      scope === "all" ? null : scope === "ticker" ? scopeRefRaw.toUpperCase() : scopeRefRaw,
      metricKey,
      condition,
      threshold,
      new Date().toISOString(),
    );

  refresh();
  return { ok: true, message: `Created "${name}". It'll be checked on the next refresh.` };
}

/**
 * Evaluate rules now rather than waiting for the daily job.
 *
 * Runs the same `alerts.py` the scheduler uses, so a rule you just wrote is
 * tested by the identical code path. Only fires on crossings, so this won't
 * resurrect alerts you already dismissed.
 */
export async function evaluateNow(): Promise<ActionResult> {
  await requireAction();

  try {
    const { stdout } = await run(PYTHON, ["alerts.py"], {
      cwd: INGEST_DIR,
      timeout: 120_000,
    });
    const match = stdout.match(/->\s*(\d+) matches,\s*(\d+) new/);
    refresh();
    if (!match) return { ok: true, message: "Checked." };
    return {
      ok: true,
      message:
        match[2] === "0"
          ? "Checked — nothing new crossed."
          : `Checked — ${match[2]} new alert(s).`,
    };
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    return { ok: false, message: `Check failed: ${detail.slice(0, 200)}` };
  }
}
