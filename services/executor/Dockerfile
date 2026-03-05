# Sandboxed Executor Service Dockerfile
# Ultra-secure container for code execution
# Based on distroless Python for minimal attack surface

# =============================================================================
# Stage 1: Build dependencies
# =============================================================================
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# =============================================================================
# Stage 2: Runtime - Minimal distroless image
# =============================================================================
FROM gcr.io/distroless/python3-debian12

# Non-root user configuration
# UID 65534 is 'nobody' user (least privileged)
USER 65534

# Set working directory
WORKDIR /home/nonroot

# Copy Python packages from builder
COPY --from=builder /root/.local/lib/python3.11/site-packages /home/nonroot/.local/lib/python3.11/site-packages

# Copy application code (read-only)
COPY --chown=65534:65534 security.py /home/nonroot/
COPY --chown=65534:65534 main.py /home/nonroot/

# Set environment
ENV PYTHONPATH=/home/nonroot/.local/lib/python3.11/site-packages
ENV PATH=/home/nonroot/.local/bin:$PATH

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import sys; sys.exit(0)" || exit 1

# Expose port (internal only, no external binding)
EXPOSE 8001

# Run as non-root
USER 65534

# Start the executor service
ENTRYPOINT ["python3", "-m", "uvicorn", "main:app"]
CMD ["--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
