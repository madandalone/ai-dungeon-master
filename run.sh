
#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
# Загружаем локальную конфигурацию из .env
if [[ -f ".env" ]]; then
    set -a
    source ".env"
    set +a
fi
PYTHON=""

if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    PYTHON="$VIRTUAL_ENV/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    echo "Ошибка: виртуальное окружение не найдено."
    exit 1
fi

echo "Используется Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"

if [[ -z "${RULES_BOOK_PATH:-}" ]]; then
    echo "Предупреждение: RULES_BOOK_PATH не задан."
    echo "Реальные игровые ходы не заработают без файла правил."
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
    echo "Предупреждение: OPENAI_API_KEY не задан."
    echo "Реальный AI Dungeon Master не сможет обратиться к LLM."
fi

BACKEND_PID=""
FRONTEND_PID=""
WARMUP_PID=""

cleanup() {
    trap - EXIT INT TERM
    echo ""
    echo "Останавливаем сервисы..."
    [[ -z "$BACKEND_PID" ]] || kill "$BACKEND_PID" 2>/dev/null || true
    [[ -z "$FRONTEND_PID" ]] || kill "$FRONTEND_PID" 2>/dev/null || true
    [[ -z "$WARMUP_PID" ]] || kill "$WARMUP_PID" 2>/dev/null || true
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "Запускаем Backend..."
"$PYTHON" -m uvicorn backend.main:app \
    --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Запускаем Frontend..."
"$PYTHON" -m http.server 5500 \
    --bind 127.0.0.1 --directory frontend &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://127.0.0.1:8000"
echo "API Docs: http://127.0.0.1:8000/docs"
echo "Frontend: http://127.0.0.1:5500"
echo ""
echo "Для остановки нажмите Ctrl+C."

warm_local_ollama() {
    [[ "${AI_DM_LOCAL_WARMUP:-1}" == "1" ]] || return 0
    case "${OPENAI_BASE_URL:-}" in
        http://127.0.0.1:11434/v1|http://127.0.0.1:11434/v1/|http://localhost:11434/v1|http://localhost:11434/v1/)
            ;;
        *) return 0 ;;
    esac
    if ! command -v curl >/dev/null 2>&1; then
        echo "Прогрев Ollama пропущен: curl не найден."
        return 0
    fi
    local ollama_base="${OPENAI_BASE_URL%/}"
    ollama_base="${ollama_base%/v1}"
    local keep_alive="${OLLAMA_KEEP_ALIVE:-15m}"
    local payload
    payload=$("$PYTHON" -c 'import json,sys; print(json.dumps({"model": sys.argv[1], "prompt": "", "stream": False, "keep_alive": sys.argv[2]}))' "${MODEL_NAME:-}" "$keep_alive")
    echo "Прогреваем локальную модель ${MODEL_NAME:-} в фоне..."
    if curl --fail --silent --show-error --max-time "${OLLAMA_WARMUP_TIMEOUT_SECONDS:-180}" \
        -H "Content-Type: application/json" -d "$payload" "$ollama_base/api/generate" >/dev/null; then
        echo "Локальная модель готова."
    else
        echo "Предупреждение: прогрев Ollama не удался. Первый ход может выполняться дольше."
    fi
}

warm_local_ollama &
WARMUP_PID=$!

while kill -0 "$BACKEND_PID" 2>/dev/null &&
      kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 1
done
