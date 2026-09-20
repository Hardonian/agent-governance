#!/usr/bin/env bash
# self-healing service mesh health check — EPYC AI lab
set -Eeuo pipefail

LOGFILE="/home/scott/ai-lab/agent-governance/mesh/health.log"
TIMESTAMP="[$(date '+%Y-%m-%d %H:%M:%S %Z')]"

log() { echo "${TIMESTAMP} $*" >> "$LOGFILE"; }

check_port() {
  local host="$1" port="$2" timeout="${3:-3}"
  nc -z -w "$timeout" "$host" "$port" 2>/dev/null
}

# Service definitions: NAME|HOST|PORT|TYPE|UNIT
# TYPE: user = systemctl --user, sys = sudo systemctl, docker = docker restart
SERVICES=(
  "ollama-default|127.0.0.1|11434|user|ollama-default.service"
  "ollama-p40|127.0.0.1|11435|user|ollama-p40.service"
  "ollama-3060|127.0.0.1|11436|user|ollama-3060.service"
  "ollama-v100|127.0.0.1|11437|user|ollama-v100.service"
  "ollama-router|127.0.0.1|11438|user|ollama-router.service"
  "ai-litellm-host|127.0.0.1|4000|sys|ai-litellm-host.service"
  "open-webui|127.0.0.1|3002|docker|ai-open-webui"
  "postgres-pgvector|127.0.0.1|5433|docker|ai-postgres-pgvector"
  "redis|127.0.0.1|6380|docker|ai-redis"
  "qdrant|127.0.0.1|6333|docker|ai-qdrant"
  "grafana|127.0.0.1|3005|docker|grafana"
  "prometheus|127.0.0.1|9090|docker|prometheus"
  "cadvisor|127.0.0.1|8080|docker|ai-cadvisor"
)

log "=== Health check cycle started ==="

ok=0; fail=0; restarted=0

for entry in "${SERVICES[@]}"; do
  IFS='|' read -r name host port stype unit <<< "$entry"

  if check_port "$host" "$port"; then
    log "OK  $name ($host:$port)"
    ok=$((ok + 1))
    continue
  fi

  log "FAIL $name ($host:$port) — attempting restart ($stype/$unit)"
  restart_output=""
  case "$stype" in
    user)   restart_output=$(systemctl --user restart "$unit" 2>&1 || true) ;;
    sys)    restart_output=$(sudo -n systemctl restart "$unit" 2>&1 || true) ;;
    docker) restart_output=$(docker restart "$unit" 2>&1 || true) ;;
  esac
  if [[ -n "$restart_output" ]]; then
    log "  restart> $restart_output"
  fi

  # re-check once with longer timeout
  if check_port "$host" "$port" 5; then
    log "RECOVERED $name after restart"
    ok=$((ok + 1))
  else
    log "STILL DOWN $name after restart attempt"
    fail=$((fail + 1))
  fi
  restarted=$((restarted + 1))
done

log "=== Cycle complete: ok=$ok fail=$fail restarted=$restarted ==="
