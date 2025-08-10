#!/bin/bash

set -e  # Exit on error

if [ -f /app/.env ]; then
    echo "Loading .env file..."
    set -a  # Auto-export subsequent assignments
    source /app/.env  # Simpler sourcing; assumes no invalid keys
    set +a
fi

# Install playwright
echo "Installing Playwright..."
playwright install chromium
playwright install-deps

# Wait for PostgreSQL using connection string (no PGPASSWORD export)
if [ "$DB_ENGINE" = "postgresql" ]; then
    echo "Waiting for PostgreSQL (timeout 30s)..."
    timeout=30
    while ! psql "host=$DB_HOST port=$DB_PORT user=$DB_USER password=$DB_PASSWORD dbname=$DB_NAME" -c '\q' 2>/dev/null; do
        sleep 1
        ((timeout--))
        if [ $timeout -le 0 ]; then
            echo "Error: PostgreSQL not ready after 30s."
            exit 1
        fi
    done
    echo "PostgreSQL is ready"
fi

# Wait for Redis
echo "Waiting for Redis (timeout 30s)..."
timeout=30
while ! redis-cli -h redis -p 6379 ping >/dev/null 2>&1; do
    sleep 1
    ((timeout--))
    if [ $timeout -le 0 ]; then
        echo "Error: Redis not ready after 30s."
        exit 1
    fi
done
echo "Redis is ready"

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput --clear

# Apply database migrations
echo "Applying database migrations..."
python manage.py migrate --noinput

# i18n: Generate JS catalog
echo "Generating i18n catalogs..."
python manage.py compilemessages

# Create superuser if env vars are set (idempotent: skip if exists)
if [ ! -z "$DJANGO_SUPERUSER_USERNAME" ] && [ ! -z "$DJANGO_SUPERUSER_PASSWORD" ]; then
    echo "Checking/creating superuser..."
    python manage.py createsuperuser --noinput \
        --username "$DJANGO_SUPERUSER_USERNAME" \
        --email "${DJANGO_SUPERUSER_EMAIL:-admin@example.com}" || true  # Ignore if exists
fi

# Start Daphne
echo "Starting Daphne..."
exec daphne nova.asgi:application \
    -b 0.0.0.0 \
    -p 8000
