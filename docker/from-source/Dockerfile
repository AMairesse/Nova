# Dockerfile

# Official python image
FROM python:alpine

# Define working directory
WORKDIR /app

# Install system deps (for psycopg2, pg_isready, gettext for i18n, and redis-cli for health checks)
RUN apt-get update && apt-get install -y gcc libpq-dev postgresql-client gettext redis-tools && rm -rf /var/lib/apt/lists/*

# Copy files & install dependencies
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY nova/ /app/nova/
COPY manage.py ./docker/from-source/entrypoint.sh /app/

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose port
EXPOSE 8000

# Start via entrypoint
CMD ["./entrypoint.sh"]
