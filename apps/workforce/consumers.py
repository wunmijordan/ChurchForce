import json, re, urllib.parse, logging, hashlib
from core.utils.colors import resolve_color

def get_user_color(user_id):
    """Stable deterministic colour for a user — delegates to core.utils.colors."""
    return resolve_color(f"user:{user_id}", variant="hex")
from datetime import timedelta
from django.utils.timezone import now
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone


logger = logging.getLogger(__name__)


# ---------- Helper ----------

def handle_file_upload(file_url):
    """
    Normalize file URLs for chat messages across dev/prod.
    Ensures no duplicate /media/ prefix and preserves Cloudinary URLs.
    """
    if not file_url:
        return None

    if file_url.startswith("http"):
        return file_url

    cleaned = file_url.lstrip("/")
    if cleaned.startswith("media/"):
        cleaned = cleaned[len("media/"):]

    if settings.DEBUG:
        return f"/media/{cleaned}"

    return cleaned


# =================== Chat Consumer ===================
class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        from units.models import ChurchUnit
        from accounts.models import ChurchMember
        import urllib.parse

        self.user = self.scope["user"]

        # Resolve church for tenant scoping.
        self.church = None
        if self.user.is_authenticated:
            member = await sync_to_async(ChurchMember.raw_objects.select_related("church").filter(user=self.user, is_active=True).first)()
            self.church = member.church if member else None

        qs = self.scope.get("query_string", b"").decode()
        params = urllib.parse.parse_qs(qs)
        unit_slug = params.get("team", [None])[0] or params.get("team_id", [None])[0]

        self.unit = None
        self.room = None
        self.room_group_name = None

        await self.channel_layer.group_add("chat_central", self.channel_name)

        # Support ?room_id=<id> (preferred) or legacy ?team=<slug/id>
        room_id = params.get("room_id", [None])[0]

        if room_id and room_id.isdigit() and self.church:
            from units.models import ChatRoom
            room = await sync_to_async(
                ChatRoom.raw_objects.select_related("unit").filter(
                    church=self.church, id=int(room_id), is_active=True
                ).first
            )()
            if room:
                self.room = room
                self.unit = room.unit
                self.room_group_name = f"chat_room_{room.id}"
                await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        # Legacy fallback: resolve by unit slug/id
        if not self.room and unit_slug and self.church:
            unit = None
            if unit_slug.isdigit():
                unit = await sync_to_async(ChurchUnit.raw_objects.filter(church=self.church, id=int(unit_slug)).first)()
            if not unit:
                unit = await sync_to_async(ChurchUnit.raw_objects.filter(church=self.church, name__iexact=unit_slug).first)()
            if unit:
                self.unit = unit
                # Find or create default room for this unit
                from units.models import ChatRoom
                room = await sync_to_async(
                    ChatRoom.raw_objects.filter(
                        church=self.church, unit=unit, is_default=True, is_active=True
                    ).first
                )()
                if room:
                    self.room = room
                    self.room_group_name = f"chat_room_{room.id}"
                    await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        if not self.room_group_name:
            self.room_group_name = "chat_central"

        await self.accept()

        if self.user.is_authenticated:
            await self.set_user_online(True)
            await self.broadcast_online_status(True)

        recent = await self.get_recent_pinned(self.unit)
        await self.send(text_data=json.dumps({
            "type": "pinned_preview",
            "messages": recent
        }))

    async def disconnect(self, close_code):
        if self.user.is_authenticated:
            await self.set_user_online(False)
            await self.broadcast_online_status(False)
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    @database_sync_to_async
    def set_user_online(self, online: bool):
        from accounts.models import CustomUser
        CustomUser.objects.filter(pk=self.user.pk).update(
            is_online=online,
            last_active=timezone.now(),
        )

    async def broadcast_online_status(self, online: bool):
        message = {
            "type": "user_online_status",
            "user_id": self.user.id,
            "is_online": online,
        }

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "broadcast", "message": message}
        )

    async def broadcast(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get("type") == "mark_read":
                unit_id = data.get("team_id")
                if unit_id:
                    await self.mark_unit_read(unit_id)
                    await self.send_unread_counts()
                return
            if data.get("type") == "get_unread_counts":
                await self.send_unread_counts()
            elif data.get("action"):
                await self.handle_action(data)
            else:
                unit_id = self.unit.id if self.unit else None
                await self.handle_new_message({**data, "team_id": unit_id})
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")

    async def send_unread_counts(self):
        from .models import ChatMessage
        from units.models import UnitMembership

        user = self.scope["user"]
        if not user.is_authenticated or not self.church:
            return

        counts = {}

        memberships = await sync_to_async(list)(
            UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member__member__user=user
            ).select_related('unit')
        )
        for membership in memberships:
            unit = membership.unit
            unread = await sync_to_async(ChatMessage.raw_objects.filter(
                church=self.church,
                unit=unit
            ).exclude(sender=user).exclude(read_by=user).count)()
            counts[str(unit.id)] = unread

        await self.send(text_data=json.dumps({
            "type": "unread_counts",
            "counts": counts
        }))

    @database_sync_to_async
    def mark_unit_read(self, unit_id):
        from .models import ChatMessage
        user = self.scope["user"]

        qs = ChatMessage.raw_objects.filter(
            church=self.church,
            unit_id=unit_id
        ).exclude(
            sender=user
        ).exclude(
            read_by=user
        )

        for msg in qs:
            msg.read_by.add(user)

    # ---------- Action Handler ----------
    async def handle_action(self, data):
        action = data.get("action")
        sender_id = data.get("sender_id")

        if action == "pin":
            message_ids = data.get("message_ids", [])
            pinned_map = await self.handle_pin(message_ids, sender_id)

            from accounts.models import CustomUser
            try:
                pinner = await sync_to_async(CustomUser.objects.get)(id=sender_id)
                pinned_by_payload = {
                    "id": pinner.id,
                    "name": pinner.full_name or pinner.username,
                    "title": getattr(pinner, "title", "")
                }
            except Exception:
                pinned_by_payload = None

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "message_pinned",
                    "message_ids": message_ids,
                    "pinned": pinned_map,
                    "pinned_by": pinned_by_payload,
                }
            )

            recent = await self.get_recent_pinned(self.unit)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "pinned_preview",
                    "messages": recent
                }
            )
            return

        elif action == "edit":
            message_id = data.get("message_id")
            new_text   = data.get("message", "").strip()
            if message_id and new_text:
                result = await self.handle_edit(message_id, sender_id, new_text)
                if result:
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "message_edited", "message_id": message_id,
                         "message": new_text, "edited_at": result}
                    )
            return

        elif action == "delete":
            message_id = data.get("message_id")
            if message_id:
                ok = await self.handle_delete(message_id, sender_id)
                if ok:
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "message_deleted", "message_id": message_id}
                    )
            return

        elif action == "forward":
            message_id    = data.get("message_id")
            target_room_id = data.get("target_room_id")
            if message_id and target_room_id:
                await self.handle_forward(message_id, sender_id, target_room_id)
            return

        elif action == "reply":
            return

        elif action == "private_request":
            recipient_id = data.get("recipient_member_id")
            if recipient_id:
                await self.handle_private_request(sender_id, recipient_id)
            return

        else:
            logger.debug("Unknown action received: %s", action)

    # ---------- New Message Handler ----------
    async def handle_new_message(self, data):
        sender_id = data.get("sender_id")
        message = data.get("message", "").rstrip()
        guest_id = data.get("guest_id")
        parent_id = data.get("reply_to_id")
        mentions_ids = data.get("mentions", [])
        file_data = data.get("file") or {}
        file_url = file_data.get("url") if file_data else None
        link_preview = data.get("link_preview")
        unit_id = data.get("team_id")

        if not message.strip() and not guest_id and not file_url and not link_preview:
            return

        saved_message = await self.create_message(sender_id, message, guest_id, parent_id, mentions_ids, file_url, link_preview, unit_id, file_data)
        payload = {
            **saved_message,
            "type": "chat_message",
            "color": get_user_color(sender_id),
            "team_id": unit_id
        }

        if unit_id:
            await self.channel_layer.group_send(f"chat_unit_{unit_id}", payload)

        await self.channel_layer.group_send("chat_central", payload)

    # ---------- WebSocket Group Events ----------
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def message_pinned(self, event):
        await self.send(text_data=json.dumps(event))

    async def pinned_preview(self, event):
        await self.send(text_data=json.dumps({
            "type": "pinned_preview",
            "messages": event["messages"]
        }))

    # =================== Database / Sync Handlers ===================
    @sync_to_async(thread_sensitive=False)
    def get_sender_name(self, sender_id):
        from accounts.models import CustomUser
        user = CustomUser.objects.only('full_name', 'username').get(id=sender_id)
        return user.full_name or user.username

    @sync_to_async(thread_sensitive=False)
    def get_sender_image(self, sender_id):
        from accounts.models import CustomUser
        user = CustomUser.objects.only('image').get(id=sender_id)
        return user.image.url if user.image else None

    @staticmethod
    def now_iso():
        return now().isoformat()

    @sync_to_async
    def get_guest_info(self, guest_id):
        from guests.models import GuestEntry
        g = GuestEntry.raw_objects.filter(church=self.church).get(id=guest_id)
        return {
            "id": g.id,
            "name": g.full_name,
            "custom_id": g.custom_id,
            "image": g.picture.url if g.picture else None,
            "title": g.title,
            "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
        }

    @sync_to_async
    def get_parent_info(self, parent_id):
        from .models import ChatMessage
        p = ChatMessage.raw_objects.filter(church=self.church).select_related("sender", "guest_card").get(id=parent_id)
        parent_data = {
            "id": p.id,
            "sender_name": p.sender.full_name or p.sender.username,
            "sender_title": p.sender.title,
            "message": p.message[:50],
        }
        if p.guest_card:
            g = p.guest_card
            parent_data["guest"] = {
                "id": g.id,
                "name": g.full_name,
                "title": g.title,
                "image": g.picture.url if g.picture else None,
                "date_of_visit": g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else "",
            }
        return parent_data

    # ---------- Action Handlers ----------
    @sync_to_async
    def handle_pin(self, message_ids, sender_id):
        from .models import ChatMessage
        res = {}
        for mid in message_ids:
            try:
                m = ChatMessage.raw_objects.filter(id=mid, church=self.church).first()
                if not m:
                    continue

                if m.pinned:
                    m.pinned = False
                    m.pinned_at = None
                    m.pinned_by = None
                else:
                    m.pinned = True
                    m.pinned_at = now()
                    m.pinned_by_id = sender_id
                m.save(update_fields=["pinned", "pinned_at", "pinned_by_id"])
                res[str(mid)] = m.pinned
            except Exception:
                logger.exception("Failed to toggle pin %s", mid)
        return res

    # ---------- Helpers ----------
    @sync_to_async
    def get_recent_pinned(self, unit=None):
        from .models import ChatMessage
        from .utils import serialize_message, build_mention_helpers
        cutoff = now() - timedelta(days=14)

        ChatMessage.raw_objects.filter(church=self.church, pinned=True, pinned_at__lt=cutoff).update(
            pinned=False, pinned_at=None, pinned_by=None
        )

        qs = ChatMessage.raw_objects.filter(church=self.church, pinned=True, pinned_at__gte=cutoff)
        if unit:
            qs = qs.filter(unit=unit)
        else:
            qs = qs.filter(unit__isnull=True)

        pinned = qs.select_related("pinned_by", "sender", "guest_card").order_by("-pinned_at")[:3]
        mention_map, mention_regex = build_mention_helpers(self.church)
        return [serialize_message(m, mention_map, mention_regex) for m in pinned]

    # ---------- Build Broadcast Payload ----------
    @sync_to_async
    def get_message_payload(self, message_id):
        from .models import ChatMessage
        from .utils import serialize_message, build_mention_helpers

        try:
            msg = ChatMessage.raw_objects.select_related("sender", "guest_card", "parent__sender").get(id=message_id, church=self.church)
        except ChatMessage.DoesNotExist:
            return {}

        mention_map, mention_regex = build_mention_helpers(self.church)
        return serialize_message(msg, mention_map, mention_regex)

    # ---------- Create Message (Async DB) ----------
    @sync_to_async
    def create_message(
        self, sender_id, message,
        guest_id=None, parent_id=None, mentions_ids=None,
        file_url=None, link_preview=None, unit_id=None, file_data=None
    ):
        from .models import ChatMessage
        from accounts.models import CustomUser, ChurchMember
        from guests.models import GuestEntry
        from units.models import ChurchUnit
        from .utils import serialize_message, build_mention_helpers, get_link_preview
        import os

        url_pattern = re.compile(r'(https?://[^\s]+)')
        mentions_ids = mentions_ids or []

        try:
            sender = CustomUser.objects.get(id=sender_id)
            unit = None
            if unit_id and str(unit_id).isdigit():
                unit = ChurchUnit.raw_objects.filter(id=int(unit_id), church=self.church).first()

            guest_card = None
            if guest_id:
                candidate = GuestEntry.raw_objects.filter(id=guest_id, church=self.church).first()
                if candidate:
                    if unit and unit.name.lower() == "magnet":
                        guest_card = candidate

            parent = ChatMessage.raw_objects.filter(id=parent_id, church=self.church).first() if parent_id else None

            if link_preview:
                link_meta = link_preview
            else:
                link_meta = {}
                match = url_pattern.search(message or "")
                if match:
                    link_meta = get_link_preview(match.group(0))

            file_field = None
            file_type = None
            file_name = None
            if file_data:
                file_type = file_data.get("type")
                if not settings.DEBUG:
                    file_field = file_data.get("public_id")
                    file_name = file_data.get("name")
                else:
                    file_field = file_data.get("path")

            saved = ChatMessage.raw_objects.create(
                church=self.church,
                sender=sender,
                unit=unit,
                message=message or "",
                guest_card=guest_card,
                parent=parent,
                file=file_field,
                file_type=file_type,
                file_name=file_name,
                link_url=link_meta.get("url"),
                link_title=link_meta.get("title"),
                link_description=link_meta.get("description"),
                link_image=link_meta.get("image"),
            )

            mention_map, mention_regex = build_mention_helpers(self.church)
            return serialize_message(saved, mention_map, mention_regex)

        except Exception as e:
            import logging
            logging.exception("create_message failed: %s", e)
            return {}



    @database_sync_to_async
    def handle_edit(self, message_id, sender_id, new_text):
        """Edit a message — only the original sender can edit."""
        from .models import ChatMessage
        from django.utils import timezone
        try:
            msg = ChatMessage.raw_objects.filter(
                church=self.church, id=message_id
            ).select_related("sender__workforce_member__member__user").first()
            if not msg or msg.is_deleted:
                return None
            # Only the sender can edit
            try:
                sender_user = msg.sender.workforce_member.member.user
                if sender_user.id != sender_id:
                    return None
            except AttributeError:
                return None
            # Preserve original before overwriting
            if not msg.original_message:
                msg.original_message = msg.message or ""
            msg.message   = new_text
            msg.edited_at = timezone.now()
            msg.save(update_fields=["message", "original_message", "edited_at"])
            return msg.edited_at.isoformat()
        except Exception as exc:
            logger.error("handle_edit failed: %s", exc)
            return None

    @database_sync_to_async
    def handle_delete(self, message_id, sender_id):
        """Soft-delete a message. Sender or unit head can delete."""
        from .models import ChatMessage
        from units.models import UnitMembership
        from django.utils import timezone
        try:
            msg = ChatMessage.raw_objects.filter(
                church=self.church, id=message_id
            ).first()
            if not msg or msg.is_deleted:
                return False
            # Allow sender or any unit head of this room's unit
            try:
                sender_user = msg.sender.workforce_member.member.user
                is_sender = (sender_user.id == sender_id)
            except AttributeError:
                is_sender = False
            if not is_sender:
                return False
            deleter = UnitMembership.raw_objects.filter(
                church=self.church, workforce_member__member__user__id=sender_id
            ).first()
            msg.is_deleted = True
            msg.deleted_at = timezone.now()
            msg.deleted_by = deleter
            msg.message    = ""  # clear content
            msg.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "message"])
            return True
        except Exception as exc:
            logger.error("handle_delete failed: %s", exc)
            return False

    @database_sync_to_async
    def handle_forward(self, message_id, sender_id, target_room_id):
        """Forward a message to another room the sender is a member of."""
        from .models import ChatMessage
        from units.models import UnitMembership, ChatRoom
        try:
            original = ChatMessage.raw_objects.filter(
                church=self.church, id=message_id
            ).first()
            if not original or original.is_deleted:
                return
            target_room = ChatRoom.raw_objects.filter(
                church=self.church, id=target_room_id, is_active=True
            ).first()
            if not target_room:
                return
            sender_membership = UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member__member__user__id=sender_id,
                unit=target_room.unit,
                is_active=True,
            ).first()
            if not sender_membership:
                return  # sender must be in target room's unit
            ChatMessage.raw_objects.create(
                church=self.church,
                sender=sender_membership,
                room=target_room,
                message=original.message or "",
                forwarded_from=original,
                file=original.file,
                file_type=original.file_type,
                file_name=original.file_name,
            )
        except Exception as exc:
            logger.error("handle_forward failed: %s", exc)

    @database_sync_to_async
    def handle_private_request(self, requester_sender_id, recipient_member_id):
        """Create a PrivateChatRequest from requester to recipient in this room."""
        from units.models import PrivateChatRequest, ChatRoom
        from accounts.models import ChurchMember
        try:
            room = ChatRoom.raw_objects.filter(
                church=self.church, unit=self.unit, is_default=True
            ).first()
            if not room:
                return
            requester = ChurchMember.raw_objects.filter(
                church=self.church, user__id=requester_sender_id
            ).first()
            recipient = ChurchMember.raw_objects.filter(
                church=self.church, id=recipient_member_id
            ).first()
            if not requester or not recipient:
                return
            PrivateChatRequest.raw_objects.get_or_create(
                church=self.church,
                room=room,
                requester=requester,
                recipient=recipient,
                defaults={"status": "pending"},
            )
        except Exception as exc:
            logger.error("handle_private_request failed: %s", exc)

    # ---------- WebSocket event forwarders ----------
    async def message_edited(self, event):
        await self.send(text_data=json.dumps(event))

    async def message_deleted(self, event):
        await self.send(text_data=json.dumps(event))


# =================== Attendance Consumer ===================
class AttendanceConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        from accounts.models import ChurchMember

        self.user = self.scope["user"]

        if not self.user.is_authenticated:
            await self.close()
            return

        self.church = None
        member = await sync_to_async(ChurchMember.raw_objects.select_related("church").filter(user=self.user, is_active=True).first)()
        self.church = member.church if member else None

        if not self.user.is_superuser:
            self.user_unit_ids = await self.get_user_unit_ids()
        else:
            self.user_unit_ids = []

        await self.channel_layer.group_add("attendance", self.channel_name)
        await self.channel_layer.group_add(f"attendance_user_{self.user.id}", self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("attendance", self.channel_name)
        await self.channel_layer.group_discard(f"attendance_user_{self.user.id}", self.channel_name)

    @sync_to_async
    def get_user_unit_ids(self):
        from units.models import UnitMembership
        if not self.church:
            return []
        return list(
            UnitMembership.raw_objects.filter(
                church=self.church,
                workforce_member__member__user=self.user
            ).values_list("unit_id", flat=True)
        )

    async def send_event(self, event):
        data = event.get("data", {})
        unit_id = data.get("team_id")

        if (
            self.user.is_superuser
            or unit_id is None
            or unit_id in self.user_unit_ids
        ):
            await self.send(text_data=json.dumps(data))

    async def send_summary(self, event):
        records = event.get("data", {}).get("records", [])

        if not self.user.is_superuser:
            records = [
                r for r in records
                if (
                    (r["event"]["team"]["id"] in self.user_unit_ids or r["event"]["team"]["id"] is None)
                    and not str(r.get("user", {}).get("full_name", "")).lower().startswith("superuser")
                )
            ]

        await self.send(text_data=json.dumps({
            "type": "send_summary",
            "records": records
        }))

    async def dashboard_summary(self, event):
        data = event.get("data", {})
        user_id = data.get("user_id")

        if user_id == self.user.id:
            await self.send(text_data=json.dumps({
                "type": "dashboard_summary",
                "summary": data.get("summary"),
                "today": data.get("today"),
                "totals": data.get("totals"),
            }))
