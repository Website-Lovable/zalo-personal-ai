#!/usr/bin/env bash
# Post-deploy smoke test against the public URL. Read-only: it logs in nowhere and sends
# nothing to Zalo. Usage: deploy/scripts/smoke_test.sh https://zalo.example.com
set -euo pipefail

BASE="${1:?usage: smoke_test.sh <base-url>}"
BASE="${BASE%/}"
fail=0
# Extra curl options, e.g. CURL_OPTS=-k for a local rehearsal with Caddy's internal CA.
read -r -a EXTRA <<< "${CURL_OPTS:-}"

check() {  # check <name> <expected-status> <path> [curl args...]
  local name="$1" expected="$2" path="$3"
  shift 3
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${EXTRA[@]}" "$@" "$BASE$path" || echo 000)"
  if [[ "$code" == "$expected" ]]; then
    echo "ok   $name ($code)"
  else
    echo "FAIL $name: expected $expected, got $code"
    fail=1
  fi
}

check "liveness"                 200 /healthz/
check "admin UI shell"           200 /app/
check "API requires auth"        401 /api/channels/
check "metrics hidden publicly"  404 /metrics/
check "readiness hidden"         404 /readyz/

if [[ "$BASE" == https://* ]]; then
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${EXTRA[@]}" "http://${BASE#https://}/healthz/" || echo 000)"
  if [[ "$code" == 308 || "$code" == 301 ]]; then echo "ok   HTTP redirects to HTTPS ($code)"
  else echo "FAIL HTTP redirect: got $code"; fail=1; fi
fi

hsts="$(curl -sSI --max-time 10 "${EXTRA[@]}" "$BASE/app/" | tr -d '\r' | grep -i '^strict-transport-security:' || true)"
if [[ -n "$hsts" ]]; then echo "ok   HSTS header"; else echo "FAIL HSTS header missing"; fail=1; fi

exit "$fail"
