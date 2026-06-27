"""
core/ai_skills.py — ChurchForce AI Skills Registry

Central interface for all AI-powered automation across the project.
Uses the Anthropic Claude API (claude-haiku-4-5 for fast/cheap tasks,
claude-sonnet-4-6 for quality tasks).

All calls are lazy-imported and wrapped in try/except so a missing
API key or network error never crashes the operation that called them.

Usage:
    from core.ai_skills import skill

    # Welcome verse
    verse = skill.bible_verse_for_date(date.today())

    # Support bot reply
    reply = skill.support_reply(
        church=church, history=messages, user_message="How do I add a guest?"
    )

    # Lyrics transcription nudge (passes raw text from Whisper)
    sections = skill.structure_lyrics(raw_lyrics="...", title="Amazing Grace")
"""

import logging
from datetime import date
from typing import Optional, List
import requests

logger = logging.getLogger(__name__)

# ── Haiku = fast/cheap, Sonnet = quality ─────────────────────────────────────
HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-4-6"


def _provider():
    try:
        from django.conf import settings

        return (getattr(settings, "AI_PROVIDER", "") or "anthropic").lower()
    except Exception:
        return "anthropic"


def _client():
    """Lazily build the Anthropic client. Returns None if key is missing."""
    try:
        import anthropic
        from django.conf import settings

        key = getattr(settings, "ANTHROPIC_API_KEY", "")
        if not key:
            logger.warning(
                "ai_skills: ANTHROPIC_API_KEY not set — AI features disabled."
            )
            return None
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        logger.warning("ai_skills: 'anthropic' package not installed.")
        return None
    except Exception as exc:
        logger.error("ai_skills: client init failed: %s", exc)
        return None


def _chat(
    system: str, user: str, model: str = HAIKU, max_tokens: int = 512
) -> Optional[str]:
    """Single-turn Claude call. Returns text or None."""
    from django.conf import settings

    if _provider() == "ollama":
        try:
            base_url = (
                getattr(settings, "OLLAMA_BASE_URL", "") or "http://127.0.0.1:11434"
            ).rstrip("/")
            local_model = getattr(settings, "OLLAMA_MODEL", "") or "qwen2.5-coder:7b"
            prompt = f"System:\n{system}\n\nUser:\n{user}\n\nAssistant:"
            resp = requests.post(
                f"{base_url}/api/generate",
                json={
                    "model": local_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.4},
                },
                timeout=45,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            text = (payload.get("response") or "").strip()
            return text or None
        except Exception as exc:
            logger.warning("ai_skills._chat ollama failed: %s", exc)
            return None

    client = _client()
    if not client:
        return None
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.error("ai_skills._chat failed: %s", exc)
        return None


def _chat_with_history(
    system: str, history: list, model: str = SONNET, max_tokens: int = 1024
) -> Optional[str]:
    """Multi-turn Claude call. history = list of {"role": ..., "content": ...}"""
    from django.conf import settings

    if _provider() == "ollama":
        try:
            base_url = (
                getattr(settings, "OLLAMA_BASE_URL", "") or "http://127.0.0.1:11434"
            ).rstrip("/")
            local_model = getattr(settings, "OLLAMA_MODEL", "") or "qwen2.5-coder:7b"
            history_text = "\n".join(
                f"{item.get('role', 'user').capitalize()}: {item.get('content', '')}"
                for item in (history or [])
            )
            prompt = f"System:\n{system}\n\n{history_text}\n\nAssistant:"
            resp = requests.post(
                f"{base_url}/api/generate",
                json={
                    "model": local_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.4},
                },
                timeout=60,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            text = (payload.get("response") or "").strip()
            return text or None
        except Exception as exc:
            logger.warning("ai_skills._chat_with_history ollama failed: %s", exc)
            return None

    client = _client()
    if not client:
        return None
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=history,
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.error("ai_skills._chat_with_history failed: %s", exc)
        return None


# ── Calendar event detection ──────────────────────────────────────────────────

CALENDAR_EVENTS = {
    # (month, day): (name, theme)
    (1, 1): ("New Year's Day", "new beginnings, hope, fresh start"),
    (2, 14): ("Valentine's Day", "love, God's love, relationships"),
    (3, 8): ("International Women's Day", "women, strength, Proverbs 31"),
    (4, 22): ("Earth Day", "creation, stewardship, God's earth"),
    (5, 1): ("Workers' Day", "diligence, work, service"),
    (6, 1): ("Children's Day", "children, faith, innocence"),
    (10, 1): ("Nigerian Independence Day", "nation, patriotism, prayer for the nation"),
    (10, 31): ("Reformation Day", "faith, scripture, salvation by grace"),
    (11, 1): ("All Saints' Day", "saints, perseverance, cloud of witnesses"),
    (12, 25): ("Christmas Day", "incarnation, Jesus birth, Emmanuel"),
    (12, 26): ("Boxing Day", "giving, generosity, service"),
    (12, 31): ("New Year's Eve", "gratitude, reflection, God's faithfulness"),
}

# Easter is dynamic — approximate table for 2024-2030
EASTER_DATES = {
    2024: date(2024, 3, 31),
    2025: date(2025, 4, 20),
    2026: date(2026, 4, 5),
    2027: date(2027, 3, 28),
    2028: date(2028, 4, 16),
    2029: date(2029, 4, 1),
    2030: date(2030, 4, 21),
}


def _calendar_context(today: date) -> str:
    """Return the calendar theme for today, or empty string if none."""
    # Check fixed dates
    key = (today.month, today.day)
    if key in CALENDAR_EVENTS:
        name, theme = CALENDAR_EVENTS[key]
        return f"{name} ({theme})"

    # Check Easter and its season (Good Friday = -2, Easter Sunday = 0, Easter Monday = +1)
    easter = EASTER_DATES.get(today.year)
    if easter:
        delta = (today - easter).days
        if delta == -7:
            return "Palm Sunday (Jesus' triumphal entry, Hosanna)"
        if delta == -2:
            return "Good Friday (crucifixion, atonement, sacrifice)"
        if delta == 0:
            return "Easter Sunday (resurrection, victory over death, He is risen)"
        if delta == 1:
            return "Easter Monday (resurrection hope, walking with the risen Christ)"
        if -14 <= delta <= -3:
            return "Holy Week / Lent (preparation, fasting, drawing closer to God)"
        if 1 < delta <= 7:
            return "Easter week (resurrection joy, new life)"

    # Advent (last 4 Sundays of the year — approximate: Dec 1-24)
    if today.month == 12 and 1 <= today.day <= 24:
        return "Advent season (waiting, hope, preparation for Christmas)"

    # Pentecost Sunday (50 days after Easter)
    if easter:
        pentecost = date(today.year, easter.month, easter.day)
        from datetime import timedelta

        pentecost = easter + timedelta(days=49)
        if today == pentecost:
            return "Pentecost Sunday (Holy Spirit, fire, power, Acts 2)"
        if today == pentecost + timedelta(days=7):
            return "Trinity Sunday (Father, Son, Holy Spirit)"

    return ""


# ── Public skill functions ────────────────────────────────────────────────────


def bible_verse_for_date(today: date = None) -> dict:
    """
    Return a motivating Bible verse themed to today's calendar context.

    Resolution order:
      1. HelloAO API (BSB) — AI selects the reference, API provides the text.
      2. Hardcoded fallback corpus — static verses by weekday.

    AI is used ONLY to pick the reference for a given day/theme.
    Actual verse text is always fetched from HelloAO/cache or the
    hardcoded fallback — never invented by the AI.

    Returns:
        {
          "reference": "John 3:16",
          "text": "For God so loved the world...",
          "theme": "Easter Sunday",   # or "" if generic
          "source": "helloao" | "helloao_cache" | "fallback",
          "translation": "BSB",
        }
    """
    if today is None:
        today = date.today()

    calendar_ctx = _calendar_context(today)
    day_name = today.strftime("%A")

    # ── Step 1: Ask AI to pick a reference (no verse text from AI) ────────
    if calendar_ctx:
        theme_instruction = (
            f"Today is {day_name}, {today.strftime('%B %d')} — {calendar_ctx}. "
            "Choose a Bible verse reference that speaks powerfully to this occasion."
        )
    else:
        theme_instruction = (
            f"Today is {day_name}, {today.strftime('%B %d')}. "
            "Choose an encouraging, motivating Bible verse for church workers and leaders."
        )

    system = (
        "You are a Bible scholar and worship pastor. "
        "Respond ONLY with the verse reference in this exact JSON format, no markdown, no extra text:\n"
        '{"reference": "Book Chapter:Verse"}\n'
        "Do NOT include verse text — only the reference (e.g. John 3:16, Romans 8:28, Psalm 23:1)."
    )
    user = (
        f"{theme_instruction}\n\n"
        "Return a single Bible verse reference in the JSON format specified."
    )

    ai_reference = None
    raw = _chat(system, user, model=HAIKU, max_tokens=80)
    if raw:
        try:
            import json as _json

            cleaned = raw.strip().strip("```json").strip("```").strip()
            data = _json.loads(cleaned)
            ai_reference = data.get("reference", "").strip()
        except Exception as exc:
            logger.warning(
                "bible_verse_for_date AI ref parse failed: %s — raw: %s", exc, raw
            )

    # ── Step 2: Fetch actual verse text from HelloAO API / cache ──────────
    if ai_reference:
        try:
            from bible.services.helloao import search_reference

            results = search_reference(ai_reference, translation="BSB")
            if results:
                v = results[0]
                # Build clean reference label
                ref_label = v.get("reference") or ai_reference
                return {
                    "reference": ref_label,
                    "text": v["text"],
                    "theme": calendar_ctx,
                    "source": "helloao",
                    "translation": "BSB",
                }
        except Exception as exc:
            logger.warning(
                "bible_verse_for_date HelloAO fetch failed for '%s': %s",
                ai_reference,
                exc,
            )

    # ── Step 3: Hardcoded fallback corpus (BSB-aligned text) ──────────────
    FALLBACKS = {
        "Sunday": (
            "Psalm 122:1",
            "I rejoiced with those who said to me, 'Let us go to the house of the LORD.'",
        ),
        "Monday": (
            "Colossians 3:23",
            "Whatever you do, work at it with all your heart, as working for the Lord and not for men.",
        ),
        "Tuesday": (
            "Proverbs 16:3",
            "Commit to the LORD whatever you do, and He will establish your plans.",
        ),
        "Wednesday": (
            "Isaiah 40:31",
            "But those who wait upon the LORD will renew their strength; "
            "they will mount up with wings like eagles; they will run and not grow weary, "
            "they will walk and not faint.",
        ),
        "Thursday": (
            "Philippians 4:13",
            "I can do all things through Christ who strengthens me.",
        ),
        "Friday": (
            "Romans 8:28",
            "And we know that God works all things together for the good of those who love Him, "
            "who are called according to His purpose.",
        ),
        "Saturday": (
            "Joshua 1:9",
            "Have I not commanded you? Be strong and courageous. "
            "Do not be afraid; do not be discouraged, for the LORD your God will be with you wherever you go.",
        ),
    }
    ref, text = FALLBACKS.get(
        day_name,
        (
            "Jeremiah 29:11",
            "For I know the plans I have for you, declares the LORD, "
            "plans to prosper you and not to harm you, plans to give you a future and a hope.",
        ),
    )
    return {
        "reference": ref,
        "text": text,
        "theme": calendar_ctx,
        "source": "fallback",
        "translation": "BSB",
    }


# ── Support Bot ───────────────────────────────────────────────────────────────


# ── TIER CONSTANTS (matches workforce/models.py WorkforceRole) ────────────────
SUPPORT_TIER_GLOBAL = 2  # tiers 1–2 → full platform support
SUPPORT_TIER_SCOPED = 99  # any higher number → scoped (church-admin) support

# ── QUICK PROMPTS split by tier ───────────────────────────────────────────────
# Used by support_views.py; import from here so both places stay in sync.
QUICK_PROMPTS_GLOBAL = [
    "How do I add a guest?",
    "How do I create a setlist?",
    "How do I mark attendance?",
    "What is the induction process?",
    "How do I send a bulk announcement?",
    "How do I upgrade my plan?",
    "How do I manage user roles and permissions?",
    "How do I create a training track?",
]

QUICK_PROMPTS_MEMBER = [
    "How do I mark attendance?",
    "How do I view my unit tasks?",
    "How do I access my training materials?",
    "How do I update my profile?",
    "How do I send a message in my unit?",
    "Who do I contact for account issues?",
]


def _resolve_member_tier(member) -> int:
    """
    Return the effective support tier for a member.
    tier 1 = Church Admin (full access)
    tier 2 = Sub-Admin    (full access)
    anything else          = scoped (church-admin support only)
    """
    if member is None:
        return 99
    try:
        # is_admin shortcut
        if getattr(member, "is_admin", False):
            return 1
        from workforce.models import WorkforceMembershipRole

        roles = WorkforceMembershipRole.raw_objects.filter(
            church=member.church,
            workforce_member__member=member,
            is_active=True,
            role__scope="global",
        ).select_related("role")
        tiers = [r.role.tier for r in roles if r.role.tier in (1, 2)]
        return min(tiers) if tiers else 99
    except Exception:
        return 99


def build_support_system_prompt(church, member=None) -> str:
    """
    Build the system prompt for the support bot.

    Tier 1–2 (admin / sub-admin):
        Full ChurchForce platform knowledge + tenant operational context.
        Can discuss billing, permissions, user management, all modules.

    Tier 3+ (unit member, trainee, etc.):
        Scoped to church-level support only — directs member to their
        church admin for account/access issues. Still answers general
        "how do I use this feature" questions about the member's own
        workflow, but cannot discuss billing or cross-church operations.
    """
    from django.utils import timezone

    tier = _resolve_member_tier(member)
    is_admin_tier = tier <= SUPPORT_TIER_GLOBAL

    if is_admin_tier:
        # ── Full ChurchForce support prompt ──────────────────────────────────
        lines = [
            "You are ChurchForce Support, the intelligent first-line support assistant "
            "for ChurchForce — a church workforce management platform built for Nigerian "
            "and African churches.",
            "",
            "Your role: help church administrators use ChurchForce effectively. "
            "Answer questions about features, guide them through workflows, explain "
            "billing and plan differences, and escalate clearly when something needs "
            "human support (hello@churchforce.io).",
            "",
            "Tone: warm, concise, pastoral yet professional. Speak directly to church "
            "administrators. Use clear, simple English.",
            "",
            "Capabilities you know about: Workforce management, Guest pipeline, LMS & "
            "induction, Units & groups, Attendance tracking, Chat rooms, Events & "
            "setlists, Music module (tracks, chord charts, rehearsals), Media module "
            "(presentations, lyric slides, Bible passages), Billing & subscriptions, "
            "Permissions system, Training tracks, Campus management, Bulk member upload.",
            "",
            "IMPORTANT: You do not have access to individual member records, guest names, "
            "financial transactions, or message content. If asked for specific personal "
            "data, redirect to the in-app views.",
        ]

        # Opt-in tenant context (insensitive operational data only)
        try:
            sub = getattr(church, "churchsubscription", None)
            plan_name = sub.plan.name if sub and sub.plan else "unknown"
            settings_obj = getattr(church, "settings", None)

            from accounts.models import ChurchMember
            from workforce.models import WorkforceMember
            from units.models import ChurchUnit

            member_count = ChurchMember.raw_objects.filter(
                church=church, is_active=True
            ).count()
            workforce_count = WorkforceMember.raw_objects.filter(
                church=church, is_active=True
            ).count()
            unit_count = ChurchUnit.raw_objects.filter(
                church=church, is_active=True
            ).count()

            modules_on = []
            if settings_obj:
                for flag, label in [
                    ("enable_music_module", "Music"),
                    ("enable_media_module", "Media"),
                    ("enable_children_module", "Children"),
                    ("enable_youth_module", "Youth"),
                    ("enable_teenagers_module", "Teenagers"),
                ]:
                    if getattr(settings_obj, flag, False):
                        modules_on.append(label)

            lines += [
                "",
                "── Tenant context (non-sensitive operational data) ──",
                f"Church: {church.name}",
                f"Plan: {plan_name}",
                f"Active members: {member_count}",
                f"Workforce members: {workforce_count}",
                f"Units/groups: {unit_count}",
                f"Active modules: {', '.join(modules_on) or 'Standard'}",
                "",
                "Use this context to give relevant advice, e.g. suggest the Music "
                "module if they ask about setlists and it is enabled, or explain "
                "routing options (path URL, subdomain, custom domain) if they ask "
                "how people reach their church online.",
            ]
        except Exception as exc:
            logger.debug("build_support_system_prompt context failed: %s", exc)

        lines += [
            "",
            "If you cannot answer a question or need to escalate, say: "
            "'For this, please contact our support team at hello@churchforce.io "
            "or use the in-app feedback button.'",
            "",
            "Never make up feature names or claim ChurchForce has features it "
            "doesn't have. If unsure, say you're not certain and recommend checking "
            "the Help Centre.",
        ]

    else:
        # ── Scoped member support prompt ─────────────────────────────────────
        church_name = church.name if church else "your church"
        admin_name = (
            "your church admin"  # could be enriched with actual name if desired
        )

        lines = [
            f"You are the {church_name} ChurchForce assistant, helping workforce "
            f"members with their day-to-day use of the platform.",
            "",
            "Your role: answer general 'how do I use this feature' questions about "
            "the member's own dashboard — attendance, tasks, chat, unit tools, "
            "training progress, and profile settings.",
            "",
            "IMPORTANT RESTRICTIONS:",
            "- You CANNOT discuss billing, subscriptions, or plan management — "
            f"  direct the member to {admin_name} for those.",
            "- You CANNOT manage accounts, change roles, or access admin features.",
            "- For account access issues, password problems, or permission changes, "
            f"  always say: 'Please contact {admin_name} directly for this.'",
            "- You do not have access to personal data, financial records, or other "
            "  members' information.",
            "",
            "Tone: helpful, warm, brief. Always direct unresolvable issues to the "
            f"church admin ({admin_name}).",
        ]

    return "\n".join(lines)


def support_reply(church, history: list, user_message: str, member=None) -> str:
    """
    Generate a support bot reply, tier-scoped.

    Tier 1-2: full ChurchForce platform support
    Tier 3+:  scoped to church-level member support, directs to admin
    """
    system = build_support_system_prompt(church, member)
    messages = list(history) + [{"role": "user", "content": user_message}]
    reply = _chat_with_history(system, messages, model=SONNET, max_tokens=600)

    if reply:
        return reply

    tier = _resolve_member_tier(member)
    if tier <= SUPPORT_TIER_GLOBAL:
        return (
            "I'm having trouble connecting right now. "
            "Please try again in a moment, or contact our team at hello@churchforce.io."
        )
    return (
        "I'm having trouble connecting right now. "
        "Please try again shortly, or contact your church admin directly."
    )


# ── Lyrics structuring ────────────────────────────────────────────────────────


def structure_lyrics(
    raw_lyrics: str, title: str = "", artist: str = ""
) -> Optional[list]:
    """
    Ask Claude to parse raw lyrics text into structured sections.

    Returns a list of dicts:
        [{"type": "verse", "label": "Verse 1", "content": "..."}, ...]
    or None on failure.
    """
    system = (
        "You are a music editor. Parse the given raw lyrics into sections "
        "(intro, verse, chorus, bridge, outro, tag, etc.). "
        "Respond ONLY with a JSON array, no markdown:\n"
        '[{"type": "verse", "label": "Verse 1", "content": "line1\\nline2"}, ...]'
    )

    context = f"Song: {title}" + (f" by {artist}" if artist else "")
    user = f"{context}\n\nRaw lyrics:\n{raw_lyrics[:3000]}"

    raw = _chat(system, user, model=HAIKU, max_tokens=1500)
    if not raw:
        return None

    try:
        import json

        raw = raw.strip().strip("```json").strip("```").strip()
        sections = json.loads(raw)
        if isinstance(sections, list):
            return sections
    except Exception as exc:
        logger.warning("structure_lyrics parse failed: %s", exc)

    return None


def analyze_song_structure(
    title: str = "",
    artist: str = "",
    lyrics: str = "",
    sections: Optional[List[dict]] = None,
    key: str = "",
    tempo: int = None,
) -> dict:
    """
    Deep analysis of a worship song for rehearsals, mix prep, and presentation sync.

    Returns a structured dict with keys:
        summary, flow_analysis, performance_cues, arrangement_notes,
        key_notes, tempo_notes, leading_tips, transitions, structure_map,
        mixer_notes, presentation_sync, markdown (legacy plain-text fallback)

    Falls back to a deterministic helper dict if AI is unavailable.
    """
    import json as _json

    sections = sections or []
    section_lines = []
    for idx, section in enumerate(sections[:24], start=1):
        label = section.get("label") or section.get("type") or f"Section {idx}"
        content = (section.get("content") or "").strip().replace("\n", " ")
        section_lines.append(f"{idx}. {label}: {content[:160]}")

    system = (
        "You are an expert worship music director, sound engineer, and arrangement coach. "
        "Analyse the given song and respond ONLY with a valid JSON object — "
        "no markdown fences, no commentary, just raw JSON.\n\n"
        "Required keys:\n"
        "  summary (str): 2-3 sentence overview of feel and congregational role.\n"
        "  flow_analysis (str): How sections build and release energy.\n"
        "  performance_cues (list[str]): 4-6 specific cues for musicians.\n"
        "  arrangement_notes (str): Instrumentation suggestions for various contexts.\n"
        "  key_notes (str): Notes on the key — range suitability, modulation options.\n"
        "  tempo_notes (str): Tempo feel, groove, and drive considerations.\n"
        "  leading_tips (list[str]): 3-5 tips for the worship leader.\n"
        "  transitions (list[str]): Suggested songs to transition to/from.\n"
        "  structure_map (list[{section, energy}]): Each section with energy level 1-10.\n"
        "  mixer_notes (str): Stem-isolation strategy, stem priority, EQ/compression tips.\n"
        "  presentation_sync (list[str]): Slide-cue notes synced to song sections."
    )
    parts = [f"Title: {title or 'Unknown'}", f"Artist: {artist or 'Unknown'}"]
    if key:
        parts.append(f"Key: {key}")
    if tempo:
        parts.append(f"Tempo: {tempo} BPM")
    if section_lines:
        parts.append("Sections:\n" + "\n".join(section_lines))
    elif lyrics:
        parts.append(f"Lyrics (excerpt):\n{lyrics[:3000]}")

    raw = _chat(system, "\n".join(parts), model=SONNET, max_tokens=1400)

    if raw:
        try:
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            result = _json.loads(cleaned)
            if isinstance(result, dict):
                # attach a legacy markdown field for backward compat
                result.setdefault("markdown", _dict_to_markdown(result))
                return result
        except Exception as exc:
            logger.warning("analyze_song_structure JSON parse failed: %s", exc)
            # Return as plain-text wrapped in the dict format
            return _plain_to_analysis_dict(raw, title)

    # Deterministic fallback
    return _fallback_analysis(title, sections)


def _dict_to_markdown(d: dict) -> str:
    parts = []
    if d.get("summary"):
        parts.append(f"### Overview\n{d['summary']}")
    if d.get("flow_analysis"):
        parts.append(f"### Flow\n{d['flow_analysis']}")
    if d.get("performance_cues"):
        parts.append(
            "### Performance Cues\n"
            + "\n".join(f"- {c}" for c in d["performance_cues"])
        )
    if d.get("mixer_notes"):
        parts.append(f"### Mixer Notes\n{d['mixer_notes']}")
    if d.get("presentation_sync"):
        parts.append(
            "### Presentation Sync\n"
            + "\n".join(f"- {c}" for c in d["presentation_sync"])
        )
    return "\n\n".join(parts)


def _plain_to_analysis_dict(text: str, title: str) -> dict:
    return {
        "summary": text[:400],
        "flow_analysis": "",
        "performance_cues": [],
        "arrangement_notes": "",
        "key_notes": "",
        "tempo_notes": "",
        "leading_tips": [],
        "transitions": [],
        "structure_map": [],
        "mixer_notes": "",
        "presentation_sync": [],
        "markdown": text,
    }


def _fallback_analysis(title: str, sections: list) -> dict:
    count = len(sections)
    return {
        "summary": (
            f"{title or 'This song'} has {count} structured section(s). "
            "Use a clear intro → verse → chorus energy arc."
        ),
        "flow_analysis": "Build energy from the verse into the chorus; use a dynamic drop for the bridge.",
        "performance_cues": [
            "Lock click and count-in before verse entry.",
            "Isolate rhythm section first, then layer pads and vocals.",
            "Cue the bridge with a clear visual from the MD.",
            "Leave space in the final chorus for congregational response.",
        ],
        "arrangement_notes": "Full band: keys pad, guitar strums, bass locks to kick. Acoustic: guitar + cajon + vocals only.",
        "key_notes": "Confirm key is within congregational range (A3–D5 for mixed voice).",
        "tempo_notes": "Maintain click discipline; no rushing into the chorus.",
        "leading_tips": [
            "Introduce the chorus melody before the song to help the congregation learn it.",
            "Make eye contact during the bridge to signal the dynamic shift.",
        ],
        "transitions": [],
        "structure_map": [
            {
                "section": s.get("label") or s.get("type", f"Section {i+1}"),
                "energy": min(10, 4 + i * 2),
            }
            for i, s in enumerate(sections[:6])
        ],
        "mixer_notes": "Prioritise vocals and kick/snare on the main mix; pull pads back in verses.",
        "presentation_sync": [
            "One slide per lyrical phrase block.",
            "Align chorus repeats with repeated slide cues.",
            "Blank slide on instrumental bridge intros.",
        ],
        "markdown": (
            "### Overview\n"
            f"{title or 'This song'} has {count} structured section(s).\n\n"
            "### Rehearsal Cues\n"
            "- Lock click and count-in.\n"
            "- Isolate rhythm section first.\n\n"
            "### Presentation Sync\n"
            "- One slide per phrase block.\n"
            "- Blank slide on bridge.\n"
        ),
    }


# ── Evangelism Research ───────────────────────────────────────────────────────


def evangelism_research(church, guest_patterns: dict) -> Optional[str]:
    """
    Generate actionable missionary research for the guest management unit.

    guest_patterns: sanitised analytics dict — counts, channels, status
    distribution, time trends. No personal data.

    Returns a markdown-formatted research brief.
    """
    settings_obj = getattr(church, "settings", None)
    values = getattr(settings_obj, "core_values", []) or []
    vision = getattr(settings_obj, "vision_statement", "") or ""
    denomination = getattr(settings_obj, "denominational_affiliation", "") or ""
    church_name = church.name

    values_str = (
        "; ".join(v.get("title", "") for v in values[:5]) if values else "Not specified"
    )

    system = (
        "You are a missionary research analyst specialising in African church growth "
        "and urban evangelism, with deep knowledge of Nigerian social dynamics, "
        "community structures, and redemptive cultural bridges. "
        "You combine data-driven insight with theological wisdom. "
        "Write in clear, pastoral English — concise but substantive. "
        "Use markdown with headers (##) and bullet points."
    )

    user = (
        f"Church: {church_name}\n"
        f"Denomination: {denomination or 'Not specified'}\n"
        f"Vision: {vision or 'Not specified'}\n"
        f"Core values: {values_str}\n\n"
        f"Guest intake analytics (last 90 days):\n"
        f"{_format_patterns(guest_patterns)}\n\n"
        "Based on this data, produce a 400-600 word missionary research brief covering:\n"
        "1. **Outreach Effectiveness** — which channels are working and why\n"
        "2. **Pipeline Leakage Analysis** — where guests are dropping off and probable causes\n"
        "3. **Target Demographics** — who is responding and who is not being reached\n"
        "4. **Strategic Recommendations** — 3-5 specific, actionable evangelism strategies "
        "grounded in the church's values and the Nigerian/regional context\n"
        "5. **Scriptural Framing** — one relevant passage to anchor the strategy\n\n"
        "Ground recommendations in real Nigerian social dynamics (e.g. family influence, "
        "workplace witness, social media, community events). Be specific, not generic."
    )

    return _chat(system, user, model=SONNET, max_tokens=1200)


def _format_patterns(patterns: dict) -> str:
    lines = []
    for k, v in patterns.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines) if lines else "  No data available."


# ── Dashboard Analysis ────────────────────────────────────────────────────────


def dashboard_analysis(
    church, user_tier: int, unit_name: str, metrics: dict
) -> Optional[str]:
    """
    Generate a brief, tier-appropriate dashboard insight narrative.

    user_tier: 1=Admin, 2=Sub-Admin, 3=Overseer, 4=Unit Head, 5=Assistant, 6=Member
    metrics: sanitised counts dict — attendance rate, task completion, guest stats, etc.
    Returns 2-4 sentence insight string for the dashboard widget.
    """
    tier_roles = {
        1: "church administrator",
        2: "sub-administrator / cross-unit operator",
        3: "unit overseer",
        4: "unit head",
        5: "assistant unit head",
        6: "workforce member",
    }
    role_label = tier_roles.get(user_tier, "workforce member")

    settings_obj = getattr(church, "settings", None)
    values = getattr(settings_obj, "core_values", []) or []
    values_str = "; ".join(v.get("title", "") for v in values[:3]) if values else ""

    system = (
        "You are a church operations analyst. Generate a brief, encouraging, "
        "insight-driven narrative (2-4 sentences max) for a dashboard widget. "
        "Be specific to the numbers given. Tone: pastoral, motivating, data-aware. "
        "No markdown. Plain text only."
    )

    context = f"Role: {role_label}"
    if unit_name:
        context += f" | Unit: {unit_name}"
    if values_str:
        context += f" | Church values: {values_str}"

    user = (
        f"{context}\n\nMetrics:\n{_format_patterns(metrics)}\n\n"
        "Write 2-4 sentences summarising performance, highlighting what's going well "
        "and one area to focus on. Speak directly to this person's role."
    )

    return _chat(system, user, model=HAIKU, max_tokens=150)


# ── Birthday Messages ─────────────────────────────────────────────────────────


def birthday_message_workforce(
    member_name: str,
    unit_name: str,
    church_name: str,
    church_values: list,
    sermon_summary: str = "",
) -> str:
    """
    Generate a personalised birthday message for a workforce member.
    Themed to the church's identity and unit role.
    """
    values_str = (
        "; ".join(v.get("title", "") for v in church_values[:3])
        if church_values
        else ""
    )

    system = (
        "You are a caring church pastor writing a warm, personalised birthday message "
        "to a dedicated church workforce member. Keep it 3-5 sentences. "
        "Spiritual but not preachy. Celebratory and affirming of their service. "
        "Include one Bible verse reference (do not quote in full). Plain text."
    )

    extras = []
    if sermon_summary:
        extras.append(f"Recent sermon theme: {sermon_summary[:200]}")
    if values_str:
        extras.append(f"Church values: {values_str}")

    user = (
        f"Name: {member_name}\n"
        f"Unit/Group: {unit_name}\n"
        f"Church: {church_name}\n"
        + ("\n".join(extras) + "\n" if extras else "")
        + "\nWrite a warm birthday message."
    )

    result = _chat(system, user, model=HAIKU, max_tokens=180)
    return result or (
        f"Happy birthday, {member_name}! 🎉 Your faithfulness in {unit_name} is a "
        f"blessing to {church_name}. May God enlarge your territory this new year. "
        f"— The Team"
    )


def birthday_message_guest(guest_name: str, guest_status: str, church_name: str) -> str:
    """
    Generate a redemptive, Bible-based birthday message for a guest.
    Intentional but not pressuring — meets the guest where they are.
    """
    status_context = {
        "new-guest": "visited once and we haven't spoken much yet",
        "in-contact": "we've been in touch and building a relationship",
        "committed": "has been attending regularly and is exploring faith",
        "planted": "has joined the church family",
    }.get(guest_status, "is in our guest outreach")

    system = (
        "You are a warm, non-pushy church outreach worker writing a birthday message "
        "to someone connected to the church. The message should feel personal and "
        "loving, with a redemptive/Biblical theme but not preachy. "
        "2-3 sentences. One Bible verse reference. Plain text."
    )

    user = (
        f"Guest name: {guest_name}\n"
        f"Context: This person {status_context}.\n"
        f"Church: {church_name}\n\n"
        "Write a warm birthday message that feels personal and reflects God's love."
    )

    result = _chat(system, user, model=HAIKU, max_tokens=150)
    return result or (
        f"Happy birthday, {guest_name}! 🎂 "
        f"Wishing you a day filled with joy. "
        f"'For I know the plans I have for you, declares the LORD.' — Jeremiah 29:11"
    )


def sermon_summary_for_period(events: list) -> str:
    """
    Summarise sermon/event themes from a list of recent events.

    events: list of dicts with keys: name, date, event_type, notes (optional)
    Returns a 1-2 sentence summary of the spiritual season, or "".
    """
    if not events:
        return ""

    event_lines = []
    for e in events[:5]:
        name = e.get("name", "Service")
        date = e.get("date", "")
        notes = e.get("notes", "")
        line = f"- {date}: {name}"
        if notes:
            line += f" ({notes[:100]})"
        event_lines.append(line)

    system = (
        "You are a church communications assistant. In 1-2 sentences, summarise "
        "the spiritual theme or season from these recent church events. "
        "Plain text, warm and pastoral."
    )
    user = "Recent services:\n" + "\n".join(event_lines)

    return _chat(system, user, model=HAIKU, max_tokens=100) or ""
