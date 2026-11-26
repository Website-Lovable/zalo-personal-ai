import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";

import { classifyClose, classifyError } from "../src/classify.js";
import { createServer, makeEmitter } from "../src/server.js";
import { LoginState, SessionManager } from "../src/sessions.js";
import { sign, signedHeaders, verify } from "../src/signing.js";

const SECRET = "s".repeat(40);
const KEY = "0b6f8f5e-1d1e-4c4a-9a51-7d1c2f0e9a10";

class FakeListener extends EventEmitter {
  start() {
    this.started = true;
  }
  stop() {
    this.started = false;
  }
}

function fakeApi({ ownId = "acc-1", sendImpl } = {}) {
  return {
    listener: new FakeListener(),
    getOwnId: () => ownId,
    sent: [],
    async sendMessage(content, threadId, type) {
      if (sendImpl) return sendImpl(content, threadId, type);
      this.sent.push([content.msg, threadId, type]);
      return { message: { msgId: 42 }, attachment: [] };
    },
  };
}

function fakeZaloFactory(api, { qr = true } = {}) {
  const state = { callback: null, resolveLogin: null };
  const createZalo = () => ({
    loginQR(_options, callback) {
      state.callback = callback;
      return new Promise((resolve, reject) => {
        state.resolveLogin = resolve;
        state.rejectLogin = reject;
        if (qr) {
          callback({ type: 0, data: { image: "QUJD" }, actions: { abort: () => {} } });
        }
      });
    },
    async login(credentials) {
      if (credentials.imei !== "imei-1") throw new Error("Request failed with status code 401");
      return api;
    },
  });
  return { createZalo, state };
}

test("signature round trip and tolerance", () => {
  const body = Buffer.from('{"a":1}');
  const headers = signedHeaders(SECRET, body);
  assert.ok(verify(SECRET, headers, body));
  assert.ok(!verify("other".repeat(10), headers, body));
  assert.ok(!verify(SECRET, headers, Buffer.from('{"a":2}')));
  const old = String(Math.floor(Date.now() / 1000) - 1000);
  assert.ok(
    !verify(SECRET, { "x-zp-timestamp": old, "x-zp-signature": sign(SECRET, old, body) }, body),
  );
});

test("classification of errors and listener close codes", () => {
  assert.equal(classifyError(new Error("Tài khoản bị chặn gửi tin")), "blocked");
  assert.equal(classifyError(new Error("Request failed with status code 401")), "session_expired");
  assert.equal(classifyError(new Error("timeout")), null);
  assert.equal(classifyClose(1000), null);
  assert.equal(classifyClose(3000), "session_conflict");
  assert.equal(classifyClose(3003), "kicked");
  assert.equal(classifyClose(1006), "disconnected");
});

test("QR login: pending -> scanned -> confirmed hands the session over once", async () => {
  const api = fakeApi();
  const { createZalo, state } = fakeZaloFactory(api);
  const manager = new SessionManager({ createZalo, emit: async () => {} });
  const started = await manager.startLogin(KEY);
  assert.match(started.qr_image, /^data:image\/png;base64,QUJD$/);
  assert.equal(manager.loginStatus(KEY, started.login_id).state, LoginState.PENDING);
  state.callback({ type: 2, data: { display_name: "Chủ" }, actions: {} });
  assert.equal(manager.loginStatus(KEY, started.login_id).state, LoginState.SCANNED);
  state.callback({ type: 4, data: { cookie: [], imei: "imei-1", userAgent: "UA" }, actions: null });
  state.resolveLogin(api);
  await new Promise((r) => setImmediate(r));
  const done = manager.loginStatus(KEY, started.login_id);
  assert.equal(done.state, LoginState.CONFIRMED);
  assert.equal(done.account_id, "acc-1");
  assert.deepEqual(JSON.parse(done.session), { cookie: [], imei: "imei-1", userAgent: "UA" });
  assert.equal(manager.loginStatus(KEY, started.login_id), null);
  assert.ok(api.listener.started);
  assert.equal(manager.loginStatus("other-key", started.login_id), null);
});

test("only 1-1 messages are forwarded; groups are dropped", async () => {
  const api = fakeApi();
  const events = [];
  const manager = new SessionManager({
    createZalo: () => ({}),
    emit: async (key, event) => events.push([key, event]),
  });
  manager.attach(KEY, api, "acc-1");
  api.listener.emit("message", {
    type: 1,
    threadId: "g1",
    data: { msgId: "1", content: "group text" },
  });
  api.listener.emit("message", {
    type: 0,
    threadId: "u1",
    isSelf: false,
    data: { msgId: "2", uidFrom: "u1", dName: "Khách", content: "Chào anh", ts: "1700000000000" },
  });
  assert.equal(events.length, 1);
  const [key, event] = events[0];
  assert.equal(key, KEY);
  assert.equal(event.message.text, "Chào anh");
  assert.equal(event.message.thread_type, "user");
  assert.equal(event.event_id, `${KEY}:2`);
});

test("listener kicked -> restriction event and session dropped", async () => {
  const api = fakeApi();
  const events = [];
  const manager = new SessionManager({ createZalo: () => ({}), emit: async (k, e) => events.push(e) });
  manager.attach(KEY, api, "acc-1");
  api.listener.emit("closed", 3003, "kick");
  assert.equal(events[0].type, "restriction");
  assert.equal(events[0].restriction.kind, "kicked");
  assert.equal(manager.sessions.size, 0);
});

test("send: no session -> 409, ok -> message id, blocked -> restriction", async () => {
  const manager = new SessionManager({ createZalo: () => ({}), emit: async () => {} });
  assert.equal((await manager.send(KEY, "u1", "hi")).status, 409);
  const api = fakeApi();
  manager.attach(KEY, api, "acc-1");
  const ok = await manager.send(KEY, "u1", "hi");
  assert.deepEqual(ok.body, { ok: true, message_id: "42" });
  assert.deepEqual(api.sent[0], ["hi", "u1", 0]);
  const blockedApi = fakeApi({
    sendImpl: async () => {
      throw new Error("Bạn đã bị chặn gửi tin nhắn");
    },
  });
  manager.attach(KEY, blockedApi, "acc-1");
  const blocked = await manager.send(KEY, "u1", "hi");
  assert.equal(blocked.body.restriction, "blocked");
  assert.equal(manager.sessions.size, 0);
});

test("restore: valid session attaches, bad one is a restriction", async () => {
  const api = fakeApi();
  const { createZalo } = fakeZaloFactory(api);
  const manager = new SessionManager({ createZalo, emit: async () => {} });
  const good = await manager.restore(KEY, JSON.stringify({ cookie: [], imei: "imei-1", userAgent: "UA" }));
  assert.deepEqual(good, { ok: true, account_id: "acc-1" });
  const bad = await manager.restore(KEY, JSON.stringify({ cookie: [], imei: "x", userAgent: "UA" }));
  assert.equal(bad.ok, false);
  assert.equal(bad.restriction, "session_expired");
  assert.equal((await manager.restore(KEY, "not json")).restriction, "session_expired");
});

test("HTTP server rejects unsigned requests and serves signed ones", async () => {
  const api = fakeApi();
  const manager = new SessionManager({ createZalo: () => ({}), emit: async () => {} });
  manager.attach(KEY, api, "acc-1");
  const server = createServer({ manager, secret: SECRET });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const health = await fetch(`${base}/healthz`);
    assert.equal(health.status, 200);
    const body = JSON.stringify({ thread_id: "u1", text: "Dạ em chào anh" });
    const unsigned = await fetch(`${base}/v1/sessions/${KEY}/send`, { method: "POST", body });
    assert.equal(unsigned.status, 401);
    const signed = await fetch(`${base}/v1/sessions/${KEY}/send`, {
      method: "POST",
      body,
      headers: signedHeaders(SECRET, Buffer.from(body)),
    });
    assert.equal(signed.status, 200);
    assert.equal((await signed.json()).ok, true);
    const missing = await fetch(`${base}/v1/nope`, { method: "POST" });
    assert.equal(missing.status, 404);
  } finally {
    server.close();
  }
});

test("emitter signs events for Django", async () => {
  const calls = [];
  const emit = makeEmitter({
    djangoUrl: "http://web:8000/",
    secret: SECRET,
    fetchImpl: async (url, init) => {
      calls.push([url, init]);
      return { ok: true, status: 200 };
    },
  });
  await emit(KEY, { type: "message" });
  assert.equal(calls[0][0], `http://web:8000/webhooks/zalo-personal/${KEY}/`);
  assert.ok(verify(SECRET, calls[0][1].headers, Buffer.from(calls[0][1].body)));
});
