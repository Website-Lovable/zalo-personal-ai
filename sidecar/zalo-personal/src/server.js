// HTTP API used only by Django (internal network). Every /v1 request must be signed.
import http from "node:http";

import { SessionManager } from "./sessions.js";
import { signedHeaders, verify } from "./signing.js";

const MAX_BODY = 64 * 1024;
const KEY = "[0-9a-fA-F-]{36}";
const ROUTES = [
  ["POST", new RegExp(`^/v1/sessions/(${KEY})/login$`), "login"],
  ["POST", new RegExp(`^/v1/sessions/(${KEY})/login/([0-9a-fA-F-]{36})/status$`), "status"],
  ["POST", new RegExp(`^/v1/sessions/(${KEY})/restore$`), "restore"],
  ["POST", new RegExp(`^/v1/sessions/(${KEY})/send$`), "send"],
  ["DELETE", new RegExp(`^/v1/sessions/(${KEY})$`), "stop"],
];

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY) {
        reject(new Error("too large"));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

function reply(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

export function makeEmitter({ djangoUrl, secret, fetchImpl = fetch, log = () => {} }) {
  return async function emit(key, event) {
    const body = JSON.stringify(event);
    const url = `${djangoUrl.replace(/\/$/, "")}/webhooks/zalo-personal/${key}/`;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await fetchImpl(url, {
          method: "POST",
          headers: signedHeaders(secret, body),
          body,
        });
        if (response.ok || (response.status >= 400 && response.status < 500)) return;
      } catch {
        // retry below
      }
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
    }
    log("event delivery failed", key, event.type);
  };
}

export function createServer({ manager, secret, toleranceSeconds = 300 }) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://sidecar");
    if (req.method === "GET" && url.pathname === "/healthz") {
      return reply(res, 200, { ok: true, sessions: manager.sessions.size });
    }
    const route = ROUTES.find(([method, re]) => method === req.method && re.test(url.pathname));
    if (!route) return reply(res, 404, { error: "not_found" });
    let body;
    try {
      body = await readBody(req);
    } catch {
      return reply(res, 413, { error: "too_large" });
    }
    if (!verify(secret, req.headers, body, toleranceSeconds)) {
      return reply(res, 401, { error: "bad_signature" });
    }
    let payload = {};
    try {
      payload = body.length ? JSON.parse(body.toString("utf8")) : {};
    } catch {
      return reply(res, 400, { error: "invalid_json" });
    }
    const [, re, name] = route;
    const [, key, extra] = url.pathname.match(re);
    try {
      switch (name) {
        case "login":
          return reply(res, 200, await manager.startLogin(key));
        case "status": {
          const status = manager.loginStatus(key, extra);
          return status ? reply(res, 200, status) : reply(res, 404, { error: "unknown_login" });
        }
        case "restore":
          return reply(res, 200, await manager.restore(key, String(payload.session ?? "")));
        case "send": {
          const text = String(payload.text ?? "");
          const threadId = String(payload.thread_id ?? "");
          if (!text || !threadId) return reply(res, 400, { error: "missing_fields" });
          const result = await manager.send(key, threadId, text);
          return reply(res, result.status, result.body);
        }
        case "stop":
          return reply(res, 200, { ok: true, stopped: manager.stop(key) });
        default:
          return reply(res, 404, { error: "not_found" });
      }
    } catch (error) {
      return reply(res, 502, { error: String(error?.name ?? "error") });
    }
  });
}

async function main() {
  const secret = process.env.ZALO_PERSONAL_SIDECAR_SECRET ?? "";
  if (secret.length < 32) {
    console.error("ZALO_PERSONAL_SIDECAR_SECRET must be at least 32 characters");
    process.exit(1);
  }
  const { Zalo } = await import("zca-js");
  const log = (...args) => console.log(new Date().toISOString(), ...args);
  const manager = new SessionManager({
    // checkUpdate=false: no call to the npm registry; logging=false: keep content out of logs
    createZalo: () => new Zalo({ selfListen: true, checkUpdate: false, logging: false }),
    emit: makeEmitter({
      djangoUrl: process.env.ZALO_PERSONAL_DJANGO_URL ?? "http://web:8000",
      secret,
      log,
    }),
    log,
  });
  const port = Number(process.env.PORT ?? 3100);
  createServer({
    manager,
    secret,
    toleranceSeconds: Number(process.env.ZALO_PERSONAL_SIGNATURE_TOLERANCE_SECONDS ?? 300),
  }).listen(port, () => log(`zalo-personal sidecar listening on :${port}`));
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main();
}
