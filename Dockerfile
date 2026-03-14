FROM python:3.12-slim

WORKDIR /app

# Install system deps for sentence-transformers and watchdog
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install the package in editable mode so `python3 -m tooldns.*` works
RUN pip install --no-cache-dir -e .

# ToolDNS data lives in /root/.tooldns — mount this from the host so
# config, skills, tools, and the database persist across container restarts.
VOLUME ["/root/.tooldns"]

EXPOSE 8787

# Healthcheck: poll /health every 30s, 3 failures = unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

CMD ["python3", "-m", "tooldns.cli", "serve"]
