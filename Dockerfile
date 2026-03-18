# ToolsDNS — Dockerfile
# Runs the ToolsDNS REST API (port 8787) and MCP server (port 8788)
#
# Build:  docker build -t tooldns .
# Run:    docker compose up -d   (see docker-compose.yml)

FROM python:3.12-slim

# System deps for sentence-transformers (numpy / tokenizers)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer-cached)
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir fastmcp mcp slowapi watchdog pyyaml pydantic-settings

# Copy application source
COPY . .

# Install the package in editable mode so `toolsdns` CLI works
RUN pip install --no-cache-dir -e .

# Pre-download the embedding model so first startup is instant
# (all-MiniLM-L6-v2 is ~23 MB — downloads to HuggingFace cache)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ToolsDNS home dir — bind-mount this for persistent config/db/skills
ENV TOOLDNS_HOME=/data
RUN mkdir -p /data

EXPOSE 8787 8788

# Health check — waits for REST API to be ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8787/health || exit 1

# Start the REST API (MCP server runs as a separate container — see docker-compose.yml)
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787"]
