import "server-only";

import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
  scryptSync,
} from "node:crypto";

/**
 * Encryption for third-party credentials at rest.
 *
 * The IBKR Flex token is a bearer credential that reads someone's brokerage
 * statements, so it does not sit in the database in plaintext. AES-256-GCM,
 * which is authenticated — a tampered ciphertext fails to decrypt rather than
 * silently returning garbage that would then be sent to IBKR.
 *
 * Deliberately separate from AUTH_SECRET. Rotating AUTH_SECRET signs everyone
 * out, which should be a cheap thing to do; if it also shredded every stored
 * token it would be an expensive one, and nobody would rotate it.
 */

const VERSION = "v1";
// Fixed salt: the input is already a high-entropy random key rather than a
// human password, so a per-value salt buys nothing and costs a lookup.
const SALT = "stock-monitor.credential.v1";

function key(): Buffer | null {
  const raw = process.env.ENCRYPTION_KEY;
  if (!raw || raw.length < 32) return null;
  return scryptSync(raw, SALT, 32);
}

/** Whether credential storage is usable. Surfaced so the UI can explain the
 *  gap instead of failing at save time. */
export function encryptionConfigured(): boolean {
  return key() !== null;
}

export function encrypt(plaintext: string): string {
  const k = key();
  if (!k) throw new Error("ENCRYPTION_KEY is not set");

  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", k, iv);
  const body = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
  ]);
  const tag = cipher.getAuthTag();

  return [VERSION, iv.toString("hex"), tag.toString("hex"), body.toString("hex")].join(
    ".",
  );
}

/** Returns null rather than throwing, so a value encrypted under a rotated key
 *  reads as "needs relinking" instead of taking a page down. */
export function decrypt(stored: string): string | null {
  const k = key();
  if (!k) return null;

  const [version, ivHex, tagHex, bodyHex] = stored.split(".");
  if (version !== VERSION || !ivHex || !tagHex || !bodyHex) return null;

  try {
    const decipher = createDecipheriv(
      "aes-256-gcm",
      k,
      Buffer.from(ivHex, "hex"),
    );
    decipher.setAuthTag(Buffer.from(tagHex, "hex"));
    return Buffer.concat([
      decipher.update(Buffer.from(bodyHex, "hex")),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    return null;
  }
}

/** For display: never send the token back, just enough to recognise it. */
export function maskToken(token: string): string {
  if (token.length <= 4) return "****";
  return `••••${token.slice(-4)}`;
}
