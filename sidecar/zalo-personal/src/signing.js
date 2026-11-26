// HMAC-SHA256 over "<timestamp>.<body>" with the secret shared with Django
// (same scheme as apps/channels/providers/personal_unofficial/provider.py).
import { createHmac, timingSafeEqual } from "node:crypto";

export const SIGNATURE_HEADER = "x-zp-signature";
export const TIMESTAMP_HEADER = "x-zp-timestamp";

export function sign(secret, timestamp, body) {
  return createHmac("sha256", secret).update(`${timestamp}.`).update(body).digest("hex");
}

export function verify(secret, headers, body, toleranceSeconds = 300, now = Date.now()) {
  const timestamp = headers[TIMESTAMP_HEADER];
  const given = headers[SIGNATURE_HEADER];
  if (!secret || !timestamp || !given) return false;
  const ts = Number.parseInt(timestamp, 10);
  if (!Number.isFinite(ts) || Math.abs(now / 1000 - ts) > toleranceSeconds) return false;
  const expected = Buffer.from(sign(secret, timestamp, body), "utf8");
  const actual = Buffer.from(String(given), "utf8");
  return expected.length === actual.length && timingSafeEqual(expected, actual);
}

export function signedHeaders(secret, body, now = Date.now()) {
  const timestamp = String(Math.floor(now / 1000));
  return {
    "content-type": "application/json",
    [TIMESTAMP_HEADER]: timestamp,
    [SIGNATURE_HEADER]: sign(secret, timestamp, body),
  };
}
