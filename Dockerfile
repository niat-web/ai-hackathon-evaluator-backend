FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application code
COPY app/ ./app/

# Note: Do not copy .env files to container
# Use Cloud Run Environment Variables or Secret Manager instead

# Cloud Run injects PORT (default 8080). Honor it instead of hardcoding.
ENV PORT=8080
EXPOSE 8080

# Shell form so ${PORT} expands at runtime (Cloud Run / compose / local).
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
