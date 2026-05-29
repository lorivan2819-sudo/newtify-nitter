from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.parse
import urllib.request


PROJECT_ID = "prj_m29Yt5NzkeV2UlrCkuErCzRqexb4"
TEAM_ID = "team_mKTDeqocEA6OFmFVXjqOT7aO"
VERCEL_API = "https://api.vercel.com"

ALLOWED_KEYS = {
    "X_SOURCE_USERS": "plain",
    "TARGET_CHANNEL": "plain",
    "PUBLISH_MAX_AGE_MINUTES": "plain",
    "X_MAX_AGE_MINUTES": "plain",
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


def upsert_env(key: str, value: str, token: str) -> dict:
    kind = ALLOWED_KEYS[key]
    body = json.dumps(
        {
            "key": key,
            "value": value,
            "type": kind,
            "target": ["production"],
            "comment": "Updated from Newtify Control",
        }
    ).encode("utf-8")
    query = urllib.parse.urlencode({"teamId": TEAM_ID, "upsert": "true"})
    request = urllib.request.Request(
        f"{VERCEL_API}/v10/projects/{PROJECT_ID}/env?{query}",
        data=body,
        method="POST",
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


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

        json_response(
            self,
            200 if changed and not errors else 207,
            {
                "ok": bool(changed) and not errors,
                "changed": changed,
                "errors": errors,
                "redeployRequired": True,
                "note": "Vercel env updates apply after the next production deployment.",
            },
        )
