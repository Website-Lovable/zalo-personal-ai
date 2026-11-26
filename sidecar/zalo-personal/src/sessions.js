// Live personal Zalo sessions, in memory only. Django keeps the encrypted copy of each session
// and restores it here after a restart. Never log message text, cookies or QR codes.
import { randomUUID } from "node:crypto";

import { classifyClose, classifyError } from "./classify.js";

export const LoginState = {
  PENDING: "pending",
  SCANNED: "scanned",
  CONFIRMED: "confirmed",
  EXPIRED: "expired",
  FAILED: "failed",
};

// zca-js LoginQRCallbackEventType
const QR_GENERATED = 0;
const QR_EXPIRED = 1;
const QR_SCANNED = 2;
const QR_DECLINED = 3;
const GOT_LOGIN_INFO = 4;

const THREAD_USER = 0; // zca-js ThreadType.User
const QR_TTL_MS = 100_000;
const LOGIN_KEEP_MS = 10 * 60_000;

export class SessionManager {
  /**
   * @param {object} deps
   * @param {() => object} deps.createZalo  returns a zca-js `Zalo` instance
   * @param {(key: string, event: object) => Promise<void>} deps.emit  posts an event to Django
   * @param {(...args: unknown[]) => void} [deps.log]
   */
  constructor({ createZalo, emit, log = () => {} }) {
    this.createZalo = createZalo;
    this.emit = emit;
    this.log = log;
    this.logins = new Map();
    this.sessions = new Map();
  }

  startLogin(key) {
    for (const [id, login] of this.logins) {
      if (login.key === key && login.abort) login.abort();
      if (login.key === key) this.logins.delete(id);
    }
    const loginId = randomUUID();
    const login = {
      key,
      state: LoginState.PENDING,
      expiresAt: new Date(Date.now() + QR_TTL_MS),
      accountId: "",
      displayName: "",
      session: "",
      error: "",
      abort: null,
    };
    this.logins.set(loginId, login);
    setTimeout(() => this.logins.delete(loginId), LOGIN_KEEP_MS).unref();

    return new Promise((resolve, reject) => {
      let qrSent = false;
      const zalo = this.createZalo();
      const callback = (event) => {
        if (event.actions?.abort) login.abort = event.actions.abort;
        switch (event.type) {
          case QR_GENERATED:
            qrSent = true;
            resolve({
              login_id: loginId,
              qr_image: `data:image/png;base64,${event.data.image}`,
              expires_at: login.expiresAt.toISOString(),
            });
            break;
          case QR_SCANNED:
            login.state = LoginState.SCANNED;
            login.displayName = String(event.data?.display_name ?? "");
            break;
          case QR_EXPIRED:
            login.state = LoginState.EXPIRED;
            event.actions?.abort?.();
            break;
          case QR_DECLINED:
            login.state = LoginState.FAILED;
            login.error = "declined";
            event.actions?.abort?.();
            break;
          case GOT_LOGIN_INFO:
            login.session = JSON.stringify({
              cookie: event.data.cookie,
              imei: event.data.imei,
              userAgent: event.data.userAgent,
            });
            break;
          default:
            break;
        }
      };
      zalo
        .loginQR({}, callback)
        .then((api) => {
          login.accountId = String(api.getOwnId());
          login.state = LoginState.CONFIRMED;
          this.attach(key, api, login.accountId);
        })
        .catch((error) => {
          if (login.state !== LoginState.EXPIRED) {
            login.state = LoginState.FAILED;
            login.error = "login_failed";
          }
          this.log("login failed", key, error?.name ?? "error");
          if (!qrSent) reject(error);
        });
    });
  }

  loginStatus(key, loginId) {
    const login = this.logins.get(loginId);
    if (!login || login.key !== key) return null;
    const result = { state: login.state };
    if (login.state === LoginState.CONFIRMED) {
      Object.assign(result, {
        account_id: login.accountId,
        display_name: login.displayName,
        session: login.session,
      });
      this.logins.delete(loginId); // the session is handed over once
    }
    if (login.error) result.error = login.error;
    return result;
  }

  async restore(key, session) {
    let credentials;
    try {
      credentials = JSON.parse(session);
    } catch {
      return { ok: false, restriction: "session_expired", error: "invalid session" };
    }
    try {
      const api = await this.createZalo().login(credentials);
      const accountId = String(api.getOwnId());
      this.attach(key, api, accountId);
      return { ok: true, account_id: accountId };
    } catch (error) {
      return {
        ok: false,
        restriction: classifyError(error) ?? "session_expired",
        error: "restore failed",
      };
    }
  }

  attach(key, api, accountId) {
    this.stop(key);
    const entry = { api, accountId };
    this.sessions.set(key, entry);
    const { listener } = api;
    listener.on("message", (message) => {
      if (message.type !== THREAD_USER) return; // 1-1 chats only; group content never leaves
      const data = message.data ?? {};
      this.emit(key, {
        type: "message",
        event_id: `${key}:${data.msgId}`,
        account_id: accountId,
        message: {
          thread_id: String(message.threadId),
          thread_type: "user",
          from_id: String(data.uidFrom ?? ""),
          from_name: String(data.dName ?? ""),
          msg_id: String(data.msgId ?? ""),
          text: typeof data.content === "string" ? data.content : null,
          ts: Number(data.ts) || null,
          is_self: Boolean(message.isSelf),
        },
      }).catch(() => {});
    });
    listener.on("closed", (code) => {
      if (this.sessions.get(key) !== entry) return;
      this.sessions.delete(key);
      const kind = classifyClose(code);
      if (kind) {
        this.emit(key, {
          type: "restriction",
          account_id: accountId,
          restriction: { kind, detail: `listener closed (${code})` },
        }).catch(() => {});
      }
    });
    listener.on("error", (error) => this.log("listener error", key, error?.name ?? "error"));
    listener.start();
  }

  async send(key, threadId, text) {
    const entry = this.sessions.get(key);
    if (!entry) return { status: 409, body: { ok: false, error: "no_session" } };
    try {
      const response = await entry.api.sendMessage({ msg: text }, threadId, THREAD_USER);
      const messageId = response?.message?.msgId;
      return { status: 200, body: { ok: true, message_id: String(messageId ?? "") } };
    } catch (error) {
      const restriction = classifyError(error);
      if (restriction) this.stop(key);
      return {
        status: 200,
        body: {
          ok: false,
          error: String(error?.message ?? "send failed").slice(0, 200),
          ...(restriction ? { restriction } : {}),
        },
      };
    }
  }

  stop(key) {
    const entry = this.sessions.get(key);
    if (!entry) return false;
    this.sessions.delete(key);
    try {
      entry.api.listener.stop();
    } catch {
      // already stopped
    }
    return true;
  }
}
