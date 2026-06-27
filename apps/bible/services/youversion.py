"""
bible/services/youversion.py

Client for the YouVersion Platform Bible API.
https://developers.youversion.com/

Auth: X-YVP-App-Key header on https://api.youversion.com/v1

Chapter fetching:
  Uses GET /bibles/{bible_id}/books/{book_id}/chapters/{chapter}/verses  (index)
  then  GET /bibles/{bible_id}/passages/{passage_id}                     (text)

  The single-request /chapters/{usfm} bulk endpoint is NOT available on the
  v1 API — the 404 on /bibles/3034/chapters/DEU.1 confirmed this. We batch
  the per-verse passage calls sequentially but only for verses not already
  cached, making it dramatically faster on warm cache hits.

Translation short-code derivation:
  We use YouVersion's own `localized_abbreviation` field verbatim (e.g. "BSB",
  "NIV"). We do NOT manufacture a code from the title — if the field is absent
  we skip that entry. This prevents BSB or other codes appearing in the
  YouVersionTranslationMap when YouVersion does not actually serve them.
"""

import logging
import re
import time

import requests
from django.conf import settings

from bible.services.analytics import log_provider_request

logger = logging.getLogger(__name__)

BASE_URL       = "https://api.youversion.com/v1"
CACHE_TTL_DAYS = 30

# ── Circuit breaker: skip network calls for 60 s after a DNS/connect failure ──
_UNREACHABLE_UNTIL: float = 0.0   # epoch seconds; 0 = not tripped
_CIRCUIT_OPEN_SECS: int   = 60    # how long to stay open after a failure


def _circuit_open() -> bool:
    """Return True if YouVersion was recently unreachable and we should skip it."""
    return time.monotonic() < _UNREACHABLE_UNTIL


def _trip_circuit() -> None:
    """Mark YouVersion unreachable for the next _CIRCUIT_OPEN_SECS seconds."""
    global _UNREACHABLE_UNTIL
    _UNREACHABLE_UNTIL = time.monotonic() + _CIRCUIT_OPEN_SECS
    logger.warning(
        "youversion: circuit breaker tripped — skipping for %ds", _CIRCUIT_OPEN_SECS
    )


def _app_key() -> str:
    return getattr(settings, "YOUVERSION_APP_KEY", "") or ""


def is_configured() -> bool:
    return bool(_app_key())


def _headers() -> dict:
    key = _app_key()
    if not key:
        raise ValueError("YOUVERSION_APP_KEY not set in settings / .env")
    return {"X-YVP-App-Key": key, "Accept": "application/json"}


def _rate_limit(resp) -> dict:
    return {
        "limit":     resp.headers.get("X-RateLimit-Limit", ""),
        "remaining": resp.headers.get("X-RateLimit-Remaining", ""),
        "reset":     resp.headers.get("X-RateLimit-Reset", ""),
    }


def _get(
    path: str,
    params: dict | None = None,
    *,
    operation: str,
    translation: str = "",
    reference: str = "",
) -> dict | None:
    start       = time.monotonic()
    status_code = None
    err         = ""
    try:
        if _circuit_open():
            logger.debug("youversion: circuit open, skipping %s", path)
            return None

        resp = requests.get(
            BASE_URL + path,
            headers=_headers(),
            params=params or {},
            timeout=(3, 10),   # 3 s connect, 10 s read
        )
        status_code = resp.status_code
        if resp.status_code in (401, 403):
            err = "YouVersion app key is missing, invalid, or not licensed for this resource."
            logger.warning("youversion: %s for %s", resp.status_code, path)
            return None
        if resp.status_code == 404:
            err = f"YouVersion: 404 Not Found for {path}"
            logger.warning("youversion: 404 for %s", path)
            return None
        if resp.status_code == 429:
            err = "YouVersion rate limit exceeded."
            logger.warning("youversion: rate limit exceeded for %s", path)
            return None
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        log_provider_request(
            "youversion", operation,
            translation=translation, reference=reference,
            status_code=status_code, ok=True,
            latency_ms=(time.monotonic() - start) * 1000,
            rate_limit=_rate_limit(resp),
        )
        return data
    except ValueError as exc:
        err = str(exc)
        logger.info("youversion: config error - %s", exc)
    except requests.exceptions.Timeout:
        err = "YouVersion request timed out."
        logger.warning("youversion: timeout on %s", path)
        _trip_circuit()
    except requests.exceptions.ConnectionError as exc:
        err = str(exc)
        logger.warning("youversion: connection error %s - %s", path, exc)
        _trip_circuit()
    except requests.exceptions.RequestException as exc:
        err = str(exc)
        logger.warning("youversion: request failed %s - %s", path, exc)
        if "NameResolution" in str(exc) or "Max retries" in str(exc):
            _trip_circuit()
    except Exception as exc:
        err = str(exc)
        logger.error("youversion: unexpected error %s - %s", path, exc)
    finally:
        if err:
            log_provider_request(
                "youversion", operation,
                translation=translation, reference=reference,
                status_code=status_code, ok=False,
                latency_ms=(time.monotonic() - start) * 1000,
                error=err,
            )
    return None


def get_bible_id(short_code: str) -> int | None:
    code = (short_code or "").upper().strip()
    try:
        from bible.models import YouVersionTranslationMap
        row = YouVersionTranslationMap.objects.filter(short_code=code).first()
        if row:
            return row.bible_id
    except Exception as exc:
        logger.debug("youversion: DB lookup failed for %s - %s", code, exc)
    return None


def get_translations(force_refresh: bool = False) -> list[dict]:
    if not is_configured():
        return []

    if not force_refresh:
        try:
            from bible.models import YouVersionTranslationMap
            cached = list(YouVersionTranslationMap.objects.all())
            if cached:
                return [
                    {
                        "id":       row.short_code,
                        "api_id":   row.bible_id,
                        "name":     row.name or row.short_code,
                        "language": row.language or "en",
                        "source":   "youversion",
                    }
                    for row in cached
                ]
        except Exception:
            pass

    results    = []
    page_token = None

    while True:
        params = {"page_size": 99}
        if page_token:
            params["page_token"] = page_token

        data = _get("/bibles", params, operation="translations")
        if not data:
            break

        for b in data.get("data", []):
            bible_id = b.get("id")

            # Use YouVersion's own abbreviation verbatim — do NOT derive from title.
            # If the field is absent this entry is skipped to avoid fake short-codes.
            abbr = (
                b.get("localized_abbreviation")
                or b.get("abbreviation")
            )
            if not abbr or not bible_id:
                continue

            # Uppercase and strip whitespace only — no splitting on / or -
            # so "NLT-SE" stays "NLT-SE" rather than becoming "NLT".
            abbr = str(abbr).upper().strip()

            results.append({
                "id":        abbr,
                "api_id":    int(bible_id),
                "name":      b.get("localized_title") or b.get("title") or abbr,
                "language":  b.get("language_tag") or "en",
                "source":    "youversion",
                "deep_link": b.get("youversion_deep_link") or "",
            })

        page_token = data.get("next_page_token")
        if not page_token:
            break

    if results:
        _sync_to_db(results)
    return results


def _sync_to_db(translations: list[dict]) -> None:
    try:
        from bible.models import YouVersionTranslationMap
        for t in translations:
            if t.get("api_id") and t.get("id"):
                YouVersionTranslationMap.objects.update_or_create(
                    short_code=t["id"],
                    defaults={
                        "bible_id":  t["api_id"],
                        "name":      t.get("name", ""),
                        "language":  t.get("language", "en"),
                        "deep_link": t.get("deep_link", ""),
                    },
                )
    except Exception as exc:
        logger.warning("youversion: DB sync failed - %s", exc)


def get_books(translation: str) -> list:
    bible_id = get_bible_id(translation)
    if not bible_id:
        get_translations()
        bible_id = get_bible_id(translation)
    if not bible_id:
        return []

    data      = _get(f"/bibles/{bible_id}/index", operation="books", translation=translation)
    raw_books = (data or {}).get("books", [])
    books     = []
    for idx, b in enumerate(raw_books, start=1):
        chapters = b.get("chapters") or []
        books.append({
            "id":               b.get("id") or "",
            "name":             b.get("title") or b.get("full_title") or b.get("id") or "",
            "numberOfChapters": len(chapters) or 1,
            "order":            idx,
        })
    return books


def get_chapter(
    book_id: str, chapter: int, translation: str, force_refresh: bool = False
) -> dict:
    """
    Fetch a full chapter from YouVersion.

    Strategy:
      1. Serve from BibleChapterCache if fresh (< CACHE_TTL_DAYS old).
      2. GET /bibles/{bible_id}/books/{book_id}/chapters/{chapter}/verses
         → returns the list of verse metadata including passage_id.
      3. For each verse, GET /bibles/{bible_id}/passages/{passage_id}
         → returns the text. Results are cached to avoid repeat calls.

    The /chapters/{usfm_id} bulk endpoint returns 404 on the v1 API
    (confirmed by production logs) so we don't use it.
    """
    from datetime import datetime, timedelta
    from bible.models import BibleChapterCache

    translation = (translation or "").upper()
    book_id     = (book_id or "").upper()
    empty       = {"book_name": book_id, "chapter": chapter, "verses": []}

    if not is_configured():
        return empty

    # ── Serve from cache when fresh ──────────────────────────────────────────
    if not force_refresh:
        cached = BibleChapterCache.objects.filter(
            translation=translation, book_id=book_id, chapter=chapter
        ).first()
        if cached and cached.verses_data:
            age = datetime.utcnow() - cached.updated_at.replace(tzinfo=None)
            if age < timedelta(days=CACHE_TTL_DAYS):
                return {
                    "book_name": cached.book_name,
                    "chapter":   chapter,
                    "verses":    cached.verses_data,
                }

    # ── Resolve bible_id ─────────────────────────────────────────────────────
    bible_id = get_bible_id(translation)
    if not bible_id:
        get_translations()
        bible_id = get_bible_id(translation)
    if not bible_id:
        return empty

    # ── Resolve book display name (best-effort, non-blocking) ────────────────
    try:
        books     = get_books(translation)
        book_name = next(
            (b.get("name") for b in books if b.get("id") == book_id), book_id
        )
    except Exception:
        book_name = book_id

    # ── Step 1: get verse index for this chapter ─────────────────────────────
    verses_meta = _get(
        f"/bibles/{bible_id}/books/{book_id}/chapters/{chapter}/verses",
        operation="chapter",
        translation=translation,
        reference=f"{book_id}.{chapter}",
    )
    raw_verses = (verses_meta or {}).get("data", [])
    if not raw_verses:
        return {"book_name": book_name, "chapter": chapter, "verses": []}

    # ── Step 2: fetch text for each verse ────────────────────────────────────
    verses = []
    for v in raw_verses:
        # verse number: YouVersion returns "id" as an integer on this endpoint
        number = v.get("id") or v.get("title")
        try:
            number = int(number)
        except (TypeError, ValueError):
            continue

        passage_id = v.get("passage_id") or f"{book_id}.{chapter}.{number}"

        verse_data = _get(
            f"/bibles/{bible_id}/passages/{passage_id}",
            {"format": "text", "include_headings": "false", "include_notes": "false"},
            operation="passage",
            translation=translation,
            reference=passage_id,
        )
        text = (verse_data or {}).get("content") or ""
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            verses.append({"number": number, "text": text})

    # ── Persist to cache ─────────────────────────────────────────────────────
    if verses:
        BibleChapterCache.objects.update_or_create(
            translation=translation,
            book_id=book_id,
            chapter=chapter,
            defaults={
                "book_name":   book_name,
                "verses_data": verses,
                "verse_count": len(verses),
            },
        )

    return {"book_name": book_name, "chapter": chapter, "verses": verses}


def sync_translations() -> tuple[int, list[str]]:
    results = get_translations(force_refresh=True)
    return len(results), [t["name"] for t in results]