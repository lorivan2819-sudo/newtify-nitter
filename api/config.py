from __future__ import annotations

from http.server import BaseHTTPRequestHandler
import json
import os


PROJECT_ENV_URL = (
    "https://vercel.com/nareks-projects-b2c59581/newtify-nitter/settings/"
    "environment-variables"
)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def is_set(name: str) -> bool:
    return bool(env(name))


def public_config() -> dict[str, object]:
    x_users = env("X_SOURCE_USERS", "swelmchannel")
    return {
        "project": {
            "name": "newtify-nitter",
            "envUrl": PROJECT_ENV_URL,
            "publishUrl": "/api/publish",
            "healthUrl": "/health",
        },
        "posting": {
            "sourceUsers": x_users,
            "targetChannel": env("TARGET_CHANNEL", "@WWAInews"),
            "rssBaseUrl": env("RSS_BASE_URL", "https://newtify-nitter.vercel.app"),
            "rssUrls": [
                f"/{item.strip().lstrip('@')}/rss"
                for item in x_users.split(",")
                if item.strip()
            ],
        },
        "filters": {
            "xMaxAgeMinutes": env("X_MAX_AGE_MINUTES", "2"),
            "publishMaxAgeMinutes": env("PUBLISH_MAX_AGE_MINUTES", "45"),
        },
        "access": {
            "telegramApiId": is_set("TELEGRAM_API_ID"),
            "telegramApiHash": is_set("TELEGRAM_API_HASH"),
            "telegramSession": is_set("TELEGRAM_SESSION_STRING"),
            "xSession": is_set("X_SESSIONS_B64"),
            "adminSecret": is_set("ADMIN_SECRET"),
            "vercelToken": is_set("VERCEL_TOKEN"),
        },
        "automation": {
            "manualPublish": True,
            "browserAutoRun": True,
            "vercelDailyCron": True,
            "vercelDailyCronSchedule": "0 8 * * *",
            "vercelCronFast": False,
            "vercelCronReason": (
                "This Vercel Hobby project rejected every-minute cron. "
                "The admin page can auto-run while it is open; fast background "
                "cron needs Vercel Pro."
            ),
        },
        "editableEnv": [
            {
                "name": "X_SOURCE_USERS",
                "section": "What to read",
                "safeValue": x_users,
                "note": "Comma-separated X usernames.",
            },
            {
                "name": "TARGET_CHANNEL",
                "section": "Where to post",
                "safeValue": env("TARGET_CHANNEL", "@WWAInews"),
                "note": "Telegram channel username or id.",
            },
            {
                "name": "PUBLISH_MAX_AGE_MINUTES",
                "section": "Posting filter",
                "safeValue": env("PUBLISH_MAX_AGE_MINUTES", "45"),
                "note": "How old a post can be and still be uploaded.",
            },
            {
                "name": "X_MAX_AGE_MINUTES",
                "section": "RSS filter",
                "safeValue": env("X_MAX_AGE_MINUTES", "2"),
                "note": "Old local bot freshness setting; publisher uses the value above.",
            },
            {
                "name": "X_SESSIONS_B64",
                "section": "X access",
                "safeValue": "set" if is_set("X_SESSIONS_B64") else "missing",
                "note": "Signed-in X cookie session, encrypted in Vercel.",
            },
            {
                "name": "RSS_BASE_URL",
                "section": "Advanced",
                "safeValue": env("RSS_BASE_URL", "https://newtify-nitter.vercel.app"),
                "note": "RSS service base URL.",
            },
            {
                "name": "TELEGRAM_SESSION_STRING",
                "section": "Telegram access",
                "safeValue": "set" if is_set("TELEGRAM_SESSION_STRING") else "missing",
                "note": "Telegram login session, encrypted in Vercel.",
            },
        ],
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        payload = json.dumps(public_config(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
