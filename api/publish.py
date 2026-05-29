from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from http.server import BaseHTTPRequestHandler
import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl


STATUS_RE = re.compile(r"/status/(\d{8,})")
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
TAG_RE = re.compile(r"<[^>]+>")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def source_users() -> list[str]:
    raw = env("X_SOURCE_USERS", "swelmchannel")
    return [item.strip().lstrip("@") for item in raw.split(",") if item.strip()]


def fetch_text(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": "Newtify-Vercel-Publisher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def clean_description(value: str) -> str:
    value = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    value = TAG_RE.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def parse_items(feed: str) -> list[dict[str, object]]:
    root = ET.fromstring(feed)
    items: list[dict[str, object]] = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        match = STATUS_RE.search(link)
        if not match:
            continue

        description = item.findtext("description") or item.findtext("title") or ""
        pub_date = item.findtext("pubDate") or ""
        images = [img.replace("&amp;", "&") for img in IMG_RE.findall(description)]
        text = clean_description(description)
        try:
            published = parsedate_to_datetime(pub_date)
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
        except Exception:
            published = datetime.now(timezone.utc)

        items.append(
            {
                "id": match.group(1),
                "url": link,
                "text": text,
                "images": images[:10],
                "published": published,
            }
        )
    return items


def is_recent(item: dict[str, object]) -> bool:
    max_age = int(env("PUBLISH_MAX_AGE_MINUTES", env("X_MAX_AGE_MINUTES", "10")) or "10")
    if max_age <= 0:
        return True
    published = item["published"]
    if not isinstance(published, datetime):
        return True
    age_seconds = (datetime.now(timezone.utc) - published).total_seconds()
    return age_seconds <= max_age * 60


def entity_url(message, entity) -> str:
    if isinstance(entity, MessageEntityTextUrl):
        return entity.url or ""
    if isinstance(entity, MessageEntityUrl):
        start = entity.offset
        end = entity.offset + entity.length
        return (message.message or "")[start:end]
    return ""


async def sent_ids(client: TelegramClient, channel: str) -> set[str]:
    ids: set[str] = set()
    async for message in client.iter_messages(channel, limit=80):
        text = message.message or ""
        ids.update(STATUS_RE.findall(text))
        for entity in message.entities or []:
            ids.update(STATUS_RE.findall(entity_url(message, entity)))
    return ids


async def send_item(client: TelegramClient, channel: str, item: dict[str, object]) -> None:
    text = str(item["text"]).strip() or str(item["url"])
    url = str(item["url"])
    hidden_source = f'\n\n<a href="{escape(url)}">&#8291;</a>'
    caption = f"{escape(text)}{hidden_source}"
    images = [str(item) for item in item.get("images", [])]

    if images:
        try:
            await client.send_file(
                channel,
                images,
                caption=caption[:3900],
                parse_mode="html",
                link_preview=False,
            )
            return
        except Exception:
            pass

    await client.send_message(
        channel,
        caption[:3900],
        parse_mode="html",
        link_preview=False,
    )


async def publish() -> dict[str, object]:
    api_id = int(env("TELEGRAM_API_ID"))
    api_hash = env("TELEGRAM_API_HASH")
    session_string = env("TELEGRAM_SESSION_STRING")
    channel = env("TARGET_CHANNEL", "@WWAInews")
    rss_base = env("RSS_BASE_URL", "https://newtify-nitter.vercel.app").rstrip("/")

    if not session_string:
        raise RuntimeError("TELEGRAM_SESSION_STRING is not configured")

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    try:
        already_sent = await sent_ids(client, channel)
        posted: list[str] = []
        checked: dict[str, object] = {}

        for user in source_users():
            status, body = fetch_text(f"{rss_base}/{user}/rss")
            checked[user] = {"status": status, "bytes": len(body)}
            if status != 200:
                checked[user]["error"] = body[:160]
                continue

            for item in sorted(parse_items(body), key=lambda row: row["published"]):
                tweet_id = str(item["id"])
                if tweet_id in already_sent or not is_recent(item):
                    continue
                await send_item(client, channel, item)
                already_sent.add(tweet_id)
                posted.append(tweet_id)

        return {"ok": True, "posted": posted, "checked": checked}
    finally:
        await client.disconnect()


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        try:
            result = asyncio.run(publish())
            status = 200
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            status = 500

        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
