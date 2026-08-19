"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

export type Job = {
  id: number;
  kind: string;
  target: string | null;
  status: "running" | "ok" | "error";
  step: string | null;
  detail: string | null;
};

const POLL_MS = 2000;

/**
 * Follow a background job to completion.
 *
 * Polling a Route Handler rather than holding the server action open is the
 * whole point: an in-flight server action makes Next queue client navigations,
 * so a long one freezes the rest of the page. A GET blocks nothing.
 *
 * On completion this calls router.refresh() so the server components re-render
 * against the rows the job just wrote.
 */
export function useJob(jobId: number | null, onSettled?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const router = useRouter();

  // Held in a ref so a caller passing an inline closure doesn't restart polling
  // on every render.
  const settled = useRef(onSettled);
  settled.current = onSettled;

  useEffect(() => {
    if (jobId === null) {
      setJob(null);
      return;
    }

    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs?id=${jobId}`, { cache: "no-store" });
        const data = (await res.json()) as { job: Job | null };
        if (stopped) return;

        if (data.job) {
          setJob(data.job);
          if (data.job.status !== "running") {
            router.refresh();
            settled.current?.(data.job);
            return;
          }
        }
      } catch {
        // A dropped poll is not a failed job — a rebuild or a sleeping laptop
        // will do it. Keep trying; the stale-job sweep is what gives up.
      }
      timer = setTimeout(poll, POLL_MS);
    };

    poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, router]);

  return job;
}

/**
 * Pick up a job that was already running when this page loaded — started from
 * another tab, or still going after a reload. Without it, a refresh you kicked
 * off would look like it had never happened.
 */
export function useAdoptRunning(kind: string, adopt: (id: number) => void) {
  const take = useRef(adopt);
  take.current = adopt;
  const done = useRef(false);

  useEffect(() => {
    if (done.current) return;
    done.current = true;

    fetch("/api/jobs", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { jobs?: Job[] }) => {
        const mine = d.jobs?.find((j) => j.kind === kind);
        if (mine) take.current(mine.id);
      })
      .catch(() => {});
  }, [kind]);
}
