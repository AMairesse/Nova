"""Validate dependency inheritance using Compose, without reading env files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import TestCase, skipUnless


@skipUnless(shutil.which("docker"), "Docker CLI is required for Compose validation")
class ComposeDependencyTests(TestCase):
    def test_postgres_restart_dependency_survives_extends_and_addons(self):
        source = Path(__file__).resolve().parents[2] / "docker"
        workspace = tempfile.TemporaryDirectory(prefix="nova-compose-test-")
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        (root / "docker").mkdir()
        for path in source.glob("docker-compose*.yml"):
            shutil.copyfile(path, root / "docker" / path.name)
        # Compose 2.x checks that service env_file paths exist even with
        # --no-env-resolution. Use an empty fixture, never the user's .env.
        (root / "docker" / ".env").touch()
        variants = [(name,) for name in ("docker-compose.yml", "docker-compose.dev.yml", "docker-compose.from-source.yml")]
        addons = ("searxng", "exec-runner", "llamacpp", "llamacpp-embeddings", "ollama", "pgadmin")
        variants += [("docker-compose.yml", f"docker-compose.add-{addon}.yml") for addon in addons]
        variants += [
            ("docker-compose.dev.yml", "docker-compose.add-exec-runner.yml", "docker-compose.add-exec-runner.dev.yml"),
            ("docker-compose.from-source.yml", "docker-compose.add-exec-runner.yml", "docker-compose.add-exec-runner.from-source.yml"),
            ("docker-compose.yml", "docker-compose.add-llamacpp-embeddings.yml", "docker-compose.add-llamacpp-embeddings-host.yml"),
        ]
        for files in variants:
            with self.subTest(files=files):
                command = ["docker", "compose", "--env-file", os.devnull]
                for name in files:
                    command.extend(["-f", str(root / "docker" / name)])
                command.extend(["config", "--no-env-resolution", "--format", "json"])
                result = subprocess.run(command, capture_output=True, text=True, timeout=30,
                                        env=dict(os.environ, HOST_PORT="18080", MINIO_SECRET_KEY="test-only"))
                self.assertEqual(result.returncode, 0, msg=result.stderr)
                services = json.loads(result.stdout)["services"]
                for name in ("web", "celery-worker", "celery-beat"):
                    dependency = services[name]["depends_on"]["db"]
                    self.assertEqual(dependency["condition"], "service_healthy")
                    self.assertIs(dependency["restart"], True)
