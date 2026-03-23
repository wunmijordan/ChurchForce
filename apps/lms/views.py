"""
lms/views.py — Church-embedded LMS.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from lms.models import (
    LMSCourse, LMSModule, LMSEnrollment,
    LMSSubmission, LMSReview, LMSCertificate,
)
from workforce.services.evaluator import evaluate_trainee_progress


def _is_admin(request):
    if request.user.is_superuser:
        return True
    return bool(getattr(request, "permissions", None) and request.permissions.can("dashboard.admin"))


@login_required
def course_list(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not church:
        return HttpResponseForbidden()

    if _is_admin(request):
        courses = LMSCourse.raw_objects.filter(church=church, is_active=True)
    elif member:
        ids = LMSEnrollment.raw_objects.filter(church=church, member=member).values_list("course_id", flat=True)
        courses = LMSCourse.raw_objects.filter(church=church, is_active=True, id__in=ids)
    else:
        courses = LMSCourse.raw_objects.none()

    enrollments = {e.course_id: e for e in LMSEnrollment.raw_objects.filter(church=church, member=member)} if member else {}

    return render(request, "lms/course_list.html", {
        "courses": courses, "enrollments": enrollments, "page_title": "My Courses",
    })


@login_required
def course_detail(request, course_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    course = get_object_or_404(LMSCourse.raw_objects.filter(church=church, is_active=True), id=course_id)
    enrollment = LMSEnrollment.raw_objects.filter(church=church, member=member, course=course).first() if member else None

    if not _is_admin(request) and not enrollment:
        return HttpResponseForbidden("You are not enrolled in this course.")

    modules = course.modules.filter(is_active=True).order_by("order")
    submissions = {}
    if enrollment:
        for sub in LMSSubmission.raw_objects.filter(church=church, enrollment=enrollment).order_by("-retake_count"):
            submissions.setdefault(sub.module_id, sub)

    return render(request, "lms/course_detail.html", {
        "course": course, "modules": modules, "enrollment": enrollment,
        "submissions": submissions, "is_admin": _is_admin(request), "page_title": course.title,
    })


@login_required
@require_POST
def submit_module(request, enrollment_id, module_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()

    enrollment = get_object_or_404(LMSEnrollment.raw_objects.filter(church=church, member=member), id=enrollment_id)
    module     = get_object_or_404(LMSModule.raw_objects.filter(church=church, course=enrollment.course, is_active=True), id=module_id)

    if enrollment.course.strict_sequence and enrollment.current_module:
        if module.order > enrollment.current_module.order:
            messages.error(request, "Please complete the previous module first.")
            return redirect("lms:course_detail", course_id=enrollment.course.id)

    prev = LMSSubmission.raw_objects.filter(church=church, enrollment=enrollment, module=module).order_by("-retake_count").first()
    retake_count = (prev.retake_count + 1) if prev else 0

    if enrollment.course.max_retakes and retake_count > enrollment.course.max_retakes:
        messages.error(request, "Maximum retakes reached.")
        return redirect("lms:course_detail", course_id=enrollment.course.id)

    attachment = request.FILES.get("attachment")
    if module.requires_attachment and not attachment:
        messages.error(request, "An attachment is required for this module.")
        return redirect("lms:course_detail", course_id=enrollment.course.id)

    LMSSubmission.raw_objects.create(
        church=church, enrollment=enrollment, module=module,
        status="submitted", content=request.POST.get("content", "").strip(),
        attachment=attachment, retake_count=retake_count, is_active=True,
    )
    messages.success(request, "Submission received. Awaiting review.")
    return redirect("lms:course_detail", course_id=enrollment.course.id)


@login_required
@require_POST
def review_submission(request, submission_id):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member or not _is_admin(request):
        return HttpResponseForbidden()

    submission = get_object_or_404(LMSSubmission.raw_objects.filter(church=church), id=submission_id)
    passed     = request.POST.get("passed") == "on"
    score_raw  = request.POST.get("score", "").strip()
    score_val  = int(score_raw) if score_raw.isdigit() else None

    LMSReview.raw_objects.update_or_create(
        church=church, submission=submission,
        defaults={"proctor": member, "passed": passed, "score": score_val,
                  "feedback": request.POST.get("feedback", "").strip(), "is_active": True},
    )
    submission.status = "passed" if passed else "failed"
    submission.score  = score_val
    submission.save(update_fields=["status", "score"])

    enrollment = submission.enrollment
    if passed and enrollment.course.strict_sequence:
        next_mod = LMSModule.raw_objects.filter(
            church=church, course=enrollment.course, order__gt=submission.module.order, is_active=True
        ).order_by("order").first()

        if next_mod:
            enrollment.current_module = next_mod
            enrollment.save(update_fields=["current_module"])
        else:
            scores = [s for s in LMSSubmission.raw_objects.filter(
                church=church, enrollment=enrollment
            ).values_list("score", flat=True) if s is not None]
            final = int(sum(scores) / len(scores)) if scores else 0
            passed_course = final >= enrollment.course.passing_score
            enrollment.status       = "passed" if passed_course else "failed"
            enrollment.score        = final
            enrollment.completed_at = timezone.now()
            enrollment.save(update_fields=["status", "score", "completed_at"])
            if passed_course:
                _issue_certificate(enrollment, issued_by=member)
                _try_promote_trainee(enrollment)

    for trainee in submission.enrollment.trainee_profiles.all():
        evaluate_trainee_progress(trainee)

    messages.success(request, f"Review submitted — {'Passed' if passed else 'Failed'}.")
    return redirect(request.META.get("HTTP_REFERER") or "/")


def _issue_certificate(enrollment, issued_by=None):
    template = enrollment.course.certificate_template or \
        "This certifies that {name} completed {course} on {date}."
    LMSCertificate.raw_objects.get_or_create(
        church=enrollment.church, enrollment=enrollment,
        defaults={
            "issued_by": issued_by,
            "certificate_text": template.format(
                name=str(enrollment.member), course=enrollment.course.title,
                date=timezone.now().strftime("%d %b %Y"),
            ),
            "is_active": True,
        },
    )


def _try_promote_trainee(enrollment):
    if enrollment.course.course_type != "induction":
        return
    try:
        from workforce.models import WorkforceTraineeProfile
        trainee = WorkforceTraineeProfile.raw_objects.filter(
            church=enrollment.church, member=enrollment.member,
            lms_enrollment=enrollment, is_active=True,
        ).first()
        if trainee:
            trainee.promote_to_workforce()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("promote_trainee failed: %s", exc)


@login_required
def my_certificates(request):
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)
    if not member:
        return HttpResponseForbidden()
    certs = LMSCertificate.raw_objects.filter(
        church=church, enrollment__member=member, is_active=True
    ).select_related("enrollment__course")
    return render(request, "lms/certificates.html", {"certificates": certs, "page_title": "My Certificates"})


@login_required
def proctor_queue(request):
    church = getattr(request, "church", None)
    if not church or not _is_admin(request):
        return HttpResponseForbidden()
    pending = LMSSubmission.raw_objects.filter(
        church=church, status="submitted", is_active=True
    ).select_related("enrollment__member__user", "enrollment__course", "module").order_by("created_at")
    return render(request, "lms/proctor_queue.html", {"submissions": pending, "page_title": "Proctor Queue"})
