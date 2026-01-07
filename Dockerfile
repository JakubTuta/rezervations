# Multi-stage build for smaller final image
FROM python:3.11-slim AS builder

# Set working directory
WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data/sessions /app/data/jobs /app/static && \
    chown -R appuser:appuser /app

# Copy application code
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser static ./static
COPY --chown=appuser:appuser main.py .

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
