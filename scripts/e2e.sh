#!/usr/bin/env bash
# scripts/e2e.sh — One-click end-to-end validation of the dual-path PEP/PDP flow.
#
# Modes:
#   ./scripts/e2e.sh                 # auto: Docker if available, else in-process
#   ./scripts/e2e.sh --in-process    # force the in-process real-HTTP e2e (no Docker)
#   ./scripts/e2e.sh --docker        # force the full Docker server+client e2e
#
# The in-process mode starts a real FastAPI server in a background thread and
# drives the Harness client over real HTTP — no Docker daemon required.
# The Docker mode brings up the server + client containers and exits with the
# client's status code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

_green='\033[0;32m'; _yellow='\033[0;33m'; _red='\033[0;31m'; _reset='\033[0m'
info()  { echo -e "${_green}[e2e]${_reset} $*"; }
warn()  { echo -e "${_yellow}[e2e]${_reset} $*"; }
error() { echo -e "${_red}[e2e]${_reset} $*" >&2; exit 1; }

MODE="auto"
for arg in "$@"; do
  case "$arg" in
    --in-process|--inprocess) MODE="in-process" ;;
    --docker) MODE="docker" ;;
  esac
done

docker_available() {
  command -v docker &>/dev/null && docker info &>/dev/null 2>&1
}

run_in_process() {
  info "Running in-process real-HTTP dual-path e2e…"
  python -m agentguard.examples.dual_path_e2e
}

run_docker() {
  info "Running full Docker server+client e2e…"
  local compose
  if docker compose version &>/dev/null 2>&1; then
    compose="docker compose"
  elif command -v docker-compose &>/dev/null; then
    compose="docker-compose"
  else
    error "Docker Compose not available."
  fi
  [ -f .env ] || cp .env.example .env
  # shellcheck disable=SC2086
  $compose -f docker-compose.yml -f docker-compose.e2e.yml up --build \
    --abort-on-container-exit --exit-code-from client
  local status=$?
  # shellcheck disable=SC2086
  $compose -f docker-compose.yml -f docker-compose.e2e.yml down -v || true
  return $status
}

case "$MODE" in
  in-process) run_in_process ;;
  docker)     run_docker ;;
  auto)
    if docker_available; then
      run_docker
    else
      warn "Docker daemon not available — falling back to in-process e2e."
      run_in_process
    fi
    ;;
esac
