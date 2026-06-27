"""
bible/services/badges.py

Badge awarding logic. All award functions are idempotent — calling them
multiple times for the same member/condition will not duplicate rows.

Public API:
    check_and_award(member, church, trigger, **kwargs) -> list[BibleBadge]
    get_member_badges(member, church) -> QuerySet
    get_member_stats(member, church) -> dict
"""

import logging
from django.utils import timezone

logger = logging.getLogger(__name__)


def _award(member, church, badge_slug: str) -> "MemberBadgeAward | None":
    """
    Award a badge to a member. Returns the award object (new or existing).
    Never raises — all errors are caught and logged.
    """
    try:
        from bible.models import BibleBadge, MemberBadgeAward

        badge = BibleBadge.objects.filter(slug=badge_slug).first()
        if not badge:
            BibleBadge.ensure_defaults()
            badge = BibleBadge.objects.filter(slug=badge_slug).first()
        if not badge:
            return None

        award, created = MemberBadgeAward.raw_objects.get_or_create(
            church=church,
            member=member,
            badge=badge,
        )
        if created:
            logger.info("Badge awarded: %s → %s", badge_slug, member)
        return award
    except Exception as exc:
        logger.error("_award failed for %s/%s: %s", member, badge_slug, exc)
        return None


def check_and_award_reading(member, church) -> list:
    """
    Check reading-related badge conditions and award as appropriate.
    Call after a member marks a plan entry complete.
    Returns list of newly-awarded badge slugs.
    """
    from bible.models import (
        MemberPlanProgress, MemberPlanStreak, MemberBadgeAward, BibleBadge, ReadingPlan
    )

    new_awards = []

    try:
        # ── First Reading ──────────────────────────────────────────────────
        total_completions = MemberPlanProgress.raw_objects.filter(
            church=church, member=member
        ).count()
        if total_completions == 1:
            award = _award(member, church, "first_reading")
            if award:
                new_awards.append("first_reading")

        # ── Streak badges ──────────────────────────────────────────────────
        streaks = MemberPlanStreak.raw_objects.filter(
            church=church, member=member
        )
        for streak_obj in streaks:
            cs = streak_obj.current_streak

            if cs >= 7:
                existing = MemberBadgeAward.raw_objects.filter(
                    church=church, member=member, badge__slug="streak_7"
                ).exists()
                if not existing:
                    award = _award(member, church, "streak_7")
                    if award:
                        new_awards.append("streak_7")

            if cs >= 30:
                existing = MemberBadgeAward.raw_objects.filter(
                    church=church, member=member, badge__slug="streak_30"
                ).exists()
                if not existing:
                    award = _award(member, church, "streak_30")
                    if award:
                        new_awards.append("streak_30")

            if cs >= 100:
                existing = MemberBadgeAward.raw_objects.filter(
                    church=church, member=member, badge__slug="streak_100"
                ).exists()
                if not existing:
                    award = _award(member, church, "streak_100")
                    if award:
                        new_awards.append("streak_100")

        # ── Plan Finisher ──────────────────────────────────────────────────
        # Check if member has completed ALL active entries in any plan
        for plan in ReadingPlan.raw_objects.filter(church=church, is_published=True, is_active=True):
            total_entries = plan.entries.filter(is_active=True).count()
            if total_entries == 0:
                continue
            completed = MemberPlanProgress.raw_objects.filter(
                church=church, member=member, plan=plan
            ).count()
            if completed >= total_entries:
                existing = MemberBadgeAward.raw_objects.filter(
                    church=church, member=member, badge__slug="plan_finisher"
                ).exists()
                if not existing:
                    award = _award(member, church, "plan_finisher")
                    if award:
                        new_awards.append("plan_finisher")

    except Exception as exc:
        logger.error("check_and_award_reading failed: %s", exc)

    return new_awards


def check_and_award_memory_verse(member, church) -> list:
    """Award memory_master badge after completing a memory verse."""
    new_awards = []
    try:
        from bible.models import MemberBadgeAward
        existing = MemberBadgeAward.raw_objects.filter(
            church=church, member=member, badge__slug="memory_master"
        ).exists()
        if not existing:
            award = _award(member, church, "memory_master")
            if award:
                new_awards.append("memory_master")
    except Exception as exc:
        logger.error("check_and_award_memory_verse failed: %s", exc)
    return new_awards


def check_and_award_study(member, church) -> list:
    """Award study_complete badge after completing a study reference."""
    new_awards = []
    try:
        from bible.models import MemberBadgeAward
        existing = MemberBadgeAward.raw_objects.filter(
            church=church, member=member, badge__slug="study_complete"
        ).exists()
        if not existing:
            award = _award(member, church, "study_complete")
            if award:
                new_awards.append("study_complete")
    except Exception as exc:
        logger.error("check_and_award_study failed: %s", exc)
    return new_awards


def check_and_award_share(member, church) -> list:
    """Award verse_sharer badge on first share."""
    new_awards = []
    try:
        from bible.models import MemberBadgeAward
        existing = MemberBadgeAward.raw_objects.filter(
            church=church, member=member, badge__slug="verse_sharer"
        ).exists()
        if not existing:
            award = _award(member, church, "verse_sharer")
            if award:
                new_awards.append("verse_sharer")
    except Exception as exc:
        logger.error("check_and_award_share failed: %s", exc)
    return new_awards


def update_streak(member, church, plan) -> "MemberPlanStreak":
    """
    Update the streak for a member on a given plan.
    Call immediately after recording a completion.
    Returns the updated streak object.
    """
    from bible.models import MemberPlanStreak
    from django.utils import timezone

    today = timezone.localdate()

    streak_obj, _ = MemberPlanStreak.raw_objects.get_or_create(
        church=church,
        member=member,
        plan=plan,
        defaults={"current_streak": 0, "longest_streak": 0},
    )

    last = streak_obj.last_completed_date

    if last is None:
        streak_obj.current_streak = 1
    elif last == today:
        # Already recorded today — no change
        pass
    elif (today - last).days == 1:
        # Consecutive day
        streak_obj.current_streak += 1
    else:
        # Streak broken
        streak_obj.current_streak = 1

    if streak_obj.current_streak > streak_obj.longest_streak:
        streak_obj.longest_streak = streak_obj.current_streak

    streak_obj.last_completed_date = today
    streak_obj.save(update_fields=["current_streak", "longest_streak", "last_completed_date", "updated_at"])
    return streak_obj


def get_member_stats(member, church) -> dict:
    """
    Return a summary dict of a member's Bible stats.
    Safe — returns zeros on any failure.
    """
    try:
        from bible.models import (
            MemberPlanProgress, MemberBadgeAward, MemberPlanStreak,
            MemberMemoryVerseProgress, MemberStudyProgress
        )

        total_readings = MemberPlanProgress.raw_objects.filter(
            church=church, member=member
        ).count()

        badges = list(
            MemberBadgeAward.raw_objects.filter(
                church=church, member=member
            ).select_related("badge").order_by("-awarded_at")
        )

        best_streak = 0
        streaks = MemberPlanStreak.raw_objects.filter(church=church, member=member)
        for s in streaks:
            if s.current_streak > best_streak:
                best_streak = s.current_streak

        memory_count = MemberMemoryVerseProgress.raw_objects.filter(
            church=church, member=member
        ).count()

        study_count = MemberStudyProgress.raw_objects.filter(
            church=church, member=member
        ).count()

        return {
            "total_readings": total_readings,
            "current_streak": best_streak,
            "badge_count": len(badges),
            "badges": [
                {
                    "slug": a.badge.slug,
                    "name": a.badge.name,
                    "icon": a.badge.icon_emoji,
                    "color": a.badge.color_hex,
                    "awarded_at": a.awarded_at.isoformat(),
                }
                for a in badges[:8]
            ],
            "memory_verses_completed": memory_count,
            "studies_completed": study_count,
        }
    except Exception as exc:
        logger.error("get_member_stats failed: %s", exc)
        return {
            "total_readings": 0,
            "current_streak": 0,
            "badge_count": 0,
            "badges": [],
            "memory_verses_completed": 0,
            "studies_completed": 0,
        }
