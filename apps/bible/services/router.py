"""
bible/services/router.py

Unified Bible API router.

Priority chain:
  1. YouVersion — primary licensed Platform Bible API.
  2. HelloAO    — open-license fallback translations (BSB, KJV/eng_kjv, WEB/ENGWEBP, NET, YLT, ASV, …)
  3. API.Bible  — secondary licensed fallback (NIV, NLT, AMP, NKJV, MSG, CSB, GNB, CEV, …)
                 Requires APIBIBLE_API_KEY in settings / .env.

Phase-3 fix: get_translations_all() deduplicates helloao and apibible entries
against YouVersion — any translation whose canonical short-code (or whose name
contains the same abbreviation, even with extra wording in parentheses) is
already served by YouVersion is excluded from the fallback lists shown in the UI.

All views import from here — never directly from helloao or apibible.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Short-codes that API.Bible serves (licensed/copyrighted).
# Any translation NOT in this set goes to HelloAO first.
# Populated dynamically from the DB after first sync; this set is the bootstrap fallback.
_APIBIBLE_ABBREVS = {
    "NIV",
    "NLT",
    "AMP",
    "AMPC",
    "NKJV",
    "MSG",
    "CSB",
    "GNB",
    "GNT",
    "CEV",
    "CEB",
    "NCV",
    "HCSB",
    "GW",
    "ERV",
    "ICB",
    "ISV",
    "NOG",
    "TPT",
    "TLB",
    "VOICE",
    "CJB",
    "EHV",
    "WE",
    "PHILLIPS",
}


def _route_apibible(translation: str) -> bool:
    """
    Return True if translation should be fetched from API.Bible.
    Checks DB first (populated after sync), falls back to static set.
    """
    code = translation.upper().strip()
    try:
        from bible.models import ApiBibleTranslationMap

        if ApiBibleTranslationMap.objects.filter(short_code=code).exists():
            return True
    except Exception:
        pass
    return code in _APIBIBLE_ABBREVS


def _route_youversion(translation: str) -> bool:
    """Return True if YouVersion has a cached mapping for this short code."""
    code = translation.upper().strip()
    try:
        from bible.models import YouVersionTranslationMap

        return YouVersionTranslationMap.objects.filter(short_code=code).exists()
    except Exception:
        return False


def _mark_fallback(provider: str, operation: str, translation: str, reference: str = "") -> None:
    try:
        from bible.services.analytics import log_provider_request

        log_provider_request(
            provider,
            operation,
            translation=translation,
            reference=reference,
            ok=False,
            fallback_used=True,
            error="Provider returned no usable result; router used fallback.",
        )
    except Exception:
        pass


# ── Phase-3 dedup helper ──────────────────────────────────────────────────────

def _extract_abbr_from_name(name: str) -> str | None:
    """
    Extract an abbreviation from a translation name that may contain one in
    parentheses, e.g. "New International Version (NIV)" → "NIV",
    or "King James Version (KJV)" → "KJV".
    Returns the abbreviation uppercased, or None if none found.
    """
    m = re.search(r"\(([A-Z0-9_]{2,12})\)", name.upper())
    return m.group(1) if m else None


def _build_youversion_seen_set(youversion_list: list[dict]) -> set[str]:
    """
    Build a set of all short-codes/abbreviations already covered by YouVersion,
    including abbreviations embedded in parentheses within the translation name.
    This set is used to deduplicate HelloAO and API.Bible lists.
    """
    seen: set[str] = set()
    for t in youversion_list:
        code = (t.get("id") or "").upper().strip()
        if code:
            seen.add(code)
        name = t.get("name") or ""
        abbr = _extract_abbr_from_name(name)
        if abbr:
            seen.add(abbr)
    return seen


def _dedup_against_youversion(entries: list[dict], yv_seen: set[str]) -> list[dict]:
    """
    Filter out any entry whose id or parenthesised name abbreviation is
    already in the YouVersion seen set.
    """
    result = []
    for t in entries:
        code = (t.get("id") or "").upper().strip()
        name = t.get("name") or ""
        abbr_in_name = _extract_abbr_from_name(name)

        if code in yv_seen:
            continue
        if abbr_in_name and abbr_in_name in yv_seen:
            continue
        result.append(t)
    return result


# ── Chapter / books routing (unchanged logic) ─────────────────────────────────

def get_chapter(book_id: str, chapter: int, translation: str) -> dict:
    """
    Fetch a chapter from the appropriate service.
    Always returns {book_name, chapter, verses} — verses may be [] on failure.
    """
    translation = translation.upper().strip()
    empty = {"book_name": book_id, "chapter": chapter, "verses": []}

    # Primary: YouVersion. If no mapping exists yet, try a one-time sync.
    try:
        from bible.services.youversion import (
            get_chapter as _yv,
            get_translations as _yv_trans,
            is_configured as _yv_configured,
        )

        if _yv_configured():
            if not _route_youversion(translation):
                _yv_trans()
            if _route_youversion(translation):
                result = _yv(book_id, chapter, translation)
                if result.get("verses"):
                    return result
                logger.warning(
                    "router: YouVersion returned no verses for %s %s:%s",
                    translation,
                    book_id,
                    chapter,
                )
                _mark_fallback("youversion", "chapter", translation, f"{book_id}.{chapter}")
    except Exception as exc:
        logger.error("router: YouVersion chapter error - %s", exc)

    # Open-license fallback.
    try:
        from bible.services.helloao import get_chapter as _hao

        result = _hao(book_id, chapter, translation)
        if result.get("verses"):
            return result
        logger.warning(
            "router: HelloAO returned no verses for %s %s:%s",
            translation,
            book_id,
            chapter,
        )
        _mark_fallback("helloao", "chapter", translation, f"{book_id}.{chapter}")
    except Exception as exc:
        logger.error("router: HelloAO chapter error - %s", exc)

    # Licensed secondary fallback.
    if _route_apibible(translation):
        try:
            from bible.services.apibible import get_chapter as _ab, is_configured

            if not is_configured():
                logger.info("router: API.Bible not configured for %s", translation)
                return empty
            result = _ab(book_id, chapter, translation)
            if result.get("verses"):
                return result
            logger.warning(
                "router: API.Bible returned no verses for %s %s:%s",
                translation,
                book_id,
                chapter,
            )
        except Exception as exc:
            logger.error("router: API.Bible chapter error - %s", exc)
    return empty


def get_books(translation: str) -> list:
    """Return the book list for a translation, routing to the right service."""
    translation = translation.upper().strip()

    try:
        from bible.services.youversion import (
            get_books as _yv_books,
            get_translations as _yv_trans,
            is_configured as _yv_configured,
        )

        if _yv_configured():
            if not _route_youversion(translation):
                _yv_trans()
            if _route_youversion(translation):
                books = _yv_books(translation)
                if books:
                    return books
                _mark_fallback("youversion", "books", translation)
    except Exception as exc:
        logger.error("router: get_books YouVersion error - %s", exc)

    try:
        from bible.services.helloao import get_books as _hao_books

        books = _hao_books(translation)
        if books:
            return books
        _mark_fallback("helloao", "books", translation)
    except Exception as exc:
        logger.error("router: get_books HelloAO error - %s", exc)

    if _route_apibible(translation):
        try:
            from bible.services.apibible import (
                get_bible_id,
                is_configured,
                _usfm_to_apibible,
            )

            if not is_configured():
                return _standard_books()
            bible_id = get_bible_id(translation)
            if not bible_id:
                return _standard_books()
            import requests
            from django.conf import settings

            resp = requests.get(
                f"https://rest.api.bible/v1/bibles/{bible_id}/books",
                headers={"api-key": settings.APIBIBLE_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            raw = resp.json().get("data", [])
            std = {b["id"]: b for b in _standard_books()}
            result = []
            for b in raw:
                bid = b.get("id", "")
                std_bk = std.get(bid, {})
                result.append(
                    {
                        "id": bid,
                        "name": b.get("name") or b.get("nameLong") or bid,
                        "numberOfChapters": std_bk.get("numberOfChapters", 1),
                        "order": std_bk.get("order", 99),
                    }
                )
            result.sort(key=lambda b: b["order"])
            return result or _standard_books()
        except Exception as exc:
            logger.error("router: get_books API.Bible error - %s", exc)
            return _standard_books()
    return _standard_books()


def search_reference(reference_str: str, translation: str) -> list:
    """Parse a reference string and return matching verses from the right service."""
    from bible.services.parser import parse_reference, resolve_book_id

    parsed = parse_reference(reference_str)
    if not parsed:
        return []

    translation = translation.upper().strip()
    book_id = resolve_book_id(parsed["book"], translation)
    if not book_id:
        logger.warning("router: could not resolve book '%s'", parsed["book"])
        return []

    ch_data = get_chapter(book_id, parsed["chapter"], translation)
    all_verses = ch_data.get("verses", [])
    book_name = ch_data.get("book_name") or parsed["book"]

    verse_start = parsed.get("verse_start")
    verse_end = parsed.get("verse_end")

    if verse_start is not None:
        all_verses = [
            v
            for v in all_verses
            if (verse_end is None and v["number"] == verse_start)
            or (verse_end is not None and verse_start <= v["number"] <= verse_end)
        ]

    if verse_start and verse_end and verse_start != verse_end:
        group_ref = f"{book_name} {parsed['chapter']}:{verse_start}-{verse_end}"
    elif verse_start:
        group_ref = f"{book_name} {parsed['chapter']}:{verse_start}"
    else:
        group_ref = f"{book_name} {parsed['chapter']}"

    results = []
    for v in all_verses:
        ref = (
            group_ref
            if (verse_start and verse_end and verse_start != verse_end)
            else f"{book_name} {parsed['chapter']}:{v['number']}"
        )
        results.append(
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
    return results


def get_translations_all() -> dict:
    """
    Return all available English translations grouped by source.

    Phase-3 change: helloao and apibible entries that duplicate a YouVersion
    translation (same short-code, or same abbreviation embedded in the name)
    are stripped out before returning, so dropdowns never show the same
    translation twice.

    Returns:
        {
          "youversion":      [{id, name, language, source}],
          "youversion_ready": bool,
          "helloao":         [{id, name, language, source}],   ← deduped
          "apibible":        [{id, name, language, source, needs_key?}],  ← deduped
          "apibible_ready":  bool
        }
    """
    youversion_list = []
    helloao_list = []
    apibible_list = []
    youversion_ready = False
    apibible_ready = False

    # ── YouVersion ───────────────────────────────────────────────────────────
    try:
        from bible.services.youversion import (
            is_configured as _yv_configured,
            get_translations as _yv_trans,
        )

        if _yv_configured():
            from bible.models import YouVersionTranslationMap

            db_count = YouVersionTranslationMap.objects.count()
            youversion_list = _yv_trans(force_refresh=(db_count == 0))
            youversion_ready = bool(youversion_list)
    except Exception as exc:
        logger.error("router: YouVersion translations error - %s", exc)

    # Build the dedup set from YouVersion (used for both helloao + apibible)
    yv_seen = _build_youversion_seen_set(youversion_list)

    # ── HelloAO ──────────────────────────────────────────────────────────────
    try:
        from bible.services.helloao import get_translations as _hao_trans

        raw = _hao_trans()
        all_helloao = []
        for t in raw:
            lang = (t.get("language") or "").lower()
            if lang in ("", "eng", "english", "en") or not lang:
                all_helloao.append(
                    {
                        "id": t["id"],
                        "name": t.get("name") or t["id"],
                        "language": "eng",
                        "source": "helloao",
                    }
                )
        # Phase-3: remove entries already covered by YouVersion
        helloao_list = _dedup_against_youversion(all_helloao, yv_seen)
    except Exception as exc:
        logger.error("router: HelloAO translations error — %s", exc)

    # ── API.Bible ─────────────────────────────────────────────────────────────
    try:
        from bible.services.apibible import (
            is_configured,
            get_translations as _ab_trans,
            _DISPLAY_NAMES,
        )

        if is_configured():
            from bible.models import ApiBibleTranslationMap

            db_count = ApiBibleTranslationMap.objects.count()

            if db_count == 0:
                logger.info("router: API.Bible DB empty — triggering first-time sync")
                raw_ab = _ab_trans(force_refresh=True)
            else:
                raw_ab = _ab_trans(force_refresh=False)

            # Phase-3: remove entries already covered by YouVersion
            apibible_list = _dedup_against_youversion(raw_ab, yv_seen)
            apibible_ready = bool(apibible_list)

        else:
            # Key not set — show what's possible but flag as needs_key
            raw_ab = [
                {
                    "id": abbr,
                    "name": _DISPLAY_NAMES.get(abbr, abbr),
                    "language": "eng",
                    "source": "apibible",
                    "needs_key": True,
                }
                for abbr in sorted(_DISPLAY_NAMES.keys())
            ]
            # Phase-3: still dedup against YouVersion
            apibible_list = _dedup_against_youversion(raw_ab, yv_seen)
            apibible_ready = False

    except Exception as exc:
        logger.error("router: API.Bible translations error — %s", exc)

    return {
        "youversion": youversion_list,
        "youversion_ready": youversion_ready,
        "helloao": helloao_list,
        "apibible": apibible_list,
        "apibible_ready": apibible_ready,
    }


# ── Standard books fallback ───────────────────────────────────────────────────


def _standard_books() -> list:
    """Canonical 66-book Protestant canon with chapter counts — used as fallback."""
    return [
        {"id": "GEN", "name": "Genesis", "numberOfChapters": 50, "order": 1},
        {"id": "EXO", "name": "Exodus", "numberOfChapters": 40, "order": 2},
        {"id": "LEV", "name": "Leviticus", "numberOfChapters": 27, "order": 3},
        {"id": "NUM", "name": "Numbers", "numberOfChapters": 36, "order": 4},
        {"id": "DEU", "name": "Deuteronomy", "numberOfChapters": 34, "order": 5},
        {"id": "JOS", "name": "Joshua", "numberOfChapters": 24, "order": 6},
        {"id": "JDG", "name": "Judges", "numberOfChapters": 21, "order": 7},
        {"id": "RUT", "name": "Ruth", "numberOfChapters": 4, "order": 8},
        {"id": "1SA", "name": "1 Samuel", "numberOfChapters": 31, "order": 9},
        {"id": "2SA", "name": "2 Samuel", "numberOfChapters": 24, "order": 10},
        {"id": "1KI", "name": "1 Kings", "numberOfChapters": 22, "order": 11},
        {"id": "2KI", "name": "2 Kings", "numberOfChapters": 25, "order": 12},
        {"id": "1CH", "name": "1 Chronicles", "numberOfChapters": 29, "order": 13},
        {"id": "2CH", "name": "2 Chronicles", "numberOfChapters": 36, "order": 14},
        {"id": "EZR", "name": "Ezra", "numberOfChapters": 10, "order": 15},
        {"id": "NEH", "name": "Nehemiah", "numberOfChapters": 13, "order": 16},
        {"id": "EST", "name": "Esther", "numberOfChapters": 10, "order": 17},
        {"id": "JOB", "name": "Job", "numberOfChapters": 42, "order": 18},
        {"id": "PSA", "name": "Psalms", "numberOfChapters": 150, "order": 19},
        {"id": "PRO", "name": "Proverbs", "numberOfChapters": 31, "order": 20},
        {"id": "ECC", "name": "Ecclesiastes", "numberOfChapters": 12, "order": 21},
        {"id": "SNG", "name": "Song of Solomon", "numberOfChapters": 8, "order": 22},
        {"id": "ISA", "name": "Isaiah", "numberOfChapters": 66, "order": 23},
        {"id": "JER", "name": "Jeremiah", "numberOfChapters": 52, "order": 24},
        {"id": "LAM", "name": "Lamentations", "numberOfChapters": 5, "order": 25},
        {"id": "EZK", "name": "Ezekiel", "numberOfChapters": 48, "order": 26},
        {"id": "DAN", "name": "Daniel", "numberOfChapters": 12, "order": 27},
        {"id": "HOS", "name": "Hosea", "numberOfChapters": 14, "order": 28},
        {"id": "JOL", "name": "Joel", "numberOfChapters": 3, "order": 29},
        {"id": "AMO", "name": "Amos", "numberOfChapters": 9, "order": 30},
        {"id": "OBA", "name": "Obadiah", "numberOfChapters": 1, "order": 31},
        {"id": "JON", "name": "Jonah", "numberOfChapters": 4, "order": 32},
        {"id": "MIC", "name": "Micah", "numberOfChapters": 7, "order": 33},
        {"id": "NAM", "name": "Nahum", "numberOfChapters": 3, "order": 34},
        {"id": "HAB", "name": "Habakkuk", "numberOfChapters": 3, "order": 35},
        {"id": "ZEP", "name": "Zephaniah", "numberOfChapters": 3, "order": 36},
        {"id": "HAG", "name": "Haggai", "numberOfChapters": 2, "order": 37},
        {"id": "ZEC", "name": "Zechariah", "numberOfChapters": 14, "order": 38},
        {"id": "MAL", "name": "Malachi", "numberOfChapters": 4, "order": 39},
        {"id": "MAT", "name": "Matthew", "numberOfChapters": 28, "order": 40},
        {"id": "MRK", "name": "Mark", "numberOfChapters": 16, "order": 41},
        {"id": "LUK", "name": "Luke", "numberOfChapters": 24, "order": 42},
        {"id": "JHN", "name": "John", "numberOfChapters": 21, "order": 43},
        {"id": "ACT", "name": "Acts", "numberOfChapters": 28, "order": 44},
        {"id": "ROM", "name": "Romans", "numberOfChapters": 16, "order": 45},
        {"id": "1CO", "name": "1 Corinthians", "numberOfChapters": 16, "order": 46},
        {"id": "2CO", "name": "2 Corinthians", "numberOfChapters": 13, "order": 47},
        {"id": "GAL", "name": "Galatians", "numberOfChapters": 6, "order": 48},
        {"id": "EPH", "name": "Ephesians", "numberOfChapters": 6, "order": 49},
        {"id": "PHP", "name": "Philippians", "numberOfChapters": 4, "order": 50},
        {"id": "COL", "name": "Colossians", "numberOfChapters": 4, "order": 51},
        {"id": "1TH", "name": "1 Thessalonians", "numberOfChapters": 5, "order": 52},
        {"id": "2TH", "name": "2 Thessalonians", "numberOfChapters": 3, "order": 53},
        {"id": "1TI", "name": "1 Timothy", "numberOfChapters": 6, "order": 54},
        {"id": "2TI", "name": "2 Timothy", "numberOfChapters": 4, "order": 55},
        {"id": "TIT", "name": "Titus", "numberOfChapters": 3, "order": 56},
        {"id": "PHM", "name": "Philemon", "numberOfChapters": 1, "order": 57},
        {"id": "HEB", "name": "Hebrews", "numberOfChapters": 13, "order": 58},
        {"id": "JAS", "name": "James", "numberOfChapters": 5, "order": 59},
        {"id": "1PE", "name": "1 Peter", "numberOfChapters": 5, "order": 60},
        {"id": "2PE", "name": "2 Peter", "numberOfChapters": 3, "order": 61},
        {"id": "1JN", "name": "1 John", "numberOfChapters": 5, "order": 62},
        {"id": "2JN", "name": "2 John", "numberOfChapters": 1, "order": 63},
        {"id": "3JN", "name": "3 John", "numberOfChapters": 1, "order": 64},
        {"id": "JUD", "name": "Jude", "numberOfChapters": 1, "order": 65},
        {"id": "REV", "name": "Revelation", "numberOfChapters": 22, "order": 66},
    ]
