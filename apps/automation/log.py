"""
automation/log.py

Single helper function for writing AutomationLog records.

Usage (from scheduler jobs, signals, webhooks, views):

    from automation.log import log_event

    log_event(
        church=church,
        category="scheduler",
        event_type="birthday_sms_sent",
        status="success",
        summary=f"Sent {count} birthday SMS messages.",
        payload={"count": count, "church_id": church.id},
    )

Silently catches all exceptions — logging should never crash the
operation that triggered it.
"""

import logging

logger = logging.getLogger(__name__)


def log_event(church, event_type, category="system", status="info",
              summary="", payload=None):
    """
    Write an AutomationLog record for a church.

    Args:
        church:     Church instance.
        event_type: Short string identifying the event, e.g. "birthday_sms_sent".
        category:   One of: scheduler, webhook, signal, billing, messaging,
                    pipeline, system.
        status:     One of: success, partial, failed, info.
        summary:    Human-readable one-line description for admin list view.
        payload:    Optional dict of structured debugging data.

    Returns:
        AutomationLog instance, or None if creation failed.
    """
    try:
        from automation.models import AutomationLog
        return AutomationLog.objects.create(
            church=church,
            category=category,
            event_type=event_type,
            status=status,
            summary=summary[:500],
            payload=payload or {},
        )
    except Exception as exc:
        logger.error("automation.log_event failed: %s", exc)
        return None