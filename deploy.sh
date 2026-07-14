#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  TestGen v2.2 — разворачивание веб-интерфейса в Docker
#
#  Использование:
#    ./deploy.sh up        — собрать образ и запустить контейнер (по умолчанию)
#    ./deploy.sh down      — остановить и удалить контейнер
#    ./deploy.sh rebuild   — пересобрать образ и перезапустить
#    ./deploy.sh logs      — показать логи контейнера
#    ./deploy.sh status    — состояние контейнера и healthcheck
#
#  Переменные окружения:
#    TESTGEN_PORT — порт на хосте (по умолчанию 8000)
# ══════════════════════════════════════════════════════════════════════

set -euo pipefail

IMAGE="testgen:2.2"
CONTAINER="testgen"
PORT="${TESTGEN_PORT:-8000}"
CMD="${1:-up}"

cd "$(dirname "$0")"

# ── Проверки окружения ──────────────────────────────────────────────
require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "❌ Docker не найден. Установите: https://docs.docker.com/get-docker/" >&2
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker-демон не запущен (или нет прав). Запустите Docker и повторите." >&2
    exit 1
  fi
}

require_files() {
  local missing=0
  for f in Dockerfile test_gen.py web_app.py index.html; do
    if [[ ! -f "$f" ]]; then
      echo "❌ Не найден файл: $f (запускайте скрипт из папки проекта)" >&2
      missing=1
    fi
  done
  [[ $missing -eq 0 ]] || exit 1
}

port_busy() {
  # Порт занят другим процессом? (проверяем только если контейнер не наш)
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    return 1
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${PORT}\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

# ── Действия ────────────────────────────────────────────────────────
do_build() {
  echo "🔨 Сборка образа ${IMAGE}..."
  docker build -t "$IMAGE" .
}

do_stop() {
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "🛑 Останавливаем контейнер ${CONTAINER}..."
    docker rm -f "$CONTAINER" >/dev/null
  fi
}

do_run() {
  if port_busy; then
    echo "❌ Порт ${PORT} уже занят другим процессом." >&2
    echo "   Задайте другой: TESTGEN_PORT=9000 ./deploy.sh up" >&2
    exit 1
  fi
  echo "🚀 Запуск контейнера ${CONTAINER} на порту ${PORT}..."
  docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    --read-only \
    --security-opt no-new-privileges:true \
    -p "127.0.0.1:${PORT}:8000" \
    "$IMAGE" >/dev/null

  # Ждём готовности (до 10 секунд)
  for _ in $(seq 1 20); do
    if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
      if command -v curl >/dev/null 2>&1 \
         && curl -sf -o /dev/null "http://127.0.0.1:${PORT}/"; then
        break
      fi
    fi
    sleep 0.5
  done

  echo
  echo "✅ TestGen развёрнут: http://127.0.0.1:${PORT}"
  echo "   Логи:      ./deploy.sh logs"
  echo "   Остановка: ./deploy.sh down"
}

do_status() {
  if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "ℹ️  Контейнер ${CONTAINER} не создан. Запустите: ./deploy.sh up"
    return
  fi
  docker ps -a --filter "name=^${CONTAINER}\$" \
    --format 'Контейнер: {{.Names}}\nСтатус:    {{.Status}}\nПорты:     {{.Ports}}'
  local health
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}—{{end}}' "$CONTAINER")"
  echo "Health:    ${health}"
}

# ── Маршрутизация команд ────────────────────────────────────────────
case "$CMD" in
  up)      require_docker; require_files; do_build; do_stop; do_run ;;
  rebuild) require_docker; require_files; do_stop
           echo "🔨 Пересборка без кэша..."; docker build --no-cache -t "$IMAGE" .
           do_run ;;
  down)    require_docker; do_stop; echo "✅ Контейнер остановлен и удалён" ;;
  logs)    require_docker; docker logs -f "$CONTAINER" ;;
  status)  require_docker; do_status ;;
  *)  echo "Использование: $0 {up|down|rebuild|logs|status}" >&2; exit 1 ;;
esac
