"""Offline-first collectors and normalized persistence for external MTG data.

This module deliberately keeps source-specific parsing separate from the local
catalog contract.  Raw names are retained for auditability, while card IDs are
resolved against ``cards`` when a matching Oracle record exists.

No source-specific private API is assumed here: adapters consume a configured
public URL or a local JSON fixture.  Network access is cached, rate limited,
bounded, and subject to robots.txt when a URL is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from catalog import (
    DATA_DIR,
    ensure_schema,
    keywords_from_scryfall,
    mana_cost_from_scryfall,
    oracle_text_from_scryfall,
    parse_price,
    set_meta,
)

DEFAULT_USER_AGENT = "ManaGraph-data-collector/1.0 (+https://github.com/)"
MAX_RESPONSE_BYTES = 100 * 1024 * 1024
MOXFIELD_SEARCH_URL = "https://api2.moxfield.com/v2/decks/search"
MOXFIELD_DECK_URL = "https://api2.moxfield.com/v3/decks/all/{deck_id}"
MOXFIELD_DECK_FALLBACK_URL = "https://api2.moxfield.com/v2/decks/all/{deck_id}"
EDHREC_JSON_BASE_URL = "https://json.edhrec.com/pages"
EDHREC_COMMANDERS_URL = EDHREC_JSON_BASE_URL + "/commanders.json"
EDHREC_TOP_COMMANDERS_URL = EDHREC_JSON_BASE_URL + "/top/commanders--{page}.json"
DEFAULT_RETRIES = 3


class CollectionError(RuntimeError):
    """A clear, actionable failure from a configured data source."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def normalize_card_name(name: str | None) -> str:
    """Normalize pasted split/DFC names without changing the raw audit value."""
    value = unicodedata.normalize("NFKC", str(name or "")).strip()
    value = re.sub(r"\s+", " ", value)
    if "//" in value:
        left, right = value.split("//", 1)
        return f"{left.strip()} // {right.strip()}"
    parts = [part.strip() for part in value.split("/")]
    if len(parts) == 2 and all(parts):
        return f"{parts[0]} // {parts[1]}"
    return value


def _name_from_entry(entry: Any, fallback: str = "") -> tuple[str, str | None]:
    """Return (raw/display name, optional source card id) from common JSON forms."""
    if isinstance(entry, str):
        return entry.strip(), None
    if not isinstance(entry, dict):
        return fallback.strip(), None
    card = entry.get("card")
    if isinstance(card, dict):
        name = card.get("name") or entry.get("name") or fallback
        card_id = card.get("scryfall_id") or card.get("id")
    else:
        name = entry.get("name") or fallback
        card_id = entry.get("scryfall_id") or entry.get("cardId") or entry.get("id")
    return str(name or "").strip(), str(card_id) if card_id else None


def _quantity(entry: Any) -> int:
    if isinstance(entry, (int, float, str)):
        try:
            return max(0, int(entry))
        except (TypeError, ValueError):
            return 1
    if isinstance(entry, dict):
        for key in ("quantity", "count", "cardQuantity", "qty"):
            if entry.get(key) is not None:
                try:
                    return max(0, int(entry[key]))
                except (TypeError, ValueError):
                    pass
    return 1


def _number(value: Any) -> float | None:
    """Parse EDHREC's numeric fields, including values formatted as ``84%``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    percent = text.endswith("%")
    if percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number / 100.0 if percent else number


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number >= 0 else None


def _edhrec_metrics(item: dict) -> dict:
    """Normalize EDHREC inclusion/synergy metrics without discarding raw data."""
    potential = next(
        (_integer(item.get(key)) for key in ("potential_decks", "potentialDecks", "total_decks")
         if item.get(key) is not None),
        None,
    )
    num_decks = next(
        (_integer(item.get(key)) for key in ("num_decks", "numDecks", "inclusion_count")
         if item.get(key) is not None),
        None,
    )
    inclusion_value = item.get("inclusion")
    inclusion_number = _number(inclusion_value)
    inclusion_count = num_decks
    if inclusion_count is None and inclusion_number is not None and inclusion_number > 1:
        inclusion_count = int(inclusion_number)
    explicit_percent = next(
        (_number(item.get(key)) for key in ("inclusion_percent", "inclusionPercent", "percentage", "percent")
         if item.get(key) is not None),
        None,
    )
    label = str(item.get("label") or "")
    if inclusion_count is None:
        match = re.search(r"\bin\s+([\d,]+)\s+decks?\b", label, re.IGNORECASE)
        if match:
            inclusion_count = _integer(match.group(1))
    if potential is None:
        match = re.search(r"\bof\s+([\d,]+)\s+decks?\b", label, re.IGNORECASE)
        if match:
            potential = _integer(match.group(1))
    if explicit_percent is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", label)
        if match:
            explicit_percent = _number(match.group(1))
    if explicit_percent is not None and 0 <= explicit_percent <= 1:
        explicit_percent *= 100
    inclusion_percent = explicit_percent
    if inclusion_percent is None and isinstance(inclusion_value, str) and "%" in inclusion_value:
        inclusion_percent = (inclusion_number or 0) * 100
    if inclusion_percent is None and inclusion_count is not None and potential:
        inclusion_percent = inclusion_count / potential * 100
    if inclusion_percent is None and inclusion_number is not None and 0 <= inclusion_number <= 1:
        inclusion_percent = inclusion_number * 100
    synergy = next(
        (_number(item.get(key)) for key in ("synergy", "synergy_score", "synergyScore")
         if item.get(key) is not None),
        None,
    )
    salt = next(
        (_number(item.get(key)) for key in ("salt", "salt_score", "saltScore")
         if item.get(key) is not None),
        None,
    )
    return {
        "score": synergy if synergy is not None else _number(item.get("score")),
        "inclusion_count": inclusion_count,
        "potential_decks": potential,
        "inclusion_percent": inclusion_percent,
        "synergy": synergy,
        "salt_score": salt,
    }


def _entries(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for fallback, entry in value.items():
            yield str(fallback), entry
    elif isinstance(value, list):
        for entry in value:
            yield "", entry


def _parse_cards(value: Any, section: str) -> list[dict]:
    result = []
    for fallback, entry in _entries(value):
        name, source_id = _name_from_entry(entry, fallback)
        quantity = _quantity(entry)
        if name and quantity > 0:
            result.append(
                {
                    "name": name,
                    "source_id": source_id,
                    "quantity": quantity,
                    "section": section,
                    "raw": entry,
                }
            )
    return result


@dataclass
class ParsedPayload:
    decks: list[dict]
    recommendations: list[dict]
    cooccurrence: list[dict]


class JsonAdapter:
    source_id = "json"
    name = "Configured JSON source"
    license = None

    def parse(self, payload: Any, external_id: str = "fixture") -> ParsedPayload:
        raise NotImplementedError


class MoxfieldAdapter(JsonAdapter):
    """Parse exported/public Moxfield JSON, without assuming a private API."""

    source_id = "moxfield"
    name = "Moxfield public/exported data"

    def parse(self, payload: Any, external_id: str = "fixture") -> ParsedPayload:
        if isinstance(payload, list):
            payload = {"decks": payload}
        if not isinstance(payload, dict):
            raise CollectionError("Moxfield input must be a JSON object or list.")
        # Search responses use data=[summaries], while single-deck responses
        # sometimes wrap the object in data={...}.  Both are public shapes.
        if isinstance(payload.get("data"), list) and not any(
            key in payload for key in ("decks", "mainboard", "commanders", "commander")
        ):
            payload = {"decks": payload["data"], "_pagination": payload}
        elif isinstance(payload.get("data"), dict) and not any(
            key in payload for key in ("decks", "mainboard", "commanders", "commander")
        ):
            payload = payload["data"]
        decks = payload.get("decks")
        if decks is None:
            decks = [payload]
        parsed = []
        for index, raw_deck in enumerate(decks):
            if not isinstance(raw_deck, dict):
                continue
            deck_id = _moxfield_public_id(raw_deck) or (
                external_id if len(decks) == 1 else f"{external_id}:{index}"
            )
            sections = []
            commanders = raw_deck.get("commanders") or raw_deck.get("commander")
            sections.extend(_parse_cards(commanders, "commander"))
            for section in ("mainboard", "main", "sideboard", "companions", "maybeboard"):
                value = raw_deck.get(section)
                if value:
                    sections.extend(_parse_cards(value, section))
            boards = raw_deck.get("boards")
            if isinstance(boards, dict):
                for section, value in boards.items():
                    sections.extend(_parse_cards(value, str(section)))
            commander = next((row["name"] for row in sections if row["section"] == "commander"), None)
            commander_source_id = next(
                (row.get("source_id") for row in sections if row["section"] == "commander"), None
            )
            parsed.append(
                {
                    "external_id": deck_id,
                    "name": raw_deck.get("name") or raw_deck.get("title") or deck_id,
                    "commander": commander,
                    "commander_source_id": commander_source_id,
                    "format": raw_deck.get("format") or "commander",
                    "url": raw_deck.get("publicUrl") or raw_deck.get("url"),
                    "cards": sections,
                    "raw": raw_deck,
                    "metadata": {
                        key: raw_deck[key]
                        for key in (
                            "format", "colors", "colorIdentity", "mainboardCount",
                            "sideboardCount", "viewCount", "likeCount",
                            "createdAtUtc", "lastUpdatedAtUtc", "createdByUser",
                        )
                        if key in raw_deck
                    },
                }
            )
        return ParsedPayload(
            decks=parsed,
            recommendations=payload.get("recommendations") or [],
            cooccurrence=payload.get("cooccurrence") or [],
        )


class EDHRECAdapter(JsonAdapter):
    """Parse explicitly supplied EDHREC JSON exports/fixtures.

    EDHREC page layouts and access controls change frequently.  The adapter
    therefore accepts a fixture or a configured public response and only
    recognizes explicit recommendation/card-list fields.
    """

    source_id = "edhrec"
    name = "EDHREC public recommendation data"

    def parse(self, payload: Any, external_id: str = "fixture") -> ParsedPayload:
        if isinstance(payload, list):
            payload = {"recommendations": payload}
        if not isinstance(payload, dict):
            raise CollectionError("EDHREC input must be a JSON object or list.")
        # Current public pages use container.json_dict.cardlists; older
        # fixtures commonly use data/recommendations directly.  Merge rather
        # than replace wrappers so commander and page metadata survive.
        nested = payload.get("container")
        if isinstance(nested, dict):
            nested = nested.get("json_dict") or nested.get("jsonDict") or nested
            if isinstance(nested, dict):
                payload = {**payload, **nested}
        nested = payload.get("data")
        if isinstance(nested, dict):
            payload = {**payload, **nested}
        commander = payload.get("commander") or payload.get("commanders") or payload.get("card")
        commander_name, commander_id = _name_from_entry(commander)
        if not commander_name:
            commander_name = str(payload.get("commander_name") or "").strip() or None
        recommendations = payload.get("recommendations")
        if recommendations is None:
            recommendations = (
                payload.get("cards")
                or payload.get("cardlists")
                or payload.get("panels")
                or (payload.get("commanders") if isinstance(payload.get("commanders"), list) else [])
            )
        recommendations = self._flatten_recommendations(recommendations)
        normalized = []
        for rank, item in enumerate(recommendations if isinstance(recommendations, list) else [], 1):
            name, source_id = _name_from_entry(item)
            if not name:
                continue
            item = item if isinstance(item, dict) else {"value": item}
            metrics = _edhrec_metrics(item)
            category = (
                item.get("category")
                or item.get("tag")
                or item.get("recommendation_type")
                or "recommendation"
            )
            category = str(category).strip() or "recommendation"
            normalized.append(
                {
                    "name": name,
                    "source_id": source_id,
                    **metrics,
                    "rank": _integer(item.get("rank")) or rank,
                    "recommendation_type": category,
                    "category": category,
                    "commander": commander_name,
                    "commander_id": commander_id,
                    "raw": item,
                    "metadata": {
                        key: item[key]
                        for key in ("sanitized", "sanitized_wo", "url", "label", "prices")
                        if key in item
                    },
                }
            )
        # The static EDHREC page may expose an average deck rather than a
        # recommendations key.  Keep it as an external deck so it can be
        # analyzed without pretending it is a user-authored decklist.
        average = next(
            (
                payload.get(key)
                for key in ("averageDeck", "average_deck", "avgdeck", "decklist")
                if payload.get(key)
            ),
            None,
        )
        decks = []
        if average:
            average_cards = _parse_cards(average, "average_deck")
            if average_cards:
                decks.append(
                    {
                        "external_id": f"{external_id}:average",
                        "name": f"EDHREC average deck{f' - {commander_name}' if commander_name else ''}",
                        "commander": commander_name,
                        "commander_source_id": commander_id,
                        "format": "commander",
                        "url": None,
                        "cards": average_cards,
                        "raw": average,
                    }
                )
        cooccurrence = self._parse_cooccurrence(payload)
        # EDHREC commander cardlists are a commander-to-card relation even
        # when no explicit pair endpoint is present.  Materialize that edge
        # in the existing graph table while keeping recommendations as the
        # richer category/metric view.
        existing_pairs = set()
        for item in cooccurrence:
            pair = self._pair_key(item.get("a"), item.get("b"))
            if pair:
                existing_pairs.add(pair)
        if commander_name:
            for item in normalized:
                pair = self._pair_key(commander_name, item["name"])
                if not pair or pair in existing_pairs:
                    continue
                cooccurrence.append(
                    {
                        "a": commander_name,
                        "b": item["name"],
                        "occurrence_count": item.get("inclusion_count") or 0,
                        "score": item.get("score"),
                        "relation_type": "commander_recommendation",
                        "category": item.get("category"),
                        "inclusion_percent": item.get("inclusion_percent"),
                        "synergy": item.get("synergy"),
                        "raw": item.get("raw", item),
                    }
                )
                existing_pairs.add(pair)
        return ParsedPayload(
            decks=decks,
            recommendations=normalized,
            cooccurrence=cooccurrence,
        )

    @staticmethod
    def _flatten_recommendations(value: Any, group: str = "recommendation") -> list:
        """Flatten EDHREC cardlists/cardviews while retaining their category."""
        if isinstance(value, list):
            result = []
            for item in value:
                result.extend(EDHRECAdapter._flatten_recommendations(item, group))
            return result
        if not isinstance(value, dict):
            return [{"name": value, "recommendation_type": group}] if isinstance(value, str) else []
        if value.get("name") or value.get("card"):
            category = value.get("category") or value.get("tag") or value.get("recommendation_type") or group
            return [{**value, "category": category, "recommendation_type": category}]
        result = []
        cardlists = value.get("cardlists")
        if isinstance(cardlists, list):
            for cardlist in cardlists:
                if not isinstance(cardlist, dict):
                    continue
                category = (
                    cardlist.get("tag")
                    or cardlist.get("header")
                    or cardlist.get("name")
                    or group
                )
                result.extend(
                    EDHRECAdapter._flatten_recommendations(
                        cardlist.get("cardviews") or cardlist.get("cards") or [],
                        str(category),
                    )
                )
        if "cardviews" in value:
            category = (
                value.get("tag")
                or value.get("header")
                or value.get("name")
                or group
            )
            result.extend(
                EDHRECAdapter._flatten_recommendations(
                    value.get("cardviews") or [], str(category)
                )
            )
        for key in ("cards", "recommendations", "panels"):
            if key in value:
                result.extend(EDHRECAdapter._flatten_recommendations(value[key], key))
        if result:
            return result
        for key in ("container", "json_dict", "jsonDict", "data"):
            if key in value:
                result.extend(EDHRECAdapter._flatten_recommendations(value[key], group))
        if result:
            return result
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                result.extend(EDHRECAdapter._flatten_recommendations(nested, str(key)))
        return result

    @staticmethod
    def _pair_key(left: Any, right: Any) -> str | None:
        left_name, _ = _name_from_entry(left)
        right_name, _ = _name_from_entry(right)
        if not left_name or not right_name:
            return None
        names = sorted((normalize_card_name(left_name).casefold(), normalize_card_name(right_name).casefold()))
        if names[0] == names[1]:
            return None
        return "|".join(names)

    @classmethod
    def _parse_cooccurrence(cls, payload: dict) -> list[dict]:
        value = payload.get("cooccurrence") or payload.get("relationships") or payload.get("relations")
        if value is None:
            value = payload.get("combos") or []
        if isinstance(value, dict):
            value = value.get("pairs") or value.get("items") or value.get("combos") or value
        if not isinstance(value, list):
            value = [value] if isinstance(value, dict) else []
        result = []
        for raw in value:
            if not isinstance(raw, dict):
                continue
            left = raw.get("a") or raw.get("card_a") or raw.get("card1") or raw.get("left")
            right = raw.get("b") or raw.get("card_b") or raw.get("card2") or raw.get("right")
            cards = raw.get("cards") or raw.get("combo")
            if (left is None or right is None) and isinstance(cards, list) and len(cards) >= 2:
                left, right = cards[0], cards[1]
            left_name, _ = _name_from_entry(left)
            right_name, _ = _name_from_entry(right)
            if not cls._pair_key(left_name, right_name):
                continue
            metrics = _edhrec_metrics(raw)
            result.append(
                {
                    "a": left_name,
                    "b": right_name,
                    **metrics,
                    "occurrence_count": _integer(
                        raw.get("occurrence_count", raw.get("count", raw.get("inclusion")))
                    ) or 1,
                    "relation_type": raw.get("relation_type", "cooccurrence"),
                    "category": raw.get("category") or raw.get("tag"),
                    "raw": raw,
                }
            )
        return result


def _cache_path(cache_dir: str, url: str) -> str:
    return os.path.join(cache_dir, f"{sha256_bytes(url.encode('utf-8'))}.body")


def fetch_cached(
    url: str,
    *,
    cache_dir: str | None = None,
    timeout: float = 30,
    user_agent: str = DEFAULT_USER_AGENT,
    min_interval: float = 1.0,
    robots: bool = True,
    max_bytes: int = MAX_RESPONSE_BYTES,
    expected_sha256: str | None = None,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = 0.5,
) -> tuple[bytes, bool]:
    """Fetch a public URL with a deterministic cache; return (bytes, cache_hit)."""
    if not url or urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise CollectionError("A public http(s) URL is required for network collection.")
    cache_dir = cache_dir or os.path.join(DATA_DIR, "source_cache")
    os.makedirs(cache_dir, exist_ok=True)
    path = _cache_path(cache_dir, url)
    if os.path.isfile(path):
        data = Path(path).read_bytes()
        if expected_sha256 and sha256_bytes(data) != expected_sha256.lower():
            raise CollectionError(f"Cached response checksum mismatch for {url}.")
        return data, True
    parsed = urllib.parse.urlparse(url)
    if robots:
        robot_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
        parser = urllib.robotparser.RobotFileParser(robot_url)
        try:
            parser.read()
        except (OSError, urllib.error.URLError):
            raise CollectionError(f"Could not verify robots.txt for {url}; use --fixture if offline.")
        if not parser.can_fetch(user_agent, url):
            raise CollectionError(f"robots.txt disallows collection from {url}.")
    if min_interval > 0:
        time.sleep(min_interval)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/json,text/html;q=0.9"},
    )
    retryable_statuses = {408, 425, 429, 500, 502, 503, 504}
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    raise CollectionError(f"Response for {url} exceeds {max_bytes} bytes.")
                data = response.read(max_bytes + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CollectionError(
                    f"{url} requires credentials or denied automated access ({exc.code}); "
                    "provide an authorized export with --fixture/--file."
                ) from exc
            last_error = exc
            if exc.code not in retryable_statuses or attempt >= retries:
                raise CollectionError(f"HTTP error fetching {url}: {exc.code} {exc.reason}") from exc
            retry_after = exc.headers.get("Retry-After")
            try:
                delay = min(60.0, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                delay = backoff_factor * (2**attempt)
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt >= retries:
                raise CollectionError(f"Could not fetch {url}: {exc}") from exc
            time.sleep(backoff_factor * (2**attempt))
    else:
        raise CollectionError(f"Could not fetch {url}: {last_error}")
    if len(data) > max_bytes:
        raise CollectionError(f"Response for {url} exceeds {max_bytes} bytes.")
    digest = sha256_bytes(data)
    if expected_sha256 and digest != expected_sha256.lower():
        raise CollectionError(f"Checksum mismatch for {url}: expected {expected_sha256}, got {digest}.")
    tmp = f"{path}.tmp"
    Path(tmp).write_bytes(data)
    os.replace(tmp, path)
    return data, False


def load_fixture(path: str) -> tuple[Any, bytes]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CollectionError(f"Fixture not readable: {path}: {exc}") from exc
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"Fixture must be UTF-8 JSON: {path}: {exc}") from exc


def _register_source(conn: sqlite3.Connection, adapter: JsonAdapter, *, url: str | None, license: str | None):
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sources (source_id, name, adapter, base_url, license, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            name=excluded.name, adapter=excluded.adapter, base_url=excluded.base_url,
            license=COALESCE(excluded.license, sources.license), updated_at=excluded.updated_at
        """,
        (adapter.source_id, adapter.name, adapter.__class__.__name__, url, license or adapter.license, now, now),
    )


def _resolve_card(conn: sqlite3.Connection, raw_name: str) -> tuple[str | None, str | None]:
    normalized = normalize_card_name(raw_name)
    candidates = [raw_name.strip(), normalized]
    for candidate in dict.fromkeys(candidates):
        row = conn.execute(
            "SELECT id, name FROM cards WHERE name = ? COLLATE NOCASE", (candidate,)
        ).fetchone()
        if row:
            return row[0], row[1]
    if normalized and " // " in normalized:
        row = conn.execute(
            "SELECT id, name FROM cards WHERE name LIKE ? COLLATE NOCASE",
            (normalized.split(" // ", 1)[0] + " //%",),
        ).fetchone()
        if row:
            return row[0], row[1]
    return None, None


def _resolve_entry(
    conn: sqlite3.Connection, raw_name: str, source_card_id: str | None = None
) -> tuple[str | None, str | None]:
    if source_card_id:
        row = conn.execute("SELECT id, name FROM cards WHERE id = ?", (source_card_id,)).fetchone()
        if row:
            return row[0], row[1]
    return _resolve_card(conn, raw_name)


def _dataset_id(source_id: str, raw: bytes, supplied: str | None) -> str:
    return supplied or f"{source_id}:{sha256_bytes(raw)[:20]}"


def _coalesce_deck_cards(cards: Iterable[dict]) -> list[dict]:
    """Combine repeated raw card entries without losing their source payloads.

    A deck has one logical row per ``(card_name_raw, section)``.  When a
    source emits that key more than once, quantities are summed because each
    entry represents cards in the same deck.  The first entry's shape remains
    unchanged for ordinary payloads; duplicate entries are retained under
    ``raw_entries`` so audit data is not discarded.
    """
    grouped: dict[tuple[str, str], dict] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        raw_name = str(card.get("name") or "").strip()
        try:
            quantity = int(card.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if not raw_name or quantity <= 0:
            continue
        section = str(card.get("section") or "mainboard").strip() or "mainboard"
        key = (normalize_card_name(raw_name).casefold(), section.casefold())
        existing = grouped.get(key)
        if existing is None:
            existing = {
                **card,
                "name": raw_name,
                "section": section,
                "quantity": quantity,
                "raw_entries": [card.get("raw", {})],
                "source_ids": [card.get("source_id")],
            }
            grouped[key] = existing
            continue
        existing["quantity"] += quantity
        existing["raw_entries"].append(card.get("raw", {}))
        if card.get("source_id"):
            existing["source_ids"].append(card["source_id"])
    return list(grouped.values())


def _deck_card_key(raw_name: str, section: str) -> tuple[str, str]:
    """Return the comparison key while leaving stored names untouched."""
    return normalize_card_name(raw_name).casefold(), section.strip().casefold()


def ingest_parsed(
    parsed: ParsedPayload,
    adapter: JsonAdapter,
    raw: bytes,
    *,
    db_path: str,
    dataset_type: str = "external_json",
    dataset_id: str | None = None,
    url: str | None = None,
    local_path: str | None = None,
    license: str | None = None,
    dry_run: bool = False,
    dataset_metadata: dict | None = None,
) -> dict:
    """Persist a parsed source transactionally and return a compact summary."""
    digest = sha256_bytes(raw)
    ds_id = _dataset_id(adapter.source_id, raw, dataset_id)
    if dry_run:
        return {
            "source": adapter.source_id,
            "dataset_id": ds_id,
            "decks": len(parsed.decks),
            "deck_cards": sum(
                len(_coalesce_deck_cards(d.get("cards", [])))
                for d in parsed.decks
            ),
            "recommendations": len(parsed.recommendations),
            "cooccurrence": len(parsed.cooccurrence),
            "dry_run": True,
        }
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        now = utc_now()
        _register_source(conn, adapter, url=url, license=license)
        conn.execute(
            """
            INSERT INTO datasets
              (dataset_id, source_id, dataset_type, version, url, local_path,
               sha256, byte_size, fetched_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dataset_id) DO UPDATE SET
              url=excluded.url, local_path=excluded.local_path, sha256=excluded.sha256,
              byte_size=excluded.byte_size, fetched_at=excluded.fetched_at,
              metadata_json=excluded.metadata_json
            """,
            (
                ds_id, adapter.source_id, dataset_type, None, url, local_path,
                digest, len(raw), now, canonical_json(dataset_metadata or {}),
            ),
        )
        deck_count = card_count = rec_count = pair_count = 0
        for deck in parsed.decks:
            external_id = str(deck.get("external_id") or sha256_json(deck.get("raw", deck))[:24])
            stable_id = f"{adapter.source_id}:{external_id}"
            deck_raw = deck.get("raw", deck)
            prov_hash = sha256_json(deck_raw)
            commander_raw = deck.get("commander")
            commander_id, commander_name = _resolve_entry(
                conn, commander_raw or "", deck.get("commander_source_id")
            )
            conn.execute(
                """
                INSERT INTO external_decks
                  (deck_id, source_id, external_id, name, commander_name_raw,
                   commander_card_id, format, url, dataset_id, raw_hash, license,
                   collected_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(deck_id) DO UPDATE SET
                  name=excluded.name, commander_name_raw=excluded.commander_name_raw,
                  commander_card_id=excluded.commander_card_id, format=excluded.format,
                  url=excluded.url, dataset_id=excluded.dataset_id, raw_hash=excluded.raw_hash,
                  license=excluded.license, collected_at=excluded.collected_at,
                  metadata_json=excluded.metadata_json
                """,
                (
                    stable_id, adapter.source_id, external_id, deck.get("name"),
                    commander_raw, commander_id, deck.get("format"), deck.get("url") or url,
                    ds_id, prov_hash, license or adapter.license, now,
                    canonical_json(deck.get("metadata") or {}),
                ),
            )
            conn.execute(
                """
                INSERT INTO provenance
                  (entity_type, entity_key, source_id, dataset_id, raw_name,
                   raw_hash, license, collected_at, raw_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_key, source_id, dataset_id, raw_hash)
                DO UPDATE SET collected_at=excluded.collected_at, raw_json=excluded.raw_json
                """,
                (
                    "external_deck", stable_id, adapter.source_id, ds_id,
                    deck.get("name") or external_id, prov_hash, license or adapter.license,
                    now, canonical_json(deck_raw), canonical_json(deck.get("metadata") or {}),
                ),
            )
            provenance = conn.execute(
                """
                SELECT provenance_id FROM provenance
                WHERE entity_type='external_deck' AND entity_key=? AND source_id=?
                  AND dataset_id=? AND raw_hash=?
                """,
                (stable_id, adapter.source_id, ds_id, prov_hash),
            ).fetchone()
            existing_card_keys = {
                _deck_card_key(row["card_name_raw"], row["section"]): (
                    row["card_name_raw"], row["section"]
                )
                for row in conn.execute(
                    "SELECT card_name_raw, section FROM deck_cards WHERE deck_id=?",
                    (stable_id,),
                )
            }
            for card in _coalesce_deck_cards(deck.get("cards", [])):
                raw_name = card["name"]
                section = card["section"]
                existing_key = existing_card_keys.get(_deck_card_key(raw_name, section))
                if existing_key:
                    # A later payload may spell/space a name differently. Use
                    # the existing primary-key value so normalization remains
                    # idempotent without replacing the raw audit payload.
                    raw_name, section = existing_key
                else:
                    existing_card_keys[_deck_card_key(raw_name, section)] = (raw_name, section)
                card_id = None
                for source_card_id in card["source_ids"]:
                    card_id, _ = _resolve_entry(conn, raw_name, source_card_id)
                    if card_id:
                        break
                if card_id is None:
                    card_id, _ = _resolve_entry(conn, raw_name)
                raw_entries = card["raw_entries"]
                raw_json = (
                    raw_entries[0]
                    if len(raw_entries) == 1
                    else {"entries": raw_entries}
                )
                conn.execute(
                    """
                    INSERT INTO deck_cards
                      (deck_id, card_name_raw, card_id, quantity, section, raw_json, provenance_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(deck_id, card_name_raw, section) DO UPDATE SET
                      card_id=COALESCE(excluded.card_id, deck_cards.card_id),
                      quantity=MAX(deck_cards.quantity, excluded.quantity),
                      raw_json=excluded.raw_json,
                      provenance_id=COALESCE(excluded.provenance_id, deck_cards.provenance_id)
                    """,
                    (
                        stable_id, raw_name, card_id, card["quantity"],
                        section, canonical_json(raw_json),
                        provenance[0] if provenance else None,
                    ),
                )
                card_count += 1
            deck_count += 1
        for item in parsed.recommendations:
            raw_name = str(item.get("name") or "").strip()
            if not raw_name:
                continue
            card_id, _ = _resolve_entry(conn, raw_name, item.get("source_id"))
            commander_id, _ = _resolve_entry(
                conn, item.get("commander") or "", item.get("commander_id")
            )
            if commander_id is None:
                # SQLite treats NULLs as distinct in UNIQUE constraints.  Remove
                # the prior unresolved row explicitly so fixture re-runs remain
                # idempotent until the catalog can resolve the commander.
                conn.execute(
                    """
                    DELETE FROM recommendations
                    WHERE source_id=? AND dataset_id=? AND commander_card_id IS NULL
                      AND card_name_raw=? AND recommendation_type=?
                    """,
                    (
                        adapter.source_id, ds_id, raw_name,
                        item.get("recommendation_type", "recommendation"),
                    ),
                )
            conn.execute(
                """
                INSERT INTO recommendations
                  (source_id, dataset_id, commander_card_id, card_id, card_name_raw,
                   score, rank, recommendation_type, category, inclusion_count,
                   potential_decks, inclusion_percent, synergy, salt_score,
                   raw_json, metadata_json, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, dataset_id, commander_card_id, card_name_raw, recommendation_type)
                DO UPDATE SET card_id=excluded.card_id, score=excluded.score, rank=excluded.rank,
                  category=excluded.category, inclusion_count=excluded.inclusion_count,
                  potential_decks=excluded.potential_decks,
                  inclusion_percent=excluded.inclusion_percent, synergy=excluded.synergy,
                  salt_score=excluded.salt_score, raw_json=excluded.raw_json,
                  metadata_json=excluded.metadata_json, collected_at=excluded.collected_at
                """,
                (
                    adapter.source_id, ds_id, commander_id, card_id, raw_name,
                    item.get("score"), item.get("rank"), item.get("recommendation_type", "recommendation"),
                    item.get("category") or item.get("recommendation_type", "recommendation"),
                    item.get("inclusion_count"), item.get("potential_decks"),
                    item.get("inclusion_percent"), item.get("synergy"), item.get("salt_score"),
                    canonical_json(item.get("raw", {})), canonical_json(item.get("metadata") or {}), now,
                ),
            )
            recommendation_key = (
                f"{commander_id or normalize_card_name(item.get('commander')) or ''}:"
                f"{raw_name}:{item.get('recommendation_type', 'recommendation')}"
            )
            conn.execute(
                """
                INSERT INTO provenance
                  (entity_type, entity_key, source_id, dataset_id, raw_name,
                   raw_hash, license, collected_at, raw_json, metadata_json)
                VALUES ('recommendation', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_key, source_id, dataset_id, raw_hash)
                DO UPDATE SET collected_at=excluded.collected_at, raw_json=excluded.raw_json
                """,
                (
                    recommendation_key, adapter.source_id, ds_id, raw_name,
                    sha256_json(item.get("raw", item)), license or adapter.license,
                    now, canonical_json(item.get("raw", item)),
                    canonical_json(item.get("metadata") or {}),
                ),
            )
            rec_count += 1
        for item in parsed.cooccurrence:
            if not isinstance(item, dict):
                continue
            a, _ = _name_from_entry(item.get("a") or item.get("card_a") or item.get("card1"))
            b, _ = _name_from_entry(item.get("b") or item.get("card_b") or item.get("card2"))
            if not a or not b or normalize_card_name(a).lower() == normalize_card_name(b).lower():
                continue
            a_id, _ = _resolve_card(conn, a)
            b_id, _ = _resolve_card(conn, b)
            conn.execute(
                """
                INSERT INTO cooccurrence
                  (source_id, dataset_id, card_id_a, card_id_b, card_name_a_raw,
                   card_name_b_raw, occurrence_count, score, relation_type, category,
                   inclusion_percent, synergy, raw_json, metadata_json, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, dataset_id, card_name_a_raw, card_name_b_raw)
                DO UPDATE SET card_id_a=excluded.card_id_a, card_id_b=excluded.card_id_b,
                  occurrence_count=excluded.occurrence_count, score=excluded.score,
                  relation_type=excluded.relation_type, category=excluded.category,
                  inclusion_percent=excluded.inclusion_percent, synergy=excluded.synergy,
                  raw_json=excluded.raw_json, metadata_json=excluded.metadata_json,
                  collected_at=excluded.collected_at
                """,
                (
                    adapter.source_id, ds_id, a_id, b_id, a, b,
                    _integer(item.get("occurrence_count", item.get("count", 1))) or 0,
                    item.get("score"), item.get("relation_type", "cooccurrence"),
                    item.get("category"), item.get("inclusion_percent"), item.get("synergy"),
                    canonical_json(item.get("raw", item)), canonical_json(item.get("metadata") or {}), now,
                ),
            )
            pair_key = f"{normalize_card_name(a)}|{normalize_card_name(b)}"
            conn.execute(
                """
                INSERT INTO provenance
                  (entity_type, entity_key, source_id, dataset_id, raw_name,
                   raw_hash, license, collected_at, raw_json, metadata_json)
                VALUES ('cooccurrence', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_type, entity_key, source_id, dataset_id, raw_hash)
                DO UPDATE SET collected_at=excluded.collected_at, raw_json=excluded.raw_json
                """,
                (
                    pair_key, adapter.source_id, ds_id, f"{a} / {b}",
                    sha256_json(item), license or adapter.license, now,
                    canonical_json(item),
                    canonical_json(item.get("metadata") or {}),
                ),
            )
            pair_count += 1
        set_meta(conn, f"dataset:{ds_id}:sha256", digest)
        set_meta(conn, f"dataset:{ds_id}:collected_at", now)
        conn.commit()
        return {
            "source": adapter.source_id,
            "dataset_id": ds_id,
            "decks": deck_count,
            "deck_cards": card_count,
            "recommendations": rec_count,
            "cooccurrence": pair_count,
            "sha256": digest,
            "dry_run": False,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def collect_json(
    adapter: JsonAdapter,
    *,
    db_path: str,
    url: str | None = None,
    fixture: str | None = None,
    cache_dir: str | None = None,
    timeout: float = 30,
    user_agent: str = DEFAULT_USER_AGENT,
    min_interval: float = 1.0,
    robots: bool = True,
    dataset_id: str | None = None,
    license: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    parsed_out: list[ParsedPayload] | None = None,
) -> dict:
    if bool(url) == bool(fixture):
        raise CollectionError("Provide exactly one of a public --url or local --fixture.")
    if fixture:
        payload, raw = load_fixture(fixture)
        cache_hit = False
    else:
        raw, cache_hit = fetch_cached(
            url, cache_dir=cache_dir, timeout=timeout, user_agent=user_agent,
            min_interval=min_interval, robots=robots,
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectionError(f"Configured source did not return JSON: {exc}") from exc
    parsed = adapter.parse(payload, external_id=dataset_id or "response")
    if limit is not None and limit >= 0:
        parsed = ParsedPayload(
            decks=parsed.decks[:limit],
            recommendations=parsed.recommendations[:limit],
            cooccurrence=parsed.cooccurrence[:limit],
        )
    if parsed_out is not None:
        parsed_out.append(parsed)
    result = ingest_parsed(
        parsed, adapter, raw, db_path=db_path, dataset_id=dataset_id, url=url,
        local_path=fixture, license=license, dry_run=dry_run,
    )
    result["cache_hit"] = cache_hit
    return result


def _url_with_query(url: str, **params: Any) -> str:
    """Add non-empty query parameters while preserving configured parameters."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        if value is not None and value != "":
            query[key] = [str(value)]
    return urllib.parse.urlunparse(
        parsed._replace(query=urllib.parse.urlencode(query, doseq=True))
    )


def _moxfield_public_id(deck: dict[str, Any]) -> str | None:
    """Return only an ID proven to identify the public deck URL.

    Moxfield search results contain both a short internal ``id`` and the
    public URL slug in ``publicId``.  The internal value is not a safe detail
    endpoint key, so an ``id``-only response is deliberately left
    unenriched.
    """
    for key in ("publicId", "public_id"):
        value = deck.get(key)
        if value:
            return str(value)
    for key in ("publicUrl", "public_url", "url"):
        value = deck.get(key)
        if not value:
            continue
        parsed = urllib.parse.urlparse(str(value))
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        try:
            decks_index = next(index for index, part in enumerate(parts) if part.casefold() == "decks")
        except StopIteration:
            continue
        if decks_index + 1 < len(parts) and parts[decks_index + 1]:
            return parts[decks_index + 1]
    return None


def _format_moxfield_detail_url(template: str, deck_id: str) -> str:
    """Support the established ``deck_id`` placeholder and useful aliases."""
    try:
        return template.format(deck_id=deck_id, public_id=deck_id, id=deck_id)
    except KeyError as exc:
        raise CollectionError(
            f"Moxfield detail URL template has unknown placeholder: {exc.args[0]}"
        ) from exc


def _is_http_not_found(error: CollectionError) -> bool:
    """Permit version fallback only for an explicit HTTP 404 response."""
    return bool(re.search(r"HTTP error fetching .*:\s*404(?:\s|$)", str(error)))


def _fetch_json(
    url: str,
    *,
    cache_dir: str | None,
    timeout: float,
    user_agent: str,
    min_interval: float,
    robots: bool,
    retries: int,
) -> tuple[Any, bytes, bool]:
    raw, cache_hit = fetch_cached(
        url,
        cache_dir=cache_dir,
        timeout=timeout,
        user_agent=user_agent,
        min_interval=min_interval,
        robots=robots,
        retries=retries,
    )
    try:
        return json.loads(raw.decode("utf-8")), raw, cache_hit
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"Configured source did not return JSON ({url}): {exc}") from exc


def _merge_parsed(target: ParsedPayload, value: ParsedPayload) -> None:
    """Merge records by source identity without losing the first raw record."""
    decks = {str(item.get("external_id")): item for item in target.decks}
    for deck in value.decks:
        key = str(deck.get("external_id") or sha256_json(deck))
        if key in decks:
            existing = decks[key]
            if deck.get("cards"):
                existing["cards"] = deck["cards"]
                if deck.get("raw") is not None:
                    existing["raw"] = deck["raw"]
            for field in ("name", "commander", "commander_source_id", "format", "url"):
                if deck.get(field) and not existing.get(field):
                    existing[field] = deck[field]
            if deck.get("metadata"):
                existing["metadata"] = {**existing.get("metadata", {}), **deck["metadata"]}
        else:
            decks[key] = deck
    target.decks = list(decks.values())
    target.recommendations.extend(value.recommendations)
    target.cooccurrence.extend(value.cooccurrence)


def _set_recommendation_context(parsed: ParsedPayload, commander: str | None) -> None:
    if not commander:
        return
    for item in parsed.recommendations:
        if not item.get("commander"):
            item["commander"] = commander
    for deck in parsed.decks:
        if not deck.get("commander"):
            deck["commander"] = commander
    existing_pairs = {
        EDHRECAdapter._pair_key(item.get("a"), item.get("b"))
        for item in parsed.cooccurrence
    }
    for item in parsed.recommendations:
        pair = EDHRECAdapter._pair_key(commander, item.get("name"))
        if not pair or pair in existing_pairs:
            continue
        parsed.cooccurrence.append(
            {
                "a": commander,
                "b": item["name"],
                "occurrence_count": item.get("inclusion_count") or 0,
                "score": item.get("score"),
                "relation_type": "commander_recommendation",
                "category": item.get("category"),
                "inclusion_percent": item.get("inclusion_percent"),
                "synergy": item.get("synergy"),
                "raw": item.get("raw", item),
            }
        )
        existing_pairs.add(pair)


def _slugify_edhrec(value: str) -> str:
    """Build the conservative slug used by EDHREC's public static pages."""
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def collect_moxfield_public(
    *,
    db_path: str,
    search_url: str = MOXFIELD_SEARCH_URL,
    deck_url_template: str = MOXFIELD_DECK_URL,
    deck_url_fallback_template: str | None = MOXFIELD_DECK_FALLBACK_URL,
    cache_dir: str | None = None,
    timeout: float = 30,
    user_agent: str = DEFAULT_USER_AGENT,
    min_interval: float = 1.0,
    robots: bool = True,
    page_size: int = 100,
    max_pages: int | None = None,
    max_decks: int | None = None,
    query: str | None = None,
    format_name: str | None = "commander",
    sort_type: str = "Updated",
    sort_direction: str = "Descending",
    fetch_details: bool = True,
    dataset_id: str | None = None,
    license: str | None = None,
    dry_run: bool = False,
    retries: int = DEFAULT_RETRIES,
    parsed_out: list[ParsedPayload] | None = None,
) -> dict:
    """Collect public Moxfield listings and, when possible, their full decks.

    The API used by Moxfield is undocumented and may disappear.  The search
    endpoint is deliberately configurable, and a failed detail request does
    not discard the public summary already collected.
    """
    if page_size < 1 or page_size > 100:
        raise CollectionError("Moxfield page_size must be between 1 and 100.")
    if max_pages is not None and max_pages < 1:
        raise CollectionError("max_pages must be positive.")
    if max_decks is not None and max_decks < 0:
        raise CollectionError("max_decks must be non-negative.")
    adapter = MoxfieldAdapter()
    aggregate = ParsedPayload([], [], [])
    raw_pages: list[Any] = []
    raw_details: list[Any] = []
    cache_hits = 0
    detail_errors: list[str] = []
    detail_requests = 0
    detail_successes = 0
    detail_fallback_attempts = 0
    detail_templates = list(
        dict.fromkeys(
            template
            for template in (deck_url_template, deck_url_fallback_template)
            if template
        )
    )
    page = 1
    total_pages: int | None = None
    while max_pages is None or page <= max_pages:
        page_url = _url_with_query(
            search_url,
            pageNumber=page,
            pageSize=page_size,
            sortType=sort_type,
            sortDirection=sort_direction,
            q=query,
            fmt=format_name,
        )
        payload, raw, cache_hit = _fetch_json(
            page_url,
            cache_dir=cache_dir,
            timeout=timeout,
            user_agent=user_agent,
            min_interval=min_interval,
            robots=robots,
            retries=retries,
        )
        cache_hits += int(cache_hit)
        raw_pages.append({"url": page_url, "payload": payload})
        page_parsed = adapter.parse(payload, external_id=f"search:{page}")
        _merge_parsed(aggregate, page_parsed)
        if isinstance(payload, dict):
            total_pages = payload.get("totalPages") or payload.get("total_pages")
            try:
                total_pages = int(total_pages) if total_pages is not None else None
            except (TypeError, ValueError):
                total_pages = None
            page_items = payload.get("data")
            if not isinstance(page_items, list):
                page_items = payload.get("decks")
        else:
            page_items = None
        if not page_items or (total_pages is not None and page >= total_pages):
            break
        if max_decks is not None and len(aggregate.decks) >= max_decks:
            break
        page += 1
    if max_decks is not None:
        aggregate.decks = aggregate.decks[:max_decks]
    summary_count = len(aggregate.decks)
    summaries_without_public_id = sum(
        1
        for deck in aggregate.decks
        if not deck.get("external_id")
        or str(deck["external_id"]).startswith("search:")
    )
    if fetch_details:
        for deck in aggregate.decks:
            deck_id = str(deck.get("external_id") or "")
            if not deck_id or ":" in deck_id and deck_id.startswith("search:"):
                continue
            encoded_id = urllib.parse.quote(deck_id, safe="")
            templates = list(dict.fromkeys(detail_templates))
            errors_for_deck = []
            for template_index, template in enumerate(templates):
                detail_url = _format_moxfield_detail_url(template, encoded_id)
                detail_requests += 1
                if template_index > 0:
                    detail_fallback_attempts += 1
                try:
                    payload, _, cache_hit = _fetch_json(
                        detail_url,
                        cache_dir=cache_dir,
                        timeout=timeout,
                        user_agent=user_agent,
                        min_interval=min_interval,
                        robots=robots,
                        retries=retries,
                    )
                    cache_hits += int(cache_hit)
                    raw_details.append({"url": detail_url, "payload": payload})
                    details = adapter.parse(payload, external_id=deck_id)
                    if details.decks:
                        detail = details.decks[0]
                        detail["external_id"] = deck_id
                        _merge_parsed(aggregate, ParsedPayload([detail], [], []))
                    if details.decks and details.decks[0].get("cards"):
                        detail_successes += 1
                        errors_for_deck = []
                        break
                    if template_index + 1 == len(templates):
                        break
                except CollectionError as exc:
                    errors_for_deck.append(f"{detail_url}: {exc}")
                    if template_index + 1 < len(templates) and _is_http_not_found(exc):
                        continue
                    break
            if errors_for_deck:
                detail_errors.append(f"{deck_id}: {'; '.join(errors_for_deck)}")
    raw = canonical_json({"search_pages": raw_pages, "deck_details": raw_details}).encode("utf-8")
    result = ingest_parsed(
        aggregate,
        adapter,
        raw,
        db_path=db_path,
        dataset_type="moxfield_public_listing",
        dataset_id=dataset_id,
        url=search_url,
        license=license,
        dry_run=dry_run,
        dataset_metadata={
            "page_size": page_size,
            "pages": len(raw_pages),
            "total_pages": total_pages,
            "query": query,
            "format": format_name,
            "details_requested": fetch_details,
            "detail_url_templates": detail_templates if fetch_details else [],
            "summary_count": summary_count,
            "summaries_without_public_id": summaries_without_public_id,
            "detail_successes": detail_successes,
            "detail_fallback_attempts": detail_fallback_attempts,
            "detail_errors": detail_errors,
        },
    )
    result.update(
        {
            "pages": len(raw_pages),
            "requests": len(raw_pages) + detail_requests,
            "cache_hits": cache_hits,
            "summary_count": summary_count,
            "summaries_without_public_id": summaries_without_public_id,
            "detail_successes": detail_successes,
            "detail_fallback_attempts": detail_fallback_attempts,
            "detail_errors": detail_errors,
            "truncated": bool(
                max_pages is not None and total_pages is not None and len(raw_pages) < total_pages
            ),
        }
    )
    if parsed_out is not None:
        parsed_out.append(aggregate)
    return result


def collect_edhrec_public(
    *,
    db_path: str,
    listing_url: str = EDHREC_COMMANDERS_URL,
    top_url_template: str = EDHREC_TOP_COMMANDERS_URL,
    commander_url_template: str = EDHREC_JSON_BASE_URL + "/commanders/{slug}.json",
    cache_dir: str | None = None,
    timeout: float = 30,
    user_agent: str = DEFAULT_USER_AGENT,
    min_interval: float = 1.0,
    robots: bool = True,
    max_pages: int = 1,
    max_commanders: int | None = 50,
    fetch_commander_pages: bool = True,
    dataset_id: str | None = None,
    license: str | None = None,
    dry_run: bool = False,
    retries: int = DEFAULT_RETRIES,
    parsed_out: list[ParsedPayload] | None = None,
) -> dict:
    """Collect EDHREC's public general listings and optional commander pages.

    No commander is required.  The listing/top pages provide a useful global
    prior; commander pages are then discovered from that listing and bounded
    by ``max_commanders`` to avoid an accidental unbounded crawl.
    """
    if max_pages < 1:
        raise CollectionError("max_pages must be positive.")
    if max_commanders is not None and max_commanders < 0:
        raise CollectionError("max_commanders must be non-negative.")
    adapter = EDHRECAdapter()
    aggregate = ParsedPayload([], [], [])
    raw_pages: list[Any] = []
    raw_commanders: list[Any] = []
    errors: list[str] = []
    cache_hits = 0
    commander_names: list[str] = []
    # The commanders index is the broadest source.  Older deployments may not
    # expose it, so fall back to the stable top/commanders page.
    for url in (listing_url,):
        try:
            payload, _, cache_hit = _fetch_json(
                url, cache_dir=cache_dir, timeout=timeout, user_agent=user_agent,
                min_interval=min_interval, robots=robots, retries=retries,
            )
            cache_hits += int(cache_hit)
            raw_pages.append({"url": url, "payload": payload})
            parsed = adapter.parse(payload, external_id="commanders")
            _merge_parsed(aggregate, parsed)
            commander_names.extend(item["name"] for item in parsed.recommendations if item.get("name"))
        except CollectionError as exc:
            errors.append(f"listing: {exc}")
    for page in range(1, max_pages + 1):
        url = top_url_template.format(page=page)
        try:
            payload, _, cache_hit = _fetch_json(
                url, cache_dir=cache_dir, timeout=timeout, user_agent=user_agent,
                min_interval=min_interval, robots=robots, retries=retries,
            )
            cache_hits += int(cache_hit)
            raw_pages.append({"url": url, "payload": payload})
            parsed = adapter.parse(payload, external_id=f"top:{page}")
            _merge_parsed(aggregate, parsed)
            commander_names.extend(item["name"] for item in parsed.recommendations if item.get("name"))
        except CollectionError as exc:
            errors.append(f"top page {page}: {exc}")
            if page == 1 and not raw_pages:
                raise
            break
    unique_commanders = list(dict.fromkeys(name for name in commander_names if name))
    if max_commanders is not None:
        unique_commanders = unique_commanders[:max_commanders]
    if fetch_commander_pages:
        for commander in unique_commanders:
            slug = _slugify_edhrec(commander)
            if not slug:
                continue
            url = commander_url_template.format(slug=urllib.parse.quote(slug, safe=""))
            try:
                payload, _, cache_hit = _fetch_json(
                    url, cache_dir=cache_dir, timeout=timeout, user_agent=user_agent,
                    min_interval=min_interval, robots=robots, retries=retries,
                )
                cache_hits += int(cache_hit)
                raw_commanders.append({"url": url, "commander": commander, "payload": payload})
                parsed = adapter.parse(payload, external_id=f"commander:{slug}")
                _set_recommendation_context(parsed, commander)
                _merge_parsed(aggregate, parsed)
            except CollectionError as exc:
                errors.append(f"{commander}: {exc}")
    if not raw_pages and not raw_commanders:
        raise CollectionError("EDHREC public collection returned no readable pages.")
    raw = canonical_json({"listing_pages": raw_pages, "commander_pages": raw_commanders}).encode("utf-8")
    result = ingest_parsed(
        aggregate,
        adapter,
        raw,
        db_path=db_path,
        dataset_type="edhrec_public_listing",
        dataset_id=dataset_id,
        url=listing_url,
        license=license,
        dry_run=dry_run,
        dataset_metadata={
            "pages": len(raw_pages),
            "commanders_requested": len(unique_commanders),
            "commander_pages": len(raw_commanders),
            "errors": errors,
        },
    )
    result.update(
        {
            "pages": len(raw_pages),
            "commander_pages": len(raw_commanders),
            "commanders_requested": len(unique_commanders),
            "requests": len(raw_pages) + len(raw_commanders),
            "cache_hits": cache_hits,
            "errors": errors,
            "truncated": max_commanders is not None and len(commander_names) > len(unique_commanders),
        }
    )
    if parsed_out is not None:
        parsed_out.append(aggregate)
    return result


def _stable_embedding(text: str, dimensions: int = 32) -> list[float]:
    """Small deterministic vectors for source records (not the card RAG index)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = []
    for index in range(dimensions):
        values.append((digest[index % len(digest)] / 127.5) - 1.0)
    return values


def persist_source_chroma(
    parsed: ParsedPayload,
    adapter: JsonAdapter,
    *,
    chroma_path: str,
    dataset_id: str,
    rebuild: bool = False,
) -> dict:
    """Persist external deck/recommendation documents in additive collections.

    The existing ``oracle_cards`` collection is intentionally not touched:
    its MiniLM dimensionality and ``type_oracle_v1`` format are owned by
    vectorize_cards.py.  These source collections use explicit deterministic
    vectors and therefore work in offline tests without downloading a model.
    """
    try:
        import chromadb
    except ImportError as exc:
        raise CollectionError("Chroma persistence requires the chromadb package.") from exc
    os.makedirs(chroma_path, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_path)
    records = []
    for deck in parsed.decks:
        external_id = str(deck.get("external_id") or sha256_json(deck)[:24])
        record_id = f"{adapter.source_id}:{external_id}"
        records.append(
            (
                "external_decks",
                record_id,
                f"{deck.get('name', external_id)}. Commander: {deck.get('commander') or 'unknown'}",
                {"source_id": adapter.source_id, "dataset_id": dataset_id, "name": str(deck.get("name") or external_id)},
            )
        )
    for item in parsed.recommendations:
        commander = normalize_card_name(item.get("commander"))
        category = str(item.get("category") or item.get("recommendation_type") or "recommendation")
        record_id = (
            f"{adapter.source_id}:{dataset_id}:recommendation:{commander}:"
            f"{category}:{normalize_card_name(item.get('name'))}"
        )
        metadata = {
            "source_id": adapter.source_id,
            "dataset_id": dataset_id,
            "name": str(item.get("name") or ""),
            "card_id": str(item.get("source_id") or ""),
            "category": category,
        }
        if commander:
            metadata["commander"] = commander
        for key in (
            "rank", "inclusion_count", "potential_decks", "inclusion_percent",
            "synergy", "salt_score",
        ):
            if item.get(key) is not None:
                metadata[key] = item[key]
        records.append(
            (
                "recommendations",
                record_id,
                (
                    f"{item.get('name') or ''} "
                    f"(category={category}, synergy={item.get('synergy')}, "
                    f"inclusion={item.get('inclusion_percent')}%)"
                ),
                metadata,
            )
        )
    for index, item in enumerate(parsed.cooccurrence):
        if not isinstance(item, dict):
            continue
        left, _ = _name_from_entry(item.get("a") or item.get("card_a") or item.get("card1"))
        right, _ = _name_from_entry(item.get("b") or item.get("card_b") or item.get("card2"))
        if not left or not right:
            continue
        record_id = (
            f"{adapter.source_id}:{dataset_id}:cooccurrence:"
            f"{normalize_card_name(left)}:{normalize_card_name(right)}"
        )
        metadata = {
            "source_id": adapter.source_id,
            "dataset_id": dataset_id,
            "name": f"{left} <> {right}",
            "relation_type": str(item.get("relation_type") or "cooccurrence"),
        }
        for key in ("category", "occurrence_count", "inclusion_percent", "synergy"):
            if item.get(key) is not None:
                metadata[key] = item[key]
        records.append(
            (
                "cooccurrence",
                record_id,
                f"{left} <> {right}",
                metadata,
            )
        )
    grouped: dict[str, list[tuple]] = {}
    for record in records:
        grouped.setdefault(record[0], []).append(record)
    written = 0
    for collection_name, items in grouped.items():
        if rebuild:
            try:
                client.delete_collection(collection_name)
            except Exception:
                pass
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"description": f"ManaGraph {collection_name} source records", "source": adapter.source_id},
        )
        collection.upsert(
            ids=[item[1] for item in items],
            documents=[item[2] for item in items],
            metadatas=[item[3] for item in items],
            embeddings=[_stable_embedding(item[2]) for item in items],
        )
        written += len(items)
    return {"collections": sorted(grouped), "documents": written, "chroma_path": chroma_path}


def ingest_oracle_lines(
    lines: Iterable[str],
    *,
    db_path: str,
    raw_sha256: str | None = None,
    raw_size: int | None = None,
    dataset_id: str = "scryfall:oracle_cards",
    source_url: str | None = None,
    local_path: str | None = None,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """Import Oracle JSONL while preserving the legacy ``cards`` shape."""
    if dry_run:
        count = 0
        for line in lines:
            if not line.strip():
                continue
            card = json.loads(line)
            if not card.get("id") or not str(card.get("name") or "").strip():
                raise CollectionError("Oracle record is missing required id/name.")
            count += 1
        return {"source": "scryfall", "dataset_id": dataset_id, "cards": count, "dry_run": True}
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        now = utc_now()
        if not dry_run:
            conn.execute(
                """
                INSERT INTO sources
                  (source_id, name, adapter, base_url, license, created_at, updated_at)
                VALUES ('scryfall', 'Scryfall Oracle catalog', 'ScryfallBulkAdapter',
                        'https://scryfall.com/docs/api/bulk-data', NULL, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (now, now),
            )
            conn.execute(
                """
                INSERT INTO datasets
              (dataset_id, source_id, dataset_type, url, local_path, sha256,
               byte_size, fetched_at, metadata_json)
                VALUES (?, 'scryfall', 'oracle_cards', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_id) DO UPDATE SET
                  url=excluded.url, local_path=excluded.local_path,
                  sha256=excluded.sha256, byte_size=excluded.byte_size,
                  fetched_at=excluded.fetched_at, metadata_json=excluded.metadata_json
                """,
                (
                    dataset_id, source_url, local_path, raw_sha256, raw_size, now,
                    canonical_json(metadata or {}),
                ),
            )
        rows = []
        count = 0
        for line in lines:
            if not line.strip():
                continue
            card = json.loads(line)
            card_id = card.get("id")
            name = str(card.get("name") or "").strip()
            if not card_id or not name:
                raise CollectionError("Oracle record is missing required id/name.")
            prices = card.get("prices") or {}
            rows.append(
                (
                    card_id, name, mana_cost_from_scryfall(card), card.get("cmc", 0.0),
                    oracle_text_from_scryfall(card), json.dumps(card.get("color_identity", [])),
                    card.get("type_line", ""), json.dumps(card.get("legalities", {})),
                    parse_price(prices.get("usd")), parse_price(prices.get("eur")),
                    keywords_from_scryfall(card),
                )
            )
            count += 1
            if len(rows) >= 2000:
                conn.executemany(
                    """INSERT OR REPLACE INTO cards
                    (id, name, mana_cost, cmc, oracle_text, color_identity, type_line,
                     legalities, price_usd, price_eur, keywords)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                rows = []
        if rows:
            conn.executemany(
                """INSERT OR REPLACE INTO cards
                (id, name, mana_cost, cmc, oracle_text, color_identity, type_line,
                 legalities, price_usd, price_eur, keywords)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
        set_meta(conn, "price_snapshot_at", utc_now())
        set_meta(conn, "scryfall_bulk_type", "oracle_cards")
        set_meta(conn, "card_count", str(count))
        if raw_sha256:
            set_meta(conn, f"dataset:{dataset_id}:sha256", raw_sha256)
        if raw_size is not None:
            set_meta(conn, f"dataset:{dataset_id}:byte_size", str(raw_size))
        conn.commit()
        return {"source": "scryfall", "dataset_id": dataset_id, "cards": count, "dry_run": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
