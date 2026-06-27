"""
lms/context_processors.py
─────────────────────────
Injects LMS counts into every request so nav_snippet.html and the
dashboard widget always have fresh badge numbers without each view
needing to call inject_lms_context manually.

Register in settings.py → TEMPLATES[0]['OPTIONS']['context_processors']:
    "lms.context_processors.lms_nav_counts",
"""


def lms_nav_counts(request):
    """
    Returns a small dict of LMS counts for the nav bar and dashboard widget.
    Fast: uses only COUNT queries, no full querysets.
    """
    defaults = {
        "lms_pending_peer_count": 0,
        "lms_pending_reviews": 0,
    }

    church = getattr(request, "church", None)
    member = getattr(request, "member", None)

    if not church:
        return defaults

    try:
        from lms.models import LMSPeerReviewAssignment, LMSSubmission

        # Peer reviews assigned to this member that are not yet completed
        if member:
            defaults["lms_pending_peer_count"] = (
                LMSPeerReviewAssignment.raw_objects.filter(
                    church=church,
                    reviewer=member,
                    is_active=True,
                    review__isnull=True,
                ).count()
            )

        # Proctor queue depth (admin-only — safe to show to all, view is guarded)
        is_admin = getattr(request, "is_admin", False) or (
            hasattr(request, "permissions")
            and request.permissions
            and request.permissions.can("dashboard.admin")
        )
        if is_admin:
            defaults["lms_pending_reviews"] = LMSSubmission.raw_objects.filter(
                church=church,
                is_active=True,
                status__in=("submitted", "reviewing"),
            ).count()

    except Exception:
        pass  # LMS tables might not exist on first run

    return defaults
