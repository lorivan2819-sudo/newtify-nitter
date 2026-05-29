from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.parse
import urllib.request


PROJECT_ID = "prj_m29Yt5NzkeV2UlrCkuErCzRqexb4"
PROJECT_NAME = "newtify-nitter"
TEAM_ID = "team_mKTDeqocEA6OFmFVXjqOT7aO"
VERCEL_API = "https://api.vercel.com"

ALLOWED_KEYS = {
    "X_SOURCE_USERS": "plain",
    "TARGET_CHANNEL": "plain",
    "PUBLISH_MAX_AGE_MINUTES": "plain",
    "X_MAX_AGE_MINUTES": "plain",
    "TRANSLATE_ENABLED": "plain",
    "TRANSLATE_PRIMARY_LANG": "plain",
    "TRANSLATE_TARGET_LANG": "plain",
    "RSS_BASE_URL": "plain",
    "X_SESSIONS_B64": "sensitive",
    "TELEGRAM_SESSION_STRING": "sensitive",
}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("content-type", "application/json; charset=utf-8")
    handler.send_header("cache-control", "no-store")
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def api_error(exc: Exception) -> str:
    if hasattr(exc, "code") and hasattr(exc, "read"):
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            message = payload.get("error", {}).get("message") or payload.get("message")
            if message:
                return f"Vercel API {exc.code}: {message}"
        except Exception:
            pass
        return f"Vercel API {exc.code}"
    return str(exc)


def vercel_request(path: str, token: str, method: str = "GET", body: dict | None = None, query: dict | None = None) -> dict:
    query = {"teamId": TEAM_ID, **(query or {})}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    url = f"{VERCEL_API}{path}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except Exception as exc:
        raise RuntimeError(api_error(exc)) from exc


def upsert_env(key: str, value: str, token: str) -> dict:
    kind = ALLOWED_KEYS[key]
    return vercel_request(
        f"/v10/projects/{PROJECT_ID}/env",
        token,
        method="POST",
        query={"upsert": "true"},
        body={
            "key": key,
            "value": value,
            "type": kind,
            "target": ["production"],
            "comment": "Updated from Newtify Control",
        },
    )


def latest_production_deployment(token: str) -> dict:
    data = vercel_request(
        "/v6/deployments",
        token,
        query={
            "projectId": PROJECT_ID,
            "target": "production",
            "state": "READY",
            "limit": "1",
        },
    )
    deployments = data.get("deployments") or []
    if not deployments:
        data = vercel_request(
            "/v6/deployments",
            token,
            query={"projectId": PROJECT_ID, "target": "production", "limit": "1"},
        )
        deployments = data.get("deployments") or []
    if not deployments:
        raise RuntimeError("No production deployment found to redeploy")
    return deployments[0]


def start_redeploy(token: str) -> dict:
    previous = latest_production_deployment(token)
    deployment_id = previous.get("uid") or previous.get("id")
    if not deployment_id:
        raise RuntimeError("Latest production deployment has no id")

    return vercel_request(
        "/v13/deployments",
        token,
        method="POST",
        query={"forceNew": "1"},
        body={
            "name": PROJECT_NAME,
            "project": PROJECT_ID,
            "deploymentId": deployment_id,
            "target": "production",
        },
    )


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            json_response(self, 400, {"ok": False, "error": "Invalid JSON"})
            return

        if payload.get("secret") != env("ADMIN_SECRET"):
            json_response(self, 403, {"ok": False, "error": "Wrong secret code"})
            return

        token = env("VERCEL_TOKEN")
        if not token:
            json_response(
                self,
                500,
                {
                    "ok": False,
                    "error": "VERCEL_TOKEN is missing. Add it once in Vercel env, then this page can edit env directly.",
                },
            )
            return

        updates = payload.get("updates") or {}
        if not isinstance(updates, dict):
            json_response(self, 400, {"ok": False, "error": "updates must be an object"})
            return

        changed = []
        errors = []
        for key, value in updates.items():
            if key not in ALLOWED_KEYS:
                errors.append({"key": key, "error": "Not editable from this page"})
                continue
            try:
                upsert_env(key, str(value), token)
                changed.append(key)
            except Exception as exc:
                errors.append({"key": key, "error": str(exc)})

        redeploy = None
        redeploy_error = ""
        if changed and not errors:
            try:
                deployment = start_redeploy(token)
                redeploy = {
                    "id": deployment.get("id") or deployment.get("uid"),
                    "url": deployment.get("url"),
                    "inspectorUrl": deployment.get("inspectorUrl"),
                    "readyState": deployment.get("readyState") or deployment.get("status"),
                }
            except Exception as exc:
                redeploy_error = str(exc)

        ok = bool(changed) and not errors and not redeploy_error
        json_response(
            self,
            200 if ok else 207,
            {
                "ok": ok,
                "changed": changed,
                "errors": errors,
                "redeployStarted": bool(redeploy),
                "redeploy": redeploy,
                "redeployError": redeploy_error,
                "redeployRequired": bool(changed) and bool(redeploy_error),
                "note": "Env saved and production redeploy started." if redeploy else "Env saved, but redeploy did not start.",
            },
        )
