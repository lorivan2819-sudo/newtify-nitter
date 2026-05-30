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
            "name": "Cedar Panel",
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
            "translateEnabled": env("TRANSLATE_ENABLED", "true"),
            "translatePrimaryLang": env("TRANSLATE_PRIMARY_LANG", "en"),
            "translateTargetLang": env("TRANSLATE_TARGET_LANG", env("TRANSLATE_SECONDARY_LANG", "ru")),
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
            "githubMinutePing": True,
            "githubMinutePingSchedule": "about every 10 seconds",
            "vercelCronFast": False,
            "vercelCronReason": (
                "GitHub Actions runs a loop and calls publish about "
                "every 10 seconds. Vercel Hobby still keeps only the daily "
                "Vercel cron fallback."
            ),
        },
        "editableEnv": [
            {
                "id": "source_accounts",
                "label": "Source accounts",
                "section": "Input",
                "safeValue": x_users,
                "note": "Comma-separated account usernames.",
            },
            {
                "id": "destination",
                "label": "Destination",
                "section": "Output",
                "safeValue": env("TARGET_CHANNEL", "@WWAInews"),
                "note": "Channel username or id.",
            },
            {
                "id": "publish_window",
                "label": "Publish window",
                "section": "Filter",
                "safeValue": env("PUBLISH_MAX_AGE_MINUTES", "45"),
                "note": "How old a post can be and still be uploaded.",
            },
            {
                "id": "feed_window",
                "label": "Feed window",
                "section": "Filter",
                "safeValue": env("X_MAX_AGE_MINUTES", "2"),
                "note": "Secondary freshness setting; publisher uses the value above.",
            },
            {
                "id": "translation_mode",
                "label": "Translation mode",
                "section": "Translation",
                "safeValue": env("TRANSLATE_ENABLED", "true"),
                "note": "Use true or false. When true, posts include a translated version.",
            },
            {
                "id": "primary_language",
                "label": "Primary language",
                "section": "Translation",
                "safeValue": env("TRANSLATE_PRIMARY_LANG", "en"),
                "note": "Primary visible language, matching the old bot default.",
            },
            {
                "id": "secondary_language",
                "label": "Secondary language",
                "section": "Translation",
                "safeValue": env("TRANSLATE_TARGET_LANG", env("TRANSLATE_SECONDARY_LANG", "ru")),
                "note": "Target language code for translation, for example ru, en, es.",
            },
            {
                "id": "source_session",
                "label": "Source session",
                "section": "Access",
                "safeValue": "set" if is_set("X_SESSIONS_B64") else "missing",
                "note": "Signed-in source session, encrypted in the host.",
            },
            {
                "id": "feed_base",
                "label": "Feed base",
                "section": "Advanced",
                "safeValue": env("RSS_BASE_URL", "https://newtify-nitter.vercel.app"),
                "note": "RSS service base URL.",
            },
            {
                "id": "delivery_session",
                "label": "Delivery session",
                "section": "Access",
                "safeValue": "set" if is_set("TELEGRAM_SESSION_STRING") else "missing",
                "note": "Delivery login session, encrypted in the host.",
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
