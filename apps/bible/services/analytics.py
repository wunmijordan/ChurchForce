import logging

from django.utils import timezone

logger = logging.getLogger(__name__)


def log_provider_request(
    provider: str,
    operation: str,
    *,
    translation: str = "",
    reference: str = "",
    status_code: int | None = None,
    ok: bool = False,
    fallback_used: bool = False,
    latency_ms: int = 0,
    rate_limit: dict | None = None,
    error: str = "",
) -> None:
    """Best-effort provider telemetry for the admin dashboard."""
    try:
        from core.request_context import get_current_church
        from bible.models import BibleProviderRequestLog

        church = get_current_church()
        if not church:
            return
        rate_limit = rate_limit or {}
        BibleProviderRequestLog.raw_objects.create(
            church=church,
            provider=provider,
            operation=operation,
            translation=(translation or "").upper()[:20],
            reference=(reference or "")[:120],
            status_code=status_code,
            ok=ok,
            fallback_used=fallback_used,
            latency_ms=max(0, int(latency_ms or 0)),
            rate_limit_limit=str(rate_limit.get("limit") or "")[:40],
            rate_limit_remaining=str(rate_limit.get("remaining") or "")[:40],
            rate_limit_reset=str(rate_limit.get("reset") or "")[:80],
            error=(error or "")[:240],
        )
    except Exception as exc:
        logger.debug("bible analytics log skipped: %s", exc)


def provider_analytics(church, *, days: int = 7) -> dict:
    """Return compact provider analytics for admin dashboard widgets."""
    from django.db.models import Avg, Count, Max, Q
    from datetime import timedelta
    from bible.models import BibleProviderRequestLog

    since = timezone.now() - timedelta(days=days)
    qs = BibleProviderRequestLog.raw_objects.filter(church=church, created_at__gte=since)
    totals = qs.aggregate(
        total=Count("id"),
        successful=Count("id", filter=Q(ok=True)),
        failed=Count("id", filter=Q(ok=False)),
        fallbacks=Count("id", filter=Q(fallback_used=True)),
        avg_latency=Avg("latency_ms"),
    )
    by_provider = list(
        qs.values("provider")
        .annotate(
            total=Count("id"),
            successful=Count("id", filter=Q(ok=True)),
            failed=Count("id", filter=Q(ok=False)),
            avg_latency=Avg("latency_ms"),
            last_seen=Max("created_at"),
        )
        .order_by("provider")
    )
    latest_limits = {}
    for provider in ("youversion", "helloao", "apibible"):
        row = (
            qs.filter(provider=provider)
            .exclude(rate_limit_remaining="")
            .order_by("-created_at")
            .first()
        )
        if row:
            latest_limits[provider] = {
                "limit": row.rate_limit_limit,
                "remaining": row.rate_limit_remaining,
                "reset": row.rate_limit_reset,
            }
    recent_errors = list(
        qs.filter(ok=False)
        .exclude(error="")
        .values("provider", "operation", "translation", "reference", "error", "created_at")[:5]
    )
    return {
        "days": days,
        "totals": totals,
        "by_provider": by_provider,
        "latest_limits": latest_limits,
        "recent_errors": recent_errors,
    }
