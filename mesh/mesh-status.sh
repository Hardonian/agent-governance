#!/usr/bin/env bash
# quick status summary of all EPYC AI lab services
set -Eeuo pipefail

BOLD='\033[1m'
GREEN='\033[32m'
RED='\033[31m'
RESET='\033[0m'

declare -A SERVICES=(
  [ollama-default]=11434
  [ollama-p40]=11435
  [ollama-3060]=11436
  [ollama-v100]=11437
  [ollama-router]=11438
  [ai-litellm-host]=4000
  [open-webui]=3002
  [postgres-pgvector]=5433
  [redis]=6380
  [qdrant]=6333
  [grafana]=3005
  [prometheus]=9090
  [cadvisor]=8080
)

printf "%-22s %-6s %s\n" "SERVICE" "PORT" "STATUS"
printf "%-22s %-6s %s\n" "-------" "----" "------"

up=0; down=0

for name in $(echo "${!SERVICES[@]}" | tr ' ' '\n' | sort); do
  port="${SERVICES[$name]}"
  if nc -z -w 2 127.0.0.1 "$port" 2>/dev/null; then
    printf "%-22s %-6s ${GREEN}%s${RESET}\n" "$name" "$port" "UP"
    up=$((up + 1))
  else
    printf "%-22s %-6s ${RED}%s${RESET}\n" "$name" "$port" "DOWN"
    down=$((down + 1))
  fi
done

echo ""
printf "${BOLD}%d UP / %d DOWN / %d total${RESET}\n" "$up" "$down" "$((up+down))"

# Show last 5 log entries
LOGFILE="/home/scott/ai-lab/agent-governance/mesh/health.log"
if [[ -f "$LOGFILE" ]]; then
  echo ""
  echo "Last 5 log entries:"
  tail -5 "$LOGFILE"
fi
