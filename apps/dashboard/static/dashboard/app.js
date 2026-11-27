/* Trợ lý Zalo AI — admin UI (Phase 4). Plain JS, no build step.
 * Talks to the JWT API under /api/. All user data is inserted with textContent (no innerHTML),
 * so names and messages cannot inject markup. Real-time hints arrive over /ws/notifications/.
 */
(function () {
  "use strict";

  // --- Labels (Vietnamese UI) --------------------------------------------------------
  const L = {
    channel_type: { zalo_oa: "Zalo OA", zalo_bot: "Zalo Bot", zalo_personal: "Zalo cá nhân" },
    channel_status: { active: "Hoạt động", paused: "Tạm dừng", restricted: "Bị hạn chế", disconnected: "Chưa kết nối" },
    mode: { auto: "Tự động gửi", assist: "Soạn sẵn, chủ tự gửi" },
    reply_permission: { auto_reply: "Tự động trả lời", need_approval: "Cần duyệt", do_not_reply: "Không trả lời" },
    consent: { unknown: "Chưa rõ", granted: "Đồng ý liên hệ", withdrawn: "Đã rút đồng ý" },
    consent_source: { "": "—", zalo_inbound: "Khách chủ động nhắn", crs: "Đồng bộ từ CRS", form: "Biểu mẫu đăng ký", manual: "Nhập tay (có bằng chứng)" },
    relationship: { family: "Gia đình", friend: "Bạn bè", colleague: "Đồng nghiệp", customer: "Khách hàng", partner: "Đối tác", acquaintance: "Người quen", unknown: "Chưa rõ" },
    segment: { b2b: "B2B", b2c: "B2C", unknown: "Chưa rõ" },
    approval_status: { pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Từ chối", modified: "Duyệt có sửa", expired: "Hết hạn" },
    approval_source: { inbound_reply: "Trả lời tin đến", aiva_outreach: "Tin chăm sóc từ Sales AI" },
    approval_kind: { draft: "Bản nháp", escalation: "Cần người xử lý" },
    rule_action: { auto_reply: "Tự động trả lời", need_approval: "Cần duyệt", do_not_reply: "Không trả lời", escalate: "Chuyển cấp trên" },
    role: { owner: "Chủ tài khoản", admin: "Quản trị", viewer: "Chỉ xem" },
    outcome: { sent: "Đã tự gửi", queued_for_owner: "Chờ chủ gửi", retry_scheduled: "Chờ gửi lại", need_approval: "Chờ duyệt", escalated: "Chuyển người", not_replied: "Không trả lời", skipped: "Bỏ qua", failed: "Lỗi" },
    outbox: { pending: "Chờ gửi", sent: "Đã gửi", failed: "Lỗi, sẽ thử lại", dead: "Bỏ (quá số lần thử)" },
    emoji: { never: "Không dùng", rarely: "Hiếm khi", sometimes: "Thỉnh thoảng", often: "Thường xuyên" },
    reason: (r) => {
      const map = {
        handoff_requested: "Khách muốn gặp người", low_confidence: "AI chưa chắc chắn", model_needs_human: "AI đề nghị người xử lý",
        llm_error: "Lỗi gọi AI", send_failed: "Gửi thất bại", aiva_outreach: "Sales AI đề xuất", group_default: "Theo nhóm",
        contact_default: "Theo cài đặt liên hệ", nothing_to_answer: "Không có gì để trả lời", model_refused: "AI từ chối trả lời",
      };
      if (map[r]) return map[r];
      if (r.startsWith("sensitive:") || r.startsWith("output:sensitive:")) return "Chủ đề nhạy cảm: " + r.split(":").pop();
      if (r.startsWith("rule:")) return "Quy tắc #" + r.split(":")[1];
      if (r.startsWith("rate_limited")) return "Vượt giới hạn tốc độ";
      if (r.startsWith("non_text")) return "Tin không phải văn bản";
      return r;
    },
  };
  const label = (group, value) => (L[group] && L[group][value]) || value || "—";

  // --- DOM helper -------------------------------------------------------------------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "value") el.value = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(svgNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    for (const c of children) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }
  const ICONS = {
    home: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
    check: "M9 16.2l-3.5-3.5L4 14.2l5 5 11-11-1.5-1.5z",
    chat: "M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H8l-4 4V5a1 1 0 0 1 1-1z",
    people: "M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zM8 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.7 0-6 1.3-6 3.5V19h12v-2.5C14 14.3 10.7 13 8 13zm8 0c-.3 0-.6 0-1 .1 1.2.8 2 1.9 2 3.4V19h5v-2.5c0-2.2-3.3-3.5-6-3.5z",
    more: "M6 10a2 2 0 1 0 2 2 2 2 0 0 0-2-2zm6 0a2 2 0 1 0 2 2 2 2 0 0 0-2-2zm6 0a2 2 0 1 0 2 2 2 2 0 0 0-2-2z",
    send: "M2 21l21-9L2 3v7l15 2-15 2z",
    channel: "M4 6h16v2H4zm0 5h16v2H4zm0 5h10v2H4z",
    chart: "M4 20V10h3v10zm6 0V4h3v16zm6 0v-7h3v7z",
    gear: "M19.4 13a7.5 7.5 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-1.7-1L15 3.5h-4L10.7 6a7 7 0 0 0-1.7 1l-2.4-1-2 3.4L4.6 11a7.5 7.5 0 0 0 0 2l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7 7 0 0 0 1.7-1l2.4 1 2-3.4zM12 15.5A3.5 3.5 0 1 1 15.5 12 3.5 3.5 0 0 1 12 15.5z",
  };
  const icon = (name) => s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true", fill: "currentColor" }, s("path", { d: ICONS[name] }));

  const fmtTime = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    return sameDay
      ? d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  };
  const fmtNum = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("vi-VN"));

  function toast(message, kind) {
    const el = h("div", { class: "toast" + (kind === "error" ? " error" : ""), text: message });
    document.getElementById("toasts").append(el);
    setTimeout(() => el.remove(), 4000);
  }

  // --- Auth + API -------------------------------------------------------------------
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  const state = { access: null, refresh: store.get("zai.refresh"), me: null, tenant: null, stats: null, ws: null, live: false };

  class ApiError extends Error {
    constructor(status, data) {
      super(apiMessage(data) || "Lỗi " + status);
      this.status = status;
      this.data = data;
    }
  }
  function apiMessage(data) {
    if (!data) return "";
    if (typeof data === "string") return data;
    if (data.detail) return typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    const first = Object.entries(data)[0];
    if (!first) return "";
    const [field, value] = first;
    return field + ": " + (Array.isArray(value) ? value.join(" ") : typeof value === "object" ? JSON.stringify(value) : value);
  }

  async function refreshAccess() {
    if (!state.refresh) return false;
    const res = await fetch("/api/auth/refresh-token/", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh: state.refresh }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    state.access = data.access;
    if (data.refresh) { state.refresh = data.refresh; store.set("zai.refresh", data.refresh); }
    return true;
  }

  async function api(path, opts = {}, retried = false) {
    if (!state.access && state.refresh && !retried) await refreshAccess();
    const headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (state.access) headers.Authorization = "Bearer " + state.access;
    const res = await fetch(path, { method: opts.method || "GET", headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
    if (res.status === 401 && !retried && (await refreshAccess())) return api(path, opts, true);
    if (res.status === 401) { logout(); throw new ApiError(401, { detail: "Phiên đăng nhập đã hết hạn." }); }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }
  const can = (min) => ({ viewer: 1, admin: 2, owner: 3 }[state.me && state.me.role] || 0) >= { viewer: 1, admin: 2, owner: 3 }[min];

  function logout() {
    state.access = null; state.refresh = null; state.me = null; store.set("zai.refresh", null);
    if (state.ws) { state.ws.onclose = null; state.ws.close(); state.ws = null; }
    render();
  }

  // --- Real-time notifications --------------------------------------------------------
  let wsRetry = 0;
  async function connectWS() {
    if (!state.me || state.ws) return;
    let ticket;
    try { ticket = await api("/api/notifications/ticket/", { method: "POST" }); } catch (e) { return scheduleWS(); }
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(proto + "//" + location.host + ticket.ws_path + "?ticket=" + encodeURIComponent(ticket.ticket));
    state.ws = ws;
    ws.onopen = () => { wsRetry = 0; setLive(true); };
    ws.onmessage = (ev) => { try { onEvent(JSON.parse(ev.data)); } catch (e) { /* ignore */ } };
    ws.onclose = () => { state.ws = null; setLive(false); scheduleWS(); };
  }
  function scheduleWS() {
    if (!state.me) return;
    wsRetry = Math.min(wsRetry + 1, 6);
    setTimeout(connectWS, 1000 * 2 ** wsRetry);
  }
  function setLive(on) {
    state.live = on;
    const dot = document.getElementById("live-dot");
    if (dot) { dot.classList.toggle("on", on); dot.title = on ? "Đang nhận thông báo trực tiếp" : "Mất kết nối thông báo, đang thử lại"; }
  }
  let refreshTimer = null;
  function onEvent(ev) {
    const messages = {
      "message.inbound": () => "Tin mới từ " + (ev.contact_name || "khách"),
      "approval.created": () => (ev.kind === "escalation" ? "Cần người xử lý: " : ev.source === "aiva_outreach" ? "Sales AI gửi tin chăm sóc chờ duyệt: " : "Bản nháp chờ duyệt: ") + (ev.contact_name || ""),
      "owner_send.created": () => "Có nháp mới trong hộp Chờ chủ gửi",
      "ai.control": () => (ev.emergency_stop ? "AI đã dừng khẩn cấp" : "AI đã được bật lại"),
      "aiva.outbox_dead": () => "Một sự kiện gửi AIVA bị lỗi: " + ev.event,
      "channel.updated": () => (ev.status === "restricted" ? "Kênh " + ev.name + " bị hạn chế" : null),
    };
    const msg = messages[ev.type] && messages[ev.type]();
    if (msg) toast(msg, ev.type === "aiva.outbox_dead" || ev.status === "restricted" ? "error" : "");
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { loadStats(); if (current && current.live) current.live(ev); }, 400);
  }

  // --- Shell -------------------------------------------------------------------------
  const NAV = [
    { href: "#/", name: "Tổng quan", icon: "home", match: /^#\/?$/ },
    { href: "#/approvals", name: "Duyệt", icon: "check", badge: "pending_approvals", match: /^#\/approvals/ },
    { href: "#/conversations", name: "Hội thoại", icon: "chat", match: /^#\/conversations/ },
    { href: "#/contacts", name: "Liên hệ", icon: "people", match: /^#\/contacts/ },
    { href: "#/owner-sends", name: "Chờ chủ gửi", icon: "send", badge: "pending_owner_sends", match: /^#\/owner-sends/, desktop: true },
    { href: "#/channels", name: "Kênh Zalo", icon: "channel", match: /^#\/channels/, desktop: true },
    { href: "#/analytics", name: "Thống kê", icon: "chart", match: /^#\/analytics/, desktop: true },
    { href: "#/settings", name: "Cài đặt", icon: "gear", match: /^#\/settings/, desktop: true },
    { href: "#/more", name: "Thêm", icon: "more", match: /^#\/(more|owner-sends|channels|analytics|settings)/, mobile: true },
  ];
  let current = null; // { live(ev) } of the visible page

  function shell(title, body) {
    const hash = location.hash || "#/";
    const nav = h("nav", { class: "nav", "aria-label": "Điều hướng chính" },
      NAV.map((item) => {
        const count = item.badge && state.stats ? state.stats[item.badge] : 0;
        return h("a", {
          href: item.href,
          class: (item.match.test(hash) ? "active " : "") + (item.desktop ? "desktop-only" : item.mobile ? "mobile-only" : ""),
          "aria-current": item.match.test(hash) ? "page" : null,
        }, icon(item.icon), h("span", { text: item.name }), count ? h("span", { class: "badge", text: count > 99 ? "99+" : count, "data-badge": item.badge }) : null);
      }));
    const top = h("header", { class: "topbar" },
      h("div", { class: "title" }, h("h1", { class: "ellipsis", text: title }), h("div", { class: "tenant ellipsis", text: (state.tenant ? state.tenant.name : "") + (state.me ? " · " + state.me.username + " (" + label("role", state.me.role) + ")" : "") })),
      h("span", { id: "live-dot", class: "live-dot" + (state.live ? " on" : ""), title: "Thông báo trực tiếp" }),
      h("button", { class: "btn ghost small", onclick: logout, text: "Đăng xuất" }));
    const stop = state.stats && state.stats.emergency_stop
      ? h("div", { class: "banner danger" }, h("strong", { text: "AI đang dừng khẩn cấp." }), h("span", { class: "grow", text: "Không có tin nào được AI tự gửi." }), can("admin") ? h("button", { class: "btn small", onclick: resumeAI, text: "Bật lại AI" }) : null)
      : null;
    return h("div", { class: "shell" }, top, nav, h("main", { class: "stack" }, stop, body));
  }

  async function loadStats() {
    try {
      state.stats = await api("/api/dashboard/stats/");
      for (const el of document.querySelectorAll("[data-badge]")) {
        const n = state.stats[el.dataset.badge] || 0;
        el.textContent = n > 99 ? "99+" : n;
        el.classList.toggle("hidden", !n);
      }
    } catch (e) { /* shown elsewhere */ }
  }

  async function emergencyStop() {
    const reason = prompt("Lý do dừng khẩn cấp (không bắt buộc):", "");
    if (reason === null) return;
    try { await api("/api/ai/emergency-stop/", { method: "POST", body: { reason } }); toast("Đã dừng AI cho toàn bộ tài khoản."); await loadStats(); route(); }
    catch (e) { toast(e.message, "error"); }
  }
  async function resumeAI() {
    if (!confirm("Bật lại AI? Các quy tắc duyệt vẫn được áp dụng.")) return;
    try { await api("/api/ai/resume/", { method: "POST" }); toast("AI đã hoạt động lại."); await loadStats(); route(); }
    catch (e) { toast(e.message, "error"); }
  }

  // --- Generic pieces ----------------------------------------------------------------
  const loading = () => h("div", { class: "stack" }, h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  const empty = (text) => h("div", { class: "empty card flat", text });
  const errorBox = (e) => h("div", { class: "banner danger", text: "Không tải được dữ liệu: " + e.message });
  const chip = (text, kind) => h("span", { class: "chip " + (kind || ""), text });
  const originChip = (o) => (o === "demo" ? chip("demo", "info") : null);

  async function page(title, build) {
    const holder = h("div", { class: "stack" }, loading());
    mount(shell(title, holder));
    try {
      const content = await build();
      holder.replaceChildren(content);
    } catch (e) {
      holder.replaceChildren(errorBox(e));
    }
  }
  function mount(node) { document.getElementById("app").replaceChildren(node); }

  function select(name, options, value, attrs) {
    return h("select", { name, id: name, ...(attrs || {}) }, Object.entries(options).map(([v, t]) => h("option", { value: v, selected: v === value ? true : null, text: t })));
  }
  const field = (labelText, control) => h("div", { class: "field" }, h("label", { for: control.id || null, text: labelText }), control);

  // --- Pages --------------------------------------------------------------------------
  async function viewDashboard() {
    current = { live: () => viewDashboard() };
    await page("Tổng quan", async () => {
      const [stats, data, activity] = await Promise.all([
        api("/api/dashboard/stats/"), api("/api/dashboard/analytics/?days=7"), api("/api/dashboard/activity/?limit=15"),
      ]);
      state.stats = stats;
      const kpi = (value, text, href, alert) => h(href ? "a" : "div", { class: "card kpi list-item" + (alert ? " alert" : ""), href }, h("div", { class: "value", text: typeof value === "string" ? value : fmtNum(value) }), h("div", { class: "label", text }));
      const control = can("admin") && !stats.emergency_stop
        ? h("div", { class: "card row spread" }, h("div", { class: "grow" }, h("h2", { text: "Dừng khẩn cấp" }), h("p", { class: "muted small", text: "Tắt ngay mọi tin AI tự gửi trên tất cả kênh." })), h("button", { class: "btn danger", onclick: emergencyStop, text: "Dừng AI" }))
        : null;
      const restricted = stats.channels_restricted ? h("a", { class: "banner warn", href: "#/channels", text: stats.channels_restricted + " kênh đang bị hạn chế. Xem chi tiết →" }) : null;
      return h("div", { class: "stack" },
        restricted,
        h("div", { class: "kpis" },
          kpi(stats.pending_approvals, "Chờ duyệt", "#/approvals", stats.pending_approvals > 0),
          kpi(stats.pending_escalations, "Cần người xử lý", "#/approvals", stats.pending_escalations > 0),
          kpi(stats.messages_in_today, "Tin đến hôm nay", "#/conversations"),
          kpi(stats.ai_auto_sent_today, "AI tự trả lời hôm nay", "#/analytics"),
          kpi(stats.pending_owner_sends, "Chờ chủ gửi", "#/owner-sends", stats.pending_owner_sends > 0),
          kpi(stats.conversations_taken_over, "Đang tiếp quản", "#/conversations"),
          kpi(stats.contacts_with_consent + "/" + stats.contacts, "Liên hệ có đồng ý", "#/contacts"),
          kpi(stats.channels_active + "/" + stats.channels, "Kênh hoạt động", "#/channels")),
        control,
        h("div", { class: "grid-2" },
          h("div", { class: "card" }, h("h2", { text: "Tin nhắn 7 ngày" }), messageChart(data)),
          h("div", { class: "card" }, h("h2", { text: "Hoạt động gần đây" }), activityList(activity.results))));
    });
  }

  function messageChart(data) {
    const W = 320, H = 150, pad = 22, n = data.days.length;
    const series = [["inbound", "var(--muted)", "Tin đến"], ["ai_sent", "var(--primary)", "AI tự gửi"], ["approved_sent", "var(--ok)", "Đã duyệt gửi"]];
    const max = Math.max(1, ...series.flatMap(([k]) => data.messages[k]));
    const slot = (W - pad) / n, bw = Math.max(3, (slot - 6) / series.length);
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Biểu đồ tin nhắn theo ngày" });
    for (let i = 0; i <= 2; i++) {
      const y = 8 + ((H - 30) * i) / 2;
      svg.append(s("line", { x1: pad, x2: W, y1: y, y2: y, stroke: "var(--border)" }));
      svg.append(s("text", { x: 0, y: y + 3 }, String(Math.round(max * (1 - i / 2)))));
    }
    data.days.forEach((day, i) => {
      series.forEach(([key, color], j) => {
        const v = data.messages[key][i];
        const bh = ((H - 30) * v) / max;
        const rect = s("rect", { x: pad + i * slot + 3 + j * bw, y: 8 + (H - 30) - bh, width: bw - 1, height: Math.max(0, bh), fill: color, rx: 1 });
        rect.append(s("title", {}, `${day}: ${v}`));
        svg.append(rect);
      });
      svg.append(s("text", { x: pad + i * slot + slot / 2, y: H - 6, "text-anchor": "middle" }, day.slice(8, 10) + "/" + day.slice(5, 7)));
    });
    const legend = h("div", { class: "legend" }, series.map(([, color, name]) => h("span", {}, h("i", { style: null, "data-color": color }), name)));
    // CSP forbids inline styles from attributes in strict mode; set colors via JS instead.
    legend.querySelectorAll("i").forEach((el) => { el.style.background = el.dataset.color; });
    return h("div", {}, svg, legend);
  }

  function bars(entries, labeler) {
    const max = Math.max(1, ...entries.map(([, v]) => v));
    return h("div", { class: "bars" }, entries.map(([k, v]) => {
      const fill = h("div", { class: "fill" });
      fill.style.width = (100 * v) / max + "%";
      return h("div", { class: "bar-row" }, h("span", { class: "ellipsis", text: labeler(k) }), h("div", { class: "track" }, fill), h("span", { class: "num", text: fmtNum(v) }));
    }));
  }

  function activityList(items) {
    if (!items.length) return empty("Chưa có hoạt động.");
    return h("div", { class: "list" }, items.map((it) => h("div", { class: "row small" },
      h("span", { class: "grow", text: it.label + (it.user ? " · " + it.user : "") }),
      h("span", { class: "muted", text: fmtTime(it.at) }))));
  }

  // Approvals --------------------------------------------------------------------------
  let approvalTab = "pending";
  async function viewApprovals() {
    current = { live: (ev) => { if (ev.type && ev.type.startsWith("approval")) viewApprovals(); } };
    await page("Duyệt tin", async () => {
      const qs = approvalTab === "mine" ? "status=pending&mine=1" : "status=" + approvalTab;
      const data = await api("/api/approvals/?" + qs);
      const tabs = h("div", { class: "tabs", role: "group", "aria-label": "Lọc" },
        [["pending", "Chờ duyệt"], ["mine", "Của tôi"], ["approved", "Đã duyệt"], ["rejected", "Từ chối"], ["expired", "Hết hạn"]].map(([k, t]) =>
          h("button", { class: "btn small", "aria-pressed": String(approvalTab === k), onclick: () => { approvalTab = k; viewApprovals(); }, text: t })));
      const list = data.results.length ? h("div", { class: "list" }, data.results.map(approvalCard)) : empty(approvalTab === "mine" && !(state.me && state.me.staff_ref) ? "Tài khoản của bạn chưa có mã nhân viên (staff_ref) nên chưa có mục được giao." : "Không có mục nào.");
      return h("div", { class: "stack" }, tabs, list);
    });
  }

  function approvalCard(a) {
    const pending = a.status === "pending";
    const text = h("textarea", { "aria-label": "Nội dung sẽ gửi", value: a.modified_response || a.proposed_response, disabled: pending ? null : true });
    const reasons = (a.reasons || []).map((r) => chip(L.reason(r), r.includes("sensitive") ? "warn" : ""));
    const decide = async (approve, btn) => {
      btn.disabled = true;
      const edited = text.value.trim() !== (a.proposed_response || "").trim();
      const body = approve ? (edited ? { modified_response: text.value } : {}) : { reason: "" };
      try {
        await api(`/api/approvals/${a.id}/${approve ? "approve" : "reject"}/`, { method: "POST", body });
        toast(approve ? "Đã duyệt và gửi." : "Đã từ chối.");
        loadStats(); viewApprovals();
      } catch (e) { toast(e.message, "error"); btn.disabled = false; }
    };
    const approveBtn = h("button", { class: "btn primary grow", text: "Duyệt & gửi" });
    approveBtn.addEventListener("click", () => decide(true, approveBtn));
    const rejectBtn = h("button", { class: "btn", text: "Từ chối" });
    rejectBtn.addEventListener("click", () => decide(false, rejectBtn));
    return h("article", { class: "card stack" },
      h("div", { class: "row spread" },
        h("strong", { class: "grow ellipsis", text: a.contact_name || "Liên hệ #" + a.contact }),
        a.kind === "escalation" ? chip("Cần người xử lý", "danger") : chip(label("approval_source", a.source), a.source === "aiva_outreach" ? "info" : ""),
        originChip(a.data_origin)),
      a.message_text ? h("div", {}, h("h3", { text: "Khách nhắn" }), h("div", { class: "quote", text: a.message_text })) : null,
      reasons.length ? h("div", { class: "row" }, reasons) : null,
      h("div", {}, h("h3", { text: a.kind === "escalation" && !a.proposed_response ? "Câu trả lời (AI không soạn được, hãy tự viết)" : "Bản nháp" }), text),
      a.send_error ? h("div", { class: "banner danger small", text: "Lần gửi trước lỗi: " + a.send_error }) : null,
      h("div", { class: "row small muted" },
        h("span", { class: "grow", text: (a.owner_staff_ref ? "Phụ trách: " + a.owner_staff_ref + " · " : "") + "Độ tin cậy AI " + Math.round((a.ai_confidence || 0) * 100) + "%" }),
        h("span", { text: pending ? "Hết hạn " + fmtTime(a.expires_at) : label("approval_status", a.status) })),
      pending ? h("div", { class: "row" }, approveBtn, rejectBtn) : null,
      a.conversation ? h("a", { class: "small", href: "#/conversations/" + a.conversation, text: "Xem hội thoại →" }) : null);
  }

  // Conversations -----------------------------------------------------------------------
  async function viewConversations() {
    current = { live: (ev) => { if (ev.type === "message.inbound") viewConversations(); } };
    await page("Hội thoại", async () => {
      const [convs, contacts] = await Promise.all([api("/api/conversations/?status=active"), api("/api/contacts/")]);
      const names = Object.fromEntries(contacts.results.map((c) => [c.id, c]));
      if (!convs.results.length) return empty("Chưa có hội thoại nào.");
      return h("div", { class: "list" }, convs.results.map((c) => h("a", { class: "card list-item row", href: "#/conversations/" + c.id },
        h("div", { class: "grow" },
          h("div", { class: "row" }, h("strong", { class: "ellipsis grow", text: c.contact_name || (names[c.contact] || {}).display_name || "#" + c.contact }), originChip(c.data_origin)),
          h("div", { class: "row small muted" },
            h("span", { class: "grow", text: "Tin cuối: " + fmtTime(c.last_message_at) }),
            c.human_takeover ? chip("Đang tiếp quản", "warn") : !c.ai_enabled ? chip("Tắt AI") : chip("AI bật", "ok"))))));
    });
  }

  async function viewConversation(id) {
    current = { live: (ev) => { if (ev.conversation_id === Number(id) || (ev.type || "").startsWith("approval")) viewConversation(id); } };
    await page("Hội thoại", async () => {
      const [conv, msgs] = await Promise.all([api(`/api/conversations/${id}/`), api(`/api/conversations/${id}/messages/?page_size=100`)]);
      const contact = await api(`/api/contacts/${conv.contact}/`);
      const list = h("div", { class: "messages" }, msgs.results.map((m) => h("div", { class: "bubble " + (m.direction === "inbound" ? "in" : "out") },
        m.content,
        h("div", { class: "meta", text: fmtTime(m.created_at) + (m.direction === "outbound" ? (m.is_ai_generated ? (m.was_approved ? " · AI, đã duyệt" : " · AI tự gửi") : " · Nhân viên") : "") }))));
      const act = async (path, body, msg) => {
        try { await api(`/api/conversations/${id}/${path}`, { method: path ? "POST" : "PATCH", body }); toast(msg); viewConversation(id); }
        catch (e) { toast(e.message, "error"); }
      };
      const controls = can("admin") ? h("div", { class: "row" },
        conv.human_takeover
          ? h("button", { class: "btn", onclick: () => act("release/", undefined, "Đã trả hội thoại cho AI."), text: "Trả cho AI" })
          : h("button", { class: "btn", onclick: () => act("takeover/", {}, "Bạn đang tiếp quản; AI im lặng."), text: "Tiếp quản" }),
        h("button", { class: "btn ghost", onclick: () => act("", { ai_enabled: !conv.ai_enabled }, conv.ai_enabled ? "Đã tắt AI cho hội thoại này." : "Đã bật AI cho hội thoại này."), text: conv.ai_enabled ? "Tắt AI hội thoại này" : "Bật AI hội thoại này" })) : null;
      const box = h("textarea", { "aria-label": "Soạn tin trả lời", placeholder: "Nhập tin trả lời…" });
      const send = h("button", { class: "btn primary", text: "Gửi" });
      send.addEventListener("click", async () => {
        if (!box.value.trim()) return;
        send.disabled = true;
        try {
          const res = await api(`/api/conversations/${id}/reply/`, { method: "POST", body: { text: box.value } });
          toast(res && res.detail ? res.detail : "Đã gửi.");
          viewConversation(id);
        } catch (e) { toast(e.message, "error"); send.disabled = false; }
      });
      return h("div", { class: "stack" },
        h("div", { class: "card stack" },
          h("div", { class: "row spread" }, h("a", { href: "#/contacts/" + contact.id, class: "grow" }, h("strong", { text: contact.display_name })), originChip(conv.data_origin)),
          h("div", { class: "row" }, chip(label("relationship", contact.relationship_type)), chip(label("reply_permission", contact.reply_permission)), chip(label("consent", contact.consent_status), contact.has_consent ? "ok" : "")),
          conv.human_takeover ? h("div", { class: "banner warn small", text: "Đang có người tiếp quản" + (conv.takeover_until ? " đến " + fmtTime(conv.takeover_until) : "") + ". AI không trả lời." }) : null,
          controls),
        msgs.results.length ? list : empty("Chưa có tin nhắn."),
        can("admin") ? h("div", { class: "composer row" }, h("div", { class: "grow" }, box), send) : null);
    });
  }

  // Owner sends -----------------------------------------------------------------------
  async function viewOwnerSends() {
    current = { live: (ev) => { if ((ev.type || "").startsWith("owner_send")) viewOwnerSends(); } };
    await page("Chờ chủ gửi", async () => {
      const data = await api("/api/owner-sends/?status=pending");
      const intro = h("p", { class: "muted small", text: "Kênh ở chế độ “Soạn sẵn, chủ tự gửi”: sao chép bản nháp, gửi trong Zalo của bạn rồi bấm “Đã gửi”." });
      if (!data.results.length) return h("div", { class: "stack" }, intro, empty("Không có nháp nào chờ gửi."));
      return h("div", { class: "stack" }, intro, h("div", { class: "list" }, data.results.map((p) => {
        const act = async (path, msg) => { try { await api(`/api/owner-sends/${p.id}/${path}/`, { method: "POST" }); toast(msg); loadStats(); viewOwnerSends(); } catch (e) { toast(e.message, "error"); } };
        return h("article", { class: "card stack" },
          h("div", { class: "quote", text: p.draft_text }),
          h("div", { class: "row small muted" }, h("span", { class: "grow", text: fmtTime(p.created_at) }), originChip(p.data_origin)),
          h("div", { class: "row" },
            h("button", { class: "btn", onclick: async () => { try { await navigator.clipboard.writeText(p.draft_text); toast("Đã sao chép."); } catch (e) { toast("Không sao chép được, hãy chọn và sao chép tay.", "error"); } }, text: "Sao chép" }),
            can("admin") ? h("button", { class: "btn primary grow", onclick: () => act("mark-sent", "Đã đánh dấu đã gửi."), text: "Đã gửi" }) : null,
            can("admin") ? h("button", { class: "btn ghost", onclick: () => act("dismiss", "Đã bỏ qua."), text: "Bỏ qua" }) : null),
          h("a", { class: "small", href: "#/conversations/" + p.conversation, text: "Xem hội thoại →" }));
      })));
    });
  }

  // Contacts -------------------------------------------------------------------------
  let contactQuery = "";
  async function viewContacts() {
    current = null;
    await page("Liên hệ", async () => {
      const data = await api("/api/contacts/?page_size=200");
      const search = h("input", { type: "search", placeholder: "Tìm theo tên…", value: contactQuery, "aria-label": "Tìm liên hệ" });
      const listBox = h("div", { class: "list" });
      const draw = () => {
        const q = search.value.trim().toLowerCase();
        contactQuery = search.value;
        const rows = data.results.filter((c) => !q || c.display_name.toLowerCase().includes(q));
        listBox.replaceChildren(...(rows.length ? rows.map((c) => h("a", { class: "card list-item", href: "#/contacts/" + c.id },
          h("div", { class: "row" }, h("strong", { class: "grow ellipsis", text: c.display_name }), originChip(c.data_origin)),
          h("div", { class: "row small" }, chip(label("relationship", c.relationship_type)), chip(label("reply_permission", c.reply_permission)), chip(label("consent", c.consent_status), c.has_consent ? "ok" : c.consent_status === "withdrawn" ? "danger" : "")))) : [empty("Không tìm thấy liên hệ.")]));
      };
      search.addEventListener("input", draw);
      draw();
      return h("div", { class: "stack" }, search, listBox);
    });
  }

  async function viewContact(id) {
    current = null;
    await page("Liên hệ", async () => {
      const [c, memory] = await Promise.all([api(`/api/contacts/${id}/`), api(`/api/contacts/${id}/memory/`)]);
      const name = h("input", { id: "display_name", value: c.display_name });
      const rel = select("relationship_type", L.relationship, c.relationship_type);
      const perm = select("reply_permission", L.reply_permission, c.reply_permission);
      const notes = h("textarea", { id: "relationship_notes", value: c.relationship_notes || "" });
      const consent = select("consent_status", L.consent, c.consent_status);
      const source = select("consent_source", L.consent_source, c.consent_source || "");
      const save = h("button", { class: "btn primary block", text: "Lưu thay đổi" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        const body = { display_name: name.value, relationship_type: rel.value, reply_permission: perm.value, relationship_notes: notes.value };
        if (consent.value !== c.consent_status || source.value !== (c.consent_source || "")) { body.consent_status = consent.value; body.consent_source = source.value; }
        try { await api(`/api/contacts/${id}/`, { method: "PATCH", body }); toast("Đã lưu."); viewContact(id); }
        catch (e) { toast(e.message, "error"); save.disabled = false; }
      });
      const readOnly = !can("admin");
      if (readOnly) [name, rel, perm, notes, consent, source].forEach((el) => { el.disabled = true; });
      return h("div", { class: "stack" },
        h("div", { class: "card stack" },
          h("div", { class: "row" }, h("h2", { class: "grow", text: c.display_name }), originChip(c.data_origin)),
          h("dl", { class: "kv" },
            h("dt", { text: "Phân khúc" }), h("dd", { text: label("segment", c.segment) }),
            h("dt", { text: "Mã khách CRS" }), h("dd", { text: c.crs_customer_id || "—" }),
            h("dt", { text: "NV phụ trách" }), h("dd", { text: c.owner_staff_ref || "—" }),
            h("dt", { text: "Đồng ý lúc" }), h("dd", { text: fmtTime(c.consent_at) + (c.consent_source ? " (" + label("consent_source", c.consent_source) + ")" : "") }),
            h("dt", { text: "Đã nhắn trước" }), h("dd", { text: c.has_messaged_first ? "Có" : "Chưa" }))),
        h("div", { class: "card" },
          field("Tên hiển thị", name), field("Quan hệ", rel), field("Quyền trả lời", perm), field("Ghi chú quan hệ", notes),
          h("h3", { class: "field", text: "Đồng ý liên hệ (consent)" }),
          h("p", { class: "muted small", text: "Chỉ đánh dấu “Đồng ý” khi có bằng chứng; luôn ghi nguồn. CRS là nguồn chính, thay đổi ở đây được báo sang CRS khi bật tích hợp." }),
          field("Trạng thái", consent), field("Nguồn", source),
          readOnly ? h("p", { class: "muted small", text: "Bạn chỉ có quyền xem." }) : h("div", { class: "field" }, save)),
        h("div", { class: "card" }, h("h2", { text: "AI ghi nhớ về liên hệ này" }),
          memory.results.length ? h("div", { class: "list" }, memory.results.map((m) => h("div", { class: "row small" }, h("span", { class: "grow", text: m.content }), chip("mức " + m.importance)))) : empty("Chưa có ghi nhớ.")));
    });
  }

  // Channels --------------------------------------------------------------------------
  async function viewChannels() {
    current = { live: (ev) => { if (ev.type === "channel.updated") viewChannels(); } };
    await page("Kênh Zalo", async () => {
      const data = await api("/api/channels/");
      if (!data.results.length) return empty("Chưa có kênh nào. Kết nối kênh theo hướng dẫn trong tài liệu Phase 2.");
      return h("div", { class: "list" }, data.results.map((ch) => {
        const statusKind = { active: "ok", restricted: "danger", paused: "warn" }[ch.status] || "";
        const toggleMode = async () => {
          const mode = ch.mode === "auto" ? "assist" : "auto";
          try { await api(`/api/channels/${ch.id}/`, { method: "PATCH", body: { mode } }); toast("Đã đổi chế độ."); viewChannels(); }
          catch (e) { toast(e.message, "error"); }
        };
        return h("article", { class: "card stack" },
          h("div", { class: "row" }, h("strong", { class: "grow ellipsis", text: ch.name }), chip(label("channel_type", ch.channel_type), "info"), originChip(ch.data_origin)),
          h("div", { class: "row small" }, chip(label("channel_status", ch.status), statusKind), chip(label("mode", ch.mode)), ch.channel_type === "zalo_personal" ? chip(ch.risk_acknowledged ? "Đã xác nhận rủi ro" : "Chưa xác nhận rủi ro", ch.risk_acknowledged ? "" : "warn") : null),
          ch.last_error ? h("div", { class: "banner danger small", text: ch.last_error }) : null,
          h("div", { class: "row small muted" }, h("span", { text: "Kết nối: " + fmtTime(ch.connected_at) })),
          can("admin") ? h("div", { class: "row" }, h("button", { class: "btn small", onclick: toggleMode, text: ch.mode === "auto" ? "Chuyển sang: chủ tự gửi" : "Chuyển sang: tự động gửi" })) : null);
      }));
    });
  }

  // Analytics -------------------------------------------------------------------------
  let analyticsDays = 7;
  async function viewAnalytics() {
    current = null;
    await page("Thống kê", async () => {
      const data = await api("/api/dashboard/analytics/?days=" + analyticsDays);
      const tabs = h("div", { class: "tabs" }, [7, 30, 90].map((d) => h("button", { class: "btn small", "aria-pressed": String(analyticsDays === d), onclick: () => { analyticsDays = d; viewAnalytics(); }, text: d + " ngày" })));
      const sum = (arr) => arr.reduce((a, b) => a + b, 0);
      const totalIn = sum(data.messages.inbound), aiSent = sum(data.messages.ai_sent), approvedSent = sum(data.messages.approved_sent);
      const outcomes = Object.entries(data.ai_outcomes).filter(([, v]) => v > 0);
      const approvals = Object.entries(data.approvals).filter(([, v]) => v > 0);
      return h("div", { class: "stack" }, tabs,
        h("div", { class: "kpis" },
          h("div", { class: "card kpi" }, h("div", { class: "value", text: fmtNum(totalIn) }), h("div", { class: "label", text: "Tin đến" })),
          h("div", { class: "card kpi" }, h("div", { class: "value", text: fmtNum(aiSent) }), h("div", { class: "label", text: "AI tự gửi" })),
          h("div", { class: "card kpi" }, h("div", { class: "value", text: fmtNum(approvedSent) }), h("div", { class: "label", text: "Gửi sau khi duyệt" })),
          h("div", { class: "card kpi" }, h("div", { class: "value", text: data.approval_median_minutes === null ? "—" : data.approval_median_minutes + "′" }), h("div", { class: "label", text: "Thời gian duyệt (trung vị)" }))),
        h("div", { class: "card" }, h("h2", { text: "Tin nhắn theo ngày" }), messageChart(data)),
        h("div", { class: "grid-2" },
          h("div", { class: "card" }, h("h2", { text: "AI đã xử lý thế nào" }), outcomes.length ? bars(outcomes, (k) => label("outcome", k)) : empty("Chưa có dữ liệu."),
            h("p", { class: "muted small", text: "Độ tin cậy trung bình: " + (data.ai_avg_confidence === null ? "—" : Math.round(data.ai_avg_confidence * 100) + "%") })),
          h("div", { class: "card" }, h("h2", { text: "Hàng đợi duyệt" }), approvals.length ? bars(approvals, (k) => label("approval_status", k)) : empty("Chưa có dữ liệu."))),
        h("div", { class: "grid-2" },
          h("div", { class: "card" }, h("h2", { text: "Theo loại kênh" }), Object.keys(data.messages_by_channel_type).length ? bars(Object.entries(data.messages_by_channel_type), (k) => label("channel_type", k)) : empty("Chưa có dữ liệu.")),
          h("div", { class: "card" }, h("h2", { text: "Sử dụng Claude API" }), h("dl", { class: "kv" },
            h("dt", { text: "Số lần gọi" }), h("dd", { text: fmtNum(data.llm.calls) }),
            h("dt", { text: "Token vào" }), h("dd", { text: fmtNum(data.llm.input) }),
            h("dt", { text: "Token ra" }), h("dd", { text: fmtNum(data.llm.output) }),
            h("dt", { text: "Token đọc từ cache" }), h("dd", { text: fmtNum(data.llm.cache_read) })))));
    });
  }

  // More (mobile) --------------------------------------------------------------------
  function viewMore() {
    current = null;
    const items = [["#/owner-sends", "Chờ chủ gửi", "pending_owner_sends"], ["#/channels", "Kênh Zalo"], ["#/analytics", "Thống kê"], ["#/settings", "Cài đặt"]];
    mount(shell("Thêm", h("div", { class: "list" }, items.map(([href, t, badge]) => h("a", { class: "card list-item row", href },
      h("span", { class: "grow", text: t }), badge && state.stats && state.stats[badge] ? h("span", { class: "badge", text: state.stats[badge] }) : null, h("span", { class: "muted", text: "›" }))))));
  }

  // Settings --------------------------------------------------------------------------
  function viewSettings() {
    current = null;
    const items = [
      ["#/settings/ai", "Trợ lý AI", "Hồ sơ chủ tài khoản, phong cách giao tiếp, công khai là trợ lý"],
      ["#/settings/rules", "Quy tắc trả lời", "Khi nào AI tự trả lời, khi nào cần duyệt"],
      ["#/settings/aiva", "Tích hợp AIVA", "Chế độ, hàng đợi sự kiện, nhật ký trao đổi"],
      ["#/settings/account", "Tài khoản & người dùng", "Thông tin tenant, người dùng và vai trò"],
    ];
    mount(shell("Cài đặt", h("div", { class: "list" }, items.map(([href, t, d]) => h("a", { class: "card list-item", href }, h("strong", { text: t }), h("div", { class: "muted small", text: d }))))));
  }

  const lines = (v) => (Array.isArray(v) ? v.join("\n") : "");
  const toList = (v) => v.split("\n").map((x) => x.trim()).filter(Boolean);

  async function viewSettingsAI() {
    current = null;
    await page("Trợ lý AI", async () => {
      const [profile, style, control] = await Promise.all([api("/api/ai/profile/"), api("/api/ai/style/"), api("/api/ai/control/")]);
      const ro = !can("admin");
      const f = {
        occupation: h("input", { id: "occupation", value: profile.occupation || "" }),
        expertise: h("textarea", { id: "expertise", value: lines(profile.expertise_areas) }),
        knowledge: h("textarea", { id: "custom_knowledge", value: profile.custom_knowledge || "" }),
        avoid: h("textarea", { id: "topics_to_avoid", value: lines(profile.topics_to_avoid) }),
        formality: h("input", { id: "formality", type: "number", min: "1", max: "10", value: style.formality_level }),
        emoji: select("emoji_usage", L.emoji, style.emoji_usage),
        greeting: h("input", { id: "greeting", value: style.typical_greeting || "" }),
        farewell: h("input", { id: "farewell", value: style.typical_farewell || "" }),
        samples: h("textarea", { id: "samples", value: lines(style.sample_messages) }),
        disclose: h("input", { type: "checkbox", id: "disclose", checked: control.disclose_ai_when_asked ? true : null }),
      };
      if (ro) Object.values(f).forEach((el) => { el.disabled = true; });
      const save = h("button", { class: "btn primary block", text: "Lưu" });
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await api("/api/ai/profile/", { method: "PATCH", body: { occupation: f.occupation.value, expertise_areas: toList(f.expertise.value), custom_knowledge: f.knowledge.value, topics_to_avoid: toList(f.avoid.value) } });
          await api("/api/ai/style/", { method: "PATCH", body: { formality_level: Number(f.formality.value), emoji_usage: f.emoji.value, typical_greeting: f.greeting.value, typical_farewell: f.farewell.value, sample_messages: toList(f.samples.value) } });
          await api("/api/ai/control/", { method: "PATCH", body: { disclose_ai_when_asked: f.disclose.checked } });
          toast("Đã lưu cài đặt AI.");
        } catch (e) { toast(e.message, "error"); }
        save.disabled = false;
      });
      return h("div", { class: "stack" },
        h("div", { class: "card" }, h("h2", { text: "Hồ sơ chủ tài khoản" }),
          field("Nghề nghiệp / vai trò", f.occupation), field("Lĩnh vực chuyên môn (mỗi dòng một mục)", f.expertise),
          field("Kiến thức riêng AI cần biết", f.knowledge), field("Chủ đề tránh nói (mỗi dòng một mục)", f.avoid)),
        h("div", { class: "card" }, h("h2", { text: "Phong cách giao tiếp" }),
          field("Mức trang trọng (1–10)", f.formality), field("Dùng emoji", f.emoji), field("Câu chào quen dùng", f.greeting),
          field("Câu kết quen dùng", f.farewell), field("Tin mẫu của bạn (mỗi dòng một tin)", f.samples)),
        h("div", { class: "card" }, h("label", { class: "check", for: "disclose" }, f.disclose, "Khi khách hỏi thật lòng, trợ lý nói mình là trợ lý AI của bạn")),
        ro ? h("p", { class: "muted small", text: "Bạn chỉ có quyền xem." }) : save);
    });
  }

  async function viewSettingsRules() {
    current = null;
    await page("Quy tắc trả lời", async () => {
      const [rules, groups] = await Promise.all([api("/api/permissions/rules/"), api("/api/contact-groups/")]);
      const groupName = Object.fromEntries(groups.results.map((g) => [g.id, g.name]));
      const list = rules.results.length ? h("div", { class: "list" }, rules.results.map((r) => h("article", { class: "card stack" },
        h("div", { class: "row" }, h("strong", { class: "grow", text: r.name }), chip("ưu tiên " + r.priority), r.is_active ? chip("Đang bật", "ok") : chip("Tắt")),
        h("div", { class: "row small" }, chip(label("rule_action", r.action), r.action === "auto_reply" ? "info" : "warn"),
          r.contact_group ? chip("Nhóm: " + (groupName[r.contact_group] || r.contact_group)) : null,
          r.time_range_start ? chip(r.time_range_start.slice(0, 5) + "–" + r.time_range_end.slice(0, 5)) : null,
          (r.keyword_triggers || []).length ? chip("Từ khóa: " + r.keyword_triggers.join(", ")) : null),
        can("admin") ? h("div", { class: "row" },
          h("button", { class: "btn small", onclick: async () => { try { await api(`/api/permissions/rules/${r.id}/`, { method: "PATCH", body: { is_active: !r.is_active } }); viewSettingsRules(); } catch (e) { toast(e.message, "error"); } }, text: r.is_active ? "Tắt" : "Bật" }),
          h("button", { class: "btn small ghost", onclick: async () => { if (!confirm("Xóa quy tắc “" + r.name + "”?")) return; try { await api(`/api/permissions/rules/${r.id}/`, { method: "DELETE" }); viewSettingsRules(); } catch (e) { toast(e.message, "error"); } }, text: "Xóa" })) : null))) : empty("Chưa có quy tắc.");
      let form = null;
      if (can("admin")) {
        const name = h("input", { id: "rule_name", placeholder: "Ví dụ: Khách hàng hỏi giá cần duyệt" });
        const action = select("rule_action", L.rule_action, "need_approval");
        const group = select("rule_group", { "": "— Mọi liên hệ —", ...groupName }, "");
        const keywords = h("input", { id: "rule_keywords", placeholder: "giá, báo giá, hợp đồng" });
        const priority = h("input", { id: "rule_priority", type: "number", value: "10" });
        const add = h("button", { class: "btn primary block", text: "Thêm quy tắc" });
        add.addEventListener("click", async () => {
          if (!name.value.trim()) return toast("Cần đặt tên quy tắc.", "error");
          try {
            await api("/api/permissions/rules/", { method: "POST", body: { name: name.value, action: action.value, contact_group: group.value ? Number(group.value) : null, keyword_triggers: keywords.value.split(",").map((x) => x.trim()).filter(Boolean), priority: Number(priority.value) || 0 } });
            toast("Đã thêm quy tắc."); viewSettingsRules();
          } catch (e) { toast(e.message, "error"); }
        });
        form = h("div", { class: "card" }, h("h2", { text: "Thêm quy tắc" }), field("Tên", name), field("Hành động", action), field("Áp dụng cho nhóm", group), field("Từ khóa kích hoạt (phân cách bằng dấu phẩy)", keywords), field("Độ ưu tiên (cao hơn xét trước)", priority), h("div", { class: "field" }, add));
      }
      return h("div", { class: "stack" },
        h("p", { class: "muted small", text: "Hỏi về giá, hợp đồng, tiền luôn cần người duyệt; khách đòi gặp người luôn được chuyển cho người, bất kể quy tắc." }),
        list, form);
    });
  }

  // Phase 6: real-data (live) enrollment of this tenant — review, owner acknowledgement, stop.
  function liveCard(lv) {
    const en = lv.enrollment;
    const stText = { acknowledged: "Chủ tài khoản đã xác nhận — chờ vận hành kích hoạt", active: "Đang trao đổi dữ liệu thật", paused: "Tạm dừng", revoked: "Đã thu hồi xác nhận" };
    const liveAction = async (op, body) => { try { await api(`/api/integrations/aiva/live/${op}/`, { method: "POST", body }); toast("Đã cập nhật."); viewSettingsAIVA(); } catch (e) { toast(e.message, "error"); } };
    const levelChip = { ok: ["Đạt", "ok"], warn: ["Chú ý", "warn"], fail: ["Chưa đạt", "danger"] };
    const checks = lv.review.checks.map((c) => h("li", { class: "check-item" }, chip(levelChip[c.level][0], levelChip[c.level][1]), h("span", { class: "small", text: c.message + (c.count != null ? ` (${c.count})` : "") })));
    const parts = [h("h2", { text: "Kết nối dữ liệu thật (live)" }),
      h("div", { class: "banner " + (lv.live_exchange ? "danger" : "info") }, lv.live_exchange ? "Đang trao đổi dữ liệu khách thật với AIVA/CRS." : "Chưa trao đổi dữ liệu thật."),
      h("dl", { class: "kv" },
        h("dt", { text: "Trạng thái" }), h("dd", { text: en ? stText[en.status] : "Chưa đăng ký" }),
        h("dt", { text: "Văn bản GĐ duyệt" }), h("dd", { text: (en && en.approval_ref) || "—" }),
        h("dt", { text: "Dừng toàn hệ thống" }), h("dd", { text: lv.global_paused ? "Đang dừng" : "Không" }),
        en && en.pause_reason ? h("dt", { text: "Lý do dừng" }) : null, en && en.pause_reason ? h("dd", { text: en.pause_reason }) : null)];
    if (can("owner") && (!en || en.status === "revoked")) {
      const box = h("input", { type: "checkbox", id: "live-ack" });
      parts.push(h("p", { class: "small", text: lv.acknowledgement_text }),
        h("label", { class: "check-item ack", for: "live-ack" }, box, h("span", { text: "Tôi đã đọc và đồng ý" })),
        h("button", { class: "btn primary block", text: "Xác nhận đồng ý", onclick: () => { if (!box.checked) return toast("Hãy đánh dấu đã đọc.", "error"); liveAction("acknowledge", { confirm: true }); } }));
    }
    if (en && en.status === "active") {
      parts.push(h("button", { class: "btn danger block", text: "Tạm dừng kết nối live", onclick: () => { const reason = prompt("Lý do tạm dừng?"); if (reason) liveAction("pause", { reason }); } }));
    }
    if (can("owner") && en && en.status !== "revoked") {
      parts.push(h("button", { class: "btn ghost block", text: "Thu hồi xác nhận (xóa sự kiện chưa gửi)", onclick: () => { if (confirm("Thu hồi xác nhận? Sự kiện thật chưa gửi sẽ bị hủy.")) liveAction("revoke", { reason: "Chủ tài khoản thu hồi trong app" }); } }));
    }
    parts.push(h("h3", { text: "Rà soát trước khi live " + (lv.review.passed ? "— đạt" : "— chưa đạt") }), h("ul", { class: "stack plain" }, checks),
      h("p", { class: "muted small", text: "Kích hoạt và mở lại sau khi dừng do bộ phận vận hành thực hiện sau khi rà soát đạt và có văn bản Giám đốc phê duyệt." }));
    return h("div", { class: "card" }, ...parts);
  }

  async function viewSettingsAIVA() {
    current = { live: (ev) => { if (ev.type === "aiva.outbox_dead") viewSettingsAIVA(); } };
    await page("Tích hợp AIVA", async () => {
      const [st, outbox, logs, lv] = await Promise.all([api("/api/integrations/aiva/status/"), api("/api/integrations/aiva/outbox/?page_size=20"), api("/api/integrations/aiva/exchanges/?page_size=20"), can("admin") ? api("/api/integrations/aiva/live/") : Promise.resolve(null)]);
      const modeText = { off: "Tắt — không trao đổi dữ liệu", sandbox: "Sandbox — chỉ dữ liệu demo", live: "Live — dữ liệu thật" }[st.mode];
      const rows = outbox.results.map((o) => h("tr", {},
        h("td", { text: o.event_type }), h("td", {}, chip(label("outbox", o.status), { sent: "ok", dead: "danger", failed: "warn" }[o.status] || "")),
        h("td", { text: String(o.attempts) }), h("td", { text: fmtTime(o.created_at) }),
        h("td", {}, can("admin") && (o.status === "dead" || o.status === "failed") ? h("button", { class: "btn small", onclick: async () => { try { await api(`/api/integrations/aiva/outbox/${o.id}/retry/`, { method: "POST" }); toast("Đã xếp lại để gửi."); viewSettingsAIVA(); } catch (e) { toast(e.message, "error"); } }, text: "Gửi lại" }) : null)));
      const logRows = logs.results.map((l) => h("tr", {},
        h("td", { text: l.direction === "inbound" ? "AIVA → Zalo AI" : "Zalo AI → AIVA" }), h("td", { text: l.event_type }),
        h("td", {}, chip(l.success ? "OK" : "Lỗi", l.success ? "ok" : "danger")), h("td", { class: "small", text: l.detail }), h("td", { text: fmtTime(l.created_at) })));
      return h("div", { class: "stack" },
        h("div", { class: "banner " + (st.mode === "off" ? "info" : st.mode === "sandbox" ? "warn" : "danger") }, h("strong", { text: "Chế độ: " }), modeText),
        h("div", { class: "card" }, h("dl", { class: "kv" },
          h("dt", { text: "Mã tenant bên CRS" }), h("dd", { text: st.tenant_ref || "Chưa đặt (cần để kết nối)" }),
          h("dt", { text: "Dữ liệu được gửi" }), h("dd", { text: st.allowed_data_origins.length ? st.allowed_data_origins.join(", ") : "không" }),
          h("dt", { text: "Kênh gửi" }), h("dd", { text: st.client === "mock" ? "Giả lập (chưa có AIVA)" : "HTTP ký HMAC" }),
          h("dt", { text: "Live đã duyệt" }), h("dd", { text: st.live_approved ? "Có" : "Chưa" })),
          h("div", { class: "row field" }, Object.entries(st.outbox).map(([k, v]) => chip(label("outbox", k) + ": " + v, k === "dead" && v ? "danger" : "")))),
        lv ? liveCard(lv) : null,
        h("div", { class: "card" }, h("h2", { text: "Sự kiện gửi AIVA gần đây" }), rows.length ? h("div", { class: "table-wrap" }, h("table", { class: "simple" }, h("thead", {}, h("tr", {}, ["Sự kiện", "Trạng thái", "Lần thử", "Lúc", ""].map((t) => h("th", { text: t })))), h("tbody", {}, rows))) : empty("Chưa có sự kiện.")),
        h("div", { class: "card" }, h("h2", { text: "Nhật ký trao đổi" }), logRows.length ? h("div", { class: "table-wrap" }, h("table", { class: "simple" }, h("thead", {}, h("tr", {}, ["Chiều", "Loại", "Kết quả", "Chi tiết", "Lúc"].map((t) => h("th", { text: t })))), h("tbody", {}, logRows))) : empty("Chưa có trao đổi.")),
        h("p", { class: "muted small", text: "Bật/tắt chế độ tích hợp bằng biến môi trường AIVA_INTEGRATION_MODE trên máy chủ. Chế độ live cần Giám đốc phê duyệt riêng." }));
    });
  }

  async function viewSettingsAccount() {
    current = null;
    await page("Tài khoản & người dùng", async () => {
      const [tenant, users] = await Promise.all([api("/api/tenant/"), can("admin") ? api("/api/users/") : Promise.resolve({ results: [] })]);
      const name = h("input", { id: "tenant_name", value: tenant.name, disabled: can("owner") ? null : true });
      const ref = h("input", { id: "external_ref", value: tenant.external_ref || "", disabled: can("owner") ? null : true });
      const save = h("button", { class: "btn primary block", text: "Lưu" });
      save.addEventListener("click", async () => {
        try { state.tenant = await api("/api/tenant/", { method: "PATCH", body: { name: name.value, external_ref: ref.value } }); toast("Đã lưu."); } catch (e) { toast(e.message, "error"); }
      });
      return h("div", { class: "stack" },
        h("div", { class: "card" }, h("h2", { text: "Tài khoản" }), field("Tên", name), field("Mã tenant bên CRS (external_ref)", ref),
          h("p", { class: "muted small", text: "Gói: " + tenant.subscription_tier }), can("owner") ? save : h("p", { class: "muted small", text: "Chỉ chủ tài khoản được sửa." })),
        users.results.length ? h("div", { class: "card" }, h("h2", { text: "Người dùng" }), h("div", { class: "list" }, users.results.map((u) => h("div", { class: "row small" },
          h("strong", { class: "grow", text: u.username }), chip(label("role", u.role)), u.staff_ref ? chip("NV " + u.staff_ref, "info") : null, u.is_active ? null : chip("Đã khóa", "danger"))))) : null);
    });
  }

  // --- Login ---------------------------------------------------------------------------
  function viewLogin(errorText) {
    current = null;
    const user = h("input", { id: "username", autocomplete: "username", required: true });
    const pass = h("input", { id: "password", type: "password", autocomplete: "current-password", required: true });
    const btn = h("button", { class: "btn primary block", type: "submit", text: "Đăng nhập" });
    const err = h("div", { class: "banner danger small" + (errorText ? "" : " hidden"), text: errorText || "" });
    const form = h("form", { class: "stack" }, field("Tên đăng nhập", user), field("Mật khẩu", pass), err, btn);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      btn.disabled = true;
      try {
        const res = await fetch("/api/auth/login/", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: user.value, password: pass.value }) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(res.status === 401 ? "Sai tên đăng nhập hoặc mật khẩu." : apiMessage(data));
        state.access = data.access; state.refresh = data.refresh; store.set("zai.refresh", data.refresh);
        await boot();
      } catch (e) {
        err.textContent = e.message; err.classList.remove("hidden"); btn.disabled = false;
      }
    });
    const logo = h("img", { class: "logo", src: document.querySelector("link[rel=icon]").href, alt: "" });
    mount(h("div", { class: "login" }, h("div", { class: "card stack" }, logo, h("h1", { text: "Trợ lý Zalo AI" }), h("p", { class: "muted", text: "Đăng nhập để quản lý trợ lý trả lời tin nhắn." }), form)));
    user.focus();
  }

  // --- Router + boot -----------------------------------------------------------------
  const ROUTES = [
    [/^#\/?$/, viewDashboard],
    [/^#\/approvals$/, viewApprovals],
    [/^#\/conversations$/, viewConversations],
    [/^#\/conversations\/(\d+)$/, viewConversation],
    [/^#\/owner-sends$/, viewOwnerSends],
    [/^#\/contacts$/, viewContacts],
    [/^#\/contacts\/(\d+)$/, viewContact],
    [/^#\/channels$/, viewChannels],
    [/^#\/analytics$/, viewAnalytics],
    [/^#\/more$/, viewMore],
    [/^#\/settings$/, viewSettings],
    [/^#\/settings\/ai$/, viewSettingsAI],
    [/^#\/settings\/rules$/, viewSettingsRules],
    [/^#\/settings\/aiva$/, viewSettingsAIVA],
    [/^#\/settings\/account$/, viewSettingsAccount],
  ];
  function route() {
    if (!state.me) return viewLogin();
    const hash = location.hash || "#/";
    for (const [re, view] of ROUTES) {
      const m = hash.match(re);
      if (m) { window.scrollTo(0, 0); return view(...m.slice(1)); }
    }
    location.hash = "#/";
  }
  function render() { route(); }

  async function boot() {
    try {
      state.me = await api("/api/auth/me/");
      [state.tenant, state.stats] = await Promise.all([api("/api/tenant/"), api("/api/dashboard/stats/")]);
    } catch (e) {
      state.me = null;
      return viewLogin(e.status === 401 ? "" : "Không kết nối được máy chủ: " + e.message);
    }
    route();
    connectWS();
  }

  window.addEventListener("hashchange", route);
  if (state.refresh) boot(); else viewLogin();
})();
