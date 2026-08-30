import { NextResponse } from "next/server";

import { db } from "@/lib/db";
import { requireApi } from "@/lib/guard";

/**
 * Job progress, polled by the buttons that start long work.
 *
 * A Route Handler rather than a server action on purpose: Next serialises
 * server actions, so polling with one would reintroduce exactly the queueing
 * this change exists to remove. A plain GET goes through the normal request
 * path and blocks nothing.
 */
export const dynamic = "force-dynamic";

export type JobRow = {
  id: number;
  kind: string;
  target: string | null;
  status: "running" | "ok" | "error";
  step: string | null;
  detail: string | null;
  started_at: string;
  finished_at: string | null;
};

const COLUMNS =
  "id, kind, target, status, step, detail, started_at, finished_at";

export async function GET(request: Request) {
  // A route handler is reachable without ever loading a page, so it carries
  // its own check. 401 rather than a redirect: this is fetched by script.
  const user = await requireApi();
  if (!user) {
    return NextResponse.json({ error: "Not signed in." }, { status: 401 });
  }

  const conn = db();
  const id = new URL(request.url).searchParams.get("id");

  if (id) {
    // Scoped to the caller: a job id is a small integer, so without this any
    // signed-in account could read another account's job by guessing.
    const job = conn
      .prepare(`SELECT ${COLUMNS} FROM jobs WHERE id = ? AND user_id = ?`)
      .get(Number(id), user.id) as JobRow | undefined;
    return NextResponse.json({ job: job ?? null });
  }

  // No id: whatever is currently running, so a freshly loaded page can adopt a
  // job started by another tab.
  const jobs = conn
    .prepare(
      `SELECT ${COLUMNS} FROM jobs WHERE status = 'running' AND user_id = ? ORDER BY id DESC`,
    )
    .all(user.id) as JobRow[];
  return NextResponse.json({ jobs });
}
