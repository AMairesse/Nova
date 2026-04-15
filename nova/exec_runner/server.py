from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import secrets

from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from nova.exec_runner.docker_backend import (
    DockerExecRunnerBackend,
    ExecRunnerError,
    ExecSessionSelector,
    load_exec_runner_config_from_env,
)
from nova.exec_runner.proxy import ExecRunnerProxyConfig, ExecRunnerProxyServer

logger = logging.getLogger(__name__)


def _extract_bearer_token(request: Request) -> str:
    auth_header = str(request.headers.get("authorization") or "").strip()
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return ""
    return token.strip()


def _is_authorized(app: Starlette, token: str) -> bool:
    expected = str(app.state.shared_token or "").strip()
    if not expected:
        return False
    return secrets.compare_digest(token, expected)


def _multipart_response(metadata: dict, diff_bundle_bytes: bytes) -> Response:
    boundary = f"nova-exec-runner-{secrets.token_hex(12)}"
    metadata_bytes = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    parts = [
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="metadata"\r\n'
            "Content-Type: application/json; charset=utf-8\r\n\r\n"
        ).encode("utf-8")
        + metadata_bytes
        + b"\r\n",
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="diff_bundle"; filename="diff.tar.gz"\r\n'
            "Content-Type: application/gzip\r\n\r\n"
        ).encode("utf-8")
        + diff_bundle_bytes
        + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    return Response(
        b"".join(parts),
        media_type=f"multipart/form-data; boundary={boundary}",
    )


async def _healthz(request: Request) -> JSONResponse:
    app = request.app
    token = _extract_bearer_token(request)
    if not _is_authorized(app, token):
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)
    try:
        payload = await app.state.backend.healthcheck()
    except ExecRunnerError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=503)
    return JSONResponse(payload)


async def _exec(request: Request) -> Response:
    app = request.app
    token = _extract_bearer_token(request)
    if not _is_authorized(app, token):
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)

    form = await request.form()
    metadata_raw = str(form.get("metadata") or "").strip()
    if not metadata_raw:
        return JSONResponse({"status": "error", "message": "Missing execution metadata."}, status_code=400)
    try:
        metadata = json.loads(metadata_raw)
    except ValueError:
        return JSONResponse({"status": "error", "message": "Invalid execution metadata."}, status_code=400)

    upload = form.get("sync_bundle")
    if not isinstance(upload, UploadFile):
        return JSONResponse({"status": "error", "message": "Missing sync bundle."}, status_code=400)

    selector_data = metadata.get("selector") or {}
    selector = ExecSessionSelector(
        user_id=selector_data.get("user_id") or "unknown",
        thread_id=selector_data.get("thread_id") or "unknown",
        agent_id=selector_data.get("agent_id") or "unknown",
    )
    sync_bundle_bytes = await upload.read()
    try:
        result = await app.state.backend.execute(
            selector=selector,
            command=str(metadata.get("command") or ""),
            cwd=str(metadata.get("cwd") or "/"),
            sync_bundle_bytes=sync_bundle_bytes,
            ensure_python=bool(metadata.get("ensure_python")),
        )
    except ExecRunnerError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)

    response_metadata = {
        "stdout": result.result.stdout,
        "stderr": result.result.stderr,
        "status": result.result.status,
        "cwd_after": result.result.cwd_after,
        "execution_plane": result.result.execution_plane,
        "removed_paths": list(result.removed_paths),
        "directory_paths": list(result.directory_paths),
    }
    return _multipart_response(response_metadata, result.diff_bundle_bytes)


async def _delete_session(request: Request) -> JSONResponse:
    app = request.app
    token = _extract_bearer_token(request)
    if not _is_authorized(app, token):
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)
    session_id = str(request.path_params.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"status": "error", "message": "Missing session id."}, status_code=400)
    parts = dict(
        item.split("-", 1)
        for item in session_id.split("--")
        if "-" in item
    )
    selector = ExecSessionSelector(
        user_id=parts.get("user", "unknown"),
        thread_id=parts.get("thread", "unknown"),
        agent_id=parts.get("agent", "unknown"),
    )
    try:
        await app.state.backend.delete_session(selector)
    except ExecRunnerError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
    return JSONResponse({"status": "success"})


async def _delete_thread_sessions(request: Request) -> JSONResponse:
    app = request.app
    token = _extract_bearer_token(request)
    if not _is_authorized(app, token):
        return JSONResponse({"status": "error", "message": "Forbidden"}, status_code=403)
    user_id = str(request.path_params.get("user_id") or "").strip()
    thread_id = str(request.path_params.get("thread_id") or "").strip()
    if not user_id or not thread_id:
        return JSONResponse({"status": "error", "message": "Missing user or thread id."}, status_code=400)
    try:
        removed = await app.state.backend.delete_sessions_for_thread(user_id=user_id, thread_id=thread_id)
    except ExecRunnerError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)
    return JSONResponse({"status": "success", "removed_sessions": removed})


async def _maintenance_loop(app: Starlette) -> None:
    interval_seconds = max(int(app.state.backend.config.gc_interval_seconds), 60)
    while True:
        try:
            await app.state.backend.run_maintenance_cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("exec-runner maintenance loop failed")
        await asyncio.sleep(interval_seconds)


def build_app() -> Starlette:
    config = load_exec_runner_config_from_env()
    backend = DockerExecRunnerBackend(config)
    proxy_port = max(int(os.getenv("EXEC_RUNNER_PROXY_PORT", "8091")), 1)
    proxy_server = ExecRunnerProxyServer(ExecRunnerProxyConfig(port=proxy_port))

    @asynccontextmanager
    async def _lifespan(app: Starlette):
        await app.state.backend.initialize()
        await app.state.proxy_server.start()
        maintenance_task = asyncio.create_task(_maintenance_loop(app))
        app.state.maintenance_task = maintenance_task
        try:
            yield
        finally:
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
            await app.state.proxy_server.close()

    app = Starlette(
        debug=False,
        routes=[
            Route("/healthz", _healthz, methods=["GET"]),
            Route("/v1/sessions/exec", _exec, methods=["POST"]),
            Route("/v1/sessions/{session_id}", _delete_session, methods=["DELETE"]),
            Route("/v1/users/{user_id}/threads/{thread_id}/sessions", _delete_thread_sessions, methods=["DELETE"]),
        ],
        lifespan=_lifespan,
    )
    app.state.backend = backend
    app.state.shared_token = config.shared_token
    app.state.proxy_server = proxy_server

    return app


app = build_app()


def main() -> None:
    import uvicorn

    host = str(os.getenv("EXEC_RUNNER_HOST", "0.0.0.0")).strip() or "0.0.0.0"
    port = max(int(os.getenv("EXEC_RUNNER_PORT", "8080")), 1)
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("EXEC_RUNNER_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
