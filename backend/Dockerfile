FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN groupadd --system --gid 10003 admiezo \
    && useradd --system --uid 10003 --gid 10003 --create-home admiezo
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend /app
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R admiezo:admiezo /app

USER 10003:10003
EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
