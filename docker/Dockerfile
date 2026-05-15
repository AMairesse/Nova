# Dockerfile

# Official python image
FROM python:3.12-slim-bookworm

# Define working directory
WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install system deps for Nova, Playwright.
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    coreutils \
    curl \
    file \
    findutils \
    gcc \
    gettext \
    git \
    gnupg \
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

#
# Docker official CLI repository
#
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian bookworm stable" \
      > /etc/apt/sources.list.d/docker.list

#
# Install modern Docker CLI only
#
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

#
# Python dependencies
#
COPY requirements.txt /app/

RUN pip install --no-cache-dir -r requirements.txt

#
# Application files
#
COPY nova/ /app/nova/
COPY user_settings/ /app/user_settings/
COPY manage.py ./docker/entrypoint.sh /app/

RUN chmod +x /app/entrypoint.sh

#
# Playwright
#
RUN playwright install chromium
RUN playwright install-deps

#
# Runtime user
#
RUN groupadd --system nova \
    && useradd --system --gid nova --create-home --home-dir /home/nova nova \
    && mkdir -p \
        /app/logs \
        /app/static \
        /app/media \
        /ms-playwright \
    && chown -R nova:nova /app /ms-playwright

EXPOSE 8000

# Start via entrypoint
CMD ["./entrypoint.sh"]