FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY identity_service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY identity_service /app
COPY identity_service/entrypoint.sh /entrypoint.sh
RUN useradd --create-home --uid 10002 identity \
    && mkdir -p /data \
    && chown -R identity:identity /app /data \
    && chmod +x /entrypoint.sh
EXPOSE 8100
ENTRYPOINT ["/entrypoint.sh"]
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn identity_boundary.wsgi:application --bind 0.0.0.0:8100 --workers 2 --timeout 60"]
