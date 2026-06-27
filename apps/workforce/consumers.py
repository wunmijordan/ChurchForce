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
        cleaned = cleaned[len("media/") :]

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
            member = await sync_to_async(
                ChurchMember.raw_objects.select_related("church")
                .filter(user=self.user, is_active=True)
                .first
            )()
            self.church = member.church if member else None

        qs = self.scope.get("query_string", b"").decode()
        params = urllib.parse.parse_qs(qs)
        unit_slug = params.get("unit", [None])[0] or params.get("unit_id", [None])[0]

        self.unit = None
        self.room = None
        self.room_group_name = None

        # Scoped central group — prevents cross-tenant message bleeding
        self.central_group = (
            f"chat_church_{self.church.id}" if self.church else "chat_orphan"
        )
        await self.channel_layer.group_add(self.central_group, self.channel_name)

        # Per-user group for private message delivery (cross-room, persistent)
        self.user_private_group = (
            f"chat_church_{self.church.id}_user_{self.user.id}"
            if self.church and self.user.is_authenticated
            else None
        )
        if self.user_private_group:
            await self.channel_layer.group_add(
                self.user_private_group, self.channel_name
            )

        # Support ?room_id=<id> (preferred) or legacy ?unit=<slug/id>
        room_id = params.get("room_id", [None])[0]

        if room_id and room_id.isdigit() and self.church:
            from units.models import ChatRoom

            room = await sync_to_async(
                ChatRoom.raw_objects.select_related("unit")
                .filter(church=self.church, id=int(room_id), is_active=True)
                .first
            )()
            if room:
                self.room = room
                self.unit = room.unit
                self.room_group_name = f"chat_room_{room.id}"
                await self.channel_layer.group_add(
                    self.room_group_name, self.channel_name
                )

        # Legacy fallback: resolve by unit slug/id
        if not self.room and unit_slug and self.church:
            unit = None
            if unit_slug.isdigit():
                unit = await sync_to_async(
                    ChurchUnit.raw_objects.filter(
                        church=self.church, id=int(unit_slug)
                    ).first
                )()
            if not unit:
                unit = await sync_to_async(
                    ChurchUnit.raw_objects.filter(
                        church=self.church, name__iexact=unit_slug
                    ).first
                )()
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
                    await self.channel_layer.group_add(
                        self.room_group_name, self.channel_name
                    )

        if not self.room_group_name:
            self.room_group_name = self.central_group

        await self.accept()

        if self.user.is_authenticated:
            await self.set_user_online(True)
            await self.broadcast_online_status(True)

        recent = await self.get_recent_pinned(self.unit)
        await self.send(
            text_data=json.dumps({"type": "pinned_preview", "messages": recent})
        )

    async def disconnect(self, close_code):
        if self.user.is_authenticated:
            await self.set_user_online(False)
            await self.broadcast_online_status(False)
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        if (
            hasattr(self, "central_group")
            and self.central_group != self.room_group_name
        ):
            await self.channel_layer.group_discard(
                self.central_group, self.channel_name
            )
        if hasattr(self, "user_private_group") and self.user_private_group:
            await self.channel_layer.group_discard(
                self.user_private_group, self.channel_name
            )

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
            self.room_group_name, {"type": "broadcast", "message": message}
        )

    async def broadcast(self, event):
        await self.send(text_data=json.dumps(event["message"]))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if data.get("type") == "mark_read":
                unit_id = data.get("unit_id")
                if unit_id:
                    await self.mark_unit_read(unit_id)
                    await self.send_unread_counts()
                return
            if data.get("type") == "get_unread_counts":
                await self.send_unread_counts()
                return
            if data.get("type") == "typing":
                sender_id = data.get("sender_id") or str(getattr(self, "user_id", "") or "")
                # Resolve sender color server-side so clients always get the right color
                # even if they haven't loaded ALL_MEMBERS yet
                try:
                    from core.utils.colors import get_member_color
                    sender_color = get_member_color(int(sender_id), variant="hex") if sender_id.isdigit() else ""
                except Exception:
                    sender_color = ""
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "broadcast",
                        "message": {
                            "type":         "typing",
                            "sender_name":  data.get("sender_name") or "Someone",
                            "sender_title": data.get("sender_title") or "",
                            "sender_id":    sender_id,
                            "sender_color": sender_color,
                            "room_id":      data.get("room_id"),
                        },
                    },
                )
                return
            if data.get("action"):
                await self.handle_action(data)
                return
            unit_id = self.unit.id if self.unit else None
            room_id = self.room.id if self.room else None
            await self.handle_new_message(
                {**data, "unit_id": unit_id, "room_id": room_id}
            )
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")

    async def send_unread_counts(self):
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from units.models import ChatRoom

        user = self.scope["user"]
        if not user.is_authenticated or not self.church:
            return

        # Resolve the caller's ChurchMember record once
        church_member = await sync_to_async(
            ChurchMember.raw_objects.filter(
                church=self.church, user=user, is_active=True
            ).first
        )()
        if not church_member:
            return

        counts = {}
        rooms = await sync_to_async(list)(
            ChatRoom.raw_objects.filter(
                church=self.church, is_active=True
            ).select_related("unit")
        )
        for room in rooms:
            unread = await sync_to_async(
                ChatMessage.raw_objects.filter(church=self.church, room=room)
                .exclude(sender=church_member)
                .exclude(read_by=church_member)
                .count
            )()
            # Key by room id so JS can correlate with data-room-id
            counts[str(room.id)] = unread

        await self.send(
            text_data=json.dumps({"type": "unread_counts", "counts": counts})
        )

    @database_sync_to_async
    def mark_unit_read(self, unit_id):
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from units.models import ChatRoom

        user = self.scope["user"]
        church_member = ChurchMember.raw_objects.filter(
            church=self.church, user=user, is_active=True
        ).first()
        if not church_member:
            return

        # Mark all unread messages in all rooms belonging to this unit
        room_ids = ChatRoom.raw_objects.filter(
            church=self.church, unit_id=unit_id, is_active=True
        ).values_list("id", flat=True)

        for msg in (
            ChatMessage.raw_objects.filter(church=self.church, room_id__in=room_ids)
            .exclude(sender=church_member)
            .exclude(read_by=church_member)
        ):
            msg.read_by.add(church_member)

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
                    "title": getattr(pinner, "title", ""),
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
                },
            )

            recent = await self.get_recent_pinned(self.unit)
            await self.channel_layer.group_send(
                self.room_group_name, {"type": "pinned_preview", "messages": recent}
            )
            return

        elif action == "edit":
            message_id = data.get("message_id")
            new_text = data.get("message", "").strip()
            if message_id and new_text:
                result = await self.handle_edit(message_id, sender_id, new_text)
                if result:
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            "type": "message_edited",
                            "message_id": message_id,
                            "message": new_text,
                            "edited_at": result,
                        },
                    )
            return

        elif action == "delete":
            message_id = data.get("message_id")
            if message_id:
                ok = await self.handle_delete(message_id, sender_id)
                if ok:
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {"type": "message_deleted", "message_id": message_id},
                    )
            return

        elif action == "forward":
            message_id = data.get("message_id")
            target_room_id = data.get("target_room_id")
            forwarded_from_room_name = data.get("forwarded_from_room_name", "")
            forwarded_from_room_id   = data.get("forwarded_from_room_id")
            if message_id:
                fwd_result = await self.handle_forward(
                    message_id, sender_id, target_room_id,
                    forwarded_from_room_name=forwarded_from_room_name,
                    forwarded_from_room_id=forwarded_from_room_id,
                )
                # Broadcast the new forwarded message to the target room so
                # members currently viewing that room receive it live.
                if fwd_result:
                    fwd_payload = {
                        "type": "chat_message",
                        "id": fwd_result["msg_id"],
                        "room_id": fwd_result["target_room_id"],
                        "target_room_id": fwd_result["target_room_id"],
                        "sender_id": fwd_result["sender_id"],
                        "sender_name": fwd_result["sender_name"],
                        "sender_title": fwd_result["sender_title"],
                        "message": fwd_result["message"],
                        "created_at": fwd_result["created_at"],
                        "forwarded_from": True,
                        "forwarded_from_room_id": fwd_result["forwarded_from_room_id"],
                        "forwarded_from_room_name": fwd_result["forwarded_from_room_name"],
                        "file": fwd_result.get("file"),
                    }
                    await self.channel_layer.group_send(
                        fwd_result["target_group"], fwd_payload
                    )
            return

        elif action == "reply":
            return

        elif action == "private_request":
            recipient_id = data.get("recipient_member_id")
            if recipient_id:
                await self.handle_private_request(sender_id, recipient_id)
            return

        elif action == "private_message":
            # Fan out to recipient — find their channel group and deliver
            await self.handle_private_message(data)
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
        unit_id = data.get("unit_id")
        room_id = data.get("room_id")  # ChatRoom.id — set by receive()

        if not message.strip() and not guest_id and not file_url and not link_preview:
            return

        saved_message = await self.create_message(
            sender_id,
            message,
            guest_id,
            parent_id,
            mentions_ids,
            file_url,
            link_preview,
            unit_id,
            file_data,
            room_id=room_id,
        )
        payload = {
            **saved_message,
            "type": "chat_message",
            "color": get_user_color(sender_id),
            "unit_id": unit_id,
        }

        if unit_id:
            await self.channel_layer.group_send(
                f"chat_unit_{self.church.id}_{unit_id}", payload
            )

        # Only broadcast to central_group (general room) when this socket is NOT
        # scoped to a specific room.  If we're in a room, room_group_name already
        # equals the room channel — sending to central_group too would cause the
        # message to bleed into every general-room subscriber across all rooms.
        if self.room_group_name == self.central_group:
            await self.channel_layer.group_send(self.central_group, payload)
        else:
            await self.channel_layer.group_send(self.room_group_name, payload)

    # ---------- WebSocket Group Events ----------
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def message_pinned(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "message_pinned",
                    "message_ids": event.get("message_ids"),
                    "pinned": event.get("pinned"),
                    "pinned_by": event.get("pinned_by"),
                }
            )
        )

    async def chat_reaction(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "chat_reaction",
                    "message_id": event["message_id"],
                    "reaction_summary": event["reaction_summary"],
                    "total_reactions": event["total_reactions"],
                }
            )
        )

    async def pinned_preview(self, event):
        await self.send(
            text_data=json.dumps(
                {"type": "pinned_preview", "messages": event["messages"]}
            )
        )

    # =================== Database / Sync Handlers ===================
    @sync_to_async(thread_sensitive=False)
    def get_sender_name(self, sender_id):
        from accounts.models import CustomUser

        user = CustomUser.objects.only("full_name", "username").get(id=sender_id)
        return user.full_name or user.username

    @sync_to_async(thread_sensitive=False)
    def get_sender_image(self, sender_id):
        from accounts.models import CustomUser

        user = CustomUser.objects.only("image").get(id=sender_id)
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
            "date_of_visit": (
                g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
            ),
        }

    @sync_to_async
    def get_parent_info(self, parent_id):
        from .models import ChatMessage

        p = (
            ChatMessage.raw_objects.filter(church=self.church)
            .select_related("sender", "guest_card")
            .get(id=parent_id)
        )
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
                "date_of_visit": (
                    g.date_of_visit.strftime("%Y-%m-%d") if g.date_of_visit else ""
                ),
            }
        return parent_data

    # ---------- Action Handlers ----------
    @sync_to_async
    def handle_pin(self, message_ids, sender_id):
        from .models import ChatMessage
        from accounts.models import ChurchMember

        pinner = ChurchMember.raw_objects.filter(
            church=self.church, user__id=sender_id, is_active=True
        ).first()

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
                    m.pinned_by = pinner
                m.save(update_fields=["pinned", "pinned_at", "pinned_by"])
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

        ChatMessage.raw_objects.filter(
            church=self.church, pinned=True, pinned_at__lt=cutoff
        ).update(pinned=False, pinned_at=None, pinned_by=None)

        qs = ChatMessage.raw_objects.filter(
            church=self.church, pinned=True, pinned_at__gte=cutoff
        )
        if unit:
            # Filter to messages in rooms belonging to this unit
            qs = qs.filter(room__unit=unit)
        # (no else restriction — show all pinned for this church when no unit given)

        pinned = qs.select_related("pinned_by", "sender", "guest_card").order_by(
            "-pinned_at"
        )[:3]
        mention_map, mention_regex = build_mention_helpers(self.church)
        return [serialize_message(m, mention_map, mention_regex) for m in pinned]

    # ---------- Build Broadcast Payload ----------
    @sync_to_async
    def get_message_payload(self, message_id):
        from .models import ChatMessage
        from .utils import serialize_message, build_mention_helpers

        try:
            msg = ChatMessage.raw_objects.select_related(
                "sender", "guest_card", "parent__sender"
            ).get(id=message_id, church=self.church)
        except ChatMessage.DoesNotExist:
            return {}

        mention_map, mention_regex = build_mention_helpers(self.church)
        return serialize_message(msg, mention_map, mention_regex)

    # ---------- Create Message (Async DB) ----------
    @sync_to_async
    def create_message(
        self,
        sender_id,
        message,
        guest_id=None,
        parent_id=None,
        mentions_ids=None,
        file_url=None,
        link_preview=None,
        unit_id=None,
        file_data=None,
        room_id=None,
    ):
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from guests.models import GuestEntry
        from units.models import ChatRoom
        from .utils import serialize_message, build_mention_helpers, get_link_preview
        import os

        url_pattern = re.compile(r"(https?://[^\s]+)")
        mentions_ids = mentions_ids or []

        try:
            # Resolve sender as ChurchMember — works for every member type
            # (workforce member, trainee, admin with no unit, etc.)
            sender_member = ChurchMember.raw_objects.filter(
                church=self.church,
                user__id=sender_id,
                is_active=True,
            ).first()
            if not sender_member:
                return {}  # not a church member at all — cannot post

            # Resolve ChatRoom
            room = None
            if room_id and str(room_id).isdigit():
                room = ChatRoom.raw_objects.filter(
                    church=self.church, id=int(room_id), is_active=True
                ).first()
            if not room and self.room:
                room = self.room

            guest_card = None
            if guest_id:
                candidate = GuestEntry.raw_objects.filter(
                    id=guest_id, church=self.church
                ).first()
                if candidate:
                    # Allow guest cards for any room that has guest_management
                    room_allows_guests = (
                        (room and room.unit and room.unit.guest_management)
                        if room
                        else True
                    )
                    if room_allows_guests:
                        guest_card = candidate

            parent = (
                ChatMessage.raw_objects.filter(id=parent_id, church=self.church).first()
                if parent_id
                else None
            )

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
                sender=sender_member,
                room=room,
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
        from datetime import timedelta

        from .models import ChatMessage
        from django.utils import timezone

        try:
            msg = (
                ChatMessage.raw_objects.filter(church=self.church, id=message_id)
                .select_related("sender__user")
                .first()
            )
            if not msg or msg.is_deleted:
                return None
            # Sender is now ChurchMember — check via sender.user.id
            if not msg.sender or msg.sender.user_id != sender_id:
                return None
            if msg.created_at and (timezone.now() - msg.created_at) > timedelta(minutes=10):
                return None
            if not msg.original_message:
                msg.original_message = msg.message or ""
            msg.message = new_text
            msg.edited_at = timezone.now()
            msg.save(update_fields=["message", "original_message", "edited_at"])
            return msg.edited_at.isoformat()
        except Exception as exc:
            logger.error("handle_edit failed: %s", exc)
            return None

    @database_sync_to_async
    def handle_delete(self, message_id, sender_id):
        """Soft-delete a message. Only the original sender can delete."""
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from django.utils import timezone

        try:
            msg = (
                ChatMessage.raw_objects.filter(church=self.church, id=message_id)
                .select_related("sender__user")
                .first()
            )
            if not msg or msg.is_deleted:
                return False
            # sender is ChurchMember — check via sender.user.id
            if not msg.sender or msg.sender.user_id != sender_id:
                return False
            deleter = ChurchMember.raw_objects.filter(
                church=self.church, user__id=sender_id, is_active=True
            ).first()
            msg.is_deleted = True
            msg.deleted_at = timezone.now()
            msg.deleted_by = deleter
            msg.message = ""
            msg.save(
                update_fields=["is_deleted", "deleted_at", "deleted_by", "message"]
            )
            return True
        except Exception as exc:
            logger.error("handle_delete failed: %s", exc)
            return False

    @database_sync_to_async
    def handle_forward(self, message_id, sender_id, target_room_id,
                       forwarded_from_room_name="", forwarded_from_room_id=None):
        """Forward a message to another room the sender has access to."""
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from units.models import ChatRoom

        try:
            original = ChatMessage.raw_objects.filter(
                church=self.church, id=message_id
            ).first()
            if not original or original.is_deleted:
                return
            target_room = None
            normalized_target_room_id = None
            if target_room_id not in (None, "", "null"):
                target_room = ChatRoom.raw_objects.filter(
                    church=self.church, id=target_room_id, is_active=True
                ).first()
                if not target_room:
                    return
                normalized_target_room_id = target_room.id
            sender_member = ChurchMember.raw_objects.filter(
                church=self.church, user__id=sender_id, is_active=True
            ).first()
            if not sender_member:
                return
            new_msg = ChatMessage.raw_objects.create(
                church=self.church,
                sender=sender_member,
                room=target_room,
                message=original.message or "",
                forwarded_from=original,
                file=original.file,
                file_type=original.file_type,
                file_name=original.file_name,
            )
            # Persist room-name on the new message so load/ can return it
            if forwarded_from_room_name and hasattr(new_msg, "forwarded_from_room_name"):
                ChatMessage.raw_objects.filter(pk=new_msg.pk).update(
                    forwarded_from_room_name=forwarded_from_room_name
                )

            # Return the new message id and target group so the async caller
            # can broadcast it to the target room's channel group.
            return {
                "msg_id": new_msg.id,
                "target_room_id": normalized_target_room_id,
                "target_group": (
                    f"chat_room_{normalized_target_room_id}"
                    if normalized_target_room_id
                    else self.central_group
                ),
                "forwarded_from_room_name": forwarded_from_room_name,
                "forwarded_from_room_id": forwarded_from_room_id,
                "sender_id": sender_id,
                "sender_name": sender_member.user.full_name or sender_member.user.username,
                "sender_title": getattr(sender_member.user, "title", "") or "",
                "message": original.message or "",
                "created_at": new_msg.created_at.isoformat() if hasattr(new_msg, "created_at") and new_msg.created_at else None,
                "file": None,
            }
        except Exception as exc:
            logger.error("handle_forward failed: %s", exc)

    async def handle_private_message(self, data):
        """Fan a private_message out to the recipient's central channel group."""
        recipient_user_id = data.get("recipient_user_id")
        if not recipient_user_id or not self.church:
            return
        saved = await self.persist_private_message(data)
        if not saved:
            return
        recipient_group = f"chat_church_{self.church.id}_user_{recipient_user_id}"
        payload = {
            "type": "private_message",
            "id": saved["id"],
            "client_id": data.get("client_id"),
            "sender_id": saved["sender_id"],
            "sender_name": saved["sender_name"],
            "sender_title": saved["sender_title"],
            "recipient_user_id": recipient_user_id,
            "message": saved["message"],
            "created_at": saved["created_at"],
        }
        # Also echo back to the sender so their other open tabs see it
        sender_group = f"chat_church_{self.church.id}_user_{data.get('sender_id')}"
        await self.channel_layer.group_send(recipient_group, payload)
        if sender_group != recipient_group:
            await self.channel_layer.group_send(sender_group, payload)

    @database_sync_to_async
    def persist_private_message(self, data):
        from .models import ChatMessage
        from accounts.models import ChurchMember
        from units.models import ChatRoom

        sender_user_id = data.get("sender_id")
        recipient_user_id = data.get("recipient_user_id")
        message = (data.get("message") or "").strip()
        if not sender_user_id or not recipient_user_id or not message:
            return None

        sender = ChurchMember.raw_objects.filter(
            church=self.church,
            user_id=sender_user_id,
            is_active=True,
        ).select_related("user").first()
        recipient = ChurchMember.raw_objects.filter(
            church=self.church,
            user_id=recipient_user_id,
            is_active=True,
        ).first()
        if not sender or not recipient:
            return None

        low_id, high_id = sorted([sender.id, recipient.id])
        room, _ = ChatRoom.raw_objects.get_or_create(
            church=self.church,
            room_type="private",
            name=f"Private:{low_id}:{high_id}",
            defaults={
                "is_default": False,
                "description": "Private chat",
                "created_by": sender,
            },
        )
        room.members.add(sender, recipient)

        saved = ChatMessage.raw_objects.create(
            church=self.church,
            sender=sender,
            room=room,
            message=message,
        )
        return {
            "id": saved.id,
            "sender_id": sender.user_id,
            "sender_name": sender.user.full_name or sender.user.username,
            "sender_title": getattr(sender.user, "title", "") or "",
            "message": saved.message or "",
            "created_at": saved.created_at.isoformat(),
        }

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

    async def private_message(self, event):
        """Deliver a private_message event to this WebSocket client."""
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
        member = await sync_to_async(
            ChurchMember.raw_objects.select_related("church")
            .filter(user=self.user, is_active=True)
            .first
        )()
        self.church = member.church if member else None

        if not self.user.is_superuser:
            self.user_unit_ids = await self.get_user_unit_ids()
        else:
            self.user_unit_ids = []

        await self.channel_layer.group_add("attendance", self.channel_name)
        await self.channel_layer.group_add(
            f"attendance_user_{self.user.id}", self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("attendance", self.channel_name)
        await self.channel_layer.group_discard(
            f"attendance_user_{self.user.id}", self.channel_name
        )

    @sync_to_async
    def get_user_unit_ids(self):
        from units.models import UnitMembership

        if not self.church:
            return []
        return list(
            UnitMembership.raw_objects.filter(
                church=self.church, workforce_member__member__user=self.user
            ).values_list("unit_id", flat=True)
        )

    async def send_event(self, event):
        data = event.get("data", {})
        unit_id = data.get("unit_id")

        if self.user.is_superuser or unit_id is None or unit_id in self.user_unit_ids:
            await self.send(text_data=json.dumps(data))

    async def send_summary(self, event):
        records = event.get("data", {}).get("records", [])

        if not self.user.is_superuser:
            records = [
                r
                for r in records
                if (
                    (
                        r["event"]["unit"]["id"] in self.user_unit_ids
                        or r["event"]["unit"]["id"] is None
                    )
                    and not str(r.get("user", {}).get("full_name", ""))
                    .lower()
                    .startswith("superuser")
                )
            ]

        await self.send(
            text_data=json.dumps({"type": "send_summary", "records": records})
        )

    async def dashboard_summary(self, event):
        data = event.get("data", {})
        user_id = data.get("user_id")

        if user_id == self.user.id:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "dashboard_summary",
                        "summary": data.get("summary"),
                        "today": data.get("today"),
                        "totals": data.get("totals"),
                    }
                )
            )