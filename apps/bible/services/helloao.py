"""
bible/services/helloao.py

Client around the HelloAO Free Use Bible API.
https://bible.helloao.org/docs/reference/

Chapter endpoint URL:  /api/{translation}/{book}/{chapter}.json
Confirmed response shape (from official HelloAO docs):
{
  "translation": {...},
  "book": { "id": "JHN", "name": "John", "commonName": "John", ... },
  "thisChapterLink": "...",
  "chapters": [
    {
      "chapter": { "number": 3 },
      "verses": [
        { "verse": { "number": 1 }, "value": "In the beginning was the Word..." },
        ...
      ]
    }
  ]
}

NOTE: "chapters" is always a list with one item for a single-chapter request.
Verse text is in the "value" key. Verse number is in verse["verse"]["number"].
"""

import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE_URL = "https://bible.helloao.org/api"
DEFAULT_TRANSLATION = "BSB"
CACHE_TTL_DAYS = 30


def _get(url: str, timeout: int = 15) -> dict | list | None:
    """Perform a GET request. Returns parsed JSON or None on any failure."""
    try:
        resp = requests.get(
            url, timeout=timeout, headers={"Accept": "application/json"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data
    except requests.exceptions.Timeout:
        logger.warning("helloao: timeout fetching %s", url)
    except requests.exceptions.HTTPError as exc:
        logger.warning("helloao: HTTP %s for %s", exc.response.status_code, url)
    except requests.exceptions.RequestException as exc:
        logger.warning("helloao: request failed %s — %s", url, exc)
    except Exception as exc:
        logger.error("helloao: unexpected error %s — %s", url, exc)
    return None


# ── Translations ──────────────────────────────────────────────────────────────


def get_translations(force_refresh: bool = False) -> list:
    from bible.models import BibleTranslationCache

    if not force_refresh:
        cached = list(BibleTranslationCache.objects.all())
        if cached:
            return [
                {
                    "id": t.translation_id,
                    "name": t.name,
                    "language": t.language,
                    "website": t.website,
                    "license_url": t.license_url,
                    "short_name": t.short_name,
                }
                for t in cached
            ]

    data = _get(f"{BASE_URL}/available_translations.json")
    if not data:
        return []

    translations = data.get("translations", []) if isinstance(data, dict) else data
    result = []
    for t in translations:
        tid = (t.get("id") or "").upper()
        if not tid:
            continue
        BibleTranslationCache.objects.update_or_create(
            translation_id=tid,
            defaults={
                "name": t.get("englishName") or t.get("name") or tid,
                "language": t.get("language") or "",
                "website": t.get("website") or "",
                "license_url": t.get("licenseUrl") or "",
                "short_name": t.get("shortName") or "",
            },
        )
        result.append({"id": tid, "name": t.get("englishName") or t.get("name") or tid})
    return result


# ── Books ─────────────────────────────────────────────────────────────────────


def get_books(translation: str = DEFAULT_TRANSLATION) -> list:
    from bible.models import BibleTranslationCache

    translation = translation.upper()
    cache_obj = BibleTranslationCache.objects.filter(translation_id=translation).first()

    if cache_obj and cache_obj.books_data:
        return cache_obj.books_data

    data = _get(f"{BASE_URL}/{translation}/books.json")
    if not data:
        return []

    raw_books = data.get("books", []) if isinstance(data, dict) else data
    normalised = []
    for b in raw_books:
        # HelloAO TranslationBook uses lastChapterNumber, not numberOfChapters
        num_chapters = (
            b.get("numberOfChapters")
            or b.get("lastChapterNumber")
            or b.get("numChapters")
            or 1
        )
        normalised.append(
            {
                "id": b.get("id") or "",
                "name": b.get("commonName") or b.get("name") or b.get("id") or "",
                "numberOfChapters": num_chapters,
                "order": b.get("order") or 0,
            }
        )
    normalised.sort(key=lambda b: b["order"])

    if cache_obj:
        cache_obj.books_data = normalised
        cache_obj.save(update_fields=["books_data", "updated_at"])
    else:
        BibleTranslationCache.objects.get_or_create(
            translation_id=translation,
            defaults={"name": translation, "books_data": normalised},
        )
    return normalised


# ── Chapter parsing ───────────────────────────────────────────────────────────


def _parse_chapter_response(data: dict) -> tuple[str, list]:
    """
    Extract (book_name, verses) from a HelloAO chapter API response.

    Actual HelloAO response shape (TranslationBookChapter):
    {
      "translation": {...},
      "book": { "id": "JHN", "commonName": "John", "name": "John", ... },
      "thisChapterLink": "...",
      "numberOfVerses": 36,
      "chapter": {
        "number": 3,
        "content": [
          { "type": "heading", "content": ["The New Birth"] },
          {
            "type": "verse",
            "number": 1,
            "content": [
              "Now there was a man of the Pharisees named Nicodemus...",
              { "text": "some formatted text", "wordsOfJesus": false },
              { "lineBreak": true },
              { "heading": "inline heading" },
              { "noteId": 1 }
            ]
          },
          ...
        ],
        "footnotes": [...]
      }
    }

    Verse text is built by joining string items and FormattedText.text items
    from each verse's content array, skipping line breaks, headings, and footnote refs.
    """
    book_obj = data.get("book") or {}
    book_name = (
        book_obj.get("commonName") or book_obj.get("name") or data.get("bookName") or ""
    )

    verses = []

    # ── Primary: data["chapter"]["content"] ─────────────────────────────
    chapter_block = data.get("chapter")
    if isinstance(chapter_block, dict):
        content_items = chapter_block.get("content", [])
        for item in content_items:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "verse":
                continue
            number = item.get("number", 0)
            if not number:
                continue

            # Build verse text from content array
            # Each element is: str | {text, ...} | {lineBreak} | {heading} | {noteId}
            text_parts = []
            for part in item.get("content", []):
                if isinstance(part, str):
                    text_parts.append(part)
                elif isinstance(part, dict):
                    if "text" in part:  # FormattedText
                        text_parts.append(part["text"])
                    # skip lineBreak, heading, noteId

            text = " ".join(text_parts).strip()
            # Normalise multiple spaces / newlines
            import re as _re

            text = _re.sub(r"\s+", " ", text).strip()

            if text and number:
                verses.append({"number": int(number), "text": text})

        if verses:
            verses.sort(key=lambda x: x["number"])
            return book_name, verses

    # ── Fallback A: legacy shape — data["chapters"][0]["verses"] ─────────
    # (kept in case the API ever returns the old shape)
    chapters_list = data.get("chapters")
    if chapters_list and isinstance(chapters_list, list):
        chapter_block_legacy = chapters_list[0] if chapters_list else {}
        raw_verses = chapter_block_legacy.get("verses", [])
        for v in raw_verses:
            verse_ref = v.get("verse", {})
            number = (
                verse_ref.get("number") if isinstance(verse_ref, dict) else verse_ref
            )
            text = v.get("value") or v.get("text") or ""
            if text and number:
                verses.append({"number": int(number), "text": str(text).strip()})
        if verses:
            verses.sort(key=lambda x: x["number"])
            return book_name, verses

    # ── Fallback B: flat data["verses"] ─────────────────────────────────
    raw_verses = data.get("verses", [])
    if raw_verses and isinstance(raw_verses, list):
        for v in raw_verses:
            number = v.get("number") or v.get("verse") or v.get("verseNumber") or 0
            text = v.get("text") or v.get("value") or ""
            if text and number:
                verses.append({"number": int(number), "text": str(text).strip()})
        if verses:
            verses.sort(key=lambda x: x["number"])
            return book_name, verses

    if not verses:
        logger.warning(
            "helloao: could not parse verses. top-level keys: %s | "
            "chapter keys: %s | chapter.content[:2]: %s",
            list(data.keys()),
            list((data.get("chapter") or {}).keys()),
            (data.get("chapter") or {}).get("content", [])[:2],
        )

    return book_name, verses


# ── Chapter fetch ─────────────────────────────────────────────────────────────


def get_chapter(
    book_id: str,
    chapter: int,
    translation: str = DEFAULT_TRANSLATION,
    force_refresh: bool = False,
) -> dict:
    """
    Return {"book_name": str, "chapter": int, "verses": [{number, text}]}
    Cached in BibleChapterCache with 30-day TTL.
    Cache entries with zero verses are treated as stale and re-fetched automatically.
    """
    from bible.models import BibleChapterCache

    translation = translation.upper()
    book_id = book_id.upper()

    # Check cache first — skip entries with no verses (bad parse from old code)
    if not force_refresh:
        cached = BibleChapterCache.objects.filter(
            translation=translation, book_id=book_id, chapter=chapter
        ).first()
        if cached and cached.verses_data:  # only use if non-empty
            age = datetime.utcnow() - cached.updated_at.replace(tzinfo=None)
            if age < timedelta(days=CACHE_TTL_DAYS):
                return {
                    "book_name": cached.book_name,
                    "chapter": chapter,
                    "verses": cached.verses_data,
                }

    # Fetch from API
    url = f"{BASE_URL}/{translation}/{book_id}/{chapter}.json"
    logger.debug("helloao: fetching %s", url)
    data = _get(url)

    if not data:
        # Return stale cache if available rather than empty
        stale = BibleChapterCache.objects.filter(
            translation=translation, book_id=book_id, chapter=chapter
        ).first()
        if stale:
            logger.info(
                "helloao: API unavailable, returning stale cache for %s %s %s",
                translation,
                book_id,
                chapter,
            )
            return {
                "book_name": stale.book_name,
                "chapter": chapter,
                "verses": stale.verses_data,
            }
        return {"book_name": book_id, "chapter": chapter, "verses": []}

    book_name, verses = _parse_chapter_response(data)
    if not book_name:
        book_name = book_id

    # Persist to cache
    BibleChapterCache.objects.update_or_create(
        translation=translation,
        book_id=book_id,
        chapter=chapter,
        defaults={
            "book_name": book_name,
            "verses_data": verses,
            "verse_count": len(verses),
        },
    )

    return {"book_name": book_name, "chapter": chapter, "verses": verses}


def get_verse(
    book_id: str,
    chapter: int,
    verse: int,
    translation: str = DEFAULT_TRANSLATION,
) -> dict | None:
    ch_data = get_chapter(book_id, chapter, translation)
    for v in ch_data.get("verses", []):
        if v["number"] == verse:
            return {
                "number": verse,
                "text": v["text"],
                "book_name": ch_data["book_name"],
                "chapter": chapter,
                "reference": f"{ch_data['book_name']} {chapter}:{verse}",
                "translation": translation,
            }
    return None


# ── Reference search ──────────────────────────────────────────────────────────


def search_reference(
    reference_str: str, translation: str = DEFAULT_TRANSLATION
) -> list:
    """
    Parse a human reference and return matching verses directly from HelloAO API.
    No AI. Supports single verse, range, and whole chapter.
    Returns [{number, text, book_name, book_id, chapter, reference, group_reference, translation}]
    """
    from bible.services.parser import parse_reference, resolve_book_id

    parsed = parse_reference(reference_str)
    if not parsed:
        logger.debug("search_reference: could not parse '%s'", reference_str)
        return []

    book_id = resolve_book_id(parsed["book"], translation)
    if not book_id:
        logger.warning(
            "search_reference: unresolved book '%s' in '%s'",
            parsed["book"],
            reference_str,
        )
        return []

    ch_data = get_chapter(book_id, parsed["chapter"], translation)
    all_verses = ch_data.get("verses", [])
    book_name = ch_data.get("book_name") or parsed["book"]

    verse_start = parsed.get("verse_start")
    verse_end = parsed.get("verse_end")

    if verse_start is not None:
        if verse_end is not None:
            filtered = [
                v for v in all_verses if verse_start <= v["number"] <= verse_end
            ]
        else:
            filtered = [v for v in all_verses if v["number"] == verse_start]
    else:
        filtered = all_verses  # whole chapter

    # Build canonical group reference label
    if verse_start and verse_end and verse_start != verse_end:
        group_ref = f"{book_name} {parsed['chapter']}:{verse_start}-{verse_end}"
    elif verse_start:
        group_ref = f"{book_name} {parsed['chapter']}:{verse_start}"
    else:
        group_ref = f"{book_name} {parsed['chapter']}"

    result = []
    for v in filtered:
        if verse_start and verse_end and verse_start != verse_end:
            ref = group_ref  # range: all verses share the range reference
        else:
            ref = f"{book_name} {parsed['chapter']}:{v['number']}"

        result.append(
            {
                "number": v["number"],
                "text": v["text"],
                "book_name": book_name,
                "book_id": book_id,
                "chapter": parsed["chapter"],
                "reference": ref,
                "group_reference": group_ref,
                "translation": translation,
            }
        )

    return result
