"""
bible/services/parser.py

Canonical reference parser for Bible verse strings.
Handles: "John 3:16", "Jn 3:16-18", "Romans 8", "Psalm 23:1", "Gen 1:1-3"

Public API:
    parse_reference(ref_str)  -> dict | None
    resolve_book_id(name, translation) -> str | None  (e.g. "JHN")
"""

import re
import logging

logger = logging.getLogger(__name__)


# ── Book alias table ──────────────────────────────────────────────────────────
# Maps aliases/abbreviations -> canonical HelloAO book_id

BOOK_ALIASES: dict[str, str] = {
    # Old Testament
    "genesis": "GEN",
    "gen": "GEN",
    "exodus": "EXO",
    "exo": "EXO",
    "ex": "EXO",
    "leviticus": "LEV",
    "lev": "LEV",
    "numbers": "NUM",
    "num": "NUM",
    "deuteronomy": "DEU",
    "deut": "DEU",
    "deu": "DEU",
    "joshua": "JOS",
    "josh": "JOS",
    "jos": "JOS",
    "judges": "JDG",
    "judg": "JDG",
    "jdg": "JDG",
    "ruth": "RUT",
    "rut": "RUT",
    "1 samuel": "1SA",
    "1samuel": "1SA",
    "1 sam": "1SA",
    "1sa": "1SA",
    "2 samuel": "2SA",
    "2samuel": "2SA",
    "2 sam": "2SA",
    "2sa": "2SA",
    "1 kings": "1KI",
    "1kings": "1KI",
    "1 kgs": "1KI",
    "1ki": "1KI",
    "2 kings": "2KI",
    "2kings": "2KI",
    "2 kgs": "2KI",
    "2ki": "2KI",
    "1 chronicles": "1CH",
    "1chronicles": "1CH",
    "1 chr": "1CH",
    "1ch": "1CH",
    "2 chronicles": "2CH",
    "2chronicles": "2CH",
    "2 chr": "2CH",
    "2ch": "2CH",
    "ezra": "EZR",
    "ezr": "EZR",
    "nehemiah": "NEH",
    "neh": "NEH",
    "esther": "EST",
    "esth": "EST",
    "est": "EST",
    "job": "JOB",
    "psalms": "PSA",
    "psalm": "PSA",
    "psa": "PSA",
    "ps": "PSA",
    "proverbs": "PRO",
    "prov": "PRO",
    "pro": "PRO",
    "ecclesiastes": "ECC",
    "eccl": "ECC",
    "ecc": "ECC",
    "song of solomon": "SNG",
    "song of songs": "SNG",
    "song": "SNG",
    "sng": "SNG",
    "sos": "SNG",
    "isaiah": "ISA",
    "isa": "ISA",
    "jeremiah": "JER",
    "jer": "JER",
    "lamentations": "LAM",
    "lam": "LAM",
    "ezekiel": "EZK",
    "ezek": "EZK",
    "ezk": "EZK",
    "daniel": "DAN",
    "dan": "DAN",
    "hosea": "HOS",
    "hos": "HOS",
    "joel": "JOL",
    "joe": "JOL",
    "amos": "AMO",
    "am": "AMO",
    "obadiah": "OBA",
    "obad": "OBA",
    "oba": "OBA",
    "jonah": "JON",
    "jon": "JON",
    "micah": "MIC",
    "mic": "MIC",
    "nahum": "NAM",
    "nah": "NAM",
    "nam": "NAM",
    "habakkuk": "HAB",
    "hab": "HAB",
    "zephaniah": "ZEP",
    "zeph": "ZEP",
    "zep": "ZEP",
    "haggai": "HAG",
    "hag": "HAG",
    "zechariah": "ZEC",
    "zech": "ZEC",
    "zec": "ZEC",
    "malachi": "MAL",
    "mal": "MAL",
    # New Testament
    "matthew": "MAT",
    "matt": "MAT",
    "mat": "MAT",
    "mt": "MAT",
    "mark": "MRK",
    "mrk": "MRK",
    "mk": "MRK",
    "luke": "LUK",
    "luk": "LUK",
    "lk": "LUK",
    "john": "JHN",
    "jhn": "JHN",
    "jn": "JHN",
    "acts": "ACT",
    "act": "ACT",
    "romans": "ROM",
    "rom": "ROM",
    "1 corinthians": "1CO",
    "1corinthians": "1CO",
    "1 cor": "1CO",
    "1co": "1CO",
    "2 corinthians": "2CO",
    "2corinthians": "2CO",
    "2 cor": "2CO",
    "2co": "2CO",
    "galatians": "GAL",
    "gal": "GAL",
    "ephesians": "EPH",
    "eph": "EPH",
    "philippians": "PHP",
    "phil": "PHP",
    "php": "PHP",
    "colossians": "COL",
    "col": "COL",
    "1 thessalonians": "1TH",
    "1thessalonians": "1TH",
    "1 thess": "1TH",
    "1th": "1TH",
    "2 thessalonians": "2TH",
    "2thessalonians": "2TH",
    "2 thess": "2TH",
    "2th": "2TH",
    "1 timothy": "1TI",
    "1timothy": "1TI",
    "1 tim": "1TI",
    "1ti": "1TI",
    "2 timothy": "2TI",
    "2timothy": "2TI",
    "2 tim": "2TI",
    "2ti": "2TI",
    "titus": "TIT",
    "tit": "TIT",
    "philemon": "PHM",
    "phlm": "PHM",
    "phm": "PHM",
    "hebrews": "HEB",
    "heb": "HEB",
    "james": "JAS",
    "jas": "JAS",
    "1 peter": "1PE",
    "1peter": "1PE",
    "1 pet": "1PE",
    "1pe": "1PE",
    "2 peter": "2PE",
    "2peter": "2PE",
    "2 pet": "2PE",
    "2pe": "2PE",
    "1 john": "1JN",
    "1john": "1JN",
    "1 jn": "1JN",
    "1jn": "1JN",
    "2 john": "2JN",
    "2john": "2JN",
    "2 jn": "2JN",
    "2jn": "2JN",
    "3 john": "3JN",
    "3john": "3JN",
    "3 jn": "3JN",
    "3jn": "3JN",
    "jude": "JUD",
    "jud": "JUD",
    "revelation": "REV",
    "rev": "REV",
    "revelations": "REV",
}

# Reverse: canonical ID -> primary display name
BOOK_NAMES: dict[str, str] = {
    "GEN": "Genesis",
    "EXO": "Exodus",
    "LEV": "Leviticus",
    "NUM": "Numbers",
    "DEU": "Deuteronomy",
    "JOS": "Joshua",
    "JDG": "Judges",
    "RUT": "Ruth",
    "1SA": "1 Samuel",
    "2SA": "2 Samuel",
    "1KI": "1 Kings",
    "2KI": "2 Kings",
    "1CH": "1 Chronicles",
    "2CH": "2 Chronicles",
    "EZR": "Ezra",
    "NEH": "Nehemiah",
    "EST": "Esther",
    "JOB": "Job",
    "PSA": "Psalms",
    "PRO": "Proverbs",
    "ECC": "Ecclesiastes",
    "SNG": "Song of Solomon",
    "ISA": "Isaiah",
    "JER": "Jeremiah",
    "LAM": "Lamentations",
    "EZK": "Ezekiel",
    "DAN": "Daniel",
    "HOS": "Hosea",
    "JOL": "Joel",
    "AMO": "Amos",
    "OBA": "Obadiah",
    "JON": "Jonah",
    "MIC": "Micah",
    "NAM": "Nahum",
    "HAB": "Habakkuk",
    "ZEP": "Zephaniah",
    "HAG": "Haggai",
    "ZEC": "Zechariah",
    "MAL": "Malachi",
    "MAT": "Matthew",
    "MRK": "Mark",
    "LUK": "Luke",
    "JHN": "John",
    "ACT": "Acts",
    "ROM": "Romans",
    "1CO": "1 Corinthians",
    "2CO": "2 Corinthians",
    "GAL": "Galatians",
    "EPH": "Ephesians",
    "PHP": "Philippians",
    "COL": "Colossians",
    "1TH": "1 Thessalonians",
    "2TH": "2 Thessalonians",
    "1TI": "1 Timothy",
    "2TI": "2 Timothy",
    "TIT": "Titus",
    "PHM": "Philemon",
    "HEB": "Hebrews",
    "JAS": "James",
    "1PE": "1 Peter",
    "2PE": "2 Peter",
    "1JN": "1 John",
    "2JN": "2 John",
    "3JN": "3 John",
    "JUD": "Jude",
    "REV": "Revelation",
}


_ROMAN_NUMERALS = {
    "i": "1",
    "ii": "2",
    "iii": "3",
}


def _normalize_book_name(name: str) -> str:
    """
    Normalize book names across translations/providers.

    Handles:
        Num.        -> num
        Deut.       -> deut
        1Cor.       -> 1 cor
        II Tim.     -> 2 tim
        1   Thess.  -> 1 thess
        Song.       -> song
    """
    if not name:
        return ""

    s = name.strip().lower()

    # Remove periods and other punctuation
    s = re.sub(r"[.,;]+", "", s)

    # Convert roman numeral prefixes
    # "ii timothy" -> "2 timothy"
    parts = s.split()
    if parts and parts[0] in _ROMAN_NUMERALS:
        parts[0] = _ROMAN_NUMERALS[parts[0]]
        s = " ".join(parts)

    # Split compact numbered books:
    # 1cor -> 1 cor
    # 2timothy -> 2 timothy
    s = re.sub(r"^([1-3])([a-z])", r"\1 \2", s)

    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    return s


def parse_reference(ref_str: str) -> dict | None:
    """
    Parse a human Bible reference into components.

    Returns:
        {
            "book":        "John",           # raw book name as given
            "chapter":     3,
            "verse_start": 16,               # None if whole chapter
            "verse_end":   18,               # None if single verse or whole chapter
        }
    or None on parse failure.

    Patterns supported:
        "John 3:16"
        "John 3:16-18"
        "1 John 3"
        "Ps 23:1"
        "Genesis 1"
        "Philippians 4:13"
    """

    if not ref_str:
        return None

    ref_str = ref_str.strip()

    # Normalize weird punctuation/spacing early
    ref_str = re.sub(r"[‐-‒–—]", "-", ref_str)
    ref_str = re.sub(r"\s+", " ", ref_str)

    pattern = re.compile(
        r"^([1-3]|I{1,3})?\s*"  # optional number / roman numeral
        r"([A-Za-z][A-Za-z\s\.]*?)"  # book
        r"\s+(\d+)"  # chapter
        r"(?::(\d+)(?:\s*[-]\s*(\d+))?)?"  # optional verses
        r"\s*$",
        re.IGNORECASE,
    )

    m = pattern.match(ref_str)
    if not m:
        return None

    leading_num = (m.group(1) or "").strip()
    book_raw = m.group(2).strip()

    if leading_num:
        book_raw = f"{leading_num} {book_raw}"

    # Normalize final book name
    book_raw = _normalize_book_name(book_raw)

    chapter = int(m.group(3))
    verse_start = int(m.group(4)) if m.group(4) else None
    verse_end = int(m.group(5)) if m.group(5) else None

    if verse_start and verse_end and verse_end < verse_start:
        verse_start, verse_end = verse_end, verse_start

    return {
        "book": book_raw,
        "chapter": chapter,
        "verse_start": verse_start,
        "verse_end": verse_end,
    }


def resolve_book_id(book_name: str, translation: str = "BSB") -> str | None:
    """
    Resolve a human book name to the HelloAO book_id (e.g. "JHN").
    Falls back to the API books list for the translation if alias lookup fails.
    """
    if not book_name:
        return None

    key = _normalize_book_name(book_name)

    # Direct alias lookup
    if key in BOOK_ALIASES:
        return BOOK_ALIASES[key]

    # Strip trailing 's' for plurals like "psalms" -> "psalm"
    if key.endswith("s") and key[:-1] in BOOK_ALIASES:
        return BOOK_ALIASES[key[:-1]]

    # Try prefix match (e.g. "phil" could match "philippians" or "philemon")
    matches = [bid for alias, bid in BOOK_ALIASES.items() if alias.startswith(key)]
    if len(matches) == 1:
        return matches[0]

    # Fall back: check API books list
    try:
        from bible.services.helloao import get_books

        books = get_books(translation)
        for b in books:
            api_name = _normalize_book_name(b.get("name", ""))
            if api_name == key:
                return b.get("id", "").upper() or None
            if b.get("id", "").upper() == book_name.upper():
                return b.get("id", "").upper()
    except Exception as exc:
        logger.warning("resolve_book_id fallback failed: %s", exc)

    logger.warning("resolve_book_id: could not resolve '%s'", book_name)
    return None


def book_id_to_name(book_id: str) -> str:
    """Return display name for a book_id, or the id itself."""
    return BOOK_NAMES.get(book_id.upper(), book_id)


def format_reference(
    book_id: str, chapter: int, verse_start=None, verse_end=None, book_name=None
) -> str:
    """Build a clean display reference string."""
    name = book_name or book_id_to_name(book_id)
    if verse_start and verse_end and verse_start != verse_end:
        return f"{name} {chapter}:{verse_start}-{verse_end}"
    elif verse_start:
        return f"{name} {chapter}:{verse_start}"
    return f"{name} {chapter}"
