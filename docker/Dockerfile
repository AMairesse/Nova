# Dockerfile

# Official python image
FROM python:3.12-slim-bookworm

# Define working directory
WORKDIR /app

# Install system deps (for psycopg2, pg_isready, gettext for i18n, and redis-cli for health checks)
RUN apt-get update && apt-get install -y gcc libpq-dev postgresql-client gettext redis-tools && rm -rf /var/lib/apt/lists/*

# Copy files & install dependencies
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY nova/ /app/nova/
COPY manage.py ./docker/entrypoint.sh /app/

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Pre-install Playwright
RUN playwright install chromium
RUN playwright install-deps

# Expose port
EXPOSE 8000

# Start via entrypoint
CMD ["./entrypoint.sh"]
