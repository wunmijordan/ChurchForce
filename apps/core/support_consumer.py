"""
core/support_consumer.py — AI Support Bot WebSocket Consumer

Handles real-time chat between a subscriber and the ChurchForce
AI support bot. Each message is sent to Claude via core.ai_skills
and streamed back to the user.

Channel group: support_{church_slug}_{user_id}
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
 
logger = logging.getLogger(__name__)
 
 
class SupportBotConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for the tenant-scoped AI support bot."""
 
    async def connect(self):
        self.user = self.scope.get("user")
 
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
 
        self.history = []
 
        # Resolve church and member once at connect time.
        # Caching on self avoids a DB round-trip on every message.
        self.church = await self._resolve_church()
        self.member = await self._resolve_member()
 
        await self.accept()
 
    async def disconnect(self, code):
        pass
 
    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except ValueError:
            return
 
        msg_type = data.get("type", "message")
 
        if msg_type == "message":
            user_text = (data.get("text") or "").strip()
            if not user_text:
                return
 
            await self.send(json.dumps({
                "type": "thinking",
                "message": "Thinking…",
            }))
 
            reply = await database_sync_to_async(self._get_reply)(user_text)
 
            # Append after generating (not before) to avoid self.history[-1] confusion
            self.history.append({"role": "user",      "content": user_text})
            self.history.append({"role": "assistant", "content": reply})
 
            # Keep last 20 turns
            if len(self.history) > 20:
                self.history = self.history[-20:]
 
            await self.send(json.dumps({"type": "reply", "text": reply}))
 
        elif msg_type == "clear":
            self.history = []
            await self.send(json.dumps({"type": "cleared"}))
 
    # ── Church resolution ─────────────────────────────────────────────────
 
    @database_sync_to_async
    def _resolve_church(self):
        """
        Resolve the Church for this connection.
 
        Priority order:
          1. ?slug= query param in the WebSocket URL (set by the bubble JS)
          2. URL route kwargs (e.g. /ws/support/<church_slug>/)
          3. Session active_church_slug fallback
        """
        try:
            from tenants.models import Church
 
            # 1. Query string — bubble JS appends ?slug=CHURCH_SLUG
            qs = self.scope.get("query_string", b"").decode("utf-8", errors="ignore")
            slug = ""
            for part in qs.split("&"):
                if part.startswith("slug="):
                    slug = part[5:]
                    break
 
            # 2. URL route kwargs
            if not slug:
                slug = (
                    self.scope
                    .get("url_route", {})
                    .get("kwargs", {})
                    .get("church_slug", "")
                )
 
            # 3. Session fallback
            if not slug:
                session = self.scope.get("session", {})
                slug = session.get("active_church_slug", "")
 
            if slug:
                return Church.raw_objects.filter(slug=slug, is_active=True).first()
 
        except Exception as exc:
            logger.warning("SupportBotConsumer._resolve_church: %s", exc)
        return None
 
    @database_sync_to_async
    def _resolve_member(self):
        """
        Resolve the ChurchMember for (user, church).
        Called once at connect so tier resolution is cheap per message.
        """
        if not self.church or not self.user or not self.user.is_authenticated:
            return None
        try:
            from accounts.models import ChurchMember
            return ChurchMember.raw_objects.filter(
                church=self.church,
                user=self.user,
                is_active=True,
            ).first()
        except Exception as exc:
            logger.warning("SupportBotConsumer._resolve_member: %s", exc)
            return None
 
    # ── Reply generation ──────────────────────────────────────────────────
 
    def _get_reply(self, user_text):
        """
        Generate a tier-aware reply via ai_skills.support_reply().
 
        self.member is already resolved at connect time.
        ai_skills._resolve_member_tier(self.member) handles the tier
        check — admins get full platform support, others get scoped help.
 
        History passed is everything EXCEPT the current exchange
        (which hasn't been appended yet when this is called).
        """
        from core.ai_skills import support_reply
 
        return support_reply(
            church=self.church,
            history=list(self.history),   # snapshot before append
            user_message=user_text,
            member=self.member,           # pre-resolved; carries tier info
        )
