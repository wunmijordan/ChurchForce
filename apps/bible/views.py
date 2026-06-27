"""
bible/views.py

All Bible feature views. All search is direct to HelloAO API — no AI.
"""

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_GET

logger = logging.getLogger(__name__)


def _church(request):
    return getattr(request, "church", None)


def _member(request):
    return getattr(request, "member", None)


def _can(request, p):
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can(p))


def _is_admin(request):
    return request.user.is_superuser or _can(request, "dashboard.admin")


def _je(msg, status=400):
    return JsonResponse({"ok": False, "error": msg}, status=status)


def _today():
    return timezone.localdate()


def _church_date_format(church, default="%b. %d, %Y"):
    try:
        from tenants.time_utils import get_effective_date_format

        return get_effective_date_format(church, default)
    except Exception:
        return default


def _church_time_format(church, default="%H:%M"):
    try:
        from tenants.time_utils import get_effective_time_format

        return get_effective_time_format(church, default)
    except Exception:
        return default


def _scoped_path(request, path):
    try:
        from accounts.views import _scoped_url

        return _scoped_url(request, path)
    except Exception:
        return path


# ── Member Bible dashboard ────────────────────────────────────────────────────


@login_required
def bible_dashboard(request):
    church = _church(request)
    member = _member(request)
    if not church:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()

    today = _today()
    from django.db.models import Q
    from bible.models import (
        ReadingPlan,
        MemberPlanProgress,
        MemoryVerse,
        BibleStudyReference,
        MemberMemoryVerseProgress,
        MemberStudyProgress,
    )
    from bible.services.badges import get_member_stats

    # Active plans
    plan_context = []
    completed_entry_ids = set()
    if member:
        try:
            completed_entry_ids = set(
                MemberPlanProgress.raw_objects.filter(
                    church=church, member=member
                ).values_list("entry_id", flat=True)
            )
        except Exception:
            pass

    try:
        active_plans = list(
            ReadingPlan.raw_objects.filter(
                church=church,
                is_published=True,
                is_active=True,
                start_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .prefetch_related("entries")[:5]
        )
    except Exception:
        active_plans = []

    for plan in active_plans:
        try:
            entries_qs = plan.entries.filter(is_active=True).order_by(
                "scheduled_date", "order"
            )
            dates = list(
                entries_qs.exclude(scheduled_date=None)
                .values_list("scheduled_date", flat=True)
                .distinct()
                .order_by("scheduled_date")
            )
            selected_date = None
            if dates:
                past_dates = [d for d in dates if d <= today]
                selected_date = (
                    today
                    if today in dates
                    else (past_dates[-1] if past_dates else dates[0])
                )
                selected_entries = list(entries_qs.filter(scheduled_date=selected_date))
            else:
                selected_entries = list(
                    entries_qs.exclude(id__in=completed_entry_ids)[:1]
                ) or list(entries_qs[:1])

            if selected_entries:
                total = entries_qs.count()
                done = (
                    MemberPlanProgress.raw_objects.filter(
                        church=church, member=member, plan=plan
                    ).count()
                    if member
                    else 0
                )
                pct = round((done / total * 100) if total else 0)
                plan_context.append(
                    {
                        "plan": plan,
                        "entry": selected_entries[0],
                        "entries": selected_entries,
                        "selected_date": selected_date,
                        "completed_entry_ids": completed_entry_ids,
                        "progress_pct": pct,
                        "completed_count": done,
                        "total_count": total,
                    }
                )
        except Exception as exc:
            logger.warning("bible_dashboard plan: %s", exc)

    # Memory verse
    memory_verse = memory_completed = None
    try:
        memory_verse = (
            MemoryVerse.raw_objects.filter(
                church=church,
                is_published=True,
                is_active=True,
                active_date__lte=today,
            )
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
            .order_by("-active_date")
            .first()
        )
        if memory_verse and member:
            memory_completed = MemberMemoryVerseProgress.raw_objects.filter(
                church=church, member=member, verse=memory_verse
            ).exists()
    except Exception as exc:
        logger.warning("bible_dashboard memory verse: %s", exc)

    # Study ref
    study_ref = study_completed = None
    try:
        study_ref = (
            BibleStudyReference.raw_objects.filter(
                church=church,
                is_published=True,
                is_active=True,
            )
            .filter(Q(active_date__isnull=True) | Q(active_date__lte=today))
            .order_by("-active_date")
            .first()
        )
        if study_ref and member:
            study_completed = MemberStudyProgress.raw_objects.filter(
                church=church, member=member, study=study_ref
            ).exists()
    except Exception as exc:
        logger.warning("bible_dashboard study: %s", exc)

    stats = get_member_stats(member, church) if member else {}

    # ── Units/groups for discussion scope selector ────────────────────────
    member_units = []
    all_units = []
    try:
        from units.models import ChurchUnit, UnitMembership
        from django.db import models

        if member:
            member_unit_ids = (
                UnitMembership.raw_objects.filter(church=church, is_active=True)
                .filter(
                    models.Q(workforce_member__member=member)
                    | models.Q(trainee_profile__member=member)
                )
                .values_list("unit_id", flat=True)
            )
            member_units = list(
                ChurchUnit.raw_objects.filter(
                    church=church, id__in=member_unit_ids, is_active=True
                ).values("id", "name", "unit_type")
            )
        if _is_admin(request):
            all_units = list(
                ChurchUnit.raw_objects.filter(church=church, is_active=True)
                .order_by("unit_type", "name")
                .values("id", "name", "unit_type")
            )
    except Exception as exc:
        logger.warning("bible_dashboard units: %s", exc)

    # ── Plan date map: for each plan, build ordered list of unique scheduled dates ──
    import json as _json

    plan_dates_json = {}
    for ctx in plan_context:
        plan = ctx["plan"]
        try:
            dates = list(
                plan.entries.filter(is_active=True)
                .exclude(scheduled_date=None)
                .values_list("scheduled_date", flat=True)
                .distinct()
                .order_by("scheduled_date")
            )
            if dates:
                plan_dates_json[plan.id] = [d.isoformat() for d in dates]
            else:
                entry = ctx.get("entry")
                plan_dates_json[plan.id] = (
                    [entry.scheduled_date.isoformat()]
                    if entry and entry.scheduled_date
                    else []
                )
        except Exception:
            plan_dates_json[plan.id] = []

    return render(
        request,
        "bible/bible_dashboard.html",
        {
            "plan_context": plan_context,
            "memory_verse": memory_verse,
            "memory_completed": memory_completed,
            "study_ref": study_ref,
            "study_completed": study_completed,
            "stats": stats,
            "is_admin": _is_admin(request),
            "today": today,
            "church": church,
            "page_title": "Bible",
            "member_units": member_units,
            "all_units": all_units,
            "plan_dates_json": _json.dumps(plan_dates_json),
            "church_date_format": _church_date_format(church, "%b. %d, %Y"),
            "church_time_format": _church_time_format(church, "%H:%M"),
        },
    )


# ── Admin: Reading Plans ──────────────────────────────────────────────────────


@login_required
def admin_plan_list(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import ReadingPlan

    plans = ReadingPlan.raw_objects.filter(church=church, is_active=True).order_by(
        "-start_date"
    )
    return render(
        request,
        "bible/admin/plan_list.html",
        {"plans": plans, "church": church, "page_title": "Bible Reading Plans"},
    )


@login_required
def admin_plan_create(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import ReadingPlan, ReadingPlanEntry
    from django.contrib import messages

    error = None
    if request.method == "POST":
        try:
            plan = ReadingPlan.raw_objects.create(
                church=church,
                title=request.POST.get("title", "").strip(),
                description=request.POST.get("description", "").strip(),
                frequency=request.POST.get("frequency", "daily"),
                start_date=request.POST.get("start_date"),
                end_date=request.POST.get("end_date") or None,
                translation=request.POST.get("translation", "BSB").strip().upper(),
                is_published=request.POST.get("is_published") == "on",
                created_by=request.user,
            )
            entry_mode = request.POST.get("entry_mode", "passage")

            def _parse_date(s):
                s = (s or "").strip()
                if not s:
                    return None
                try:
                    from datetime import date as _date

                    return _date.fromisoformat(s)
                except ValueError:
                    return None

            def _build_passage_ref(
                book_id_val, ch_from, ch_to, v_from, v_to, books_cache
            ):
                """Build a human-readable passage reference from dropdown values."""
                # Look up book name from cache
                book_name = book_id_val
                for b in books_cache:
                    if b["id"] == book_id_val:
                        book_name = b.get("name") or book_id_val
                        break
                if not ch_from:
                    return book_name
                ref = f"{book_name} {ch_from}"
                if ch_to and int(ch_to) != int(ch_from):
                    ref += f"-{ch_to}"
                if v_from:
                    ref += f":{v_from}"
                    if v_to and int(v_to) != int(v_from):
                        ref += f"-{v_to}"
                return ref

            # Fetch book names for passage reference building (best-effort)
            _books_for_ref = []
            _get_books = None
            try:
                from bible.services.router import get_books as _get_books

                _books_for_ref = _get_books(plan.translation)
            except Exception:
                pass

            if entry_mode == "devotional":
                # ── Devotional entries from the structured builder ─────────
                dev_dates = request.POST.getlist("dev_date[]")
                dev_books = request.POST.getlist("dev_book[]")
                dev_ch_from = request.POST.getlist("dev_ch_from[]")
                dev_ch_to = request.POST.getlist("dev_ch_to[]")
                dev_v_from = request.POST.getlist("dev_v_from[]")
                dev_v_to = request.POST.getlist("dev_v_to[]")
                dev_trans = request.POST.getlist("dev_translation[]")
                dev_titles = request.POST.getlist("dev_title[]")
                dev_bodies = request.POST.getlist("dev_body[]")
                dev_refs = request.POST.getlist("dev_refs[]")

                count = len(dev_books)
                for i in range(count):

                    def _g(lst, idx, default=""):
                        return lst[idx].strip() if idx < len(lst) else default

                    book_id_val = _g(dev_books, i, "")
                    if not book_id_val:
                        continue
                    ch_from = _g(dev_ch_from, i) or "1"
                    ch_to = _g(dev_ch_to, i) or ch_from
                    v_from = _g(dev_v_from, i) or ""
                    v_to = _g(dev_v_to, i) or ""
                    entry_trans = (_g(dev_trans, i) or plan.translation).upper()

                    # Build book cache for this entry's translation if different
                    entry_books = _books_for_ref
                    if entry_trans != plan.translation:
                        try:
                            entry_books = _get_books(entry_trans)
                        except Exception:
                            entry_books = _books_for_ref

                    passage = _build_passage_ref(
                        book_id_val, ch_from, ch_to, v_from, v_to, entry_books
                    )
                    scheduled_date = _parse_date(_g(dev_dates, i))

                    raw_refs = _g(dev_refs, i)
                    inline_refs = [
                        {"reference": r.strip()}
                        for r in raw_refs.split(",")
                        if r.strip()
                    ]
                    # Devotional body: typed text OR uploaded file
                    dev_body_text = _g(dev_bodies, i)
                    uploaded_body_file = request.FILES.getlist("dev_body_file[]")
                    if not dev_body_text and i < len(uploaded_body_file):
                        ufile = uploaded_body_file[i]
                        try:
                            name = ufile.name.lower()
                            if name.endswith(".txt") or name.endswith(".md"):
                                dev_body_text = (
                                    ufile.read()
                                    .decode("utf-8", errors="replace")
                                    .strip()
                                )
                            elif name.endswith(".pdf"):
                                try:
                                    import pypdf

                                    reader = pypdf.PdfReader(ufile)
                                    dev_body_text = "\n".join(
                                        p.extract_text() or "" for p in reader.pages
                                    ).strip()
                                except ImportError:
                                    dev_body_text = "[PDF uploaded — pypdf not installed for extraction]"
                            elif name.endswith(".docx"):
                                try:
                                    import docx as _docx
                                    from io import BytesIO

                                    doc = _docx.Document(BytesIO(ufile.read()))
                                    dev_body_text = "\n".join(
                                        p.text for p in doc.paragraphs if p.text.strip()
                                    )
                                except ImportError:
                                    dev_body_text = "[DOCX uploaded — python-docx not installed for extraction]"
                        except Exception as ex:
                            logger.warning("devotional file extract failed: %s", ex)

                    ReadingPlanEntry.raw_objects.create(
                        church=church,
                        plan=plan,
                        order=i + 1,
                        entry_type="devotional",
                        scheduled_date=scheduled_date,
                        translation=entry_trans,
                        passage_reference=passage,
                        devotional_title=_g(dev_titles, i),
                        devotional_body=dev_body_text,
                        inline_references=inline_refs,
                        book_id=book_id_val,
                        chapter=int(ch_from) if ch_from else None,
                        chapter_to=(
                            int(ch_to)
                            if ch_to and int(ch_to) != int(ch_from or 1)
                            else None
                        ),
                        verse_start=int(v_from) if v_from else None,
                        verse_end=int(v_to) if v_to else None,
                    )
            else:
                # ── Passage list — structured dropdown entries ─────────────
                entry_books_list = request.POST.getlist("entry_book[]")
                entry_ch_from = request.POST.getlist("entry_ch_from[]")
                entry_ch_to = request.POST.getlist("entry_ch_to[]")
                entry_v_from = request.POST.getlist("entry_v_from[]")
                entry_v_to = request.POST.getlist("entry_v_to[]")
                entry_trans_list = request.POST.getlist("entry_translation[]")
                entry_dates = request.POST.getlist("entry_date[]")

                for i, book_id_val in enumerate(entry_books_list):
                    book_id_val = book_id_val.strip()
                    if not book_id_val:
                        continue

                    def _g(lst, idx, default=""):
                        return lst[idx].strip() if idx < len(lst) else default

                    ch_from = _g(entry_ch_from, i) or "1"
                    ch_to = _g(entry_ch_to, i) or ch_from
                    v_from = _g(entry_v_from, i) or ""
                    v_to = _g(entry_v_to, i) or ""
                    entry_trans = (_g(entry_trans_list, i) or plan.translation).upper()

                    entry_books = _books_for_ref
                    if entry_trans != plan.translation:
                        try:
                            entry_books = _get_books(entry_trans)
                        except Exception:
                            entry_books = _books_for_ref

                    passage = _build_passage_ref(
                        book_id_val, ch_from, ch_to, v_from, v_to, entry_books
                    )
                    scheduled_date = _parse_date(_g(entry_dates, i))

                    ReadingPlanEntry.raw_objects.create(
                        church=church,
                        plan=plan,
                        order=i + 1,
                        entry_type="passage",
                        scheduled_date=scheduled_date,
                        translation=entry_trans,
                        passage_reference=passage,
                        book_id=book_id_val,
                        chapter=int(ch_from) if ch_from else None,
                        chapter_to=(
                            int(ch_to)
                            if ch_to and int(ch_to) != int(ch_from or 1)
                            else None
                        ),
                        verse_start=int(v_from) if v_from else None,
                        verse_end=int(v_to) if v_to else None,
                    )
            messages.success(request, f"Reading plan '{plan.title}' created.")
            return redirect("bible:admin_plan_list")
        except Exception as exc:
            logger.error("admin_plan_create POST: %s", exc)
            error = str(exc)

    return render(
        request,
        "bible/admin/plan_form.html",
        {
            "church": church,
            "page_title": "Create Reading Plan",
            "error": error,
            "entries_data_json": "[]",
            "frequency_choices": [
                ("daily", "Daily"),
                ("weekly", "Weekly"),
                ("monthly", "Monthly"),
                ("quarterly", "Quarterly"),
                ("yearly", "Yearly"),
            ],
            "church_date_format": _church_date_format(church, "%d %b %Y"),
        },
    )


@login_required
def admin_plan_edit(request, plan_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import ReadingPlan, ReadingPlanEntry
    from django.contrib import messages

    plan = get_object_or_404(ReadingPlan, id=plan_id, church=church)
    error = None
    if request.method == "POST":
        try:
            plan.title = request.POST.get("title", plan.title).strip()
            plan.description = request.POST.get("description", plan.description).strip()
            plan.frequency = request.POST.get("frequency", plan.frequency)
            plan.start_date = request.POST.get("start_date", str(plan.start_date))
            plan.end_date = request.POST.get("end_date") or None
            plan.translation = (
                request.POST.get("translation", plan.translation).strip().upper()
            )
            plan.is_published = request.POST.get("is_published") == "on"
            plan.save()

            posted_passages = bool(request.POST.getlist("entry_book[]"))
            posted_devotionals = bool(request.POST.getlist("dev_book[]"))
            if posted_passages or posted_devotionals:
                plan.entries.filter(is_active=True).update(is_active=False)

                def _parse_date(s):
                    s = (s or "").strip()
                    if not s:
                        return None
                    try:
                        from datetime import date as _date

                        return _date.fromisoformat(s)
                    except ValueError:
                        return None

                def _g(lst, idx, default=""):
                    return lst[idx].strip() if idx < len(lst) else default

                def _build_passage_ref(
                    book_id_val, ch_from, ch_to, v_from, v_to, books_cache
                ):
                    book_name = book_id_val
                    for b in books_cache:
                        if b["id"] == book_id_val:
                            book_name = b.get("name") or book_id_val
                            break
                    if not ch_from:
                        return book_name
                    ref = f"{book_name} {ch_from}"
                    if ch_to and int(ch_to) != int(ch_from):
                        ref += f"-{ch_to}"
                    if v_from:
                        ref += f":{v_from}"
                        if v_to and int(v_to) != int(v_from):
                            ref += f"-{v_to}"
                    return ref

                try:
                    from bible.services.router import get_books as _get_books
                except Exception:
                    _get_books = None

                _books_by_translation = {}

                def _books_for(translation):
                    translation = (translation or plan.translation or "BSB").upper()
                    if translation not in _books_by_translation:
                        try:
                            _books_by_translation[translation] = (
                                _get_books(translation) if _get_books else []
                            )
                        except Exception:
                            _books_by_translation[translation] = []
                    return _books_by_translation[translation]

                entry_mode = request.POST.get("entry_mode", "passage")
                if entry_mode == "devotional":
                    dates = request.POST.getlist("dev_date[]")
                    books = request.POST.getlist("dev_book[]")
                    ch_froms = request.POST.getlist("dev_ch_from[]")
                    ch_tos = request.POST.getlist("dev_ch_to[]")
                    v_froms = request.POST.getlist("dev_v_from[]")
                    v_tos = request.POST.getlist("dev_v_to[]")
                    trans = request.POST.getlist("dev_translation[]")
                    titles = request.POST.getlist("dev_title[]")
                    bodies = request.POST.getlist("dev_body[]")
                    refs = request.POST.getlist("dev_refs[]")
                    files = request.FILES.getlist("dev_body_file[]")
                    for i, book_id_val in enumerate(books):
                        book_id_val = (book_id_val or "").strip()
                        if not book_id_val:
                            continue
                        ch_from = _g(ch_froms, i) or "1"
                        ch_to = _g(ch_tos, i) or ch_from
                        v_from = _g(v_froms, i)
                        v_to = _g(v_tos, i)
                        entry_trans = (
                            _g(trans, i) or plan.translation or "BSB"
                        ).upper()
                        body_text = _g(bodies, i)
                        if not body_text and i < len(files):
                            ufile = files[i]
                            try:
                                name = ufile.name.lower()
                                if name.endswith(".txt") or name.endswith(".md"):
                                    body_text = (
                                        ufile.read()
                                        .decode("utf-8", errors="replace")
                                        .strip()
                                    )
                            except Exception as ex:
                                logger.warning("devotional file extract failed: %s", ex)
                        ReadingPlanEntry.raw_objects.create(
                            church=church,
                            plan=plan,
                            order=i + 1,
                            entry_type="devotional",
                            scheduled_date=_parse_date(_g(dates, i)),
                            translation=entry_trans,
                            passage_reference=_build_passage_ref(
                                book_id_val,
                                ch_from,
                                ch_to,
                                v_from,
                                v_to,
                                _books_for(entry_trans),
                            ),
                            devotional_title=_g(titles, i),
                            devotional_body=body_text,
                            inline_references=[
                                {"reference": r.strip()}
                                for r in _g(refs, i).split(",")
                                if r.strip()
                            ],
                            book_id=book_id_val,
                            chapter=int(ch_from) if ch_from else None,
                            chapter_to=(
                                int(ch_to)
                                if ch_to and int(ch_to) != int(ch_from or 1)
                                else None
                            ),
                            verse_start=int(v_from) if v_from else None,
                            verse_end=int(v_to) if v_to else None,
                        )
                else:
                    dates = request.POST.getlist("entry_date[]")
                    books = request.POST.getlist("entry_book[]")
                    ch_froms = request.POST.getlist("entry_ch_from[]")
                    ch_tos = request.POST.getlist("entry_ch_to[]")
                    v_froms = request.POST.getlist("entry_v_from[]")
                    v_tos = request.POST.getlist("entry_v_to[]")
                    trans = request.POST.getlist("entry_translation[]")
                    for i, book_id_val in enumerate(books):
                        book_id_val = (book_id_val or "").strip()
                        if not book_id_val:
                            continue
                        ch_from = _g(ch_froms, i) or "1"
                        ch_to = _g(ch_tos, i) or ch_from
                        v_from = _g(v_froms, i)
                        v_to = _g(v_tos, i)
                        entry_trans = (
                            _g(trans, i) or plan.translation or "BSB"
                        ).upper()
                        ReadingPlanEntry.raw_objects.create(
                            church=church,
                            plan=plan,
                            order=i + 1,
                            entry_type="passage",
                            scheduled_date=_parse_date(_g(dates, i)),
                            translation=entry_trans,
                            passage_reference=_build_passage_ref(
                                book_id_val,
                                ch_from,
                                ch_to,
                                v_from,
                                v_to,
                                _books_for(entry_trans),
                            ),
                            book_id=book_id_val,
                            chapter=int(ch_from) if ch_from else None,
                            chapter_to=(
                                int(ch_to)
                                if ch_to and int(ch_to) != int(ch_from or 1)
                                else None
                            ),
                            verse_start=int(v_from) if v_from else None,
                            verse_end=int(v_to) if v_to else None,
                        )
            messages.success(request, f"Plan '{plan.title}' updated.")
            return redirect("bible:admin_plan_list")
        except Exception as exc:
            error = str(exc)
    entries = plan.entries.filter(is_active=True).order_by("order")
    entries_data = [
        {
            "entry_type": e.entry_type,
            "scheduled_date": e.scheduled_date.isoformat() if e.scheduled_date else "",
            "translation": e.translation or plan.translation,
            "book_id": e.book_id,
            "chapter": e.chapter,
            "chapter_to": e.chapter_to or e.chapter,
            "verse_start": e.verse_start,
            "verse_end": e.verse_end,
            "devotional_title": e.devotional_title,
            "devotional_body": e.devotional_body,
            "inline_references": ", ".join(
                r.get("reference", "")
                for r in (e.inline_references or [])
                if isinstance(r, dict)
            ),
        }
        for e in entries
    ]
    return render(
        request,
        "bible/admin/plan_form.html",
        {
            "plan": plan,
            "entries": entries,
            "entries_data_json": json.dumps(entries_data).replace("</", "<\\/"),
            "church": church,
            "error": error,
            "page_title": f"Edit: {plan.title}",
            "frequency_choices": [
                ("daily", "Daily"),
                ("weekly", "Weekly"),
                ("monthly", "Monthly"),
                ("quarterly", "Quarterly"),
                ("yearly", "Yearly"),
            ],
            "church_date_format": _church_date_format(church, "%d %b %Y"),
        },
    )


@login_required
def admin_plan_delete(request, plan_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import ReadingPlan

    plan = get_object_or_404(ReadingPlan, id=plan_id, church=church)
    if request.method == "POST":
        plan.is_active = False
        plan.save(update_fields=["is_active", "updated_at"])
        from django.contrib import messages

        messages.success(request, f"Plan '{plan.title}' deleted.")
    return redirect("bible:admin_plan_list")


# ── Admin: Memory Verses ──────────────────────────────────────────────────────


@login_required
def admin_memory_verse_list(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import MemoryVerse

    verses = MemoryVerse.raw_objects.filter(church=church, is_active=True).order_by(
        "-active_date"
    )
    return render(
        request,
        "bible/admin/memory_verse_list.html",
        {"verses": verses, "church": church, "page_title": "Memory Verses"},
    )


@login_required
def admin_memory_verse_create(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import MemoryVerse
    from django.contrib import messages

    error = None
    if request.method == "POST":
        try:
            MemoryVerse.raw_objects.create(
                church=church,
                reference=request.POST.get("reference", "").strip(),
                text=request.POST.get("text", "").strip(),
                translation=request.POST.get("translation", "BSB").strip().upper(),
                scope=request.POST.get("scope", "weekly"),
                active_date=request.POST.get("active_date"),
                end_date=request.POST.get("end_date") or None,
                is_published=request.POST.get("is_published") == "on",
                created_by=request.user,
            )
            messages.success(request, "Memory verse created.")
            return redirect("bible:admin_memory_verse_list")
        except Exception as exc:
            logger.error("admin_memory_verse_create: %s", exc)
            error = str(exc)
    return render(
        request,
        "bible/admin/memory_verse_form.html",
        {"church": church, "page_title": "Add Memory Verse", "error": error},
    )


@login_required
def admin_memory_verse_edit(request, verse_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import MemoryVerse
    from django.contrib import messages

    verse = get_object_or_404(MemoryVerse, id=verse_id, church=church)
    error = None
    if request.method == "POST":
        try:
            verse.reference = request.POST.get("reference", verse.reference).strip()
            verse.text = request.POST.get("text", verse.text).strip()
            verse.translation = (
                request.POST.get("translation", verse.translation).strip().upper()
            )
            verse.scope = request.POST.get("scope", verse.scope)
            verse.active_date = request.POST.get("active_date", str(verse.active_date))
            verse.end_date = request.POST.get("end_date") or None
            verse.is_published = request.POST.get("is_published") == "on"
            verse.save()
            messages.success(request, "Memory verse updated.")
            return redirect("bible:admin_memory_verse_list")
        except Exception as exc:
            error = str(exc)
    return render(
        request,
        "bible/admin/memory_verse_form.html",
        {
            "verse": verse,
            "church": church,
            "error": error,
            "page_title": f"Edit: {verse.reference}",
        },
    )


@login_required
def admin_memory_verse_delete(request, verse_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import MemoryVerse

    verse = get_object_or_404(MemoryVerse, id=verse_id, church=church)
    if request.method == "POST":
        verse.is_active = False
        verse.save(update_fields=["is_active", "updated_at"])
        from django.contrib import messages

        messages.success(request, "Memory verse deleted.")
    return redirect("bible:admin_memory_verse_list")


# ── Admin: Study References ───────────────────────────────────────────────────


@login_required
def admin_study_ref_list(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import BibleStudyReference

    studies = BibleStudyReference.raw_objects.filter(
        church=church, is_active=True
    ).order_by("-active_date", "-created_at")
    return render(
        request,
        "bible/admin/study_ref_list.html",
        {"studies": studies, "church": church, "page_title": "Bible Study References"},
    )


@login_required
def admin_study_ref_create(request):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import BibleStudyReference
    from django.contrib import messages

    error = None
    if request.method == "POST":
        try:
            BibleStudyReference.raw_objects.create(
                church=church,
                title=request.POST.get("title", "").strip(),
                description=request.POST.get("description", "").strip(),
                reference=request.POST.get("reference", "").strip(),
                study_notes=request.POST.get("study_notes", "").strip(),
                translation=request.POST.get("translation", "BSB").strip().upper(),
                active_date=request.POST.get("active_date") or None,
                is_published=request.POST.get("is_published") == "on",
                created_by=request.user,
            )
            messages.success(request, "Study reference created.")
            return redirect("bible:admin_study_ref_list")
        except Exception as exc:
            logger.error("admin_study_ref_create: %s", exc)
            error = str(exc)
    return render(
        request,
        "bible/admin/study_ref_form.html",
        {"church": church, "page_title": "Add Bible Study Reference", "error": error},
    )


@login_required
def admin_study_ref_edit(request, study_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import BibleStudyReference
    from django.contrib import messages

    study = get_object_or_404(BibleStudyReference, id=study_id, church=church)
    error = None
    if request.method == "POST":
        try:
            study.title = request.POST.get("title", study.title).strip()
            study.description = request.POST.get(
                "description", study.description
            ).strip()
            study.reference = request.POST.get("reference", study.reference).strip()
            study.study_notes = request.POST.get(
                "study_notes", study.study_notes
            ).strip()
            study.translation = (
                request.POST.get("translation", study.translation).strip().upper()
            )
            study.active_date = request.POST.get("active_date") or None
            study.is_published = request.POST.get("is_published") == "on"
            study.save()
            messages.success(request, "Study reference updated.")
            return redirect("bible:admin_study_ref_list")
        except Exception as exc:
            error = str(exc)
    return render(
        request,
        "bible/admin/study_ref_form.html",
        {
            "study": study,
            "church": church,
            "error": error,
            "page_title": f"Edit: {study.title}",
        },
    )


@login_required
def admin_study_ref_delete(request, study_id):
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from bible.models import BibleStudyReference

    study = get_object_or_404(BibleStudyReference, id=study_id, church=church)
    if request.method == "POST":
        study.is_active = False
        study.save(update_fields=["is_active", "updated_at"])
        from django.contrib import messages

        messages.success(request, "Study reference deleted.")
    return redirect("bible:admin_study_ref_list")


# ── AJAX: Member Progress ─────────────────────────────────────────────────────


@login_required
@require_POST
def mark_entry_complete(request, entry_id):
    church, member = _church(request), _member(request)
    if not church or not member:
        return _je("No church/member context", 403)
    from bible.models import ReadingPlanEntry, MemberPlanProgress
    from bible.services.badges import check_and_award_reading, update_streak

    entry = get_object_or_404(
        ReadingPlanEntry, id=entry_id, plan__church=church, is_active=True
    )
    progress, created = MemberPlanProgress.raw_objects.get_or_create(
        church=church,
        member=member,
        entry=entry,
        defaults={"plan": entry.plan},
    )
    new_badges = []
    if created:
        update_streak(member, church, entry.plan)
        new_badges = check_and_award_reading(member, church)
    return JsonResponse(
        {"ok": True, "created": created, "new_badges": new_badges, "entry_id": entry.id}
    )


@login_required
@require_POST
def mark_memory_complete(request, verse_id):
    church, member = _church(request), _member(request)
    if not church or not member:
        return _je("No church/member context", 403)
    from bible.models import MemoryVerse, MemberMemoryVerseProgress
    from bible.services.badges import check_and_award_memory_verse

    verse = get_object_or_404(MemoryVerse, id=verse_id, church=church, is_active=True)
    progress, created = MemberMemoryVerseProgress.raw_objects.get_or_create(
        church=church,
        member=member,
        verse=verse,
    )
    new_badges = check_and_award_memory_verse(member, church) if created else []
    return JsonResponse({"ok": True, "created": created, "new_badges": new_badges})


@login_required
@require_POST
def mark_study_complete(request, study_id):
    church, member = _church(request), _member(request)
    if not church or not member:
        return _je("No church/member context", 403)
    from bible.models import BibleStudyReference, MemberStudyProgress
    from bible.services.badges import check_and_award_study

    study = get_object_or_404(
        BibleStudyReference, id=study_id, church=church, is_active=True
    )
    progress, created = MemberStudyProgress.raw_objects.get_or_create(
        church=church,
        member=member,
        study=study,
    )
    new_badges = check_and_award_study(member, church) if created else []
    return JsonResponse({"ok": True, "created": created, "new_badges": new_badges})


# ── AJAX: Share to Feed ───────────────────────────────────────────────────────


@login_required
@require_POST
def share_to_feed(request):
    church, member = _church(request), _member(request)
    if not church or not member:
        return _je("No church/member context", 403)
    try:
        body = json.loads(request.body)
    except Exception:
        body = request.POST
    share_type = body.get("share_type", "verse")
    reference = body.get("reference", "").strip()
    verse_text = body.get("verse_text", "").strip()
    translation = body.get("translation", "BSB").strip().upper()
    badge_slug = body.get("badge_slug", "").strip()
    progress_label = body.get("progress_label", "").strip()
    plan_title = body.get("plan_title", "").strip()

    if share_type == "verse" and reference and verse_text:
        post_body = f'📖 *{reference}* ({translation})\n\n"{verse_text}"'
    elif share_type == "badge" and badge_slug:
        from bible.models import BibleBadge

        badge = BibleBadge.objects.filter(slug=badge_slug).first()
        post_body = f"{badge.icon_emoji if badge else '🏅'} I just earned the **{badge.name if badge else badge_slug}** badge! 🙌"
    elif share_type == "progress":
        post_body = f"📅 Bible Reading Progress: {progress_label}"
        if plan_title:
            post_body += f"\nPlan: *{plan_title}*"
    else:
        return _je("Invalid share payload")

    try:
        from feeds.models import Feed

        feed = Feed.raw_objects.create(
            church=church,
            author=request.user,
            body=post_body,
            scope="general",
            is_admin_post=False,
        )
        from bible.models import BibleBadge, BibleShareRecord

        badge_obj = (
            BibleBadge.objects.filter(slug=badge_slug).first() if badge_slug else None
        )
        BibleShareRecord.raw_objects.create(
            church=church,
            member=member,
            share_type=share_type,
            destination="feed",
            reference=reference,
            verse_text=verse_text,
            translation=translation,
            badge=badge_obj,
            feed_post_id=feed.id,
        )
        from bible.services.badges import check_and_award_share

        check_and_award_share(member, church)
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            layer = get_channel_layer()
            if layer:
                async_to_sync(layer.group_send)(
                    f"feed_{church.id}", {"type": "feed.new", "feed_id": feed.id}
                )
        except Exception:
            pass
        return JsonResponse({"ok": True, "feed_id": feed.id})
    except Exception as exc:
        logger.error("share_to_feed: %s", exc)
        return _je(str(exc), 500)


# ── AJAX: Bible Search ────────────────────────────────────────────────────────


@login_required
@require_GET
def bible_search(request):
    """
    Direct HelloAO API search — zero AI.
    GET ?q=John+3:16 | ?q=Romans+8:28-30 | ?q=Psalm+23 | ?q=keyword
    """
    q = request.GET.get("q", "").strip()
    translation = request.GET.get("translation", "BSB").strip().upper() or "BSB"

    if not q or len(q) < 2:
        return JsonResponse(
            {"ok": True, "results": [], "mode": "empty", "warming": False}
        )

    from bible.services.parser import parse_reference
    from bible.services.router import search_reference

    # ── Reference lookup ──────────────────────────────────────────────────
    parsed = parse_reference(q)
    if parsed:
        try:
            results = search_reference(q, translation)
            if results:
                return JsonResponse(
                    {
                        "ok": True,
                        "results": results[:30],
                        "mode": "reference",
                        "warming": False,
                    }
                )
            return JsonResponse(
                {
                    "ok": True,
                    "results": [],
                    "mode": "reference",
                    "warming": False,
                    "message": f"No verses found for '{q}'. Check the reference.",
                }
            )
        except Exception as exc:
            logger.error("bible_search reference: %s", exc)
            return JsonResponse(
                {
                    "ok": False,
                    "results": [],
                    "mode": "reference",
                    "warming": False,
                    "message": "Could not reach the Bible API. Please try again.",
                }
            )

    # ── Keyword search ────────────────────────────────────────────────────
    from bible.models import BibleChapterCache

    total_cached = BibleChapterCache.objects.filter(translation=translation).count()
    if total_cached < 5:
        return JsonResponse(
            {
                "ok": True,
                "results": [],
                "mode": "keyword",
                "warming": True,
                "message": "Verse index is still building. Try a reference like 'John 3:16'.",
            }
        )

    q_lower = q.lower()
    results = []
    for ch in BibleChapterCache.objects.filter(translation=translation):
        for v in ch.verses_data:
            if q_lower in v.get("text", "").lower():
                ref = f"{ch.book_name} {ch.chapter}:{v['number']}"
                results.append(
                    {
                        "number": v["number"],
                        "text": v["text"],
                        "book_name": ch.book_name,
                        "book_id": ch.book_id,
                        "chapter": ch.chapter,
                        "reference": ref,
                        "group_reference": ref,
                        "translation": translation,
                    }
                )
                if len(results) >= 20:
                    break
        if len(results) >= 20:
            break

    return JsonResponse(
        {"ok": True, "results": results, "mode": "keyword", "warming": False}
    )


# ── AJAX: Translations list ───────────────────────────────────────────────────

# Copyrighted translations NOT on HelloAO — shown greyed-out with tooltip
_UNAVAILABLE = {
    "NIV": "New International Version (copyright Zondervan — not freely available)",
    "NLT": "New Living Translation (copyright Tyndale — not freely available)",
    "AMP": "Amplified Bible (copyright Zondervan — not freely available)",
    "NKJV": "New King James Version (copyright Thomas Nelson — not freely available)",
    "TPT": "The Passion Translation (copyright BroadStreet — not freely available)",
    "GNB": "Good News Bible (copyright ABS — not freely available)",
    "ESV": "English Standard Version (copyright Crossway — not freely available)",
    "NASB": "New American Standard Bible (copyright Lockman — not freely available)",
    "MSG": "The Message (copyright NavPress — not freely available)",
    "NCV": "New Century Version (copyright Thomas Nelson — not freely available)",
}

# Known HelloAO English translation IDs → human-readable names
# (populated from /api/available_translations.json; these are confirmed free/open)
_ENGLISH_KNOWN = [
    {"id": "BSB", "name": "Berean Standard Bible (BSB)"},
    {"id": "eng_kjv", "name": "King James Version (KJV)"},
    {"id": "ENGWEBP", "name": "World English Bible — Protestant (WEB)"},
    {"id": "NET", "name": "New English Translation (NET)"},
    {"id": "LSV", "name": "Literal Standard Version (LSV)"},
    {"id": "YLT", "name": "Young's Literal Translation (YLT)"},
    {"id": "DARBY", "name": "Darby Translation"},
    {"id": "ASV", "name": "American Standard Version (ASV)"},
    {"id": "WBT", "name": "Webster Bible Translation"},
    {"id": "RWV", "name": "Revised Webster Version"},
    {"id": "UKJV", "name": "Updated King James Version"},
    {"id": "BBE", "name": "Bible in Basic English (BBE)"},
    {"id": "WNT", "name": "Weymouth New Testament"},
    {"id": "eng_web", "name": "World English Bible (WEB)"},
]


@never_cache
@login_required
@require_GET
def translations_list(request):
    """
    Return all available English Bible translations from all configured services.
    Groups them into:
      - youversion: primary YouVersion Platform Bible API
      - helloao: open-license fallback (BSB, KJV, WEB, NET, YLT, ...)
      - apibible: secondary fallback via API.Bible (NIV, NLT, AMP, NKJV, ...)
      - needs_key: copyrighted but APIBIBLE_API_KEY not yet set
    """
    try:
        from bible.services.router import get_translations_all
        from bible.services.apibible import is_configured as apibible_configured
        from bible.services.youversion import is_configured as youversion_configured

        all_t = get_translations_all()

        youversion_list = [
            {
                "id": t["id"],
                "name": t.get("name") or t["id"],
            }
            for t in all_t.get("youversion", [])
        ]
        youversion_list.sort(key=lambda t: t["name"])

        helloao_list = [
            {
                "id": _normalise_id(t["id"]),
                "name": _label(t["id"], t.get("name") or t["id"]),
            }
            for t in all_t.get("helloao", [])
        ]
        # Sort: put the most-used ones first
        _priority = {
            "BSB": 0,
            "ENG_KJV": 1,
            "NET": 2,
            "ENGWEBP": 3,
            "LSV": 4,
            "YLT": 5,
            "ASV": 6,
        }
        helloao_list.sort(key=lambda t: (_priority.get(t["id"].upper(), 99), t["name"]))

        apibible_raw = all_t.get("apibible", [])
        apibible_list = [
            {
                "id": t["id"],
                "name": t.get("name") or t["id"],
                "needs_key": t.get("needs_key", False),
            }
            for t in apibible_raw
        ]
        apibible_list.sort(key=lambda t: t["name"])

        return JsonResponse(
            {
                "ok": True,
                "youversion": youversion_list,
                "youversion_ready": youversion_configured(),
                "helloao": helloao_list,
                "apibible": apibible_list,
                "apibible_ready": apibible_configured(),
            }
        )

    except Exception as exc:
        logger.error("translations_list: %s", exc)
        return JsonResponse(
            {
                "ok": True,
                "youversion": [],
                "youversion_ready": False,
                "helloao": _ENGLISH_KNOWN,
                "apibible": [],
                "apibible_ready": False,
            }
        )


def _normalise_id(tid: str) -> str:
    """Normalise translation IDs: ENG_KJV → ENG_KJV (keep as-is for API calls)."""
    return tid.upper()


def _label(tid: str, name: str) -> str:
    """Return a clean display name, appending the ID abbreviation if not already present."""
    tid = tid.upper()
    # Normalise known IDs to friendly names
    _overrides = {
        "ENG_KJV": "King James Version (KJV)",
        "ENGWEBP": "World English Bible — Protestant (WEB)",
        "ENG_WEB": "World English Bible (WEB)",
        "BSB": "Berean Standard Bible (BSB)",
        "NET": "New English Translation (NET)",
        "LSV": "Literal Standard Version (LSV)",
        "YLT": "Young's Literal Translation (YLT)",
        "ASV": "American Standard Version (ASV)",
        "BBE": "Bible in Basic English (BBE)",
        "WNT": "Weymouth New Testament",
        "DARBY": "Darby Translation",
    }
    return _overrides.get(tid, name)


# ── AJAX: Books list ──────────────────────────────────────────────────────────


@never_cache
@login_required
@require_GET
def book_list(request):
    translation = request.GET.get("translation", "BSB").strip().upper() or "BSB"
    try:
        from bible.services.router import get_books

        books = get_books(translation)
        return JsonResponse({"ok": True, "books": books, "translation": translation})
    except Exception as exc:
        logger.error("book_list: %s", exc)
        return _je(str(exc), 500)


# ── AJAX: Chapter verses ──────────────────────────────────────────────────────


@login_required
@require_GET
def chapter_verses(request):
    book_id = request.GET.get("book_id", "").strip().upper()
    translation = request.GET.get("translation", "BSB").strip().upper() or "BSB"
    try:
        chapter = int(request.GET.get("chapter", 1))
    except (ValueError, TypeError):
        return _je("Invalid chapter")
    if not book_id:
        return _je("book_id required")
    try:
        from bible.services.router import get_chapter

        data = get_chapter(book_id, chapter, translation)
        return JsonResponse(
            {
                "ok": True,
                "verses": data.get("verses", []),
                "book_name": data.get("book_name", book_id),
                "chapter": chapter,
                "translation": translation,
                "total_chapters": data.get("total_chapters", 1),
            }
        )
    except Exception as exc:
        logger.error("chapter_verses: %s", exc)
        return _je(str(exc), 500)


# ── AJAX: Debug (dev only) ────────────────────────────────────────────────────


@login_required
@require_GET
def bible_debug(request):
    """
    Dev/superuser diagnostic for all three Bible providers.

    GET /bible/api/debug/?book_id=JHN&chapter=3&translation=BSB
    GET /bible/api/debug/?book_id=JHN&chapter=3&translation=NIV   ← tests API.Bible
    GET /bible/api/debug/?action=sync_apibible                     ← force-sync API.Bible translations
    GET /bible/api/debug/?action=sync_youversion                   ← force-sync YouVersion translations (Phase 4)
    GET /bible/api/debug/?action=youversion_status                 ← show YouVersion config & DB state
    """
    from django.conf import settings

    if not settings.DEBUG and not request.user.is_superuser:
        return _je("Not available in production", 403)

    action = request.GET.get("action", "")

    # ── Action: force-sync API.Bible translations ─────────────────────────
    if action == "sync_apibible":
        try:
            from bible.services.apibible import sync_translations, is_configured

            if not is_configured():
                return JsonResponse(
                    {"ok": False, "error": "APIBIBLE_API_KEY not set in settings"}
                )
            count, names = sync_translations()
            return JsonResponse({"ok": True, "synced": count, "translations": names})
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)})

    # ── Action: force-sync YouVersion translations (Phase 4) ─────────────
    if action == "sync_youversion":
        try:
            from bible.services.youversion import sync_translations, is_configured

            if not is_configured():
                return JsonResponse(
                    {"ok": False, "error": "YOUVERSION_APP_KEY not set in settings"}
                )
            count, names = sync_translations()
            return JsonResponse(
                {"ok": True, "synced": count, "translations": names[:30]}
            )
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)})

    # ── Action: YouVersion config & DB status ─────────────────────────────
    if action == "youversion_status":
        try:
            from bible.services.youversion import is_configured
            from bible.services.youversion_oauth import is_oauth_configured
            from bible.models import (
                YouVersionTranslationMap,
                BibleChapterCache,
                BibleProviderRequestLog,
            )

            yv_translations = YouVersionTranslationMap.objects.count()
            yv_cached_chapters = BibleChapterCache.objects.filter(
                translation__in=list(
                    YouVersionTranslationMap.objects.values_list(
                        "short_code", flat=True
                    )
                )
            ).count()
            yv_requests_ok = BibleProviderRequestLog.objects.filter(
                provider="youversion", ok=True
            ).count()
            yv_requests_fail = BibleProviderRequestLog.objects.filter(
                provider="youversion", ok=False
            ).count()

            return JsonResponse(
                {
                    "ok": True,
                    "youversion_api_configured": is_configured(),
                    "youversion_oauth_configured": is_oauth_configured(),
                    "youversion_translations_in_db": yv_translations,
                    "youversion_cached_chapters": yv_cached_chapters,
                    "youversion_requests_ok": yv_requests_ok,
                    "youversion_requests_failed": yv_requests_fail,
                    "hint_sync": "GET /bible/api/debug/?action=sync_youversion to refresh translation list",
                    "hint_oauth": "Add YOUVERSION_CLIENT_ID + YOUVERSION_CLIENT_SECRET to .env for OAuth sign-in",
                }
            )
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)})

    # ── Standard diagnostic: test a chapter fetch ─────────────────────────
    book_id = request.GET.get("book_id", "JHN").strip().upper()
    translation = request.GET.get("translation", "BSB").strip().upper()
    try:
        chapter = int(request.GET.get("chapter", 3))
    except (ValueError, TypeError):
        chapter = 3

    from bible.services.apibible import (
        is_configured as ab_configured,
        get_bible_id,
        _DISPLAY_NAMES,
    )
    from bible.models import ApiBibleTranslationMap
    from bible.services.router import _route_apibible, _route_youversion

    uses_youversion = _route_youversion(translation)
    uses_apibible = _route_apibible(translation)

    # ── YouVersion diagnostic ─────────────────────────────────────────────
    if uses_youversion:
        from bible.services.youversion import (
            is_configured as yv_configured,
            get_bible_id as yv_bible_id,
        )

        if not yv_configured():
            return JsonResponse(
                {
                    "ok": False,
                    "error": "YOUVERSION_APP_KEY not set — add it to .env and restart.",
                    "hint": "Register at https://developers.youversion.com/",
                }
            )
        bible_id = yv_bible_id(translation)
        from bible.services.youversion import get_chapter as yv_get_chapter

        result = yv_get_chapter(book_id, chapter, translation, force_refresh=True)
        return JsonResponse(
            {
                "ok": bool(result.get("verses")),
                "service": "youversion",
                "translation": translation,
                "bible_id": bible_id,
                "book_name": result.get("book_name"),
                "verse_count": len(result.get("verses", [])),
                "sample_verses": result.get("verses", [])[:3],
            }
        )

    if uses_apibible:
        # ── API.Bible diagnostic ──────────────────────────────────────────
        if not ab_configured():
            return JsonResponse(
                {
                    "ok": False,
                    "error": "APIBIBLE_API_KEY not set — add it to .env and restart.",
                    "hint": "Register free at https://scripture.api.bible",
                }
            )
        bible_id = get_bible_id(translation)
        if not bible_id:
            db_count = ApiBibleTranslationMap.objects.count()
            return JsonResponse(
                {
                    "ok": False,
                    "error": f"No API.Bible ID found for '{translation}'.",
                    "db_count": db_count,
                    "hint": f"Run: GET /bible/api/debug/?action=sync_apibible",
                    "known_codes": list(_DISPLAY_NAMES.keys()),
                }
            )
        import requests as _req

        url = (
            f"https://rest.api.bible/v1/bibles/{bible_id}/chapters/{book_id}.{chapter}"
        )
        try:
            resp = _req.get(
                url,
                headers={"api-key": settings.APIBIBLE_API_KEY},
                params={"content-type": "json", "include-verse-numbers": "true"},
                timeout=15,
            )
            raw = resp.json()
        except Exception as exc:
            return JsonResponse({"ok": False, "url": url, "error": str(exc)})

        from bible.services.apibible import _parse_content

        content = (raw.get("data") or {}).get("content", [])
        verses = _parse_content(content)
        return JsonResponse(
            {
                "ok": resp.status_code == 200,
                "service": "apibible",
                "translation": translation,
                "bible_id": bible_id,
                "url": url,
                "http_status": resp.status_code,
                "verse_count": len(verses),
                "sample_verses": verses[:3],
            }
        )

    else:
        # ── HelloAO diagnostic ────────────────────────────────────────────
        from bible.services.helloao import BASE_URL, _get

        url = f"{BASE_URL}/{translation}/{book_id}/{chapter}.json"
        raw = _get(url)
        if raw is None:
            return JsonResponse(
                {
                    "ok": False,
                    "service": "helloao",
                    "url": url,
                    "error": "API returned None (network failure or non-200)",
                }
            )
        chapter_block = raw.get("chapter") or {}
        content_items = chapter_block.get("content", [])
        verse_items = [
            c for c in content_items if isinstance(c, dict) and c.get("type") == "verse"
        ]
        return JsonResponse(
            {
                "ok": True,
                "service": "helloao",
                "translation": translation,
                "url": url,
                "top_level_keys": list(raw.keys()),
                "book": raw.get("book"),
                "chapter_number": chapter_block.get("number"),
                "total_content_items": len(content_items),
                "verse_count": len(verse_items),
                "sample_verses": verse_items[:3],
            }
        )


# ── AJAX: Fetch passage ───────────────────────────────────────────────────────


@never_cache
@login_required
@require_GET
def fetch_passage(request):
    book_id = request.GET.get("book_id", "").strip().upper()
    translation = request.GET.get("translation", "BSB").strip().upper() or "BSB"
    try:
        chapter = int(request.GET.get("chapter", 1))
    except (ValueError, TypeError):
        return _je("Invalid chapter")
    # ch_to allows fetching a chapter range (e.g. Genesis 1–3)
    try:
        chapter_to = int(request.GET.get("chapter_to", chapter))
    except (ValueError, TypeError):
        chapter_to = chapter
    try:
        verse_start = (
            int(request.GET.get("verse_start"))
            if request.GET.get("verse_start")
            else None
        )
        verse_end = (
            int(request.GET.get("verse_end")) if request.GET.get("verse_end") else None
        )
    except (ValueError, TypeError):
        verse_start = verse_end = None
    if not book_id:
        return _je("book_id required")

    from bible.services.router import get_chapter as fetch_chapter
    from bible.services.parser import format_reference

    # Single chapter — existing fast path
    if chapter_to <= chapter:
        data = fetch_chapter(book_id, chapter, translation)
        verses = data.get("verses", [])
        book_nm = data.get("book_name", book_id)
        if verse_start is not None:
            verses = [
                v
                for v in verses
                if (verse_end is None and v["number"] == verse_start)
                or (verse_end is not None and verse_start <= v["number"] <= verse_end)
            ]
        ref = format_reference(book_id, chapter, verse_start, verse_end, book_nm)
        return JsonResponse(
            {
                "ok": True,
                "verses": verses,
                "book_name": book_nm,
                "chapter": chapter,
                "chapter_to": chapter,
                "reference": ref,
                "translation": translation,
                "total_chapters": data.get("total_chapters", 1),
            }
        )

    # Multi-chapter range — fetch each chapter and merge, labelling verses with chapter
    all_verses = []
    book_nm = book_id
    total_ch = chapter
    for ch in range(chapter, chapter_to + 1):
        try:
            data = fetch_chapter(book_id, ch, translation)
            book_nm = data.get("book_name", book_id)
            total_ch = data.get("total_chapters", chapter_to)
            ch_verses = data.get("verses", [])
            # Annotate with chapter number so the reader can show chapter headers
            for v in ch_verses:
                v = dict(v)
                v["chapter"] = ch
                all_verses.append(v)
        except Exception as exc:
            logger.warning("fetch_passage multi-chapter ch=%s: %s", ch, exc)

    ref = f"{book_nm} {chapter}–{chapter_to}"
    return JsonResponse(
        {
            "ok": True,
            "verses": all_verses,
            "book_name": book_nm,
            "chapter": chapter,
            "chapter_to": chapter_to,
            "reference": ref,
            "translation": translation,
            "total_chapters": total_ch,
        }
    )


# ── Primary Bible Reader ──────────────────────────────────────────────────────


@never_cache
@login_required
@require_GET
def bible_reader(request):
    """Full-page primary Bible reader with highlighter (YouVersion-style)."""
    church = _church(request)
    member = _member(request)
    if not church:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    # Pre-load member highlights for initial chapter (defaults to John 1)
    book_id = request.GET.get("book_id", "JHN").upper()
    chapter = int(request.GET.get("chapter", 1) or 1)
    translation = request.GET.get("translation", "BSB").upper()
    from bible.models import BibleHighlight

    highlights = []
    if member:
        try:
            highlights = list(
                BibleHighlight.raw_objects.filter(
                    church=church,
                    member=member,
                    book_id=book_id,
                    chapter=chapter,
                ).values("verse_number", "color", "note")
            )
        except Exception:
            pass
    return render(
        request,
        "bible/bible_reader.html",
        {
            "church": church,
            "is_admin": _is_admin(request),
            "initial_book_id": book_id,
            "initial_chapter": chapter,
            "initial_translation": translation,
            "highlights": highlights,
            "page_title": "Bible Reader",
        },
    )


@login_required
@require_POST
def ajax_save_highlight(request):
    """Save or delete a verse highlight."""
    church = _church(request)
    member = _member(request)
    if not church or not member:
        return JsonResponse({"ok": False}, status=403)
    import json as _json

    try:
        body = _json.loads(request.body.decode())
    except Exception:
        return _je("invalid json")
    book_id = (body.get("book_id") or "").upper()
    book_name = body.get("book_name", "")
    chapter = int(body.get("chapter") or 0)
    verse = int(body.get("verse") or 0)
    color = body.get("color", "yellow")
    note = body.get("note", "")
    delete = body.get("delete", False)
    if not (book_id and chapter and verse):
        return _je("book_id, chapter, verse required")
    from bible.models import BibleHighlight

    if delete:
        BibleHighlight.raw_objects.filter(
            church=church,
            member=member,
            book_id=book_id,
            chapter=chapter,
            verse_number=verse,
        ).delete()
        return JsonResponse({"ok": True, "deleted": True})
    obj, _ = BibleHighlight.raw_objects.update_or_create(
        church=church,
        member=member,
        book_id=book_id,
        chapter=chapter,
        verse_number=verse,
        defaults={
            "color": color,
            "note": note,
            "book_name": book_name,
            "translation": body.get("translation", "BSB"),
        },
    )
    return JsonResponse({"ok": True, "verse": verse, "color": obj.color})


@login_required
@require_GET
def ajax_load_highlights(request):
    """Return highlights for a given book/chapter."""
    church = _church(request)
    member = _member(request)
    if not church or not member:
        return JsonResponse({"ok": False, "highlights": []})
    book_id = request.GET.get("book_id", "").upper()
    chapter = int(request.GET.get("chapter", 0) or 0)
    if not (book_id and chapter):
        return _je("book_id and chapter required")
    from bible.models import BibleHighlight

    qs = BibleHighlight.raw_objects.filter(
        church=church, member=member, book_id=book_id, chapter=chapter
    ).values("verse_number", "color", "note")
    return JsonResponse({"ok": True, "highlights": list(qs)})


# ── Admin: Member Bible Badge Progress ───────────────────────────────────────


@login_required
def admin_member_bible_progress(request, member_id):
    """Admin view: see a specific member's bible plan badge progress."""
    church = _church(request)
    if not church or not _is_admin(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()
    from accounts.models import ChurchMember
    from bible.models import (
        ReadingPlan,
        MemberPlanProgress,
        MemberBadgeAward,
        MemberPlanStreak,
        MemberMemoryVerseProgress,
        MemberStudyProgress,
    )
    from bible.services.badges import get_member_stats
    from django.db.models import Q

    target = get_object_or_404(ChurchMember.raw_objects, church=church, id=member_id)
    today = _today()

    # Per-plan progress
    active_plans = list(
        ReadingPlan.raw_objects.filter(
            church=church, is_published=True, is_active=True
        ).prefetch_related("entries")
    )
    plan_progress = []
    for plan in active_plans:
        total = plan.entries.filter(is_active=True).count()
        done = MemberPlanProgress.raw_objects.filter(
            church=church, member=target, plan=plan
        ).count()
        streak = MemberPlanStreak.raw_objects.filter(
            church=church, member=target, plan=plan
        ).first()
        plan_progress.append(
            {
                "plan": plan,
                "total": total,
                "done": done,
                "pct": round(done / total * 100) if total else 0,
                "streak": streak.current_streak if streak else 0,
                "longest": streak.longest_streak if streak else 0,
            }
        )

    stats = get_member_stats(target, church)
    badges = list(
        MemberBadgeAward.raw_objects.filter(church=church, member=target)
        .select_related("badge")
        .order_by("-awarded_at")
    )
    return render(
        request,
        "bible/admin/member_bible_progress.html",
        {
            "church": church,
            "target_member": target,
            "plan_progress": plan_progress,
            "stats": stats,
            "badges": badges,
            "is_admin": True,
            "page_title": f"Bible Progress — {target}",
        },
    )


# ── Plan Discussion: Create/List ──────────────────────────────────────────────


@login_required
@require_POST
def ajax_create_plan_discussion(request):
    """
    Create a discussion thread for a reading plan entry.
    Posts to chat or feeds depending on destination.
    """
    church = _church(request)
    member = _member(request)
    if not church or not member:
        return JsonResponse({"ok": False}, status=403)
    import json as _json

    try:
        body = _json.loads(request.body.decode())
    except Exception:
        return _je("invalid json")
    from bible.models import ReadingPlanEntry, ReadingPlanDiscussion

    entry_id = body.get("entry_id")
    plan_id = body.get("plan_id")
    text = (body.get("body") or "").strip()
    scope = body.get("scope", "workforce")  # "workforce" | "unit" | "group"
    unit_id = body.get("unit_id")
    destination = body.get("destination", "feeds")  # "feeds" | "chat"
    is_admin_p = _is_admin(request) and body.get("is_admin_prompt", False)

    if not text:
        return _je("body required")

    try:
        entry = ReadingPlanEntry.raw_objects.get(
            id=entry_id,
            plan_id=plan_id,
            plan__church=church,
            is_active=True,
        )
        plan = entry.plan
    except Exception:
        return _je("entry not found", 404)

    unit = None
    if scope in ("unit", "group") and unit_id:
        from units.models import ChurchUnit, UnitMembership
        from django.db import models

        unit = ChurchUnit.raw_objects.filter(church=church, id=unit_id).first()
        if not unit:
            return _je("unit/group not found", 404)
        if not _is_admin(request):
            can_post_unit = (
                UnitMembership.raw_objects.filter(
                    church=church,
                    unit=unit,
                    is_active=True,
                )
                .filter(
                    models.Q(workforce_member__member=member)
                    | models.Q(trainee_profile__member=member)
                )
                .exists()
            )
            if not can_post_unit:
                return _je("not allowed for this unit/group", 403)

    normalized_scope = "unit" if unit else "workforce"

    disc = ReadingPlanDiscussion.raw_objects.create(
        church=church,
        entry=entry,
        plan=plan,
        author=member,
        body=text,
        scope=normalized_scope,
        unit=unit,
        destination=destination,
        is_admin_prompt=is_admin_p,
    )

    # ── Post to feeds ─────────────────────────────────────────────────────
    if destination == "feeds":
        try:
            from feeds.models import Feed
            import json as _json_inner

            # Determine feed scope from normalized_scope + unit
            # normalized_scope is "unit" when a unit is present, else "workforce"
            # Feed.scope is "unit" or "general"
            _feed_scope = "unit" if unit else "general"

            # Encode plan discussion metadata as a sentinel prefix on the body.
            # The feeds JS renderer parses this to render a special plan-card layout.
            PLAN_DISC_SENTINEL = "%%PLAN_DISC%%"
            _meta = _json_inner.dumps(
                {
                    "plan_id": plan.id,
                    "plan_title": plan.title,
                    "entry_id": entry.id,
                    "passage_reference": entry.passage_reference,
                    "scheduled_date": (
                        str(entry.scheduled_date) if entry.scheduled_date else ""
                    ),
                    "devotional_title": entry.devotional_title or "",
                    "is_devotional": entry.entry_type == "devotional",
                },
                separators=(",", ":"),
            )
            body_for_feed = f"{PLAN_DISC_SENTINEL}{_meta}\n\n{text}"

            post = Feed.raw_objects.create(
                church=church,
                author=member.user,
                body=body_for_feed,
                scope=_feed_scope,
                unit=unit if unit else None,
                is_admin_post=is_admin_p,
            )
            disc.feed_post_id = post.id
            disc.save(update_fields=["feed_post_id"])

            # Broadcast via WebSocket so the feed updates in real-time
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync

                layer = get_channel_layer()
                if layer:
                    async_to_sync(layer.group_send)(
                        f"feed_{church.id}",
                        {"type": "feed.new", "feed_id": post.id},
                    )
            except Exception:
                pass
        except Exception as e:
            logger.warning("plan discussion feed post failed: %s", e)

    # ── Post to chat (creates a thread message) ───────────────────────────
    elif destination == "chat":
        try:
            from units.models import ChatRoom
            from workforce.models import ChatMessage
            from workforce.utils import serialize_message
            import json as _json_inner

            room = None
            if unit:
                room = ChatRoom.raw_objects.filter(
                    church=church, unit=unit, is_active=True, is_default=True
                ).first()
            if not room:
                room = ChatRoom.raw_objects.filter(
                    church=church, is_active=True, is_default=True, unit__isnull=True
                ).first()
            if not room:
                room = ChatRoom.raw_objects.filter(
                    church=church, is_active=True, is_default=True
                ).first()
            if room:
                # Build JSON meta — same keys as %%PLAN_DISC%% for parity
                _chat_meta = _json_inner.dumps(
                    {
                        "plan_id": plan.id,
                        "plan_title": plan.title,
                        "entry_id": entry.id,
                        "passage_reference": entry.passage_reference,
                        "scheduled_date": (
                            str(entry.scheduled_date) if entry.scheduled_date else None
                        ),
                        "devotional_title": entry.devotional_title or None,
                        "is_devotional": entry.entry_type == "devotional",
                        "is_admin_post": is_admin_p,
                    },
                    separators=(",", ":"),
                )
                CHAT_SENTINEL = "%%PLAN_CHAT%%"
                msg = ChatMessage.raw_objects.create(
                    church=church,
                    room=room,
                    sender=member,
                    message=f"{CHAT_SENTINEL}{_chat_meta}\n\n{text}",
                )
                disc.chat_room_id = room.id
                disc.chat_message_id = msg.id
                disc.save(update_fields=["chat_room_id", "chat_message_id"])

                # ── Broadcast to connected clients via channel layer ─────────
                try:
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    from core.utils.colors import get_member_color

                    serialized = serialize_message(msg)
                    layer = get_channel_layer()
                    if layer:
                        payload = {
                            **serialized,
                            "type": "chat_message",
                            "color": get_member_color(member.user_id, variant="hex"),
                            "unit_id": room.unit_id,
                        }
                        room_group = f"chat_room_{church.id}_{room.id}"
                        async_to_sync(layer.group_send)(room_group, payload)
                        # Also send to unit group if scoped
                        if room.unit_id:
                            async_to_sync(layer.group_send)(
                                f"chat_unit_{church.id}_{room.unit_id}", payload
                            )
                except Exception as _bc_err:
                    logger.warning("plan discussion chat broadcast failed: %s", _bc_err)
        except Exception as e:
            logger.warning("plan discussion chat post failed: %s", e)

    elif destination == "thread":
        # Reply posted from inside the chat bubble thread panel:
        # save as destination="chat" (so it appears in chat thread queries)
        # but do NOT create a new room message or broadcast.
        disc.destination = "chat"
        disc.save(update_fields=["destination"])

    redirect_url = None
    if disc.destination == "chat" and disc.chat_room_id:
        redirect_url = _scoped_path(request, f"/workforce/?room_id={disc.chat_room_id}")
    elif disc.destination == "feeds" and disc.feed_post_id:
        if disc.unit_id:
            redirect_url = _scoped_path(
                request,
                f"/dashboard/?feed_scope=unit&unit_id={disc.unit_id}#feed-card-{disc.feed_post_id}",
            )
        else:
            redirect_url = _scoped_path(
                request, f"/dashboard/?feed_scope=general#feed-card-{disc.feed_post_id}"
            )

    return JsonResponse(
        {"ok": True, "discussion_id": disc.id, "redirect_url": redirect_url}
    )


@login_required
@require_GET
def ajax_list_plan_discussions(request, entry_id):
    """List discussions for a plan entry, filtered by destination (chat|feeds)."""
    church = _church(request)
    if not church:
        return JsonResponse({"ok": False}, status=403)
    from bible.models import ReadingPlanDiscussion

    # destination param keeps chat threads and feed discussions distinct
    destination = request.GET.get("destination", "")  # "chat" | "feeds" | "" = all

    qs = ReadingPlanDiscussion.raw_objects.filter(church=church, entry_id=entry_id)
    if destination in ("chat", "feeds"):
        qs = qs.filter(destination=destination)
    qs = qs.select_related("author__user").order_by("created_at")[:100]

    try:
        from core.utils.colors import get_member_color
    except Exception:

        def get_member_color(mid, variant="hex"):
            return "#6366f1"

    data = [
        {
            "id": d.id,
            "author": str(d.author.user.title or "")
            + str(d.author.user.full_name or str(d.author.user.username)),
            "author_id": d.author_id,
            "author_color": get_member_color(d.author_id, variant="hex"),
            "initials": (str(d.author.user.full_name) or "?")[:1].upper(),
            "body": d.body,
            "scope": d.scope,
            "destination": d.destination,
            "is_admin": d.is_admin_prompt,
            "created_at": d.created_at.isoformat(),
        }
        for d in qs
    ]
    return JsonResponse({"ok": True, "discussions": data})


# ── AJAX: entries for a plan on a specific date ───────────────────────────────


@login_required
@require_GET
def ajax_plan_entries(request, plan_id):
    """Return all entries for a plan on a given date (ISO) as JSON."""
    church = _church(request)
    member = _member(request)
    if not church:
        return JsonResponse({"ok": False}, status=403)
    from bible.models import ReadingPlan, ReadingPlanEntry, MemberPlanProgress

    date_str = request.GET.get("date", "")
    try:
        from datetime import date as _date

        target_date = _date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return _je("invalid date")

    try:
        plan = ReadingPlan.raw_objects.get(id=plan_id, church=church, is_published=True)
    except Exception:
        return _je("plan not found", 404)

    entries = list(
        ReadingPlanEntry.raw_objects.filter(
            church=church, plan=plan, is_active=True, scheduled_date=target_date
        ).order_by("order")
    )

    completed_ids = set()
    if member:
        completed_ids = set(
            MemberPlanProgress.raw_objects.filter(
                church=church, member=member, plan=plan
            ).values_list("entry_id", flat=True)
        )

    data = [
        {
            "id": e.id,
            "passage_reference": e.passage_reference,
            "entry_type": e.entry_type,
            "devotional_title": e.devotional_title,
            "devotional_body": e.devotional_body,
            "book_id": e.book_id,
            "chapter": e.chapter,
            "chapter_to": e.chapter_to,
            "verse_start": e.verse_start,
            "verse_end": e.verse_end,
            "notes": e.notes,
            "translation": e.translation or plan.translation,
            "is_completed": e.id in completed_ids,
        }
        for e in entries
    ]
    return JsonResponse({"ok": True, "entries": data, "date": date_str})


# ── YouVersion OAuth views ────────────────────────────────────────────────────


@login_required
def youversion_oauth_login(request):
    """Step 1 — redirect the member to YouVersion to authorise."""
    from bible.services.youversion_oauth import (
        is_oauth_configured,
        build_authorization_url,
    )

    if not is_oauth_configured():
        from django.http import HttpResponse

        return HttpResponse(
            "YouVersion OAuth is not configured. " "Add YOUVERSION_CLIENT_ID to .env.",
            status=503,
        )

    member = _member(request)
    if not member:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()

    # Preserve next= so after OAuth we return the user to where they were
    next_url = request.GET.get("next", "").strip()
    if next_url:
        request.session["youversion_oauth_next"] = next_url

    # Pass request so the callback URI is slug-prefixed correctly for this tenant
    url = build_authorization_url(member, request=request)
    from django.shortcuts import redirect

    return redirect(url)


@login_required
def youversion_oauth_callback(request):
    """
    Step 2 — YouVersion redirects here with ?code=&state=
    Exchange the code for tokens, sync highlights, redirect back to reader.
    """
    from bible.services.youversion_oauth import exchange_code
    from django.shortcuts import redirect
    from django.urls import reverse

    member = _member(request)
    if not member:
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden()

    code = request.GET.get("code", "").strip()
    state = request.GET.get("state", "").strip()
    error = request.GET.get("error", "").strip()

    # Retrieve and clear the stored next URL (set by youversion_oauth_login)
    next_url = request.session.pop("youversion_oauth_next", "").strip()

    # Validate next_url — only allow same-origin relative paths
    def _safe_next(url: str) -> str:
        if url and url.startswith("/") and not url.startswith("//"):
            return url
        return ""

    safe_next = _safe_next(next_url)
    reader_url = safe_next or reverse("bible:bible_reader")

    if error or not code:
        logger.warning("youversion_oauth: callback error=%s", error)
        sep = "&" if "?" in reader_url else "?"
        return redirect(f"{reader_url}{sep}yv_auth=error")

    # Pass request so exchange_code uses the identical redirect_uri
    success = exchange_code(member, code, state, request=request)
    sep = "&" if "?" in reader_url else "?"
    return redirect(f"{reader_url}{sep}yv_auth={'ok' if success else 'error'}")


@login_required
@require_POST
def youversion_oauth_logout(request):
    """Disconnect the member's YouVersion account."""
    from bible.services.youversion_oauth import disconnect

    member = _member(request)
    if not member:
        return JsonResponse({"ok": False}, status=403)

    disconnect(member)
    return JsonResponse({"ok": True})


@login_required
@require_GET
def youversion_oauth_status(request):
    """JSON connection status — called by the reader on load."""
    from bible.services.youversion_oauth import (
        get_connection_status,
        is_oauth_configured,
    )

    member = _member(request)
    if not member:
        return JsonResponse({"ok": False, "connected": False}, status=403)

    status = get_connection_status(member)
    return JsonResponse(
        {
            "ok": True,
            "oauth_available": is_oauth_configured(),
            **status,
        }
    )


@login_required
@require_POST
def youversion_oauth_sync(request):
    """Re-sync YouVersion highlights on demand."""
    from bible.services.youversion_oauth import sync_highlights, get_connection_status

    member = _member(request)
    if not member:
        return JsonResponse({"ok": False}, status=403)

    if not get_connection_status(member).get("connected"):
        return JsonResponse({"ok": False, "error": "Not connected to YouVersion"})

    count = sync_highlights(member)
    return JsonResponse({"ok": True, "synced": count})