// Decide whether an error from zca-js is a sign that Zalo is restricting the account.
// Returns a restriction kind understood by Django, or null for an ordinary failure.
// The patterns are a first guess and must be checked against real errors (see docs).

const BLOCKED = /(block|bị chặn|chặn|spam|restrict|hạn chế|khóa|khoá|banned|locked|vi phạm)/i;
const AUTH = /(status code 40[13]|unauthori[sz]ed|forbidden|session|đăng nhập lại|login)/i;

export function classifyError(error) {
  const message = String(error?.message ?? error ?? "");
  if (BLOCKED.test(message)) return "blocked";
  if (AUTH.test(message)) return "session_expired";
  return null;
}

// Listener close codes from zca-js (CloseReason)
export const CLOSE_MANUAL = 1000;
export const CLOSE_DUPLICATE = 3000;
export const CLOSE_KICK = 3003;

export function classifyClose(code) {
  if (code === CLOSE_MANUAL) return null;
  if (code === CLOSE_DUPLICATE) return "session_conflict";
  if (code === CLOSE_KICK) return "kicked";
  return "disconnected";
}
