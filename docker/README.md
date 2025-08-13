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

2. Build containers:

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
   docker compose -f docker/docker-compose.from-source.yml up -d
   ```

7. (optional) Updates:

   ```
   git pull
   docker compose -f docker/docker-compose.from-source.yml up -d
   ```

## Development setup

### Start the development setup

1. Download the development setup:

   ```
   git clone https://github.com/amairesse/nova.git
   cd nova
   cp docker/.env.example docker/.env
   ```

   Edit the `docker/.env` file to match your environment (see [Environment Variables](#environment-variables)).

2. First build of the containers:

   The following commands are meant to be run from the `nova` directory.

   ```
   docker compose -f docker/docker-compose.dev.yml up -d --build
   ```
   Warning : first start may take a while because of the chromium install, you can check progress with the logs (see below).

3. Access the app at `http://localhost:8080` (or your configured port). Log in and configure LLM providers/agents/tools via the UI.

   Note : the app can also be accessed at `http://localhost:8000` without nginx.

### Manage containers

#### View logs

   ```
   docker compose -f docker/docker-compose.dev.yml logs -f
   ```

#### Stop/restart the containers

   ```
   docker compose -f docker/docker-compose.dev.yml down
   docker compose -f docker/docker-compose.dev.yml up -d
   ```

#### Update the application

   ```
   git pull
   docker compose -f docker/docker-compose.dev.yml up -d
   ```

### Launch a debug session

   Add a debug config in VSCode (or your IDE of choice) and run the debug session.
   ```
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

### Add Langfuse

You can add Langfuse to the setup so that you can see the agents messages in detail.

1. Add Langfuse to the setup:

   Remplace the lauch command by:
   ```
   docker compose -f docker/docker-compose.dev.yml -f docker/docker-compose.add-langfuse.yml up -d
   ```

2. Access Langfuse at `http://localhost:3000`

   - Create a user and log in
   - Create an org and a project
   - Create API keys for the project

3. Add Langfuse to the app:

   Access Nova and configure Langfuse via the UI.

4. Interact with and Agent and see the messages in Langfuse (refresh the page to see the messages in "Traces").


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
