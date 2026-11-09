#!/bin/sh
# Entrypoint of the backend images. Owner: P03. Runs as uid 10001 (non-root).
# Roles: api | worker | scheduler | outbox-relay | mcp ; anything else is exec'd as given (migrate/seed jobs).
# Containers never auto-migrate on start (D-P01). Listens on the container network only; the host never
# publishes these ports (only the proxy is published, on 127.0.0.1).
set -eu

has() { python -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('$1') else 1)" 2>/dev/null; }
port="${ERP_HTTP_PORT:-8000}"
workers="${ERP_WEB_CONCURRENCY:-2}"

case "${1:-api}" in
  api)
    if has gunicorn && has uvicorn_worker; then
      exec gunicorn erp.asgi:application -k uvicorn_worker.UvicornWorker --bind ":${port}" --workers "$workers" \
        --access-logfile - --forwarded-allow-ips '*' --timeout 60
    elif has gunicorn && has uvicorn; then
      exec gunicorn erp.asgi:application -k uvicorn.workers.UvicornWorker --bind ":${port}" --workers "$workers" \
        --access-logfile - --forwarded-allow-ips '*' --timeout 60
    elif has gunicorn; then
      exec gunicorn erp.wsgi:application --bind ":${port}" --workers "$workers" --access-logfile - --timeout 60
    elif has uvicorn; then
      exec uvicorn erp.asgi:application --host "$(hostname -i | cut -d' ' -f1)" --port "$port" --proxy-headers
    fi
    echo "nexus-entrypoint: neither gunicorn nor uvicorn is in uv.lock; using the Django development server (DEV only, see CCR P03-1)" >&2
    exec python manage.py runserver "$(hostname -i | cut -d' ' -f1):${port}" --noreload
    ;;
  worker)
    exec celery -A "${ERP_CELERY_APP:-erp}" worker --loglevel INFO --concurrency "${ERP_WORKER_CONCURRENCY:-2}" -n "celery@$(hostname)"
    ;;
  scheduler)
    exec python manage.py run_scheduler
    ;;
  outbox-relay)
    exec python manage.py outbox_relay
    ;;
  mcp)
    exec python -m mcp_server
    ;;
  *)
    exec "$@"
    ;;
esac
