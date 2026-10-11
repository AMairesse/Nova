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
    def setUp(self):
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
        self.root = root

    def _compose_services(self, files, **overrides):
        command = ["docker", "compose", "--env-file", os.devnull]
        for name in files:
            command.extend(["-f", str(self.root / "docker" / name)])
        command.extend(["config", "--no-env-resolution", "--format", "json"])
        environment = dict(os.environ, HOST_PORT="18080", MINIO_SECRET_KEY="test-only")
        for name in ("SEARXNG_HOST_IP", "SEARXNG_HOST_PORT", "MEMORY_EMBEDDINGS_TIMEOUT_SECONDS"):
            environment.pop(name, None)
        environment.update(overrides)
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=environment)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        return json.loads(result.stdout)["services"]

    def test_postgres_restart_dependency_survives_extends_and_addons(self):
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
                services = self._compose_services(files)
                for name in ("web", "celery-worker", "celery-beat"):
                    dependency = services[name]["depends_on"]["db"]
                    self.assertEqual(dependency["condition"], "service_healthy")
                    self.assertIs(dependency["restart"], True)

    def test_searxng_host_access_is_optional_and_preserves_internal_access(self):
        variants = [(name, "docker-compose.add-searxng.yml") for name in
                    ("docker-compose.yml", "docker-compose.dev.yml", "docker-compose.from-source.yml")]
        variants.append(("docker-compose.yml", "docker-compose.add-searxng.yml", "docker-compose.add-exec-runner.yml"))
        customized = {"SEARXNG_HOST_IP": "192.0.2.10", "SEARXNG_HOST_PORT": "18083"}
        for files in variants:
            for host_access in (False, True):
                for environment in ({}, customized):
                    with self.subTest(files=files, host_access=host_access, environment=environment):
                        composition = files + (("docker-compose.add-searxng-host.yml",) if host_access else ())
                        services = self._compose_services(composition, **environment)
                        self.assertEqual(services["web"]["environment"]["SEARNGX_SERVER_URL"], "http://searxng:8080")
                        ports = services["searxng"].get("ports", [])
                        if host_access:
                            self.assertEqual(len(ports), 1)
                            self.assertEqual(ports[0]["host_ip"], environment.get("SEARXNG_HOST_IP", "127.0.0.1"))
                            self.assertEqual(str(ports[0]["published"]), environment.get("SEARXNG_HOST_PORT", "8083"))
                            self.assertEqual(ports[0]["target"], 8080)
                        else:
                            self.assertEqual(ports, [])
                        if "docker-compose.add-exec-runner.yml" in files:
                            self.assertIn("exec-runner", services)

    def test_embeddings_timeout_reaches_web_and_worker(self):
        for base in ("docker-compose.yml", "docker-compose.dev.yml", "docker-compose.from-source.yml"):
            for overrides in ({}, {"MEMORY_EMBEDDINGS_TIMEOUT_SECONDS": "180"}):
                with self.subTest(base=base, overrides=overrides):
                    services = self._compose_services((base, "docker-compose.add-llamacpp-embeddings.yml"), **overrides)
                    for name in ("web", "celery-worker"):
                        self.assertEqual(services[name]["environment"]["MEMORY_EMBEDDINGS_TIMEOUT_SECONDS"],
                                         overrides.get("MEMORY_EMBEDDINGS_TIMEOUT_SECONDS", "30"))
