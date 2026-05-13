# =============================================================================
# 4S1T Agent AI — Main Service Dockerfile
# Multi-stage build: builder installs deps, runtime image stays slim.
# Matches the user/group (1000:1000) declared in docker-compose.yml.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1 — dependency builder
# Needs gcc + libffi-dev to compile argon2-cffi, cryptography, cffi, hnswlib.
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# Install native build toolchain (only needed at build time)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        libssl-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies into a prefix we can transplant
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2 — lean runtime image
# No build tools, no root, curl kept only for the Docker healthcheck.
# -----------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Install runtime dependencies:
#   curl               — Docker healthcheck
#   pandoc             — export_document skill: DOCX/PDF/PPTX/HTML/ODT generation
#   default-jre-headless — render_diagram skill: required to run plantuml.jar
#   graphviz           — PlantUML: required for class/component/deployment diagrams
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        pandoc \
        default-jre-headless \
        graphviz \
    && rm -rf /var/lib/apt/lists/*

# Download PlantUML JAR (not packaged in Debian trixie)
# Pinned to a specific stable release for reproducibility.
RUN mkdir -p /opt/plantuml \
 && curl -L -o /opt/plantuml/plantuml.jar \
        https://github.com/plantuml/plantuml/releases/download/v1.2024.8/plantuml-1.2024.8.jar \
 && chmod 644 /opt/plantuml/plantuml.jar

# Create non-root user/group matching docker-compose user: "1000:1000"
RUN groupadd -g 1000 appuser \
 && useradd  -u 1000 -g 1000 -m -s /bin/false appuser

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Runtime Python settings
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# src/ is mounted at /app/src at runtime; set path so imports resolve correctly
ENV PYTHONPATH=/app/src

WORKDIR /app/src

# Pre-create writable directories; ownership transferred to appuser.
# These are also bind-mounted from the host in docker-compose (./data, ./logs).
RUN mkdir -p /app/data /app/logs \
 && chown -R 1000:1000 /app

USER 1000:1000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run uvicorn; main.py is in WORKDIR (/app/src) at runtime via volume mount.
CMD ["python", "-m", "uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info"]
