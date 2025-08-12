# Nova - Docker reference

Using docker compose is the recommended way to run Nova for real use.

## Minimal setup (recommended)

This setup include :
   - A PostgreSQL database
   - A Redis server
   - A web server (nginx)
   - The Nova web app

1. Download the minimal setup:

   You will need the `docker-compose.yml` file, the `nginx.conf` file and a `.env` file.

   ```
   mkdir nova
   cd nova
   wget https://raw.githubusercontent.com/amairesse/nova/main/docker/minimal/docker-compose.yml
   wget https://raw.githubusercontent.com/amairesse/nova/main/docker/nginx.conf
   wget https://raw.githubusercontent.com/amairesse/nova/main/.env.example
   mv .env.example .env
   ```

   Edit the `.env` file to match your environment (see [Environment Variables](#environment-variables)).

2. Start containers:

   ```
   docker compose up -d
   ```
   Warning : first start may take a while because of the chromium install, you can check progress with `docker compose logs web -f`.

3. Access the app at `http://localhost:80` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

4. (optional) View logs:

   ```
   docker compose logs -f
   ```

5. (optional) Stop/restart:

   ```
   docker compose down
   docker compose up -d
   ```

7. Updates:

   ```
   git pull
   docker compose up -d
   ```

## Build from source

This setup include :
   - A PostgreSQL database
   - A Redis server
   - A web server (nginx)
   - The Nova web app built from source

1. Download the build from source setup:

   ```
   git clone https://github.com/amairesse/nova.git
   cd nova
   cp .env.example docker/from-source/.env
   ```

   Edit the `.env` file to match your environment (see [Environment Variables](#environment-variables)).

2. Start containers:

   The following commands are meant to be run from the `nova` directory.

   ```
   docker compose -f docker/from-source/docker-compose.yml up -d
   ```
   Warning : first start may take a while because of the chromium install, you can check progress with `docker compose -f docker/from-source/docker-compose.yml logs web -f`.

3. Access the app at `http://localhost:80` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

4. (optional) View logs:

   ```
   docker compose -f docker/from-source/docker-compose.yml logs -f
   ```

5. (optional) Stop/restart:

   ```
   docker compose -f docker/from-source/docker-compose.yml down
   docker compose -f docker/from-source/docker-compose.yml up -d
   ```

7. Updates:

   ```
   git pull
   docker compose -f docker/from-source/docker-compose.yml up -d
   ```


## Environment Variables

Nova uses a `.env` file for configuration. Copy `.env.example` to `.env` and edit as needed.

Edit the `.env` file to match your environment :
   - Set `DB_ENGINE=postgresql`.
   - Set `DJANGO_SUPERUSER_*` vars for auto-admin creation.
   - Change `FIELD_ENCRYPTION_KEY` and `DJANGO_SECRET_KEY` for security.
   - Set `DJANGO_DEBUG=False` for production.
   - Set `HOST_PORT` to your desired port (e.g., `HOST_PORT=80`). Note: `ALLOWED_HOSTS` should be kept to localhost.
   - Set `CSRF_TRUSTED_ORIGINS` to your domain and internet port if the app is exposed on the internet
      - For example: `CSRF_TRUSTED_ORIGINS=https://my-domain.com`
      - The port should be the one exposed on internet (e.g. if you use a proxy like HAProxy for SSL).  
