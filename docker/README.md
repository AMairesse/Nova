# Nova - Docker reference

Using docker compose is the recommended way to run Nova, even for development.

## Minimal setup (recommended)

This setup include :
   - A PostgreSQL database
   - A Redis server
   - A web server (nginx)
   - A Minio S3 server
   - The Nova web app

1. Download the minimal setup:

   You will need the `docker-compose.yml` file, the `nginx.conf` file and a `.env` file.

   ```
   mkdir nova
   cd nova
   wget https://raw.githubusercontent.com/amairesse/nova/main/docker/docker-compose.minimal.yml
   wget https://raw.githubusercontent.com/amairesse/nova/main/docker/nginx.conf
   wget https://raw.githubusercontent.com/amairesse/nova/main/docker/.env.example
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
   - A Minio S3 server
   - The Nova web app built from source

1. Download the build from source setup:

   ```
   git clone https://github.com/amairesse/nova.git
   cd nova
   cp docker/.env.example docker/.env
   ```

   Edit the `docker/.env` file to match your environment (see [Environment Variables](#environment-variables)).

2. Start containers:

   The following commands are meant to be run from the `nova` directory.

   ```
   docker compose -f docker/docker-compose.from-source.yml up -d --build
   ```
   Warning : first start may take a while because of the chromium install, you can check progress with `docker compose -f docker/docker-compose.from-source.yml logs web -f`.

3. Access the app at `http://localhost:80` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

4. (optional) View logs:

   ```
   docker compose -f docker/docker-compose.from-source.yml logs -f
   ```

5. (optional) Stop/restart:

   ```
   docker compose -f docker/docker-compose.from-source.yml down
   docker compose -f docker/docker-compose.from-source.yml up -d --build
   ```

7. (optional) Updates:

   ```
   git pull
   docker compose -f docker/docker-compose.from-source.yml up -d --build
   ```

## Development setup

This setup include :
   - A PostgreSQL database
   - A Redis server
   - A web server (nginx)
   - A Minio S3 server
   - The Nova web app built from source with hot-reload and debug enabled

1. Download the development setup:

   ```
   git clone https://github.com/amairesse/nova.git
   cd nova
   cp docker/.env.example docker/.env
   ```

   Edit the `docker/.env` file to match your environment (see [Environment Variables](#environment-variables)).

2. Start containers:

   The following commands are meant to be run from the `nova` directory.

   ```
   docker compose -f docker/docker-compose.dev.yml up -d --build
   ```
   Warning : first start may take a while because of the chromium install, you can check progress with `docker compose -f docker/docker-compose.dev.yml logs web -f`.

3. Access the app at `http://localhost:80` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

4. (optional) View logs:

   ```
   docker compose -f docker/docker-compose.dev.yml logs -f
   ```

5. (optional) Stop/restart:

   ```
   docker compose -f docker/docker-compose.dev.yml down
   docker compose -f docker/docker-compose.dev.yml up -d --build
   ```

7. (optional) Updates:

   ```
   git pull
   docker compose -f docker/docker-compose.dev.yml up -d --build
   ```

8. (optional) Debug:

   ```
   docker compose -f docker/docker-compose.dev.yml up -d --build
   ```

   Add a debug config in VSCode (or your IDE of choice) and run the debug session.
   ````
   {
   "version": "0.2.0",
   "configurations": [
      {
         "name": "Python: Remote Attach",
         "type": "python",
         "request": "attach",
         "connect": { "host": "localhost", "port": 5678 },
         "pathMappings": [{ "localRoot": "${workspaceFolder}", "remoteRoot": "/app" }]
      }
   ]
   }
   ```

## Environment Variables

Nova uses a `.env` file for configuration. Copy `.env.example` to `.env` and edit as needed.

Edit the `.env` file to match your environment :
   - Set `DB_USER` and `DB_PASSWORD` vars for database user.
   - Set `DJANGO_SUPERUSER_*` vars for auto-admin creation.
   - Set `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` vars for minio admin access.
   - Change `FIELD_ENCRYPTION_KEY` and `DJANGO_SECRET_KEY` for security.
   - Set `HOST_PORT` to your desired port (e.g., `HOST_PORT=80`).
      - Note: `ALLOWED_HOSTS` should be kept to localhost.
   - Set `CSRF_TRUSTED_ORIGINS` to your domain and internet port if the app is exposed on the internet
      - For example: `CSRF_TRUSTED_ORIGINS=https://my-domain.com`
      - The port should be the one exposed on internet (e.g. if you use a proxy like HAProxy for SSL).  
