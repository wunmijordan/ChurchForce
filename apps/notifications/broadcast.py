"""
notifications/broadcast.py

Broadcast a deferred notification via WebSocket.
Called by the scheduler for notifications with a future send_at.
Immediate notifications are pushed directly by notify_members() in utils.py.
"""

from notifications.utils import push_websocket_notification, push_webpush_notification


def broadcast_notification(notification):
    """
    Push a notification that was deferred (has send_at set).
    Called by the scheduler once send_at is reached.
    """
    push_websocket_notification(notification)
    push_webpush_notification(notification)