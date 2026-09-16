FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY storage_gateway/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY storage_gateway/main.py /app/main.py
RUN useradd --create-home --uid 10001 storage && mkdir -p /data /replica /backup && chown -R storage:storage /data /replica /backup /app
USER storage
EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000", "--proxy-headers"]
