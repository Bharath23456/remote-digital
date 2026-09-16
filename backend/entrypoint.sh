#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput
if [ "${LOAD_DEMO_DATA:-false}" = "true" ]; then
  python manage.py bootstrap_demo
fi
if [ "$#" -gt 0 ]; then
  exec "$@"
fi
exec gunicorn evaluation_core.wsgi:application --bind 0.0.0.0:8000 --workers "${GUNICORN_WORKERS:-3}" --timeout 60 --access-logfile -
