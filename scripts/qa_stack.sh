#!/usr/bin/env bash
# Production-like QA stack on one machine: PostgreSQL 16 :5544, Redis :6390, simulated LLM :8899,
# backend (ENV=production, AUTO_MIGRATE=false, explicit alembic) :8801, built Next.js BFF :3100.
# usage: scripts/qa_stack.sh up | down | status | stop <svc> | start <svc>      svc: pg redis llm api web
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; RUN=/tmp/bg-qa; PG="$RUN/pg"; mkdir -p "$RUN"
export QA_SECRET="qa-only-secret-$(printf 'x%.0s' {1..40})"
DBURL="postgresql://buildguard:qa-pass@127.0.0.1:5544/buildguard"
pgbin=/usr/lib/postgresql/16/bin
alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }
wait_http() { for _ in $(seq 1 60); do curl -s -m 1 -o /dev/null "$1" && return 0; sleep 0.5; done; return 1; }

start_pg() {
  if [ ! -d "$PG/data" ]; then mkdir -p "$PG"; chown postgres "$PG"; runuser -u postgres -- $pgbin/initdb -D "$PG/data" -A trust -E UTF8 --locale=C.UTF-8 >/dev/null; fi
  runuser -u postgres -- $pgbin/pg_ctl -D "$PG/data" -o "-p 5544 -k $PG -c listen_addresses=127.0.0.1 -c max_connections=100" -l "$PG/log" -w start >/dev/null
  runuser -u postgres -- psql -h 127.0.0.1 -p 5544 -d postgres -tc "select 1 from pg_roles where rolname='buildguard'" | grep -q 1 || \
    runuser -u postgres -- psql -h 127.0.0.1 -p 5544 -d postgres -qc "create role buildguard login password 'qa-pass'" -c "create database buildguard owner buildguard"
}
stop_pg() { runuser -u postgres -- $pgbin/pg_ctl -D "$PG/data" -m fast stop >/dev/null 2>&1; }
# launch <name> <cmd...>: detached, and the pid file holds the REAL process id (exec replaces the wrapper shell).
launch() { local n=$1; shift; setsid nohup bash -c 'echo $$ >"$0"; exec "$@"' "$RUN/$n.pid" "$@" >"$RUN/$n.log" 2>&1 & }
start_redis() { alive redis && return; launch redis redis-server --port 6390 --save "" --appendonly no; }
start_llm()   { alive llm && return; cd "$ROOT"; launch llm python scripts/fake_llm.py 8899; }
start_api() {
  alive api && return; cd "$ROOT"
  ENV=production DATABASE_URL="$DBURL" AUTO_MIGRATE=false JWT_SECRET="$QA_SECRET" ADMIN_EMAIL=admin@qa.test ALLOW_SELF_REGISTER=true \
  CORS_ORIGINS=http://localhost:3100 TRUST_PROXY=true PUBLIC_BASE_URL=http://localhost:3100 UPLOAD_DIR="$RUN/uploads" REDIS_URL=redis://127.0.0.1:6390/0 \
  AI_PROVIDER=anthropic AI_API_KEY=qa-fake-key AI_BASE_URL=http://127.0.0.1:8899/anthropic AI_MODEL=fake AI_MAX_REQUESTS_PER_MINUTE=${QA_AI_RPM:-1000} \
  RATE_LIMIT_AI_PER_MIN=${QA_AI_RPM:-1000} RATE_LIMIT_DEFAULT_PER_MIN=${QA_RPM:-100000} RATE_LIMIT_LOGIN_PER_MIN=${QA_LOGIN_RPM:-1000} RATE_LIMIT_REGISTER_PER_MIN=${QA_REG_RPM:-1000} \
  launch api uvicorn app.main:app --port 8801 --log-level warning
}
start_web() { alive web && return; cd "$ROOT/frontend"; PORT=3100 HOSTNAME=0.0.0.0 BACKEND_URL=http://127.0.0.1:8801 COOKIE_SECURE=true NODE_ENV=production launch web node .next/standalone/server.js; }
kill_svc() { case "$1" in pg) stop_pg;; *) if alive "$1"; then kill "$(cat "$RUN/$1.pid")"; for _ in 1 2 3 4 5 6 7 8 9 10; do alive "$1" || break; sleep 0.3; done; fi; rm -f "$RUN/$1.pid";; esac; }
start_svc() { case "$1" in pg) start_pg;; redis) start_redis;; llm) start_llm;; api) start_api;; web) start_web;; esac; }
case "${1:-}" in
  up)
    for s in web api llm redis; do kill_svc $s; done; stop_pg; sleep 1; rm -rf "$RUN/uploads" "$PG"
    start_pg; start_redis; start_llm
    cd "$ROOT" && DATABASE_URL="$DBURL" ENV=production JWT_SECRET="$QA_SECRET" ADMIN_EMAIL=admin@qa.test CORS_ORIGINS=http://localhost:3100 alembic upgrade head 2>&1 | tail -3
    start_api; start_web; wait_http http://127.0.0.1:8801/ready; wait_http http://127.0.0.1:3100/login
    echo "ready: $(curl -s -m 2 localhost:8801/ready)  web:$(curl -s -m 2 -o /dev/null -w '%{http_code}' localhost:3100/login)" ;;
  down) for s in web api llm redis; do kill_svc $s; done; stop_pg; echo stopped ;;
  status) for s in pg redis llm api web; do if [ $s = pg ]; then runuser -u postgres -- $pgbin/pg_ctl -D "$PG/data" status >/dev/null 2>&1 && echo "pg up" || echo "pg down"; else alive $s && echo "$s up" || echo "$s down"; fi; done ;;
  stop) kill_svc "$2"; echo "$2 stopped" ;;
  start) start_svc "$2"; sleep 2; echo "$2 started" ;;
esac
