"use server";

import type { StartResult } from "@/app/actions";
import { requireAction } from "@/lib/guard";
import { getLink, getToken } from "@/lib/ibkr";
import { expireStaleJobs, jobRunning, startJob } from "@/lib/jobs";

/**
 * Pull holdings from IBKR now, rather than waiting for the daily job.
 *
 * The token goes to the child through the environment, never argv — argv is
 * visible in the process list, and this reads brokerage statements. The Python
 * side can also decrypt it itself for the unattended run; passing it here just
 * saves that work when a person is already signed in.
 */
export async function syncIbkr(): Promise<StartResult> {
  const user = await requireAction();
  expireStaleJobs();

  const link = getLink(user.id);
  if (!link.linked) {
    return { ok: false, message: "No IBKR account linked yet." };
  }
  if (link.unreadable) {
    return {
      ok: false,
      message: "The stored token cannot be decrypted — re-enter it to relink.",
    };
  }
  if (jobRunning("ibkr", String(user.id))) {
    return { ok: false, message: "A sync is already running." };
  }

  const token = getToken(user.id);
  if (!token || !link.queryId) {
    return { ok: false, message: "The stored credentials are incomplete — relink." };
  }

  const jobId = startJob({
    script: "ibkr.py",
    args: ["--user-id", String(user.id)],
    kind: "ibkr",
    // Scoped to the user so two accounts can sync at once without one
    // reporting the other's progress.
    target: String(user.id),
    firstStep: "Contacting IBKR",
    env: { IBKR_FLEX_TOKEN: token, IBKR_QUERY_ID: link.queryId },
  });

  return { ok: true, jobId, message: "Sync started." };
}
