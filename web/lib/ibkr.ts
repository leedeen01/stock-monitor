import "server-only";

import { db } from "@/lib/db";
import { decrypt, encrypt } from "@/lib/secrets";

/**
 * Storage for a user's IBKR Flex Web Service link.
 *
 * Flex is the right surface for an unattended server: a query id plus a token
 * that lasts up to a year, no gateway process, no daily re-login. The trade is
 * that data refreshes once overnight rather than in real time — which suits an
 * app whose prices are already previous-close.
 *
 * The token never leaves this module in plaintext except to the code that
 * actually calls IBKR. Nothing here is sent to the browser.
 */

export type IbkrLink = {
  linked: boolean;
  queryId: string | null;
  accountLabel: string | null;
  linkedAt: string | null;
  lastSyncAt: string | null;
  lastSyncStatus: string | null;
  lastSyncDetail: string | null;
  /** True when the stored token cannot be decrypted — usually a rotated
   *  ENCRYPTION_KEY. The link exists but needs re-entering. */
  unreadable: boolean;
};

type Row = {
  flex_query_id: string;
  token_cipher: string;
  account_label: string | null;
  linked_at: string;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_detail: string | null;
};

const NOT_LINKED: IbkrLink = {
  linked: false,
  queryId: null,
  accountLabel: null,
  linkedAt: null,
  lastSyncAt: null,
  lastSyncStatus: null,
  lastSyncDetail: null,
  unreadable: false,
};

export function getLink(userId: number): IbkrLink {
  const row = db()
    .prepare(
      `SELECT flex_query_id, token_cipher, account_label, linked_at,
              last_sync_at, last_sync_status, last_sync_detail
         FROM ibkr_links WHERE user_id = ?`,
    )
    .get(userId) as Row | undefined;

  if (!row) return NOT_LINKED;

  return {
    linked: true,
    queryId: row.flex_query_id,
    accountLabel: row.account_label,
    linkedAt: row.linked_at,
    lastSyncAt: row.last_sync_at,
    lastSyncStatus: row.last_sync_status,
    lastSyncDetail: row.last_sync_detail,
    unreadable: decrypt(row.token_cipher) === null,
  };
}

export function saveLink(
  userId: number,
  queryId: string,
  token: string,
  accountLabel: string | null,
): void {
  db()
    .prepare(
      `INSERT INTO ibkr_links
         (user_id, flex_query_id, token_cipher, account_label, linked_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(user_id) DO UPDATE SET
         flex_query_id = excluded.flex_query_id,
         token_cipher  = excluded.token_cipher,
         account_label = excluded.account_label,
         linked_at     = excluded.linked_at,
         last_sync_at     = NULL,
         last_sync_status = NULL,
         last_sync_detail = NULL`,
    )
    .run(
      userId,
      queryId,
      encrypt(token),
      accountLabel,
      new Date().toISOString(),
    );
}

export function removeLink(userId: number): void {
  db().prepare("DELETE FROM ibkr_links WHERE user_id = ?").run(userId);
}

/** The plaintext token, for the code that calls IBKR. Never render this. */
export function getToken(userId: number): string | null {
  const row = db()
    .prepare("SELECT token_cipher FROM ibkr_links WHERE user_id = ?")
    .get(userId) as { token_cipher: string } | undefined;
  return row ? decrypt(row.token_cipher) : null;
}
