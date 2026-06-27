"""
lms/views.py — Church LMS (Canvas-style, full-featured)

New vs existing:
  - rubric_edit / rubric_save         → admin builds/edits a rubric for a module
  - review_submission                 → updated to handle rubric scoring
  - peer_review_submit                → trainee submits peer review
  - discussion_post                   → POST a discussion reply
  - announcement_create               → admin posts a course announcement
  - module_complete                   → mark non-graded module as done
  - course_detail                     → extended: rubrics, peer queue, announcements,
                                         discussions, completion map
  - proctor_queue                     → extended: peer-review pending count
"""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from lms.models import (
    LMSAnnouncement,
    LMSCertificate,
    LMSCourse,
    LMSDiscussionPost,
    LMSEnrollment,
    LMSModule,
    LMSModuleCompletion,
    LMSPeerReview,
    LMSPeerReviewAssignment,
    LMSReview,
    LMSRubric,
    LMSRubricCriterion,
    LMSRubricScore,
    LMSSubmission,
)
from lms.services import sync_empty_current_modules
from workforce.services.evaluator import evaluate_trainee_progress


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(request):
    if request.user.is_superuser:
        return True
    return bool(
        getattr(request, "permissions", None)
        and request.permissions.can("dashboard.admin")
    )


def _get_reading_plans(course):
    """Return active reading plans for the same church as the course."""
    try:
        from bible.models import ReadingPlan
        church = getattr(course, "church", None)
        if not church:
            return []
        return list(
            ReadingPlan.raw_objects.filter(church=church, is_active=True)
            .order_by("-start_date")[:30]
        )
    except Exception:
        return []


def _module_template_data_from_post(request):
    content_type  = request.POST.get("content_type", "text")
    template_key  = request.POST.get("content_template", "").strip()
    template_type = request.POST.get("template_type", "none")
    data = {}
    if template_key:
        data["template"] = template_key

    # ── Quiz questions ────────────────────────────────────────────────────────
    if content_type == "quiz" or template_type == "quiz_bank":
        questions = []
        question_texts = request.POST.getlist("quiz_question")
        answers        = request.POST.getlist("quiz_answer")
        explanations   = request.POST.getlist("quiz_explanation")
        option_groups  = [
            request.POST.getlist("quiz_option_a"),
            request.POST.getlist("quiz_option_b"),
            request.POST.getlist("quiz_option_c"),
            request.POST.getlist("quiz_option_d"),
        ]
        for idx, text in enumerate(question_texts):
            text = text.strip()
            options = [
                group[idx].strip()
                for group in option_groups
                if idx < len(group) and group[idx].strip()
            ]
            if not text and not options:
                continue
            questions.append({
                "question":    text,
                "options":     options,
                "answer":      answers[idx].strip() if idx < len(answers) else "",
                "explanation": explanations[idx].strip() if idx < len(explanations) else "",
            })
        if questions:
            data["questions"] = questions

    # ── Lesson page ───────────────────────────────────────────────────────────
    if template_type == "lesson":
        data.update({
            "objective":  request.POST.get("tpl_objective", "").strip(),
            "scripture":  request.POST.get("tpl_scripture", "").strip(),
            "teaching":   request.POST.get("tpl_teaching", "").strip(),
            "reflection": request.POST.get("tpl_reflection", "").strip(),
            "action":     request.POST.get("tpl_action", "").strip(),
        })

    # ── Case study ────────────────────────────────────────────────────────────
    elif template_type == "case_study":
        data.update({
            "context":    request.POST.get("tpl_context", "").strip(),
            "challenge":  request.POST.get("tpl_challenge", "").strip(),
            "principle":  request.POST.get("tpl_principle", "").strip(),
            "discussion": request.POST.get("tpl_discussion", "").strip(),
        })

    # ── Guided reflection ─────────────────────────────────────────────────────
    elif template_type == "reflection":
        data.update({
            "opening":        request.POST.get("tpl_opening", "").strip(),
            "med_scripture":  request.POST.get("tpl_med_scripture", "").strip(),
            "prompts":        request.POST.get("tpl_prompts", "").strip(),
            "prayer":         request.POST.get("tpl_prayer", "").strip(),
        })

    # ── Bible devotional ──────────────────────────────────────────────────────
    elif template_type == "bible_devotional":
        data.update({
            "dev_title":       request.POST.get("tpl_dev_title", "").strip(),
            "dev_passage":     request.POST.get("tpl_dev_passage", "").strip(),
            "dev_body":        request.POST.get("tpl_dev_body", "").strip(),
            "dev_application": request.POST.get("tpl_dev_application", "").strip(),
        })

    # ── Action checklist ──────────────────────────────────────────────────────
    elif template_type == "checklist":
        data.update({
            "check_context":  request.POST.get("tpl_check_context", "").strip(),
            "check_items":    request.POST.get("tpl_check_items", "").strip(),
            "check_criteria": request.POST.get("tpl_check_criteria", "").strip(),
        })

    # ── Discussion prompt ─────────────────────────────────────────────────────
    elif template_type == "discussion_prompt":
        data.update({
            "disc_scripture":   request.POST.get("tpl_disc_scripture", "").strip(),
            "disc_questions":   request.POST.get("tpl_disc_questions", "").strip(),
            "disc_facilitator": request.POST.get("tpl_disc_facilitator", "").strip(),
        })

    return data


def _submission_content_from_post(module, request):
    if module.content_type != "quiz":
        return request.POST.get("content", "").strip()

    answers = []
    questions = (module.template_data or {}).get("questions", [])
    for idx, question in enumerate(questions, start=1):
        answers.append({
            "question": question.get("question", f"Question {idx}"),
            "answer": request.POST.get(f"quiz_answer_{idx}", "").strip(),
        })
    if answers:
        return json.dumps(answers)
    return request.POST.get("content", "").strip()


def _get_unlocked_module_ids(church, course, member, modules, is_admin):
    """
    Return set of module IDs the member is allowed to submit for,
    based on attendance records for linked sessions.
    Admins always get everything unlocked.
    """
    if is_admin:
        return {mod.id for mod in modules}

    from lms.models import LMSSession
    course_sessions = LMSSession.raw_objects.filter(
        church=church, course=course, is_active=True,
        event__isnull=False, event__is_active=True,
    )
    if not course_sessions.exists():
        return set()

    if not member:
        return set()

    from workforce.models import AttendanceRecord
    unlocked = set()
    for mod in modules:
        specific_ids = list(
            course_sessions.filter(modules_covered=mod).values_list("id", flat=True)
        )
        blanket_ids = [s.id for s in course_sessions if not s.modules_covered.exists()]
        relevant = specific_ids + blanket_ids
        if not relevant:
            continue
        event_ids = list(
            course_sessions.filter(id__in=relevant).values_list("event_id", flat=True)
        )
        if AttendanceRecord.raw_objects.filter(
            church=church,
            user=member,
            event_id__in=event_ids,
            status__in=("present", "late", "excused"),
        ).exists():
            unlocked.add(mod.id)
    return unlocked


# ─────────────────────────────────────────────────────────────────────────────
# Course list
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def course_list(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church:
        return HttpResponseForbidden()

    # Back-fill slugs
    from django.utils.text import slugify
    for course in LMSCourse.raw_objects.filter(church=church, slug=""):
        base = slugify(course.title)[:100] or f"course-{course.pk}"
        slug, n = base, 1
        while LMSCourse.raw_objects.filter(church=church, slug=slug).exclude(pk=course.pk).exists():
            slug = f"{base}-{n}"; n += 1
        course.slug = slug
        course.save(update_fields=["slug"])

    filter_type = request.GET.get("type", "")
    is_admin = _is_admin(request)

    if is_admin:
        qs = LMSCourse.raw_objects.filter(church=church, is_active=True)
        if filter_type and filter_type not in ("all", "mine"):
            qs = qs.filter(course_type=filter_type)
        elif filter_type == "mine" and member:
            ids = LMSEnrollment.raw_objects.filter(church=church, member=member).values_list("course_id", flat=True)
            qs = qs.filter(id__in=ids)
        courses = qs.order_by("course_type", "title")
    elif member:
        ids = LMSEnrollment.raw_objects.filter(church=church, member=member).values_list("course_id", flat=True)
        courses = LMSCourse.raw_objects.filter(church=church, is_active=True, id__in=ids).order_by("course_type", "title")
    else:
        courses = LMSCourse.raw_objects.none()

    enrollments = (
        {e.course_id: e for e in LMSEnrollment.raw_objects.filter(church=church, member=member)}
        if member else {}
    )
    return render(request, "lms/course_list.html", {
        "courses": courses,
        "enrollments": enrollments,
        "filter_type": filter_type,
        "page_title": "Training",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Course detail — main view, extended with all Canvas features
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def course_detail(request, course_slug):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    course = get_object_or_404(
        LMSCourse.raw_objects.filter(church=church, is_active=True), slug=course_slug
    )
    enrollment = (
        LMSEnrollment.raw_objects.filter(church=church, member=member, course=course).first()
        if member else None
    )
    is_admin = _is_admin(request)

    if not is_admin and not enrollment:
        return HttpResponseForbidden("You are not enrolled in this course.")

    modules = list(course.modules.filter(is_active=True).order_by("order"))

    # Latest submission per module
    submissions = {}
    if enrollment:
        for sub in LMSSubmission.raw_objects.filter(
            church=church, enrollment=enrollment
        ).order_by("-retake_count"):
            submissions.setdefault(sub.module_id, sub)

    # Module completions (for non-graded modules)
    completions = set()
    if enrollment:
        completions = set(
            LMSModuleCompletion.raw_objects.filter(
                church=church, enrollment=enrollment
            ).values_list("module_id", flat=True)
        )

    # Rubrics per module
    rubrics = {}
    for mod in modules:
        try:
            rubrics[mod.id] = mod.rubric
        except Exception:
            rubrics[mod.id] = None

    # Unlocked modules
    unlocked_module_ids = _get_unlocked_module_ids(church, course, member, modules, is_admin)

    # Sessions
    from lms.models import LMSSession
    from workforce.models import AttendanceRecord
    sessions = (
        LMSSession.raw_objects.filter(
            church=church, course=course, is_active=True,
            event__isnull=False, event__is_active=True,
        )
        .select_related("event")
        .prefetch_related("modules_covered")
        .order_by("event__date", "event__time")
    )

    session_attendance = {}
    for session in sessions:
        if session.event_id and member:
            session_attendance[session.id] = AttendanceRecord.raw_objects.filter(
                church=church, user=member, event_id=session.event_id,
            ).first()

    module_session_map = {}
    for session in sessions:
        covered_ids = list(session.modules_covered.values_list("id", flat=True))
        if not covered_ids:
            for mod in modules:
                module_session_map.setdefault(mod.id, []).append(session)
        else:
            for mid in covered_ids:
                module_session_map.setdefault(mid, []).append(session)

    # Certificate
    has_certificate = bool(
        LMSCertificate.raw_objects.filter(church=church, enrollment=enrollment, is_active=True).exists()
    ) if enrollment else False

    # Admin stats
    passed_count = (
        LMSEnrollment.raw_objects.filter(church=church, course=course, status="passed").count()
        if is_admin else 0
    )

    # Announcements (latest 5)
    announcements = LMSAnnouncement.raw_objects.filter(
        church=church, course=course, is_active=True
    ).order_by("-created_at")[:5]

    # Discussion posts (top-level per module — loaded in template via module id)
    # We pass a map module_id → top-level posts
    discussion_map = {}
    if any(mod.discussion_enabled for mod in modules):
        from django.db.models import Prefetch
        posts_qs = LMSDiscussionPost.raw_objects.filter(
            church=church,
            module__course=course,
            parent__isnull=True,
            is_active=True,
        ).select_related("author__user").prefetch_related(
            Prefetch("replies", queryset=LMSDiscussionPost.raw_objects.filter(
                church=church, is_active=True
            ).select_related("author__user"))
        )
        for post in posts_qs:
            discussion_map.setdefault(post.module_id, []).append(post)

    # Peer review tasks for this member
    peer_tasks = {}
    if member:
        for assignment in LMSPeerReviewAssignment.raw_objects.filter(
            church=church,
            reviewer=member,
            submission__module__course=course,
            is_active=True,
        ).select_related("submission__module", "submission__enrollment__member__user"):
            has_review = hasattr(assignment, "review")
            peer_tasks[assignment.id] = {
                "assignment": assignment,
                "done": has_review,
            }

    # Pending peer review count for badge
    pending_peer_count = sum(1 for t in peer_tasks.values() if not t["done"])

    # Module progress JSON for JS renderer
    module_data = []
    for mod in modules:
        sub = submissions.get(mod.id)
        rubric = rubrics.get(mod.id)
        module_data.append({
            "id": mod.id,
            "order": mod.order,
            "title": mod.title,
            "description": mod.description,
            "content_type": mod.content_type,
            "content_type_icon": mod.content_type_icon,
            "estimated_minutes": mod.estimated_minutes,
            "is_required": mod.is_required,
            "requires_attachment": mod.requires_attachment,
            "has_rubric": rubric is not None,
            "rubric_total": rubric.total_points if rubric else 0,
            "enable_peer_review": mod.enable_peer_review,
            "discussion_enabled": mod.discussion_enabled,
            "unlocked": mod.id in unlocked_module_ids,
            "completed": mod.id in completions,
            "submission_status": sub.status if sub else None,
            "submission_score": sub.score if sub else None,
            "submission_id": sub.id if sub else None,
            "edit_url": reverse("lms:module_edit", args=[mod.id]) if is_admin else "",
            "rubric_url": reverse("lms:rubric_edit", args=[mod.id]) if is_admin else "",
        })

    return render(request, "lms/course_detail.html", {
        "course": course,
        "modules": modules,
        "module_data_json": json.dumps(module_data),
        "enrollment": enrollment,
        "submissions": submissions,
        "completions": completions,
        "rubrics": rubrics,
        "is_admin": is_admin,
        "has_certificate": has_certificate,
        "passed_count": passed_count,
        "unlocked_module_ids": unlocked_module_ids,
        "unlocked_module_ids_json": json.dumps(list(unlocked_module_ids)),
        "module_session_map": module_session_map,
        "sessions": sessions,
        "session_attendance": session_attendance,
        "announcements": announcements,
        "discussion_map": discussion_map,
        "peer_tasks": peer_tasks,
        "pending_peer_count": pending_peer_count,
        "page_title": course.title,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Submit module
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def submit_module(request, enrollment_id, module_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()

    enrollment = get_object_or_404(
        LMSEnrollment.raw_objects.filter(church=church, member=member), id=enrollment_id
    )
    module = get_object_or_404(
        LMSModule.raw_objects.filter(church=church, course=enrollment.course, is_active=True),
        id=module_id,
    )
    modules = list(enrollment.course.modules.filter(is_active=True).order_by("order"))
    if module.id not in _get_unlocked_module_ids(
        church, enrollment.course, member, modules, _is_admin(request)
    ):
        messages.error(
            request,
            "You must attend the linked training session before opening this module.",
        )
        return redirect("lms:course_detail", course_slug=enrollment.course.slug)

    if enrollment.course.strict_sequence and enrollment.current_module:
        if module.order > enrollment.current_module.order:
            messages.error(request, "Please complete the previous module first.")
            return redirect("lms:course_detail", course_slug=enrollment.course.slug)

    prev = (
        LMSSubmission.raw_objects.filter(church=church, enrollment=enrollment, module=module)
        .order_by("-retake_count")
        .first()
    )
    retake_count = (prev.retake_count + 1) if prev else 0

    if enrollment.course.max_retakes and retake_count > enrollment.course.max_retakes:
        messages.error(request, "Maximum retakes reached.")
        return redirect("lms:course_detail", course_slug=enrollment.course.slug)

    attachment = request.FILES.get("attachment")
    if module.requires_attachment and not attachment:
        messages.error(request, "An attachment is required for this module.")
        return redirect("lms:course_detail", course_slug=enrollment.course.slug)

    submission = LMSSubmission.raw_objects.create(
        church=church,
        enrollment=enrollment,
        module=module,
        status="submitted",
        content=_submission_content_from_post(module, request),
        attachment=attachment,
        retake_count=retake_count,
        is_active=True,
    )

    # If peer review enabled: assign peers and flip to peer_review status
    if module.enable_peer_review and module.peer_review_count > 0:
        _assign_peers(church, submission, module)
        submission.status = "peer_review"
        submission.save(update_fields=["status"])
        messages.success(request, "Submission received. Sent to peers for review.")
    else:
        messages.success(request, "Submission received. Awaiting proctor review.")

    return redirect("lms:course_detail", course_slug=enrollment.course.slug)


def _assign_peers(church, submission, module):
    """
    Randomly assign `peer_review_count` peers (other enrolled members) to review this submission.
    Excludes the submitter and people already assigned.
    """
    import random
    submitter_id = submission.enrollment.member_id
    course = submission.enrollment.course

    eligible = list(
        LMSEnrollment.raw_objects.filter(
            church=church, course=course, is_active=True, status="active"
        )
        .exclude(member_id=submitter_id)
        .values_list("member_id", flat=True)
    )
    already_assigned = set(
        LMSPeerReviewAssignment.raw_objects.filter(
            church=church, submission=submission
        ).values_list("reviewer_id", flat=True)
    )
    candidates = [m for m in eligible if m not in already_assigned]
    random.shuffle(candidates)
    count = min(module.peer_review_count, len(candidates))
    from accounts.models import ChurchMember
    for member_id in candidates[:count]:
        try:
            reviewer = ChurchMember.raw_objects.get(pk=member_id)
            LMSPeerReviewAssignment.raw_objects.create(
                church=church,
                submission=submission,
                reviewer=reviewer,
                is_active=True,
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Peer review submit
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def peer_review_submit(request, assignment_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()

    assignment = get_object_or_404(
        LMSPeerReviewAssignment.raw_objects.filter(church=church, reviewer=member, is_active=True),
        id=assignment_id,
    )

    if hasattr(assignment, "review"):
        messages.info(request, "You have already submitted this peer review.")
        return redirect("lms:course_detail", course_slug=assignment.submission.module.course.slug)

    score_raw = request.POST.get("score", "")
    score = int(score_raw) if score_raw.isdigit() else None
    peer_review = LMSPeerReview.raw_objects.create(
        church=church,
        assignment=assignment,
        score=score,
        feedback=request.POST.get("feedback", "").strip(),
        completed_at=timezone.now(),
        is_active=True,
    )

    # Rubric scores for peer review
    module = assignment.submission.module
    if hasattr(module, "rubric"):
        rubric = module.rubric
        total_awarded = 0
        for criterion in rubric.criteria.filter(is_active=True):
            pts_raw = request.POST.get(f"criterion_{criterion.id}", "0")
            pts = int(pts_raw) if pts_raw.isdigit() else 0
            pts = min(pts, criterion.max_points)
            LMSRubricScore.raw_objects.create(
                church=church,
                peer_review=peer_review,
                criterion=criterion,
                points_awarded=pts,
                comment=request.POST.get(f"comment_{criterion.id}", "").strip(),
                is_active=True,
            )
            total_awarded += pts
        if rubric.total_points > 0:
            peer_review.score = int((total_awarded / rubric.total_points) * 100)
            peer_review.save(update_fields=["score"])

    # Check if all peer reviews are complete → move back to 'reviewing' for proctor
    submission = assignment.submission
    total_assigned = submission.peer_assignments.filter(is_active=True).count()
    completed = submission.peer_assignments.filter(
        is_active=True, review__isnull=False
    ).count()
    if completed >= total_assigned:
        submission.status = "reviewing"
        submission.save(update_fields=["status"])

    messages.success(request, "Peer review submitted. Thank you!")
    return redirect("lms:course_detail", course_slug=module.course.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Proctor review — updated to handle rubric
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def review_submission(request, submission_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    submission = get_object_or_404(
        LMSSubmission.raw_objects.filter(church=church), id=submission_id
    )

    if hasattr(submission, "review"):
        messages.warning(request, "This submission has already been reviewed.")
        return redirect("lms:proctor_queue")

    passed = request.POST.get("passed") == "1"

    # Rubric scoring
    rubric = None
    total_awarded = 0
    try:
        rubric = submission.module.rubric
    except Exception:
        pass

    review = LMSReview.raw_objects.create(
        church=church,
        submission=submission,
        proctor=member,
        passed=passed,
        score=None,
        feedback=request.POST.get("feedback", "").strip(),
        is_active=True,
    )

    if rubric:
        for criterion in rubric.criteria.filter(is_active=True):
            pts_raw = request.POST.get(f"criterion_{criterion.id}", "0")
            pts = int(pts_raw) if pts_raw.isdigit() else 0
            pts = min(pts, criterion.max_points)
            LMSRubricScore.raw_objects.create(
                church=church,
                review=review,
                criterion=criterion,
                points_awarded=pts,
                comment=request.POST.get(f"comment_{criterion.id}", "").strip(),
                is_active=True,
            )
            total_awarded += pts
        if rubric.total_points > 0:
            rubric_pct = int((total_awarded / rubric.total_points) * 100)
            review.score = rubric_pct
        else:
            score_raw = request.POST.get("score", "")
            review.score = int(score_raw) if score_raw.isdigit() else None
    else:
        score_raw = request.POST.get("score", "")
        review.score = int(score_raw) if score_raw.isdigit() else None

    review.save(update_fields=["score"])

    # Update submission
    submission.status = "passed" if passed else "failed"
    submission.score = review.score
    submission.save(update_fields=["status", "score"])

    # Update enrollment progress
    enrollment = submission.enrollment
    if passed:
        modules = list(enrollment.course.modules.filter(is_active=True).order_by("order"))
        passed_module_ids = set(
            LMSSubmission.raw_objects.filter(
                church=church, enrollment=enrollment, status="passed"
            ).values_list("module_id", flat=True)
        )
        # Advance current_module
        next_mod = None
        for mod in modules:
            if mod.id not in passed_module_ids:
                next_mod = mod
                break
        enrollment.current_module = next_mod
        # Check if all required modules passed
        required_ids = {m.id for m in modules if m.is_required}
        if required_ids and required_ids.issubset(passed_module_ids | {submission.module_id}):
            enrollment.status = "passed"
            enrollment.completed_at = timezone.now()
            # Compute overall score
            scores = LMSSubmission.raw_objects.filter(
                church=church, enrollment=enrollment, status="passed"
            ).values_list("score", flat=True)
            valid = [s for s in scores if s is not None]
            enrollment.score = int(sum(valid) / len(valid)) if valid else None
            # Issue certificate
            if not LMSCertificate.raw_objects.filter(church=church, enrollment=enrollment).exists():
                tmpl = enrollment.course.certificate_template or (
                    "This certifies that {name} has successfully completed {course} on {date}."
                )
                cert_text = tmpl.format(
                    name=str(enrollment.member),
                    course=enrollment.course.title,
                    date=timezone.now().date().strftime("%d %B %Y"),
                )
                LMSCertificate.raw_objects.create(
                    church=church,
                    enrollment=enrollment,
                    issued_by=member,
                    certificate_text=cert_text,
                    is_active=True,
                )
            try:
                evaluate_trainee_progress(enrollment.member, church)
            except Exception:
                pass
        enrollment.save()
    elif not passed and not enrollment.course.allow_retake:
        enrollment.status = "failed"
        enrollment.save(update_fields=["status"])

    messages.success(
        request,
        f"Review saved — {'Passed ✅' if passed else 'Failed ❌'}"
        + (f" ({review.score}%)" if review.score is not None else ""),
    )
    return redirect("lms:proctor_queue")


# ─────────────────────────────────────────────────────────────────────────────
# Module "mark as done" (non-graded content)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def module_complete(request, enrollment_id, module_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()

    enrollment = get_object_or_404(
        LMSEnrollment.raw_objects.filter(church=church, member=member), id=enrollment_id
    )
    module = get_object_or_404(
        LMSModule.raw_objects.filter(church=church, course=enrollment.course, is_active=True),
        id=module_id,
    )
    modules = list(enrollment.course.modules.filter(is_active=True).order_by("order"))
    if module.id not in _get_unlocked_module_ids(
        church, enrollment.course, member, modules, _is_admin(request)
    ):
        messages.error(
            request,
            "You must attend the linked training session before completing this module.",
        )
        return redirect("lms:course_detail", course_slug=enrollment.course.slug)
    LMSModuleCompletion.raw_objects.get_or_create(
        church=church, enrollment=enrollment, module=module,
        defaults={"is_active": True},
    )
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    messages.success(request, f"'{module.title}' marked as complete.")
    return redirect("lms:course_detail", course_slug=enrollment.course.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Discussion board
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def discussion_post(request, module_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()

    module = get_object_or_404(
        LMSModule.raw_objects.filter(church=church, is_active=True, discussion_enabled=True),
        id=module_id,
    )
    if not _is_admin(request):
        enrollment = LMSEnrollment.raw_objects.filter(
            church=church,
            member=member,
            course=module.course,
            is_active=True,
        ).first()
        modules = list(module.course.modules.filter(is_active=True).order_by("order"))
        if not enrollment or module.id not in _get_unlocked_module_ids(
            church, module.course, member, modules, False
        ):
            return HttpResponseForbidden("This discussion unlocks after attendance.")

    body = request.POST.get("body", "").strip()
    if not body:
        messages.error(request, "Post cannot be empty.")
        return redirect("lms:course_detail", course_slug=module.course.slug)

    parent_id = request.POST.get("parent_id") or None
    parent = None
    if parent_id:
        try:
            parent = LMSDiscussionPost.raw_objects.get(
                church=church, id=int(parent_id), module=module, is_active=True
            )
        except (LMSDiscussionPost.DoesNotExist, ValueError):
            pass

    LMSDiscussionPost.raw_objects.create(
        church=church, module=module, author=member, parent=parent, body=body, is_active=True
    )
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True})
    return redirect("lms:course_detail", course_slug=module.course.slug)


# ─────────────────────────────────────────────────────────────────────────────
# Announcements
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def announcement_create(request, course_slug):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    course = get_object_or_404(LMSCourse.raw_objects.filter(church=church), slug=course_slug)
    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()
    if not title or not body:
        messages.error(request, "Announcement needs a title and body.")
        return redirect("lms:course_detail", course_slug=course_slug)

    LMSAnnouncement.raw_objects.create(
        church=church, course=course, author=member, title=title, body=body, is_active=True
    )
    messages.success(request, "Announcement posted.")
    return redirect("lms:course_detail", course_slug=course_slug)


# ─────────────────────────────────────────────────────────────────────────────
# Rubric builder (admin)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def rubric_edit(request, module_id):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    module = get_object_or_404(LMSModule.raw_objects.filter(church=church), id=module_id)
    rubric = None
    try:
        rubric = module.rubric
    except Exception:
        pass

    if request.method == "POST":
        rubric_title = request.POST.get("rubric_title", "Rubric").strip()
        if not rubric:
            rubric = LMSRubric.raw_objects.create(
                church=church, module=module, title=rubric_title, is_active=True
            )
        else:
            rubric.title = rubric_title
            rubric.save(update_fields=["title"])

        # Delete removed criteria
        keep_ids = [int(v) for k, v in request.POST.items() if k.startswith("criterion_id_") and v]
        rubric.criteria.filter(is_active=True).exclude(id__in=keep_ids).update(is_active=False)

        # Update/create criteria
        i = 0
        while True:
            desc = request.POST.get(f"desc_{i}", "")
            if not desc:
                break
            crit_id = request.POST.get(f"criterion_id_{i}", "")
            max_pts = int(request.POST.get(f"max_pts_{i}", 10))
            ratings_json = request.POST.get(f"ratings_json_{i}", "")
            if crit_id:
                try:
                    crit = LMSRubricCriterion.raw_objects.get(
                        church=church, rubric=rubric, id=int(crit_id)
                    )
                    crit.description = desc
                    crit.max_points = max_pts
                    crit.order = i
                    crit.ratings_json = ratings_json
                    crit.save()
                except LMSRubricCriterion.DoesNotExist:
                    pass
            else:
                LMSRubricCriterion.raw_objects.create(
                    church=church, rubric=rubric,
                    description=desc, max_points=max_pts,
                    order=i, ratings_json=ratings_json, is_active=True,
                )
            i += 1

        messages.success(request, "Rubric saved.")
        return redirect("lms:course_detail", course_slug=module.course.slug)

    criteria = rubric.criteria.filter(is_active=True).order_by("order") if rubric else []
    return render(request, "lms/rubric_edit.html", {
        "module": module,
        "course": module.course,
        "rubric": rubric,
        "criteria": criteria,
        "page_title": f"Rubric — {module.title}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Proctor queue — extended with peer review stats
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def proctor_queue(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden()
    is_admin = _is_admin(request)
    member = getattr(request, "member", None)

    # Submissions pending proctor review (status = submitted or reviewing)
    submissions = (
        LMSSubmission.raw_objects.filter(
            church=church, status__in=("submitted", "reviewing"), is_active=True
        )
        .select_related(
            "enrollment__member__user",
            "enrollment__course",
            "module",
        )
        .order_by("created_at")
    )

    # Peer assignments pending for this member
    pending_peer = []
    if member:
        pending_peer = list(
            LMSPeerReviewAssignment.raw_objects.filter(
                church=church, reviewer=member, is_active=True
            )
            .select_related(
                "submission__module__course",
                "submission__enrollment__member__user",
            )
            .filter(review__isnull=True)
        )

    # Pre-load rubrics for submissions
    rubric_map = {}
    for sub in submissions:
        try:
            rubric_map[sub.id] = sub.module.rubric
        except Exception:
            rubric_map[sub.id] = None

    return render(request, "lms/proctor_queue.html", {
        "submissions": submissions,
        "pending_peer": pending_peer,
        "rubric_map": rubric_map,
        "is_admin": is_admin,
        "page_title": "Review Queue",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Admin: course list, create, edit, module add/edit (unchanged structure)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def admin_courses(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    from guests.models import MembershipTrack
    from units.models import ChurchUnit

    courses = (
        LMSCourse.raw_objects.filter(church=church, is_active=True)
        .prefetch_related("modules", "enrollments")
        .order_by("course_type", "title")
    )
    return render(request, "lms/admin_courses.html", {
        "courses": courses,
        "membership_tracks": MembershipTrack.raw_objects.filter(church=church, is_active=True),
        "all_units": (
            __import__("units.models", fromlist=["ChurchUnit"]).ChurchUnit
            .raw_objects.filter(church=church, is_active=True).order_by("name")
        ),
        "page_title": "Manage Courses",
    })


@login_required
@require_POST
def course_create(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    from guests.models import MembershipTrack
    from units.models import ChurchUnit

    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Title is required.")
        return redirect("lms:admin_courses")

    unit_id = request.POST.get("unit") or None
    track_id = request.POST.get("membership_track") or None
    unit = ChurchUnit.raw_objects.filter(id=unit_id, church=church).first() if unit_id else None
    track = MembershipTrack.raw_objects.filter(id=track_id, church=church).first() if track_id else None

    course = LMSCourse.raw_objects.create(
        church=church,
        title=title,
        description=request.POST.get("description", "").strip(),
        course_type=request.POST.get("course_type", "unit_training"),
        delivery_mode=request.POST.get("delivery_mode", "physical"),
        passing_score=int(request.POST.get("passing_score", 70)),
        allow_retake="allow_retake" in request.POST,
        max_retakes=int(request.POST.get("max_retakes", 2)),
        strict_sequence="strict_sequence" in request.POST,
        certificate_template=request.POST.get("certificate_template", "").strip(),
        unit=unit,
        membership_track=track,
        created_by=member,
        is_active=True,
    )
    course_image = request.FILES.get("course_image")
    if course_image:
        course.course_image = course_image
        course.save(update_fields=["course_image"])

    messages.success(request, f"Course '{course.title}' created.")
    return redirect("lms:course_detail", course_slug=course.slug)


@login_required
def course_edit(request, course_slug):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    from guests.models import MembershipTrack
    from units.models import ChurchUnit

    course = get_object_or_404(LMSCourse.raw_objects.filter(church=church), slug=course_slug)
    if request.method == "POST":
        course.title = request.POST.get("title", course.title).strip()
        course.description = request.POST.get("description", "").strip()
        course.course_type = request.POST.get("course_type", course.course_type)
        course.delivery_mode = request.POST.get("delivery_mode", course.delivery_mode)
        course.passing_score = int(request.POST.get("passing_score", course.passing_score))
        course.allow_retake = "allow_retake" in request.POST
        course.max_retakes = int(request.POST.get("max_retakes", course.max_retakes))
        course.strict_sequence = "strict_sequence" in request.POST
        course.certificate_template = request.POST.get("certificate_template", "").strip()
        course.is_active = "is_active" in request.POST
        unit_id = request.POST.get("unit") or None
        track_id = request.POST.get("membership_track") or None
        course.unit = ChurchUnit.raw_objects.filter(id=unit_id, church=church).first() if unit_id else None
        course.membership_track = MembershipTrack.raw_objects.filter(id=track_id, church=church).first() if track_id else None
        course_image = request.FILES.get("course_image")
        if course_image:
            course.course_image = course_image
        course.save()
        messages.success(request, "Course updated.")
        return redirect("lms:course_detail", course_slug=course.slug)
    return render(request, "lms/course_edit.html", {
        "course": course,
        "membership_tracks": MembershipTrack.raw_objects.filter(church=church, is_active=True),
        "all_units": (
            __import__("units.models", fromlist=["ChurchUnit"]).ChurchUnit
            .raw_objects.filter(church=church, is_active=True).order_by("name")
        ),
        "page_title": f"Edit — {course.title}",
    })


@login_required
@require_POST
def module_add(request, course_slug):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    course = get_object_or_404(LMSCourse.raw_objects.filter(church=church), slug=course_slug)
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Module title required.")
        return redirect("lms:course_detail", course_slug=course.slug)
    order_raw = request.POST.get("order", "")
    order = int(order_raw) if order_raw.isdigit() else course.modules.count() + 1
    mod = LMSModule.raw_objects.create(
        church=church, course=course, title=title, order=order,
        content_type=request.POST.get("content_type", "text"),
        description=request.POST.get("description", "").strip(),
        content=request.POST.get("content", "").strip(),
        external_url=request.POST.get("external_url", "").strip(),
        embed_code=request.POST.get("embed_code", "").strip(),
        estimated_minutes=(
            int(request.POST["estimated_minutes"])
            if request.POST.get("estimated_minutes", "").strip().isdigit()
            else None
        ),
        template_data=_module_template_data_from_post(request),
        is_required="is_required" in request.POST,
        requires_attachment="requires_attachment" in request.POST,
        enable_peer_review="enable_peer_review" in request.POST,
        peer_review_count=int(request.POST.get("peer_review_count", 2)),
        discussion_enabled="discussion_enabled" in request.POST,
        is_active=True,
    )
    resource_file = request.FILES.get("resource")
    if resource_file:
        mod.resource = resource_file
        mod.save(update_fields=["resource"])
    sync_empty_current_modules(course)
    messages.success(request, f"Module '{title}' added.")
    return redirect("lms:course_detail", course_slug=course.slug)


@login_required
def module_edit(request, module_id):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    module = get_object_or_404(LMSModule.raw_objects.filter(church=church), id=module_id)
    if request.method == "POST":
        old_order = module.order
        new_order_raw = request.POST.get("order", "").strip()
        new_order = int(new_order_raw) if new_order_raw.isdigit() else old_order

        module.title = request.POST.get("title", module.title).strip()
        module.description = request.POST.get("description", "").strip()
        module.content = request.POST.get("content", "").strip()
        module.content_type = request.POST.get("content_type", module.content_type)
        module.template_type = request.POST.get("template_type", "none")
        module.is_required = "is_required" in request.POST
        module.requires_attachment = "requires_attachment" in request.POST
        module.enable_peer_review = "enable_peer_review" in request.POST
        module.peer_review_count = int(request.POST.get("peer_review_count", 2))
        module.discussion_enabled = "discussion_enabled" in request.POST
        module.is_active = "is_active" in request.POST
        module.external_url = request.POST.get("external_url", "").strip()
        module.embed_code = request.POST.get("embed_code", "").strip()
        module.template_data = _module_template_data_from_post(request)
        em = request.POST.get("estimated_minutes", "").strip()
        module.estimated_minutes = int(em) if em.isdigit() else None
        # Reading plan FK (for reading_plan content type)
        rp_id = request.POST.get("reading_plan_id", "").strip()
        if rp_id and rp_id.isdigit():
            from bible.models import ReadingPlan
            module.reading_plan = ReadingPlan.raw_objects.filter(
                church=church, id=int(rp_id)
            ).first()
        elif not rp_id:
            module.reading_plan = None
        resource_file = request.FILES.get("resource")
        if resource_file:
            module.resource = resource_file
        with transaction.atomic():
            if new_order != old_order:
                conflicting = (
                    LMSModule.raw_objects.filter(
                        church=church,
                        course=module.course,
                        order=new_order,
                    )
                    .exclude(pk=module.pk)
                    .first()
                )
                if conflicting:
                    temp_order = (
                        LMSModule.raw_objects.filter(church=church, course=module.course)
                        .order_by("-order")
                        .values_list("order", flat=True)
                        .first()
                        or 0
                    ) + 1
                    conflicting.order = temp_order
                    conflicting.save(update_fields=["order"])
                    module.order = new_order
                    module.save()
                    conflicting.order = old_order
                    conflicting.save(update_fields=["order"])
                else:
                    module.order = new_order
                    module.save()
            else:
                module.save()
        messages.success(request, "Module updated.")
        return redirect("lms:course_detail", course_slug=module.course.slug)
    return render(request, "lms/module_edit.html", {
        "module": module,
        "course": module.course,
        "content_type_choices": LMSModule.CONTENT_TYPE_CHOICES,
        "template_type_choices": [
            ("none",              "No Template (free-form)"),
            ("lesson",            "Lesson Page"),
            ("case_study",        "Case Study"),
            ("reflection",        "Guided Reflection"),
            ("bible_devotional",  "Bible Devotional"),
            ("reading_plan_embed","Reading Plan Embed"),
            ("discussion_prompt", "Discussion Prompt"),
            ("checklist",         "Action Checklist"),
            ("quiz_bank",         "Quiz / Question Bank"),
        ],
        "reading_plans": _get_reading_plans(module.course),
        "page_title": f"Edit Module — {module.title}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def session_list(request, course_slug):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()

    from lms.models import LMSSession
    from workforce.models import AttendanceRecord

    course = get_object_or_404(LMSCourse.raw_objects.filter(church=church), slug=course_slug)

    if request.method == "POST" and "update_session_notes" in request.POST:
        session_id = request.POST.get("session_id")
        s = LMSSession.raw_objects.filter(church=church, course=course, id=session_id).first()
        if s:
            new_title = request.POST.get("session_title", "").strip()
            if new_title:
                s.title = new_title
            s.notes = request.POST.get("session_notes", "").strip()
            s.save(update_fields=["title", "notes"])
            messages.success(request, "Session updated.")
        return redirect("lms:session_list", course_slug=course_slug)

    LMSSession.raw_objects.filter(
        church=church, course=course, is_active=True, event__isnull=True
    ).update(is_active=False)

    sessions = (
        LMSSession.raw_objects.filter(
            church=church, course=course, is_active=True,
            event__isnull=False, event__is_active=True,
        )
        .select_related("event")
        .prefetch_related("modules_covered")
        .order_by("event__date", "event__time")
    )

    from services.models import Event
    available_events = Event.raw_objects.filter(
        church=church, event_type="Training", is_active=True,
        lms_course=course, lms_session__isnull=True,
    ).order_by("-date", "name")

    session_enrollments = {}
    session_attendance = {}
    for session in sessions:
        if session.event_id:
            enrollments = LMSEnrollment.raw_objects.filter(
                church=church, course=course, is_active=True
            ).select_related("member__user")
            existing = AttendanceRecord.raw_objects.filter(
                church=church, event_id=session.event_id,
                status__in=("present", "late", "excused"),
            )
            m2r = {r.user_id: r for r in existing}
            session_enrollments[session.id] = enrollments
            session_attendance[session.id] = {enr.id: m2r.get(enr.member_id) for enr in enrollments}

    all_course_modules = course.modules.filter(is_active=True).order_by("order")
    return render(request, "lms/session_list.html", {
        "course": course,
        "sessions": sessions,
        "modules": all_course_modules,
        "available_events": available_events,
        "session_enrollments": session_enrollments,
        "session_attendance": session_attendance,
        "page_title": f"Sessions — {course.title}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Certificates + trainee redirect
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def my_certificates(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church:
        return HttpResponseForbidden()
    certs = LMSCertificate.raw_objects.filter(
        church=church, enrollment__member=member, is_active=True
    ).select_related("enrollment__course") if member else []
    return render(request, "lms/certificates.html", {
        "certificates": certs,
        "page_title": "My Certificates",
    })


@login_required
def trainee_redirect(request):
    return redirect("dashboard:trainee_dashboard")
