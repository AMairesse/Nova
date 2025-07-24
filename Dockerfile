# Dockerfile

# Official python image
FROM python:3.12-slim

# Define working directory
WORKDIR /app

# Install system deps (for psycopg2, pg_isready, and gettext for i18n)
RUN apt-get update && apt-get install -y gcc libpq-dev postgresql-client gettext && rm -rf /var/lib/apt/lists/*

# Copy files
COPY . /app

# Install dependencies
RUN pip install -r requirements.txt

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose port
EXPOSE 8000

# Start via entrypoint
CMD ["./entrypoint.sh"]
