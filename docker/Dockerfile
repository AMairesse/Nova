# Dockerfile

# Official python image
FROM python:3.12-slim-bookworm

# Define working directory
WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system deps for Nova, Playwright, and the sandbox runner image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    coreutils \
    curl \
    docker.io \
    file \
    findutils \
    gcc \
    gettext \
    git \
    grep \
    gzip \
    libmagic1 \
    libpq-dev \
    nodejs \
    npm \
    postgresql-client \
    python3-venv \
    redis-tools \
    ripgrep \
    sed \
    tar \
    unzip \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copy files & install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY nova/ /app/nova/
COPY user_settings/ /app/user_settings/
COPY manage.py ./docker/entrypoint.sh /app/

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Pre-install Playwright
RUN playwright install chromium
RUN playwright install-deps

# Create runtime user and writable directories
RUN groupadd --system nova && useradd --system --gid nova --create-home --home-dir /home/nova nova \
    && mkdir -p /app/logs /app/static /app/media /ms-playwright \
    && chown -R nova:nova /app /ms-playwright

# Expose port
EXPOSE 8000

# Start via entrypoint
CMD ["./entrypoint.sh"]
