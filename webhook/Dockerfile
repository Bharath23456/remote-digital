FROM docker:cli

# Install Python3, pip, git, and curl for healthchecks
RUN apk add --no-cache python3 py3-pip git bash curl \
    && python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir flask

ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV REPO_DIR=/app
ENV COMPOSE_PROJECT_NAME=remote-digital

WORKDIR /app

EXPOSE 5000

CMD ["python3", "webhook.py"]
