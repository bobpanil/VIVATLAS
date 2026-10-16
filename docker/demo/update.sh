#!/usr/bin/env bash
# Keep the public demo on the newest VIVATLAS release. Run every 15 minutes by
# vivatlas-demo-update.timer; safe to run by hand too.
#
# The "stable" image moves only when a v* tag is pushed. When it has moved, the
# demo is restarted on it and has to report healthy. If it doesn't, the demo goes
# back to the image it ran before and the new one is remembered as bad, so the
# next run doesn't try it again. The next release clears that.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

IMAGE=${DEMO_IMAGE:-ghcr.io/bobpanil/vivatlas:stable}  # DEMO_IMAGE: for trying the script out
PREVIOUS=vivatlas-demo:previous
BAD=.bad-image
HEALTH_WAIT=180

log() { echo "vivatlas-demo-update: $*"; }

running_image() {
	local id
	id=$(docker compose ps -q vivatlas 2>/dev/null || true)
	[ -n "$id" ] && docker inspect -f '{{.Image}}' "$id" 2>/dev/null || true
}

wait_healthy() {
	local id state waited=0
	while [ "$waited" -lt "$HEALTH_WAIT" ]; do
		id=$(docker compose ps -q vivatlas)
		state=$(docker inspect -f '{{.State.Health.Status}}' "$id" 2>/dev/null || echo none)
		[ "$state" = healthy ] && return 0
		sleep 5
		waited=$((waited + 5))
	done
	return 1
}

docker pull -q "$IMAGE" >/dev/null
latest=$(docker image inspect -f '{{.Id}}' "$IMAGE")
running=$(running_image)

if [ "$running" = "$latest" ]; then
	exit 0
fi
if [ -f "$BAD" ] && [ "$(cat "$BAD")" = "$latest" ]; then
	log "skipping $latest: it failed its health check before"
	exit 0
fi

if [ -n "$running" ]; then
	docker image tag "$running" "$PREVIOUS"
fi
log "starting $latest (was ${running:-nothing})"
IMAGE="$IMAGE" docker compose up -d --remove-orphans

if wait_healthy; then
	rm -f "$BAD"
	# The starter catalogue. Links already queued once are skipped.
	docker compose exec -T vivatlas python -m vivatlas.demo seed /seed/links.txt || log "seeding failed"
	docker image prune -f >/dev/null
	log "running $latest"
	exit 0
fi

echo "$latest" >"$BAD"
log "$latest did not become healthy in ${HEALTH_WAIT}s; last log lines:"
docker compose logs --tail 30 vivatlas || true
if [ -n "$running" ]; then
	IMAGE="$PREVIOUS" docker compose up -d
	log "rolled back to $running"
fi
exit 1
