from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from http.server import BaseHTTPRequestHandler
import io
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import MediaCaptionTooLongError
from telethon.sessions import StringSession
from telethon.tl.types import MessageEntityTextUrl, MessageEntityUrl

try:
    from deep_translator import GoogleTranslator
except Exception:
    GoogleTranslator = None


STATUS_RE = re.compile(r"/status/(\d{8,})")
IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
TAG_RE = re.compile(r"<[^>]+>")
TCO_RE = re.compile(r"https://t\.co/[A-Za-z0-9_]+")
MARKER_START = "\u2063"
MARKER_END = "\u2064"
MARKER_ZERO = "\u200b"
MARKER_ONE = "\u200c"
MEDIA_MAX_BYTES = int(os.environ.get("MEDIA_MAX_BYTES", str(12 * 1024 * 1024)) or str(12 * 1024 * 1024))
MEDIA_MAX_FILES = int(os.environ.get("MEDIA_MAX_FILES", "4") or "4")

LANG_NAMES = {
    "en": "English",
    "ru": "Русский",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "pt": "Português",
    "zh": "中文",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
    "hi": "हिन्दी",
    "tr": "Türkçe",
    "uk": "Українська",
}


@dataclass(frozen=True)
class FormattedMessage:
    text: str
    html_text: str | None = None


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def source_users() -> list[str]:
    raw = env("X_SOURCE_USERS", "swelmchannel")
    return [item.strip().lstrip("@") for item in raw.split(",") if item.strip()]


def fetch_text(url: str, timeout: int = 12) -> tuple[int, str]:
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


def media_type(url: str, fallback: str = "") -> str:
    if fallback:
        return fallback
    path = urllib.parse.urlparse(url).path.lower()
    guess = mimetypes.guess_type(path)[0]
    return guess or "image/jpeg"


def media_name(url: str, content_type: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = os.path.basename(parsed.path) or "media"
    if "." not in name:
        name += mimetypes.guess_extension(content_type.split(";")[0]) or ".jpg"
    return name


def download_media(url: str, content_type: str = "") -> io.BytesIO:
    req = urllib.request.Request(
        url,
        headers={
            "accept": "image/avif,image/webp,image/apng,image/*,video/*,*/*;q=0.8",
            "referer": "https://x.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
            ),
        },
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        resolved_type = media_type(url, response.headers.get("content-type", content_type))
        content_length = int(response.headers.get("content-length") or "0")
        if content_length > MEDIA_MAX_BYTES:
            raise RuntimeError("media file is too large")
        buffer = io.BytesIO()
        total = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > MEDIA_MAX_BYTES:
                raise RuntimeError("media file is too large")
            buffer.write(chunk)
        buffer.seek(0)
        buffer.name = media_name(url, resolved_type)
        return buffer


def clean_description(value: str) -> str:
    value = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    value = TAG_RE.sub("", value)
    value = TCO_RE.sub("", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def encode_hidden_id(source_url: str) -> str:
    match = STATUS_RE.search(source_url)
    if not match:
        return ""
    bits = "".join(f"{int(digit):04b}" for digit in match.group(1))
    payload = bits.replace("0", MARKER_ZERO).replace("1", MARKER_ONE)
    return f"{MARKER_START}{payload}{MARKER_END}"


def decode_hidden_ids(text: str) -> set[str]:
    ids = set()
    start = 0
    while True:
        left = text.find(MARKER_START, start)
        if left < 0:
            break
        right = text.find(MARKER_END, left + 1)
        if right < 0:
            break
        payload = text[left + 1 : right]
        bits = payload.replace(MARKER_ZERO, "0").replace(MARKER_ONE, "1")
        if bits and len(bits) % 4 == 0 and set(bits) <= {"0", "1"}:
            digits = []
            for index in range(0, len(bits), 4):
                value = int(bits[index : index + 4], 2)
                if value > 9:
                    digits = []
                    break
                digits.append(str(value))
            if digits:
                ids.add("".join(digits))
        start = right + 1
    return ids


def translate_text(text: str, target: str) -> str:
    if not GoogleTranslator or not text.strip():
        return ""
    try:
        translated = GoogleTranslator(source="auto", target=target).translate(text)
    except Exception as exc:
        print(f"Translation failed: {exc}")
        return ""
    if not translated or translated.strip() == text.strip():
        return ""
    return translated.strip()


def html_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_bilingual_message(
    original_text: str,
    primary_lang: str = "en",
    secondary_lang: str = "ru",
) -> FormattedMessage:
    if not original_text or not original_text.strip() or not GoogleTranslator:
        return FormattedMessage(original_text)

    primary_text = translate_text(original_text, primary_lang) or original_text
    secondary_text = translate_text(original_text, secondary_lang)
    if not secondary_text or secondary_text.strip() == primary_text.strip():
        return FormattedMessage(primary_text)

    name = LANG_NAMES.get(secondary_lang, secondary_lang.upper())
    text = f"{primary_text}\n\n{name}:\n{secondary_text}"
    html_text = (
        f"{html_escape(primary_text)}\n\n"
        f"<blockquote expandable>{html_escape(name)}:\n{html_escape(secondary_text)}</blockquote>"
    )
    return FormattedMessage(text=text, html_text=html_text)


async def format_post_text(text: str) -> FormattedMessage:
    if not env_bool("TRANSLATE_ENABLED", True):
        return FormattedMessage(text)
    primary = env("TRANSLATE_PRIMARY_LANG", "en") or "en"
    secondary = env("TRANSLATE_TARGET_LANG", env("TRANSLATE_SECONDARY_LANG", "ru")) or "ru"
    return await asyncio.to_thread(format_bilingual_message, text, primary, secondary)


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
        media = []
        seen_media = set()
        for enclosure in item.findall("enclosure"):
            media_url = (enclosure.attrib.get("url") or "").replace("&amp;", "&").strip()
            if media_url and media_url not in seen_media:
                seen_media.add(media_url)
                media.append(
                    {
                        "url": media_url,
                        "type": media_type(media_url, enclosure.attrib.get("type", "")),
                    }
                )
        for img in IMG_RE.findall(description):
            media_url = img.replace("&amp;", "&").strip()
            if media_url and media_url not in seen_media:
                seen_media.add(media_url)
                media.append({"url": media_url, "type": media_type(media_url)})
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
                "media": media[:10],
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


async def sent_statuses(client: TelegramClient, channel: str) -> dict[str, bool]:
    ids: dict[str, bool] = {}
    async for message in client.iter_messages(channel, limit=80):
        text = message.message or ""
        message_ids = set(STATUS_RE.findall(text))
        message_ids.update(decode_hidden_ids(text))
        for entity in message.entities or []:
            message_ids.update(STATUS_RE.findall(entity_url(message, entity)))
        for tweet_id in message_ids:
            ids[tweet_id] = ids.get(tweet_id, False) or bool(message.media)
    return ids


def message_chunks(value: str, limit: int = 3900) -> list[str]:
    chunks = []
    remaining = value
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


async def send_text_message(
    client: TelegramClient,
    channel: str,
    formatted: FormattedMessage,
    source_url: str,
) -> None:
    marker = encode_hidden_id(source_url)
    if formatted.html_text and len(formatted.html_text + marker) <= 4096:
        await client.send_message(
            channel,
            formatted.html_text + marker,
            parse_mode="html",
            link_preview=False,
        )
        return

    text = formatted.text or source_url
    chunks = message_chunks(text)
    for index, chunk in enumerate(chunks):
        suffix = marker if index == len(chunks) - 1 else ""
        await client.send_message(
            channel,
            f"{escape(chunk)}{suffix}",
            parse_mode="html",
            link_preview=False,
        )


async def send_item(
    client: TelegramClient,
    channel: str,
    item: dict[str, object],
    require_media: bool = False,
) -> dict[str, object]:
    text = str(item["text"]).strip() or str(item["url"])
    formatted = await format_post_text(text)
    url = str(item["url"])
    marker = encode_hidden_id(url)
    caption_html = f"{formatted.html_text}{marker}" if formatted.html_text else None
    caption_text = f"{escape(formatted.text)}{marker}" if formatted.text else marker
    media = [entry for entry in item.get("media", []) if isinstance(entry, dict)]
    has_video = any(str(entry.get("type", "")).startswith("video/") for entry in media)

    if media:
        files = []
        media_errors = []
        for entry in media[:MEDIA_MAX_FILES]:
            try:
                files.append(download_media(str(entry["url"]), str(entry.get("type", ""))))
            except Exception as exc:
                media_errors.append(f"{entry.get('url')}: {exc}")

        if files:
            try:
                await asyncio.wait_for(
                    client.send_file(
                        channel,
                        files,
                        caption=caption_html or caption_text,
                        parse_mode="html" if caption_html else None,
                        supports_streaming=has_video,
                    ),
                    timeout=35,
                )
                return {"media_found": len(media), "media_sent": len(files), "media_errors": media_errors}
            except MediaCaptionTooLongError:
                await asyncio.wait_for(
                    client.send_file(channel, files, supports_streaming=has_video),
                    timeout=35,
                )
                await send_text_message(client, channel, formatted, url)
                return {"media_found": len(media), "media_sent": len(files), "media_errors": media_errors}
            except Exception as exc:
                media_errors.append(f"telegram upload: {exc}")

        if media_errors:
            print(f"Media failed for {url}: {'; '.join(media_errors)[:900]}")

        if require_media:
            return {"media_found": len(media), "media_sent": 0, "media_errors": media_errors}

    legacy_images = [str(item) for item in item.get("images", [])]
    if legacy_images:
        try:
            await asyncio.wait_for(
                client.send_file(
                    channel,
                    legacy_images[:MEDIA_MAX_FILES],
                    caption=caption_html or caption_text,
                    parse_mode="html" if caption_html else None,
                ),
                timeout=35,
            )
            return {"media_found": len(legacy_images), "media_sent": min(len(legacy_images), MEDIA_MAX_FILES), "media_errors": []}
        except MediaCaptionTooLongError:
            await asyncio.wait_for(
                client.send_file(channel, legacy_images[:MEDIA_MAX_FILES]),
                timeout=35,
            )
            await send_text_message(client, channel, formatted, url)
            return {"media_found": len(legacy_images), "media_sent": min(len(legacy_images), MEDIA_MAX_FILES), "media_errors": []}
        except Exception:
            pass

    if require_media:
        return {"media_found": len(media), "media_sent": 0, "media_errors": []}

    await send_text_message(client, channel, formatted, url)
    return {"media_found": len(media), "media_sent": 0, "media_errors": media_errors if media else []}


async def publish() -> dict[str, object]:
    api_id = int(env("TELEGRAM_API_ID"))
    api_hash = env("TELEGRAM_API_HASH")
    session_string = env("TELEGRAM_SESSION_STRING")
    channel = env("TARGET_CHANNEL", "@WWAInews")
    rss_base = env("RSS_BASE_URL", "https://newtify-nitter.vercel.app").rstrip("/")
    max_posts = int(env("PUBLISH_MAX_POSTS", "3") or "3")

    if not session_string:
        raise RuntimeError("TELEGRAM_SESSION_STRING is not configured")

    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    try:
        already_sent = await sent_statuses(client, channel)
        posted: list[str] = []
        sent_details: dict[str, object] = {}
        checked: dict[str, object] = {}

        for user in source_users():
            status, body = fetch_text(f"{rss_base}/{user}/rss")
            checked[user] = {"status": status, "bytes": len(body)}
            if status != 200:
                checked[user]["error"] = body[:160]
                continue

            for item in sorted(parse_items(body), key=lambda row: row["published"]):
                tweet_id = str(item["id"])
                if not is_recent(item):
                    continue
                has_media = bool(item.get("media") or item.get("images"))
                if tweet_id in already_sent:
                    if has_media and not already_sent[tweet_id]:
                        detail = await send_item(client, channel, item, require_media=True)
                        detail["retry_missing_media"] = True
                        sent_details[tweet_id] = detail
                        if int(detail.get("media_sent", 0)):
                            already_sent[tweet_id] = True
                            posted.append(tweet_id)
                            if len(posted) >= max_posts:
                                return {"ok": True, "posted": posted, "sent": sent_details, "checked": checked}
                    continue
                sent_details[tweet_id] = await send_item(client, channel, item)
                already_sent[tweet_id] = bool(sent_details[tweet_id].get("media_sent", 0))
                posted.append(tweet_id)
                if len(posted) >= max_posts:
                    return {"ok": True, "posted": posted, "sent": sent_details, "checked": checked}

        return {"ok": True, "posted": posted, "sent": sent_details, "checked": checked}
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
