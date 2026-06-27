"""
bible/services/apibible.py

Client for the API.Bible service (https://scripture.api.bible).
Free tier: 5,000 requests/day, no cost.
Hosts NIV, NLT, AMP, NKJV, CSB, GNB, MSG, CEV, CEB, ICB, GW, ERV, PHILLIPS and more.

Setup:
  1. Register free at https://scripture.api.bible
  2. Create an app → copy the API key
  3. Set APIBIBLE_API_KEY=<key> in your .env
  4. On first request the service will fetch and cache all English Bible IDs automatically.

How it works:
  API.Bible uses opaque UUID-style Bible IDs (e.g. "06125adad2d5898a-01"), NOT short-codes.
  We never hardcode these UUIDs because they can differ between API accounts and may change.
  Instead we call GET /bibles?language=eng once and map abbreviation → bible_id in the DB.
  All subsequent lookups are served from the DB (zero extra API calls).
"""

import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://rest.api.bible/v1"

# Human-readable names for well-known abbreviations returned by the API.
# Only used for display — not for routing.
_DISPLAY_NAMES: dict[str, str] = {
    "NIV": "New International Version (NIV)",
    "NLT": "New Living Translation (NLT)",
    "AMP": "Amplified Bible (AMP)",
    "AMPC": "Amplified Bible Classic Edition (AMPC)",
    "NKJV": "New King James Version (NKJV)",
    "MSG": "The Message (MSG)",
    "CSB": "Christian Standard Bible (CSB)",
    "GNB": "Good News Bible (GNB)",
    "GNT": "Good News Translation (GNT)",
    "CEV": "Contemporary English Version (CEV)",
    "CEB": "Common English Bible (CEB)",
    "NCV": "New Century Version (NCV)",
    "HCSB": "Holman Christian Standard Bible (HCSB)",
    "GW": "God's Word Translation (GW)",
    "ERV": "Easy-to-Read Version (ERV)",
    "ICB": "International Children's Bible (ICB)",
    "ISV": "International Standard Version (ISV)",
    "NOG": "Names of God Bible (NOG)",
    "TPT": "The Passion Translation (TPT)",
    "TLB": "The Living Bible (TLB)",
    "VOICE": "The Voice Bible (VOICE)",
    "CJB": "Complete Jewish Bible (CJB)",
    "EHV": "Evangelical Heritage Version (EHV)",
    "WE": "Worldwide English NT (WE)",
    "PHILLIPS": "The New Testament in Modern English (Phillips)",
}


def _api_key() -> str:
    return getattr(settings, "APIBIBLE_API_KEY", "") or ""


def _headers() -> dict:
    key = _api_key()
    if not key:
        raise ValueError("APIBIBLE_API_KEY not set in settings / .env")
    # API.Bible docs specify 'api-key' header for authentication.
    # Content-Type is not required for GET requests.
    return {"api-key": key}


def _get(path: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(
            BASE_URL + path,
            headers=_headers(),
            params=params or {},
            timeout=15,
        )
        if resp.status_code == 401:
            logger.warning(
                "apibible: 401 Unauthorised — APIBIBLE_API_KEY may be wrong or inactive. "
                "Key used: %s… | Verify at scripture.api.bible → My Apps",
                (_api_key() or "")[:8],
            )
            return None
        if resp.status_code == 403:
            logger.error(
                "apibible: 403 Forbidden for %s — this Bible may not be licensed to your API app. "
                "Log into scripture.api.bible and enable the Bible under your app's settings.",
                path,
            )
            return None
        resp.raise_for_status()
        return resp.json()
    except ValueError as exc:
        logger.warning("apibible: config error — %s", exc)
    except requests.exceptions.Timeout:
        logger.warning("apibible: timeout on %s", path)
    except requests.exceptions.RequestException as exc:
        logger.warning("apibible: request failed %s — %s", path, exc)
    except Exception as exc:
        logger.error("apibible: unexpected error %s — %s", path, exc)
    return None


def is_configured() -> bool:
    """Return True if APIBIBLE_API_KEY is set in settings."""
    return bool(_api_key())


# ── Translation ID resolution ─────────────────────────────────────────────────


def get_bible_id(short_code: str) -> str | None:
    """
    Return the API.Bible UUID for a translation short-code (e.g. 'NIV').
    Looks up the DB cache populated by get_translations().
    Returns None if not found (no hardcoded UUIDs — they differ per account).
    """
    code = short_code.upper().strip()
    try:
        from bible.models import ApiBibleTranslationMap

        row = ApiBibleTranslationMap.objects.filter(short_code=code).first()
        if row:
            return row.bible_id
    except Exception as exc:
        logger.debug("apibible: DB lookup failed for %s — %s", code, exc)
    return None


def get_translations(force_refresh: bool = False) -> list[dict]:
    """
    Fetch all English Bibles from API.Bible and cache them in the DB.
    Returns [{id, api_id, name, language, source}].

    Call this once on setup (or let translations_list trigger it automatically).
    After this, get_bible_id() works for any translation the API returns.
    """
    if not is_configured():
        return []

    # Serve from DB unless forced
    if not force_refresh:
        try:
            from bible.models import ApiBibleTranslationMap

            cached = list(ApiBibleTranslationMap.objects.all())
            if cached:
                return [
                    {
                        "id": row.short_code,
                        "api_id": row.bible_id,
                        "name": row.name
                        or _DISPLAY_NAMES.get(row.short_code, row.short_code),
                        "language": "eng",
                        "source": "apibible",
                    }
                    for row in cached
                ]
        except Exception:
            pass

    data = _get("/bibles", {"language": "eng"})
    if not data:
        return []

    results = []
    for b in data.get("data", []):
        api_id = b.get("id", "").strip()
        if not api_id:
            continue

        # Build the short code from the abbreviation the API returns
        abbr = (
            (
                b.get("abbreviationLocal")
                or b.get("abbreviation")
                or b.get("nameLocal")
                or b.get("name")
                or api_id[:8]
            )
            .upper()
            .strip()
        )

        # Clean up: strip spaces, slashes, take first word if multi-word
        abbr = re.split(r"[\s/\-]", abbr)[0]
        if not abbr:
            continue

        name = b.get("nameLocal") or b.get("name") or abbr
        # Use our nicer display name if we have one
        display = _DISPLAY_NAMES.get(abbr, name)

        results.append(
            {
                "id": abbr,
                "api_id": api_id,
                "name": display,
                "language": "eng",
                "source": "apibible",
            }
        )

    if results:
        _sync_to_db(results)

    return results


def _sync_to_db(translations: list[dict]) -> None:
    """Persist api_id mappings to DB so get_bible_id() works without network calls."""
    try:
        from bible.models import ApiBibleTranslationMap

        for t in translations:
            if t.get("api_id") and t.get("id"):
                ApiBibleTranslationMap.objects.update_or_create(
                    short_code=t["id"],
                    defaults={
                        "bible_id": t["api_id"],
                        "name": t.get("name", ""),
                    },
                )
        logger.info("apibible: synced %d translations to DB", len(translations))
    except Exception as exc:
        logger.warning("apibible: DB sync failed — %s", exc)


# ── Book & Chapter fetching ───────────────────────────────────────────────────


def _usfm_to_apibible(book_id: str) -> str:
    """
    API.Bible book IDs are USFM-compatible for most books.
    A few OT books differ — mapping provided for completeness.
    """
    # Known differences between common USFM and API.Bible
    _map = {
        "SOS": "SNG",  # Song of Solomon → Song of Songs
        "SOL": "SNG",
    }
    return _map.get(book_id.upper(), book_id.upper())


def get_chapter(book_id: str, chapter: int, translation: str) -> dict:
    """
    Fetch a chapter from API.Bible.
    Returns {book_name, chapter, verses: [{number, text}]}.
    Returns empty verses on failure — never raises.
    """
    empty = {"book_name": book_id, "chapter": chapter, "verses": []}

    if not is_configured():
        logger.info("apibible: get_chapter called but APIBIBLE_API_KEY not set")
        return empty

    # Ensure translations are cached so get_bible_id works
    bible_id = get_bible_id(translation)
    if not bible_id:
        # Trigger a translations fetch to populate the DB
        logger.info("apibible: no bible_id for %s — fetching translations", translation)
        get_translations()
        bible_id = get_bible_id(translation)

    if not bible_id:
        logger.warning("apibible: translation %s not found in API.Bible", translation)
        return empty

    api_book = _usfm_to_apibible(book_id)
    chapter_id = f"{api_book}.{chapter}"

    data = _get(
        f"/bibles/{bible_id}/chapters/{chapter_id}",
        {
            "content-type": "json",
            "include-notes": "false",
            "include-titles": "true",
            "include-chapter-numbers": "false",
            "include-verse-numbers": "true",
            "include-verse-spans": "false",
        },
    )

    if not data:
        return empty

    chapter_data = data.get("data", {})
    # book_name from the chapter response
    ref_obj = (chapter_data.get("reference") or "").strip()

    if ref_obj:
        # Remove trailing chapter number only:
        # "John 3" -> "John"
        # "1 Chronicles 12" -> "1 Chronicles"
        # "Song of Songs 2" -> "Song of Songs"
        book_name = re.sub(r"\s+\d+\s*$", "", ref_obj).strip()
    else:
        book_name = book_id.title()

    content = chapter_data.get("content", [])
    verses = _parse_content(content)

    if not verses:
        logger.warning(
            "apibible: parsed 0 verses for %s %s %s — raw content[:2]: %s",
            translation,
            book_id,
            chapter,
            content[:2],
        )

    return {"book_name": book_name, "chapter": chapter, "verses": verses}


def _parse_content(content: list) -> list[dict]:
    """
    Parse API.Bible's content-type=json nested structure into flat verse list.

    API.Bible returns TWO distinct shapes depending on the Bible/version.
    We handle both:

    Shape A — "verse start/end" siblings (most common):
      Each para's items list contains verse-start markers, plain text nodes,
      and verse-end markers as siblings at the same level.
      Text belongs to the most recently seen verse-start.

      {"type": "tag", "name": "para", "items": [
        {"type": "tag", "name": "verse start", "attrs": {"number": "1", "sid": "JHN 3:1"}, "items": []},
        {"type": "text", "text": "Now there was a man..."},
        {"type": "tag", "name": "char", "items": [{"type": "text", "text": "more text"}]},
        {"type": "tag", "name": "verse end", "attrs": {"eid": "JHN 3:1"}, "items": []},
        ...
      ]}

    Shape B — "verse" wrapper with children (older/some translations):
      {"type": "tag", "name": "verse", "attrs": {"number": "1"}, "items": [
        {"type": "text", "text": "In the beginning..."},
      ]}

    Verses can be split across multiple para nodes; we merge by verse number.
    """
    verses: dict[int, list[str]] = {}

    def _num_from_attrs(attrs: dict) -> int | None:
        """Extract verse number from attrs.number or attrs.sid ('JHN 3:16' → 16)."""
        num_s = attrs.get("number") or ""
        if not num_s:
            sid = attrs.get("sid", "")
            num_s = sid.split(":")[-1] if ":" in sid else ""
        num_s = num_s.strip()
        return int(num_s) if num_s.isdigit() else None

    def _walk_items(items: list, current_verse: list) -> None:
        """
        Walk a flat items list, tracking the active verse number via current_verse[0].
        current_verse is a 1-element list used as a mutable cell (avoids nonlocal).
        """
        for item in items:
            if not isinstance(item, dict):
                if isinstance(item, str) and current_verse[0] is not None:
                    verses.setdefault(current_verse[0], []).append(item)
                continue

            kind = item.get("type", "")
            tag = item.get("name", "")

            # ── Shape A: verse start marker ──────────────────────────────
            if kind == "tag" and tag == "verse start":
                attrs = item.get("attrs") or {}
                num = _num_from_attrs(attrs)
                if num is not None:
                    current_verse[0] = num
                continue

            # ── Shape A: verse end marker ────────────────────────────────
            if kind == "tag" and tag == "verse end":
                current_verse[0] = None
                continue

            # ── Shape B: verse wrapper ───────────────────────────────────
            # Many licensed translations (NIV, NLT…) use this shape.  The
            # "verse" tag's own items contain ONLY the verse-number label
            # (e.g. [{"type":"text","text":"1"}]); the actual verse text
            # follows as SIBLING nodes in the same para.items list.
            # Fix: set current_verse[0] so the sibling text is captured;
            # don't collect text from inside the wrapper (just a label).
            if kind == "tag" and tag == "verse":
                attrs = item.get("attrs") or {}
                num = _num_from_attrs(attrs)
                if num is not None:
                    current_verse[0] = num
                continue  # skip wrapper's own children (just the number label)

            # ── Plain text node ──────────────────────────────────────────
            if kind == "text":
                if current_verse[0] is not None:
                    t = item.get("text", "")
                    if t:
                        verses.setdefault(current_verse[0], []).append(t)
                continue

            # ── Skip footnotes, cross-refs, inline verse-number tags ─────
            # "v" is API.Bible's inline verse-number marker injected when
            # include-verse-numbers=true.  Its text is just "1", "2" etc —
            # capturing it would corrupt the verse text.
            if kind == "tag" and tag in ("note", "v"):
                continue

            # ── Recurse into char, wj, nd, and other inline tags ─────────
            if kind == "tag":
                child_items = item.get("items", [])
                if child_items:
                    _walk_items(child_items, current_verse)

    def _walk_node(node):
        """Top-level walker for para / section / other block tags."""
        if not isinstance(node, dict):
            return
        kind = node.get("type", "")
        tag = node.get("name", "")

        if kind == "tag" and tag == "verse":
            # Shape B at top level: treat as a para whose items carry verse content.
            # Set current_verse and walk items so text siblings are captured.
            attrs = node.get("attrs") or {}
            num = _num_from_attrs(attrs)
            if num is not None:
                _walk_items(node.get("items", []), [num])
            return

        items = node.get("items", [])
        if items:
            _walk_items(items, [None])  # fresh verse tracker per block

    for node in content:
        _walk_node(node)

    result = []
    for num, parts in verses.items():
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        if text:
            result.append({"number": num, "text": text})

    result.sort(key=lambda v: v["number"])
    return result


def _collect_text(items: list) -> str:
    """Recursively collect plain text from API.Bible content items."""
    parts = []
    for item in items:
        if not isinstance(item, dict):
            if isinstance(item, str):
                parts.append(item)
            continue
        kind = item.get("type", "")
        if kind == "text":
            parts.append(item.get("text", ""))
        elif kind == "tag":
            tag = item.get("name", "")
            # Skip note tags (footnotes, cross-references)
            if tag in ("note", "char") and (item.get("attrs") or {}).get(
                "style", ""
            ) in ("f", "x", "fr", "ft", "xo", "xt"):
                continue
            parts.append(_collect_text(item.get("items", [])))
        else:
            parts.append(_collect_text(item.get("items", [])))
    return " ".join(p for p in parts if p.strip()).strip()


# ── Management helper ─────────────────────────────────────────────────────────


def sync_translations() -> tuple[int, list[str]]:
    """
    Fetch and cache all English API.Bible translations.
    Returns (count, [name, ...]) — call from a management command or shell.

    Usage:
        from bible.services.apibible import sync_translations
        count, names = sync_translations()
        print(f"Synced {count} translations: {names[:5]}")
    """
    results = get_translations(force_refresh=True)
    return len(results), [t["name"] for t in results]
