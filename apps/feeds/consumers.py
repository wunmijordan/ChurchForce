"""
feeds/consumers.py
WebSocket consumer for live feed events.

Groups:
  feeds_church_{church_id}  — broadcasts to all members of a church
  feeds_user_{user_id}      — per-user notification pushes
"""
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async


class FeedConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.user = self.scope["user"]
        if not self.user.is_authenticated:
            await self.close()
            return

        # Resolve church from request middleware
        self.church = await self._get_church()
        if not self.church:
            await self.close()
            return

        self.church_group = f"feeds_church_{self.church.id}"
        self.user_group   = f"feeds_user_{self.user.id}"

        await self.channel_layer.group_add(self.church_group, self.channel_name)
        await self.channel_layer.group_add(self.user_group,   self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "church_group"):
            await self.channel_layer.group_discard(self.church_group, self.channel_name)
        if hasattr(self, "user_group"):
            await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def receive(self, text_data):
        # Clients are read-only via WebSocket; all mutations go through AJAX.
        # We accept a "ping" to keep the connection alive.
        try:
            data = json.loads(text_data)
            if data.get("type") == "ping":
                await self.send(text_data=json.dumps({"type": "pong"}))
        except Exception:
            pass

    # ── Group event handlers ───────────────────────────────────────────────

    async def feed_event(self, event):
        """Forward any group event to the connected client."""
        await self.send(text_data=json.dumps({
            "type": event.get("event_type"),
            "payload": event.get("payload", {}),
        }))

    # ── Helpers ───────────────────────────────────────────────────────────

    @sync_to_async
    def _get_church(self):
        try:
            from accounts.models import ChurchMember
            member = ChurchMember.raw_objects.filter(
                user=self.user, is_active=True
            ).select_related("church").first()
            return member.church if member else None
        except Exception:
            return None
