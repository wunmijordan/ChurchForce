"""
feeds/views.py
All feed AJAX endpoints + the WebSocket consumer helper.
All views return JSON — no page reloads.
"""
import json
import mimetypes
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from tenants.time_utils import get_effective_time_format, get_effective_date_format


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _church(request):
    return getattr(request, "church", None)


def _can(request, permission):
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can(permission))


def _member(request):
    return getattr(request, "member", None)


def _is_admin(request):
    """True if user can see the admin dashboard."""
    return request.user.is_superuser or _can(request, "dashboard.admin")


def _has_global_workforce_tier(request):
    church = _church(request)
    member = _member(request)
    if not church or not member:
        return False

    from workforce.models import WorkforceMember

    return WorkforceMember.raw_objects.filter(
        church=church,
        member=member,
        is_active=True,
        roles__role__tier__isnull=False,
    ).exists()


def _is_trainee_only(request):
    church = _church(request)
    member = _member(request)
    if not church or not member:
        return False

    from units.models import UnitMembership
    from workforce.models import WorkforceTraineeProfile

    has_unit = UnitMembership.raw_objects.filter(
        church=church,
        workforce_member__member=member,
        is_active=True,
    ).exists()
    return (
        not has_unit
        and WorkforceTraineeProfile.raw_objects.filter(
            church=church,
            member=member,
            is_active=True,
        ).exists()
    )


def _can_access_unit_feed(request, unit):
    if not unit:
        return True
    if _is_admin(request):
        return True
    if _is_trainee_only(request):
        return False
    if _has_global_workforce_tier(request):
        return True

    from units.models import UnitMembership

    return UnitMembership.raw_objects.filter(
        church=_church(request),
        unit=unit,
        workforce_member__member__user=request.user,
        is_active=True,
    ).exists()


def _csrf(request):
    from django.middleware.csrf import get_token
    return get_token(request)


def _serialize_feed(feed, user):
    """Return a JSON-safe dict for a single Feed."""
    my_reaction = None
    try:
        r = feed.reactions.filter(user=user).values_list("reaction", flat=True).first()
        my_reaction = r
    except Exception:
        pass

    media_list = []
    for m in feed.media.all():
        try:
            url = m.file.url
        except Exception:
            url = ""
        media_list.append({
            "id": m.id,
            "type": m.media_type,
            "url": url,
            "thumbnail": m.thumbnail.url if m.thumbnail else None,
        })

    author = feed.author
    repost = None
    repost_original = None
    try:
        repost_obj = feed.reposted_from.select_related(
            "user", "original", "original__author"
        ).first()
        if repost_obj:
            repost_user = repost_obj.user
            repost = {
                "user_id": repost_user.id,
                "user_name": f"{repost_user.title or ''} {repost_user.full_name or repost_user.username}".strip(),
                "original_id": repost_obj.original_id,
            }
            orig = repost_obj.original
            if orig and orig.is_active:
                oa = orig.author
                omedia = []
                for m in orig.media.all():
                    try:
                        url = m.file.url
                    except Exception:
                        url = ""
                    omedia.append({
                        "id": m.id,
                        "type": m.media_type,
                        "url": url,
                        "thumbnail": m.thumbnail.url if m.thumbnail else None,
                    })
                repost_original = {
                    "id": orig.id,
                    "body": orig.body or "",
                    "author": {
                        "id": oa.id,
                        "name": f"{oa.title or ''} {oa.full_name or oa.username}".strip(),
                        "initials": oa.initials,
                        "image": oa.image.url if oa.image else None,
                    },
                    "cover_image": orig.cover_image.url if orig.cover_image else None,
                    "media": omedia,
                }
    except Exception:
        repost = None
        repost_original = None

    # Has the current user reposted this feed?
    has_my_repost = False
    try:
        from feeds.models import FeedRepost as _FR
        has_my_repost = _FR.raw_objects.filter(
            church=feed.church, original=feed, user=user
        ).exists()
    except Exception:
        pass

    local_created_at = timezone.localtime(feed.created_at)
    return {
        "id": feed.id,
        "body": feed.body,
        "has_my_repost": has_my_repost,
        "scope": feed.scope,
        "unit_name": feed.unit.name if feed.unit else None,
        "unit_id": feed.unit_id,
        "is_pinned": feed.is_pinned,
        "is_admin_post": feed.is_admin_post,
        "repost_count": feed.repost_count,
        "created_at": local_created_at.isoformat(),
        "created_label": local_created_at.strftime(
            get_effective_time_format(feed.church, "%H:%M:%S")
        ),
        "created_date_label": local_created_at.strftime(
            get_effective_date_format(feed.church, "%d %b %Y")
        ),
        "repost": repost,
        "repost_original": repost_original,
        "cover_image": feed.cover_image.url if feed.cover_image else None,
        "media": media_list,
        "reaction_summary": feed.reaction_summary,
        "total_reactions": feed.total_reactions,
        "comment_count": feed.comment_count,
        "my_reaction": my_reaction,
        "author": {
            "id": author.id,
            "name": f"{author.title or ''} {author.full_name or author.username}".strip(),
            "initials": author.initials,
            "image": author.image.url if author.image else None,
        },
    }


def _serialize_comment(comment, user):
    a = comment.author
    replies = [
        {
            "id": r.id,
            "body": r.body,
            "created_at": timezone.localtime(r.created_at).isoformat(),
            "created_label": timezone.localtime(r.created_at).strftime(
                get_effective_time_format(r.church, "%H:%M:%S")
            ),
            "author": {
                "id": r.author.id,
                "name": f"{r.author.title or ''} {r.author.full_name or r.author.username}".strip(),
                "image": r.author.image.url if r.author.image else None,
                "initials": r.author.initials,
            },
        }
        for r in comment.replies.all()
    ]
    return {
        "id": comment.id,
        "body": comment.body,
        "created_at": timezone.localtime(comment.created_at).isoformat(),
        "created_label": timezone.localtime(comment.created_at).strftime(
            get_effective_time_format(comment.church, "%H:%M:%S")
        ),
        "replies": replies,
        "author": {
            "id": a.id,
            "name": f"{a.title or ''} {a.full_name or a.username}".strip(),
            "image": a.image.url if a.image else None,
            "initials": a.initials,
        },
    }


def _broadcast_feed(church_id, event_type, payload):
    """Push a new/updated feed over WebSocket to the church channel group."""
    try:
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            f"feeds_church_{church_id}",
            {"type": "feed_event", "event_type": event_type, "payload": payload},
        )
    except Exception:
        pass  # WebSocket broadcast is best-effort


def _notify(church, actor, recipient, verb, feed, comment=None):
    """Create a FeedNotification and broadcast it."""
    if actor == recipient:
        return
    try:
        from feeds.models import FeedNotification
        FeedNotification.raw_objects.create(
            church=church, actor=actor, recipient=recipient,
            verb=verb, feed=feed, comment=comment,
        )
        layer = get_channel_layer()
        async_to_sync(layer.group_send)(
            f"feeds_user_{recipient.id}",
            {
                "type": "feed_event",
                "event_type": "notification",
                "payload": {
                    "actor": f"{actor.title or ''} {actor.full_name or actor.username}".strip(),
                    "verb": verb,
                    "feed_id": feed.id,
                },
            },
        )
    except Exception:
        pass


# ─── Feed list (paginated) ────────────────────────────────────────────────────

@login_required
@require_GET
def feed_list(request):
    """
    GET /feeds/list/?scope=general|unit&unit_id=<id>&page=1&pinned=1
    """
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    from feeds.models import Feed, FeedRepost

    scope   = request.GET.get("scope", "general")
    unit_id = request.GET.get("unit_id")
    pinned  = request.GET.get("pinned") == "1"
    page    = int(request.GET.get("page", 1))

    qs = Feed.raw_objects.filter(
        church=church, is_active=True
    ).select_related("author", "unit").prefetch_related(
        "media",
        Prefetch("reactions"),
        Prefetch(
            "reposted_from",
            queryset=FeedRepost.raw_objects.select_related(
                "user", "original", "original__author"
            ).prefetch_related("original__media"),
        ),
        Prefetch("comments", queryset=__import__("feeds.models", fromlist=["FeedComment"]).FeedComment.raw_objects.filter(
            church=church, parent__isnull=True
        ).select_related("author").prefetch_related("replies__author")),
    )

    if pinned:
        qs = qs.filter(is_pinned=True)
    elif scope == "unit" and unit_id:
        from units.models import ChurchUnit
        unit = ChurchUnit.raw_objects.filter(church=church, id=unit_id, is_active=True).first()
        if not unit or not _can_access_unit_feed(request, unit):
            return JsonResponse({"feeds": [], "page": page, "has_next": False, "total_pages": 0})
        qs = qs.filter(scope="unit", unit_id=unit_id)
    else:
        qs = qs.filter(scope="general")

    paginator = Paginator(qs, 10)
    page_obj  = paginator.get_page(page)

    items = [_serialize_feed(f, request.user) for f in page_obj.object_list]
    return JsonResponse({
        "feeds": items,
        "page": page,
        "has_next": page_obj.has_next(),
        "total_pages": paginator.num_pages,
    })


# ─── Create post ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_create(request):
    """
    POST /feeds/create/
    Multipart: body, scope, unit_id (optional), files[] (optional)
    """
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    from feeds.models import Feed, FeedMedia

    body    = request.POST.get("body", "").strip()
    scope   = request.POST.get("scope", "general")
    unit_id = request.POST.get("unit_id") or None
    files   = request.FILES.getlist("files")

    if not body and not files:
        return JsonResponse({"error": "Post must have text or media."}, status=400)

    unit = None
    if unit_id:
        from units.models import ChurchUnit
        unit = ChurchUnit.raw_objects.filter(church=church, id=unit_id, is_active=True).first()
        if not unit:
            return JsonResponse({"error": "Unit not found."}, status=404)
        if not _can_access_unit_feed(request, unit):
            return JsonResponse({"error": "You cannot post to this unit feed."}, status=403)

    is_admin_post = _is_admin(request)

    with transaction.atomic():
        feed = Feed.raw_objects.create(
            church=church,
            author=request.user,
            body=body,
            scope=scope,
            unit=unit,
            is_admin_post=is_admin_post,
        )

        for i, f in enumerate(files):
            mime = f.content_type or mimetypes.guess_type(f.name)[0] or ""
            if mime.startswith("video"):
                mtype = "video"
            elif mime.startswith("audio"):
                mtype = "audio"
            else:
                mtype = "image"
            FeedMedia.raw_objects.create(
                church=church, feed=feed, file=f, media_type=mtype, order=i
            )

    payload = _serialize_feed(feed, request.user)
    _broadcast_feed(church.id, "new_feed", payload)

    return JsonResponse({"feed": payload, "ok": True}, status=201)


# ─── React ───────────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_react(request, feed_id):
    """
    POST /feeds/<feed_id>/react/
    JSON body: {"reaction": "like"|"love"|"fire"|"pray"|"clap"}
    Toggle: same reaction = remove. Different = switch.
    """
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church"}, status=403)

    from feeds.models import Feed, FeedReaction

    feed = Feed.raw_objects.filter(church=church, id=feed_id, is_active=True).first()
    if not feed:
        return JsonResponse({"error": "Not found"}, status=404)

    try:
        data = json.loads(request.body)
    except (ValueError, AttributeError):
        data = {}

    reaction_type = data.get("reaction", "like")
    VALID = {c[0] for c in FeedReaction.reaction.field.choices}
    if reaction_type not in VALID:
        reaction_type = "like"

    existing = FeedReaction.raw_objects.filter(
        church=church, feed=feed, user=request.user
    ).first()

    if existing:
        if existing.reaction == reaction_type:
            existing.delete()
            my_reaction = None
        else:
            existing.reaction = reaction_type
            existing.save(update_fields=["reaction"])
            my_reaction = reaction_type
    else:
        FeedReaction.raw_objects.create(
            church=church, feed=feed, user=request.user, reaction=reaction_type
        )
        my_reaction = reaction_type
        _notify(church, request.user, feed.author, "reacted", feed)

    feed.refresh_from_db()
    summary = feed.reaction_summary
    total   = feed.total_reactions

    _broadcast_feed(church.id, "react", {
        "feed_id": feed_id, "summary": summary, "total": total,
    })

    return JsonResponse({"ok": True, "summary": summary, "total": total, "my_reaction": my_reaction})


# ─── Comment ─────────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_comment(request, feed_id):
    """
    POST /feeds/<feed_id>/comment/
    JSON body: {"body": "...", "parent_id": null|<int>}
    """
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church"}, status=403)

    from feeds.models import Feed, FeedComment

    feed = Feed.raw_objects.filter(church=church, id=feed_id, is_active=True).first()
    if not feed:
        return JsonResponse({"error": "Not found"}, status=404)

    try:
        data = json.loads(request.body)
    except (ValueError, AttributeError):
        data = {}

    body = (data.get("body") or "").strip()
    if not body:
        return JsonResponse({"error": "Comment cannot be empty"}, status=400)

    parent_id = data.get("parent_id")
    parent = None
    if parent_id:
        parent = FeedComment.raw_objects.filter(
            church=church, feed=feed, id=parent_id, parent__isnull=True
        ).first()

    comment = FeedComment.raw_objects.create(
        church=church, feed=feed, author=request.user, body=body, parent=parent
    )

    verb = "replied" if parent else "commented"
    target = parent.author if parent else feed.author
    _notify(church, request.user, target, verb, feed, comment)

    payload_c = _serialize_comment(comment, request.user)
    _broadcast_feed(church.id, "comment", {
        "feed_id": feed_id,
        "comment": payload_c,
        "is_reply": bool(parent),
        "parent_id": parent_id,
    })

    return JsonResponse({"comment": payload_c, "ok": True}, status=201)


# ─── Load comments for a post ────────────────────────────────────────────────

@login_required
@require_GET
def feed_comments(request, feed_id):
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church"}, status=403)

    from feeds.models import Feed, FeedComment

    feed = Feed.raw_objects.filter(church=church, id=feed_id, is_active=True).first()
    if not feed:
        return JsonResponse({"error": "Not found"}, status=404)

    comments = FeedComment.raw_objects.filter(
        church=church, feed=feed, parent__isnull=True
    ).select_related("author").prefetch_related("replies__author").order_by("created_at")

    return JsonResponse({
        "comments": [_serialize_comment(c, request.user) for c in comments]
    })


# ─── Repost ──────────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_repost(request, feed_id):
    """Toggle repost. Reposting creates a new Feed with scope from original."""
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church"}, status=403)

    from feeds.models import Feed, FeedRepost

    original = Feed.raw_objects.filter(church=church, id=feed_id, is_active=True).first()
    if not original:
        return JsonResponse({"error": "Not found"}, status=404)

    existing = FeedRepost.raw_objects.filter(
        church=church, original=original, user=request.user
    ).first()

    # Read optional quote comment (sent as JSON or form data)
    quote_body = ""
    try:
        data = json.loads(request.body)
        quote_body = (data.get("quote_body") or "").strip()
    except Exception:
        quote_body = (request.POST.get("quote_body") or "").strip()

    repost_feed = None
    if existing:
        # Undo repost
        if existing.repost:
            existing.repost.delete()
        existing.delete()
        original.repost_count = max(0, original.repost_count - 1)
        original.save(update_fields=["repost_count"])
        reposted = False
    else:
        with transaction.atomic():
            repost_feed = Feed.raw_objects.create(
                church=church,
                author=request.user,
                # quote_body is the reposter's comment; blank string for bare reposts
                body=quote_body,
                scope=original.scope,
                unit=original.unit,
            )
            FeedRepost.raw_objects.create(
                church=church, original=original,
                repost=repost_feed, user=request.user,
            )
            original.repost_count += 1
            original.save(update_fields=["repost_count"])
        _notify(church, request.user, original.author, "reposted", original)
        reposted = True

    _broadcast_feed(church.id, "repost", {
        "feed_id": feed_id, "repost_count": original.repost_count,
    })
    if repost_feed:
        payload = _serialize_feed(repost_feed, request.user)
        _broadcast_feed(church.id, "new_feed", payload)
    else:
        payload = None

    return JsonResponse({"ok": True, "reposted": reposted, "repost_count": original.repost_count, "feed": payload})


# ─── Pin / unpin ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_pin(request, feed_id):
    """Toggle pin — admin/leader only."""
    if not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    church = _church(request)
    from feeds.models import Feed

    feed = Feed.raw_objects.filter(church=church, id=feed_id, is_active=True).first()
    if not feed:
        return JsonResponse({"error": "Not found"}, status=404)

    feed.is_pinned = not feed.is_pinned
    feed.save(update_fields=["is_pinned"])

    if feed.is_pinned:
        _notify(church, request.user, feed.author, "pinned", feed)

    _broadcast_feed(church.id, "pin", {"feed_id": feed_id, "is_pinned": feed.is_pinned})
    return JsonResponse({"ok": True, "is_pinned": feed.is_pinned})


# ─── Delete post ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def feed_delete(request, feed_id):
    church = _church(request)
    from feeds.models import Feed

    feed = Feed.raw_objects.filter(church=church, id=feed_id).first()
    if not feed:
        return JsonResponse({"error": "Not found"}, status=404)

    if feed.author != request.user and not _is_admin(request):
        return JsonResponse({"error": "Permission denied"}, status=403)

    feed.is_active = False
    feed.save(update_fields=["is_active"])
    _broadcast_feed(church.id, "delete", {"feed_id": feed_id})
    return JsonResponse({"ok": True})


# ─── Notifications list ───────────────────────────────────────────────────────

@login_required
@require_GET
def feed_notifications(request):
    church = _church(request)
    if not church:
        return JsonResponse({"error": "No church"}, status=403)

    from feeds.models import FeedNotification

    notifs = FeedNotification.raw_objects.filter(
        church=church, recipient=request.user
    ).select_related("actor", "feed").order_by("-created_at")[:30]

    data = [
        {
            "id": n.id,
            "verb": n.get_verb_display(),
            "is_read": n.is_read,
            "created_at": n.created_at.isoformat(),
            "feed_id": n.feed_id,
            "actor": {
                "name": f"{n.actor.title or ''} {n.actor.full_name or n.actor.username}".strip(),
                "image": n.actor.image.url if n.actor.image else None,
                "initials": n.actor.initials,
            },
        }
        for n in notifs
    ]

    # Mark all as read
    FeedNotification.raw_objects.filter(
        church=church, recipient=request.user, is_read=False
    ).update(is_read=True)

    unread = FeedNotification.raw_objects.filter(
        church=church, recipient=request.user, is_read=False
    ).count()

    return JsonResponse({"notifications": data, "unread": unread})


# ─── Unread notification count ────────────────────────────────────────────────

@login_required
@require_GET
def feed_notif_count(request):
    church = _church(request)
    if not church:
        return JsonResponse({"count": 0})

    from feeds.models import FeedNotification

    count = FeedNotification.raw_objects.filter(
        church=church, recipient=request.user, is_read=False
    ).count()
    return JsonResponse({"count": count})


# ─── Unit list (for scope picker) ────────────────────────────────────────────

@login_required
@require_GET
def feed_units(request):
    """Return units the current user belongs to + general option."""
    church = _church(request)
    if not church:
        return JsonResponse({"units": []})

    from units.models import ChurchUnit, UnitMembership

    if _is_trainee_only(request):
        return JsonResponse({"units": [], "is_admin": _is_admin(request), "global_access": False})

    if _is_admin(request) or _has_global_workforce_tier(request):
        units = [
            {"id": u.id, "name": u.name}
            for u in ChurchUnit.raw_objects.filter(church=church, is_active=True).order_by("name")
        ]
        return JsonResponse({"units": units, "is_admin": _is_admin(request), "global_access": True})

    memberships = UnitMembership.raw_objects.filter(
        church=church,
        workforce_member__member__user=request.user,
        is_active=True,
    ).select_related("unit").distinct()

    units = [{"id": m.unit.id, "name": m.unit.name} for m in memberships if m.unit]
    return JsonResponse({"units": units, "is_admin": _is_admin(request)})