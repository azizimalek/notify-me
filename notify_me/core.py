from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_USER_AGENT = (
    "notify-me-property-monitor/0.1 "
    "(personal listing monitor; contact: configure-user-agent)"
)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "wbraid",
    "yclid",
}


@dataclass(frozen=True)
class Listing:
    source: str
    title: str
    url: str
    listing_id: str


@dataclass(frozen=True)
class Anchor:
    href: str
    text: str
    title: str
    aria_label: str


class ListingMonitorError(RuntimeError):
    """Raised when monitor configuration or execution fails."""


class AnchorExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Anchor] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return

        if self._current is not None:
            self._finish_current()

        attr_map = {key.lower(): value or "" for key, value in attrs}
        self._current = {
            "href": attr_map.get("href", ""),
            "title": attr_map.get("title", ""),
            "aria_label": attr_map.get("aria-label", ""),
            "text_parts": [],
        }

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current is not None:
            self._finish_current()

    def close(self) -> None:
        super().close()
        if self._current is not None:
            self._finish_current()

    def _finish_current(self) -> None:
        if self._current is None:
            return

        self.anchors.append(
            Anchor(
                href=str(self._current.get("href", "")),
                text=normalize_whitespace(" ".join(self._current.get("text_parts", []))),
                title=normalize_whitespace(str(self._current.get("title", ""))),
                aria_label=normalize_whitespace(str(self._current.get("aria_label", ""))),
            )
        )
        self._current = None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def canonicalize_url(url: str) -> str:
    parts = urlsplit(html_lib.unescape(url))
    path = quote(unquote(parts.path or "/"), safe="/:@")
    if len(path) > 1:
        path = path.rstrip("/")

    query_items = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith("utm_") or key_lower in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, value))

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query_items, doseq=True),
            "",
        )
    )


def title_from_url(url: str) -> str:
    path = urlsplit(url).path
    last_segment = unquote(path.rstrip("/").split("/")[-1])
    title = normalize_whitespace(re.sub(r"[-_]+", " ", last_segment))
    return title or url


def compile_patterns(patterns: list[str] | None, source_name: str, key: str) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as exc:
            raise ListingMonitorError(
                f"Invalid regex in source {source_name!r} field {key!r}: {pattern!r} ({exc})"
            ) from exc
    return compiled


def pattern_matches(url: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(url) for pattern in patterns)


def extract_listings(
    html: str,
    *,
    base_url: str,
    source_name: str,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[Listing]:
    include = compile_patterns(include_patterns, source_name, "listing_url_patterns")
    exclude = compile_patterns(exclude_patterns, source_name, "exclude_url_patterns")

    parser = AnchorExtractor()
    parser.feed(html)
    parser.close()

    listings_by_id: dict[str, Listing] = {}
    for anchor in parser.anchors:
        href = anchor.href.strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        url = canonicalize_url(urljoin(base_url, href))
        scheme = urlsplit(url).scheme
        if scheme not in {"http", "https"}:
            continue
        if include and not pattern_matches(url, include):
            continue
        if exclude and pattern_matches(url, exclude):
            continue

        title = anchor.text or anchor.title or anchor.aria_label or title_from_url(url)
        listings_by_id.setdefault(
            url,
            Listing(
                source=source_name,
                title=title,
                url=url,
                listing_id=url,
            ),
        )

    return list(listings_by_id.values())


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise ListingMonitorError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ListingMonitorError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ListingMonitorError(f"Expected top-level JSON object in {path}")
    return data


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "sources": {}}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise ListingMonitorError(f"Invalid state JSON in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ListingMonitorError(f"Expected state file to contain a JSON object: {path}")
    data.setdefault("version", 1)
    data.setdefault("sources", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2, sort_keys=True)
        file.write("\n")
    temporary_path.replace(path)


def update_source_state(
    state: dict[str, Any],
    *,
    source_name: str,
    listings: list[Listing],
    checked_at: str,
    max_seen_per_source: int,
) -> None:
    sources = state.setdefault("sources", {})
    source_state = sources.setdefault(source_name, {})
    stored_listings = source_state.setdefault("listings", {})

    for listing in listings:
        stored_listings.setdefault(
            listing.listing_id,
            {
                "first_seen_at": checked_at,
                "title": listing.title,
                "url": listing.url,
            },
        )

    if len(stored_listings) > max_seen_per_source:
        ordered_items = sorted(
            stored_listings.items(),
            key=lambda item: item[1].get("first_seen_at", ""),
            reverse=True,
        )
        source_state["listings"] = dict(ordered_items[:max_seen_per_source])

    source_state["last_checked_at"] = checked_at


def fetch_html(source: dict[str, Any], request_config: dict[str, Any]) -> str:
    url = str(source.get("url", "")).strip()
    if not url:
        raise ListingMonitorError(f"Source {source.get('name', '<unnamed>')!r} has no URL")

    timeout = float(request_config.get("timeout_seconds", 20))
    max_bytes = int(request_config.get("max_response_bytes", 5_000_000))
    user_agent = str(request_config.get("user_agent", DEFAULT_USER_AGENT))
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": user_agent,
    }

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ListingMonitorError(f"Response from {url} exceeded {max_bytes} bytes")
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace")
    except HTTPError as exc:
        raise ListingMonitorError(f"HTTP {exc.code} while fetching {url}") from exc
    except URLError as exc:
        raise ListingMonitorError(f"Could not fetch {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ListingMonitorError(f"Timed out fetching {url}") from exc


def find_new_listings(
    state: dict[str, Any],
    *,
    source: dict[str, Any],
    listings: list[Listing],
    notify_on_first_run: bool,
) -> list[Listing]:
    source_name = str(source["name"])
    source_states = state.setdefault("sources", {})
    source_seen_before = source_name in source_states
    known_ids = set(source_states.get(source_name, {}).get("listings", {}).keys())
    new_listings = [listing for listing in listings if listing.listing_id not in known_ids]

    if not source_seen_before and not notify_on_first_run:
        return []
    return new_listings


def format_listing_message(listings: list[Listing], *, max_chars: int = 3800) -> str:
    if not listings:
        return "No new property listings found."

    grouped: dict[str, list[Listing]] = {}
    for listing in listings:
        grouped.setdefault(listing.source, []).append(listing)

    lines = [f"{len(listings)} new property rental listing(s) found:"]
    for source, source_listings in grouped.items():
        lines.append("")
        lines.append(source)
        for listing in source_listings:
            lines.append(f"- {listing.title}")
            lines.append(f"  {listing.url}")

    message = "\n".join(lines)
    if len(message) <= max_chars:
        return message
    return message[: max_chars - 40].rstrip() + "\n...message truncated"


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 20) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": DEFAULT_USER_AGENT},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        response.read()


def get_env_value(env_name: str | None, provider: str, setting: str) -> str | None:
    if not env_name:
        return None
    value = os.environ.get(env_name)
    if not value:
        print(
            f"Notification provider {provider!r} is enabled but ${env_name} "
            f"for {setting} is not set.",
            file=sys.stderr,
        )
    return value


def send_notifications(config: dict[str, Any], listings: list[Listing], *, dry_run: bool) -> None:
    notifications = config.get("notifications", {})
    if not isinstance(notifications, dict):
        raise ListingMonitorError("Config field 'notifications' must be an object")

    message = format_listing_message(
        listings,
        max_chars=int(notifications.get("max_message_chars", 3800)),
    )

    console_config = notifications.get("console", {"enabled": True})
    if console_config.get("enabled", True):
        print(message)

    if dry_run or not listings:
        return

    timeout = float(config.get("request", {}).get("timeout_seconds", 20))

    telegram = notifications.get("telegram", {})
    if telegram.get("enabled", False):
        token = get_env_value(telegram.get("bot_token_env"), "telegram", "bot token")
        chat_id = get_env_value(telegram.get("chat_id_env"), "telegram", "chat id")
        if token and chat_id:
            post_json(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
                timeout=timeout,
            )

    discord = notifications.get("discord", {})
    if discord.get("enabled", False):
        webhook_url = get_env_value(discord.get("webhook_url_env"), "discord", "webhook URL")
        if webhook_url:
            post_json(webhook_url, {"content": message}, timeout=timeout)

    webhook = notifications.get("webhook", {})
    if webhook.get("enabled", False):
        webhook_url = get_env_value(webhook.get("url_env"), "webhook", "URL")
        if webhook_url:
            post_json(
                webhook_url,
                {
                    "text": message,
                    "listings": [listing.__dict__ for listing in listings],
                },
                timeout=timeout,
            )


def enabled_sources(config: dict[str, Any]) -> list[dict[str, Any]]:
    sources = config.get("sources")
    if not isinstance(sources, list):
        raise ListingMonitorError("Config field 'sources' must be a list")

    enabled: list[dict[str, Any]] = []
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ListingMonitorError(f"Source at index {index} must be an object")
        if not source.get("enabled", True):
            continue
        if not source.get("name"):
            raise ListingMonitorError(f"Source at index {index} must include a name")
        enabled.append(source)
    return enabled


def check_sources(config: dict[str, Any], state: dict[str, Any], *, list_current: bool) -> list[Listing]:
    request_config = config.get("request", {})
    if not isinstance(request_config, dict):
        raise ListingMonitorError("Config field 'request' must be an object")

    all_new_listings: list[Listing] = []
    sources = enabled_sources(config)
    notify_on_first_run = bool(config.get("notify_on_first_run", False))
    max_per_source = int(config.get("max_new_per_source", 10))
    max_seen_per_source = int(config.get("max_seen_per_source", 5000))
    delay_seconds = float(request_config.get("delay_seconds", 2))

    if not sources:
        print("No enabled sources configured.")
        return []

    for index, source in enumerate(sources):
        source_name = str(source["name"])
        html = fetch_html(source, request_config)
        listings = extract_listings(
            html,
            base_url=str(source["url"]),
            source_name=source_name,
            include_patterns=source.get("listing_url_patterns"),
            exclude_patterns=source.get("exclude_url_patterns"),
        )

        if list_current:
            all_new_listings.extend(listings[:max_per_source])
        else:
            all_new_listings.extend(
                find_new_listings(
                    state,
                    source=source,
                    listings=listings,
                    notify_on_first_run=notify_on_first_run,
                )[:max_per_source]
            )

            update_source_state(
                state,
                source_name=source_name,
                listings=listings,
                checked_at=utc_now(),
                max_seen_per_source=max_seen_per_source,
            )

        if delay_seconds > 0 and index < len(sources) - 1:
            time.sleep(delay_seconds)

    return all_new_listings


def run_once(config_path: Path, *, dry_run: bool = False, list_current: bool = False) -> int:
    config = load_json_file(config_path)
    state_file = Path(config.get("state_file", "state/listings.json"))
    if not state_file.is_absolute():
        state_file = config_path.parent / state_file

    state = load_state(state_file)
    try:
        listings = check_sources(config, state, list_current=list_current)
    except ListingMonitorError:
        raise
    except Exception as exc:
        raise ListingMonitorError(str(exc)) from exc

    send_notifications(config, listings, dry_run=dry_run or list_current)
    if not dry_run and not list_current:
        save_state(state_file, state)
    return 0
