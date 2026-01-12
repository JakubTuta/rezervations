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

# Install Playwright browsers (without --with-deps, we'll add deps manually in final stage)
RUN playwright install chromium

# Final stage
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Install runtime dependencies for Playwright/Chromium and tzdata
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tzdata \
    ca-certificates \
    fonts-liberation \
    # Playwright Chromium runtime dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    libcairo2 \
    libpango-1.0-0 \
    libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data/sessions /app/data/jobs /app/static && \
    chown -R appuser:appuser /app

# Copy Playwright browsers from builder and set correct ownership
COPY --from=builder --chown=appuser:appuser /root/.cache/ms-playwright /home/appuser/.cache/ms-playwright

# Set environment variables
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Warsaw \
    PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright

# Copy application code
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser static ./static
COPY --chown=appuser:appuser main.py .

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
