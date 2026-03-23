import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class UnitConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time unit updates.

    Connects to a per-unit channel group (unit_{unit_id}).
    Verifies on connect that the requested unit belongs to the
    user's church — prevents cross-tenant channel access.
    """

    async def connect(self):
        self.user    = self.scope["user"]
        self.unit_id = self.scope["url_route"]["kwargs"]["unit_id"]
        self.group_name = f"unit_{self.unit_id}"

        # ── Church scoping guard ──────────────────────────────────────
        # Verify this unit belongs to the user's church before joining.
        # Prevents a user from subscribing to another tenant's unit channel.
        if not self.user.is_authenticated:
            await self.close(code=4001)
            return

        unit = await self._get_unit_for_user()
        if not unit:
            logger.warning(
                "UnitConsumer: user %s tried to connect to unit %s "
                "but it does not belong to their church or they are not a member.",
                self.user.id, self.unit_id,
            )
            await self.close(code=4003)
            return

        self.unit = unit

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Read-only consumer — clients receive updates, they don't send
        pass

    async def send_unit_update(self, event):
        """Receive a group message and forward it to the WebSocket client."""
        await self.send(text_data=json.dumps(event["data"]))

    # ── Helpers ───────────────────────────────────────────────────────

    @sync_to_async
    def _get_unit_for_user(self):
        """
        Return the ChurchUnit if it belongs to the user's church AND
        the user is an active member of that unit. Return None otherwise.
        """
        from units.models import ChurchUnit, UnitMembership
        from accounts.models import ChurchMember

        # Resolve user's church
        member = (
            ChurchMember.raw_objects
            .filter(user=self.user, is_active=True)
            .select_related("church")
            .first()
        )
        if not member:
            return None

        church = member.church

        # Verify the unit belongs to this church
        unit = ChurchUnit.raw_objects.filter(
            id=self.unit_id,
            church=church,
            is_active=True,
        ).first()

        if not unit:
            return None

        # Verify the user is a member of this unit
        is_member = UnitMembership.raw_objects.filter(
            church=church,
            unit=unit,
            workforce_member__member=member,
            is_active=True,
        ).exists()

        return unit if is_member else None
