#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OLLAMA_BASE_URL_DEFAULT="http://127.0.0.1:11434"
OLLAMA_MODEL_DEFAULT="qwen2.5-coder:3b"

# Load env overrides from .env.dev first, then .env.
if [[ -f ".env.dev" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.dev"
  set +a
elif [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

AI_PROVIDER="${AI_PROVIDER:-ollama}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-$OLLAMA_BASE_URL_DEFAULT}"
OLLAMA_MODEL="${OLLAMA_MODEL:-$OLLAMA_MODEL_DEFAULT}"

echo "==> ChurchForce start_dev"
echo "    AI_PROVIDER=$AI_PROVIDER"
echo "    OLLAMA_BASE_URL=$OLLAMA_BASE_URL"
echo "    OLLAMA_MODEL=$OLLAMA_MODEL"

cleanup() {
  if [[ -n "${OLLAMA_PID:-}" ]] && kill -0 "$OLLAMA_PID" >/dev/null 2>&1; then
    echo "==> Stopping local ollama serve (pid $OLLAMA_PID)"
    kill "$OLLAMA_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

start_ollama_if_needed() {
  if [[ "$AI_PROVIDER" != "ollama" ]]; then
    echo "==> AI provider is not ollama; skipping ollama startup."
    return 0
  fi

  if ! command -v ollama >/dev/null 2>&1; then
    echo "!! ollama binary not found in PATH."
    echo "   Install Ollama first, or set AI_PROVIDER to anthropic in env."
    return 1
  fi

  if curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
    echo "==> Ollama API already running."
  else
    echo "==> Starting ollama serve in background..."
    mkdir -p .logs
    : > .logs/ollama.log
    ollama serve >> .logs/ollama.log 2>&1 &
    OLLAMA_PID=$!
    echo "    ollama pid: $OLLAMA_PID"
  fi

  local wait_ok=0
  local attempts=20
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
      wait_ok=1
      break
    fi

    # If we started ollama in this script and it already died, fail fast.
    if [[ -n "${OLLAMA_PID:-}" ]] && ! kill -0 "$OLLAMA_PID" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if [[ "$wait_ok" -ne 1 ]]; then
    echo "!! Ollama API did not become healthy at $OLLAMA_BASE_URL."
    echo "   Check .logs/ollama.log for details."
    if [[ -s .logs/ollama.log ]]; then
      echo "   --- last ollama.log lines ---"
      tail -n 20 .logs/ollama.log || true
      echo "   ----------------------------"
    fi
    return 1
  fi

  if ! ollama list | awk '{print $1}' | rg -x "$OLLAMA_MODEL(:latest)?" >/dev/null 2>&1; then
    echo "==> Pulling model: $OLLAMA_MODEL"
    if ! ollama pull "$OLLAMA_MODEL"; then
      echo "!! Model pull failed (likely network timeout)."
      echo "   Continuing startup without blocking Django."
      echo "   Retry later with: ollama pull $OLLAMA_MODEL"
    fi
  else
    echo "==> Model present: $OLLAMA_MODEL"
  fi
}

start_ollama_if_needed

echo "==> Starting Django dev server"
python manage.py devserver "$@"
