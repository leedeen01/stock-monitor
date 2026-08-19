import { NextResponse } from "next/server";

import { db } from "@/lib/db";

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
  const conn = db();
  const id = new URL(request.url).searchParams.get("id");

  if (id) {
    const job = conn
      .prepare(`SELECT ${COLUMNS} FROM jobs WHERE id = ?`)
      .get(Number(id)) as JobRow | undefined;
    return NextResponse.json({ job: job ?? null });
  }

  // No id: whatever is currently running, so a freshly loaded page can adopt a
  // job started by another tab.
  const jobs = conn
    .prepare(`SELECT ${COLUMNS} FROM jobs WHERE status = 'running' ORDER BY id DESC`)
    .all() as JobRow[];
  return NextResponse.json({ jobs });
}
