#!/usr/bin/env bash
# Deploy (or upgrade to) one image tag on the production server.
#
#   deploy/scripts/deploy.sh <image-tag>          e.g. sha-1a2b3c4 or v1.0.0
#
# Steps: pull images -> back up the database -> run migrations -> start the new version ->
# wait until /readyz/ answers -> smoke test. If the new version never becomes healthy, the
# previous tag is started again (migrations are not reversed: keep them backward compatible,
# and restore the backup by hand only if needed, see docs/PHASE5_TESTING_DEPLOYMENT.md).
set -euo pipefail

cd "$(dirname "$0")/../.."
TAG="${1:?usage: deploy.sh <image-tag>}"
COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.production)
STATE_FILE=".deployed-tag"
PROFILES="${COMPOSE_PROFILES:-}"

[[ -f .env.production ]] || { echo "Missing .env.production (copy .env.production.example)"; exit 1; }
export COMPOSE_PROFILES="$PROFILES"

PREVIOUS="$(cat "$STATE_FILE" 2>/dev/null || true)"
echo "==> Deploying $TAG (previous: ${PREVIOUS:-none})"

export IMAGE_TAG="$TAG"
# SKIP_PULL=1 only for a local rehearsal with locally built images.
[[ "${SKIP_PULL:-0}" == 1 ]] || "${COMPOSE[@]}" pull --quiet --ignore-buildable

if [[ -n "$PREVIOUS" ]]; then
  echo "==> Backing up the database before migrating"
  deploy/scripts/backup.sh
fi

echo "==> Running migrations"
"${COMPOSE[@]}" up -d db redis
"${COMPOSE[@]}" run --rm --no-deps web python manage.py migrate --noinput
"${COMPOSE[@]}" run --rm --no-deps web python manage.py check --deploy --fail-level ERROR

echo "==> Starting $TAG"
# `up` itself fails when web never turns healthy (caddy waits for it): handle that below.
"${COMPOSE[@]}" up -d --remove-orphans || echo "!! docker compose up reported a failure"

healthy() {
  local id
  id="$("${COMPOSE[@]}" ps -q web)"
  [[ -n "$id" ]] && [[ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" == "healthy" ]]
}

wait_healthy() {
  for _ in $(seq 1 40); do
    healthy && return 0
    sleep 3
  done
  return 1
}

if wait_healthy; then
  echo "$TAG" > "$STATE_FILE"
  echo "==> $TAG is healthy"
  deploy/scripts/smoke_test.sh "https://$(grep -E '^DOMAIN=' .env.production | cut -d= -f2-)"
  exit $?
fi

echo "!! $TAG did not become healthy; recent web logs:"
"${COMPOSE[@]}" logs --tail 80 web || true
if [[ -n "$PREVIOUS" ]]; then
  echo "==> Rolling back to $PREVIOUS"
  export IMAGE_TAG="$PREVIOUS"
  "${COMPOSE[@]}" up -d --remove-orphans || true
  if wait_healthy; then echo "==> Back on $PREVIOUS"; else echo "!! $PREVIOUS is not healthy either"; fi
fi
exit 1
