FROM python:3.11-slim

# No interactive prompts, no .pyc files written to the read-only layer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Install data-analysis packages
RUN pip install --no-cache-dir \
        pandas==2.2.3 \
        numpy==1.26.4 \
        matplotlib==3.9.4 \
        plotly==5.24.1 \
        scipy==1.13.1 \
        openpyxl==3.1.5

# Drop to non-root user for extra safety
RUN useradd --no-create-home --shell /bin/false executor
USER executor

# The handler mounts code at /code/user_code.py and output at /output
# These directories just need to exist so Docker can bind-mount into them
WORKDIR /code
