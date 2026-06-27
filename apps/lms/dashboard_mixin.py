"""
lms/dashboard_mixin.py
──────────────────────
Call `inject_lms_context(context, request)` inside any dashboard view
that renders a template using the trainee_dashboard_widget.html snippet.

Example (in dashboard/views.py):
    from lms.dashboard_mixin import inject_lms_context
    ...
    context = { ... existing dashboard context ... }
    inject_lms_context(context, request)
    return render(request, "dashboard/dashboard.html", context)
"""


def inject_lms_context(context: dict, request) -> None:
    """
    Mutates `context` in-place, adding all keys needed by
    lms/trainee_dashboard_widget.html.
    Safe to call even if the lms app isn't fully set up yet.
    """
    church = getattr(request, "church", None)
    member = getattr(request, "member", None)

    try:
        from lms.models import (
            LMSCertificate,
            LMSEnrollment,
            LMSPeerReviewAssignment,
            LMSSubmission,
        )

        # ── Enrollments for this member ──────────────────────────────
        enrollments = (
            LMSEnrollment.raw_objects.filter(
                church=church,
                member=member,
                is_active=True,
            )
            .select_related("course")
            .order_by("status", "course__title")
            if (church and member)
            else LMSEnrollment.raw_objects.none()
        )

        # ── Pending peer-review assignments ───────────────────────────
        pending_peer_count = 0
        if church and member:
            pending_peer_count = (
                LMSPeerReviewAssignment.raw_objects.filter(
                    church=church,
                    reviewer=member,
                    is_active=True,
                    review__isnull=True,
                ).count()
            )

        # ── Certificates earned ───────────────────────────────────────
        certificates = (
            LMSCertificate.raw_objects.filter(
                church=church,
                enrollment__member=member,
                is_active=True,
            ).select_related("enrollment__course")
            if (church and member)
            else LMSCertificate.raw_objects.none()
        )

        # ── Pending proctor reviews (admin only) ─────────────────────
        pending_reviews_count = 0
        is_admin = getattr(request, "is_admin", False) or (
            hasattr(request, "permissions")
            and request.permissions
            and request.permissions.can("dashboard.admin")
        )
        if church and is_admin:
            pending_reviews_count = LMSSubmission.raw_objects.filter(
                church=church,
                is_active=True,
                status__in=("submitted", "reviewing"),
            ).count()

        context.update(
            {
                "lms_enrollments": enrollments,
                "lms_pending_peer_count": pending_peer_count,
                "lms_certificates": certificates,
                "lms_pending_reviews": pending_reviews_count,
            }
        )

    except Exception:
        # Graceful degradation — widget will just show nothing
        context.update(
            {
                "lms_enrollments": [],
                "lms_pending_peer_count": 0,
                "lms_certificates": [],
                "lms_pending_reviews": 0,
            }
        )
