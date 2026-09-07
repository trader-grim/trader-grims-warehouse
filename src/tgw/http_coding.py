"""Coding-provision HTTP surface — separated from the item operator API.

PP-ROLES-001 / the git→Luet separation: the coding workflow and the item/listing
workflow are distinct applications on the same state-machine substrate and must
not share a running server.  These routes were inline in ``http_server.py`` and
therefore live on tgw-prod, which is supposed to carry no coding surface.

They register onto the FastAPI app unconditionally (the item server loads its
config lazily), but every ``/api/coding/*`` path checks ``coding.routes_enabled``
per request and returns 404 when it is ``false``.  Production sets it ``false``
so tgw-prod serves the item workflow only.  The route bodies are unchanged.
"""
from __future__ import annotations

import os
import secrets
from typing import Any, Callable, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field


class CodingProvisionStart(BaseModel):
    todo_id: int = Field(gt=0)
    object_generation: str | None = None
    source_commit: str | None = None


class CodingWorkerClaim(BaseModel):
    host: str = Field(min_length=1)
    envelope_hash: str = Field(min_length=1)
    location: Dict[str, Any]
    snapshot: Dict[str, Any]


class CodingWorkerLease(BaseModel):
    lease_token: str = Field(min_length=1)


class CodingWorkerComplete(CodingWorkerLease):
    result: Dict[str, Any]


class CodingWorkerFail(CodingWorkerLease):
    error: str = Field(min_length=1, max_length=2000)
    result: Dict[str, Any] | None = None


def coding_routes_enabled(cfg: Dict[str, Any]) -> bool:
    """True unless the config explicitly turns the coding HTTP surface off."""
    coding = cfg.get("coding")
    if not isinstance(coding, dict):
        return True
    return bool(coding.get("routes_enabled", True))


def register_coding_routes(
    app: FastAPI,
    get_cfg: Callable[[], Dict[str, Any]],
    auth_dependency: Any,
) -> None:
    """Attach the ``/api/coding/*`` provision + worker routes to *app*.

    The routes are always registered (the item server loads its config lazily,
    so an import-time check would see an empty config); ``coding.routes_enabled``
    is enforced per request via ``_require_coding_enabled``.  When it is
    ``false`` every ``/api/coding/*`` path returns 404 — tgw-prod's item-only
    posture.

    *get_cfg* is re-read on every request so a config reload takes effect.
    *auth_dependency* is the item server's operator ``Depends(_require_auth)``.
    """

    def _require_coding_enabled() -> None:
        if not coding_routes_enabled(get_cfg()):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    ENABLED = Depends(_require_coding_enabled)
    AUTH = auth_dependency

    def _require_coding_worker(request: Request) -> str:
        """Authenticate a coding worker with its dedicated referenced secret."""
        coding = get_cfg().get("coding")
        if not isinstance(coding, dict):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="coding worker is not configured")
        reference = coding.get("worker_credential_env")
        supplied = request.headers.get("X-TGW-Worker-Authorization", "")
        identity = request.headers.get("X-TGW-Worker-Identity", "")
        expected = os.environ.get(reference) if isinstance(reference, str) and reference else None
        if not expected or not supplied.startswith("Bearer ") or not identity:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid coding worker credential")
        if not secrets.compare_digest(supplied.removeprefix("Bearer ").encode(), expected.encode()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid coding worker credential")
        if identity != coding.get("worker_identity"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid coding worker identity")
        return identity

    WORKER_AUTH = Depends(_require_coding_worker)

    @app.get("/api/coding/worker/requests/next", dependencies=[ENABLED])
    def coding_worker_next(worker_identity: str = WORKER_AUTH):
        from .coding_provision import next_request

        try:
            return next_request(get_cfg(), worker_identity) or {}
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/coding/requests", dependencies=[ENABLED, AUTH])
    def coding_provision_start(body: CodingProvisionStart):
        """Persist a request-safe coding job; the tgw-lib worker resolves and
        validates its local worktree envelope after claim."""
        from .coding_provision import create_request

        try:
            return create_request(
                get_cfg(),
                todo_id=body.todo_id,
                object_generation=body.object_generation,
                source_commit=body.source_commit,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/coding/requests/{request_id}", dependencies=[ENABLED, AUTH])
    def coding_provision_status(request_id: str):
        from .coding_provision import get_request

        try:
            return get_request(get_cfg(), request_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/coding/requests/{request_id}/stop", dependencies=[ENABLED, AUTH])
    def coding_provision_stop(request_id: str):
        from .coding_provision import stop_request

        try:
            return stop_request(get_cfg(), request_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/coding/access-status", dependencies=[ENABLED, AUTH])
    def coding_access_status(request_id: str | None = None):
        from .coding_provision import access_status

        try:
            return access_status(get_cfg(), request_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/coding/worker/requests/{request_id}", dependencies=[ENABLED])
    def coding_worker_request(request_id: str, worker_identity: str = WORKER_AUTH):
        from .coding_provision import get_request

        try:
            document = get_request(get_cfg(), request_id)
            if document.get("worker_identity") != worker_identity:
                raise HTTPException(status_code=403, detail="request is assigned to another worker")
            return document
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/coding/worker/requests/{request_id}/claim", dependencies=[ENABLED])
    def coding_worker_claim(request_id: str, body: CodingWorkerClaim, worker_identity: str = WORKER_AUTH):
        from .coding_provision import claim_request

        try:
            return claim_request(
                get_cfg(), request_id=request_id, local_host=body.host,
                worker_identity=worker_identity, envelope_hash=body.envelope_hash,
                location=body.location, snapshot=body.snapshot,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/coding/worker/requests/{request_id}/start", dependencies=[ENABLED])
    def coding_worker_start(request_id: str, body: CodingWorkerLease, worker_identity: str = WORKER_AUTH):
        from .coding_provision import start_request

        try:
            return start_request(get_cfg(), request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/coding/worker/requests/{request_id}/complete", dependencies=[ENABLED])
    def coding_worker_complete(request_id: str, body: CodingWorkerComplete, worker_identity: str = WORKER_AUTH):
        from .coding_provision import complete_request

        try:
            return complete_request(get_cfg(), request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token, result=body.result)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/coding/worker/requests/{request_id}/fail", dependencies=[ENABLED])
    def coding_worker_fail(request_id: str, body: CodingWorkerFail, worker_identity: str = WORKER_AUTH):
        from .coding_provision import fail_request

        try:
            return fail_request(get_cfg(), request_id=request_id, worker_identity=worker_identity, lease_token=body.lease_token, error=body.error, result=body.result)
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
