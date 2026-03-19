# ===========================
# Multi-tenant refactor overrides
# ===========================

import csv
import io
import calendar
from datetime import date, datetime, timedelta

import openpyxl
from openpyxl.utils import get_column_letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q, Count, Max
from django.db.models.functions import ExtractMonth
from django.http import HttpResponse, JsonResponse, HttpResponseRedirect, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import urlencode
from django.utils.timezone import now, localdate, make_aware, is_naive
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from permissions.services.resolver import PermissionResolver
from permissions.models import UnitRole
from units.models import ChurchUnit, UnitMembership
from workforce.utils import get_available_events_for_user
from workforce.views import mark_attendance as workforce_mark_attendance

from .models import (
    GuestEntry,
    FollowUpReport,
    SocialMediaEntry,
    Review,
    GuestStatus,
    VisitPurpose,
    VisitChannel,
    ChurchService,
)
from .forms import GuestEntryForm, FollowUpReportForm

User = get_user_model()


def _get_church(request):
    return getattr(request, "church", None)


def _get_permissions(request):
    perms = getattr(request, "permissions", None)
    if perms:
        return perms

    church = _get_church(request)
    if not church or not request.user.is_authenticated:
        return None

    return PermissionResolver(request.user, church)


def _can(request, permission, unit=None):
    if request.user.is_superuser:
        return True

    perms = _get_permissions(request)
    return bool(perms and perms.can(permission, unit=unit))


def _guest_queryset(request):
    church = _get_church(request)
    if not church:
        return GuestEntry.raw_objects.none()

    return GuestEntry.objects.for_request(request)


def _default_status(church):
    if not church:
        return None
    return (
        GuestStatus.raw_objects.filter(church=church, is_active=True).order_by("order").filter(is_default=True).first()
        or GuestStatus.raw_objects.filter(church=church, is_active=True).order_by("order").first()
    )


def _default_service(church):
    if not church:
        return None
    return ChurchService.raw_objects.filter(church=church, is_active=True).first()


def _find_or_create_status(church, name):
    if not name:
        return _default_status(church)
    status = GuestStatus.raw_objects.filter(church=church, name__iexact=name).first()
    if status:
        return status
    return GuestStatus.raw_objects.create(church=church, name=name)


def _find_or_create_purpose(church, name):
    if not name:
        return None
    obj = VisitPurpose.raw_objects.filter(church=church, name__iexact=name).first()
    if obj:
        return obj
    return VisitPurpose.raw_objects.create(church=church, name=name)


def _find_or_create_channel(church, name):
    if not name:
        return None
    obj = VisitChannel.raw_objects.filter(church=church, name__iexact=name).first()
    if obj:
        return obj
    return VisitChannel.raw_objects.create(church=church, name=name)


def _find_or_create_service(church, name):
    if not name:
        return _default_service(church)
    obj = ChurchService.raw_objects.filter(church=church, name__iexact=name).first()
    if obj:
        return obj
    return ChurchService.raw_objects.create(church=church, name=name)


def parse_flexible_date(date_str):
    if not date_str:
        return None

    date_formats = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _build_field_data(guest):
    excluded_fields = {
        "id", "custom_id", "title", "full_name", "gender",
        "picture", "phone_number", "assigned_to",
    }

    field_data = []
    for field in guest._meta.fields:
        if field.name in excluded_fields:
            continue

        value = getattr(guest, field.name)
        time_since = None
        formatted_value = value

        if field.name == "date_of_visit" and value:
            formatted_value = value.strftime("%b. %d, %Y")
            dt_value = datetime.combine(value, datetime.min.time())
            if is_naive(dt_value):
                dt_value = make_aware(dt_value)
            delta = timesince(dt_value, now())
            time_since = delta.split(",")[0]

        if field.name == "assigned_at" and value:
            formatted_value = value.strftime("%b. %d, %Y %I:%M %p")

        field_data.append({
            "name": field.name,
            "verbose_name": field.verbose_name.title(),
            "value": formatted_value,
            "time_since": time_since,
            "icon": field.name,
        })

    return field_data


SVG_ICONS = {
    "email": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M12 12m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0\" /><path d=\"M16 12v1.5a2.5 2.5 0 0 0 5 0v-1.5a9 9 0 1 0 -5.5 8.28\" />",
    "date_of_birth": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M4 7a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12z\" /><path d=\"M16 3v4\" /><path d=\"M8 3v4\" /><path d=\"M4 11h16\" /><path d=\"M11 15h1\" /><path d=\"M12 15v3\" />",
    "age_range": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0 -18 0\" /><path d=\"M11.5 10.5m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0 -3 0\" /><path d=\"M11.5 13.5m-1.5 0a1.5 1.5 0 1 0 3 0a1.5 1.5 0 1 0 -3 0\" /><path d=\"M7 15v-6\" /><path d=\"M15.5 12h3\" /><path d=\"M17 10.5v3\" />",
    "marital_status": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M7 5m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0\" /><path d=\"M5 22v-5l-1 -1v-4a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v4l-1 1v5\" /><path d=\"M17 5m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0\" /><path d=\"M15 22v-4h-2l2 -6a1 1 0 0 1 1 -1h2a1 1 0 0 1 1 1l2 6h-2v4\" />",
    "home_address": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M5 12l-2 0l9 -9l9 9l-2 0\" /><path d=\"M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-7\" /><path d=\"M9 21v-6a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2v6\" />",
    "occupation": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M3 7m0 2a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2z\" /><path d=\"M8 7v-2a2 2 0 0 1 2 -2h4a2 2 0 0 1 2 2v2\" /><path d=\"M12 12l0 .01\" /><path d=\"M3 13a20 20 0 0 0 18 0\" />",
    "date_of_visit": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M11.795 21h-6.795a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v4\" /><path d=\"M18 18m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0\" /><path d=\"M15 3v4\" /><path d=\"M7 3v4\" /><path d=\"M3 11h16\" /><path d=\"M18 16.496v1.504l1 1\" />",
    "purpose_of_visit": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M3 21l18 0\" /><path d=\"M10 21v-4a2 2 0 0 1 4 0v4\" /><path d=\"M10 5l4 0\" /><path d=\"M12 3l0 5\" /><path d=\"M6 21v-7m-2 2l8 -8l8 8m-2 -2v7\" />",
    "channel_of_visit": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M3 7m0 2a2 2 0 0 1 2 -2h14a2 2 0 0 1 2 2v9a2 2 0 0 1 -2 2h-14a2 2 0 0 1 -2 -2z\" /><path d=\"M16 3l-4 4l-4 -4\" />",
    "service_attended": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M12 5m-1 0a1 1 0 1 0 2 0a1 1 0 1 0 -2 0\" /><path d=\"M7 20h8l-4 -4v-7l4 3l2 -2\" />",
    "status": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M9 11a3 3 0 1 0 6 0a3 3 0 0 0 -6 0\" /><path d=\"M14.997 19.317l-1.583 1.583a2 2 0 0 1 -2.827 0l-4.244 -4.243a8 8 0 1 1 13.657 -5.584\" /><path d=\"M19 22v.01\" /><path d=\"M19 19a2.003 2.003 0 0 0 .914 -3.782a1.98 1.98 0 0 0 -2.414 .483\" />",
    "assigned_at": "<path stroke=\"none\" d=\"M0 0h24v24H0z\" fill=\"none\"/><path d=\"M8 7a4 4 0 1 0 8 0a4 4 0 0 0 -8 0\" /><path d=\"M16 19h6\" /><path d=\"M19 16v6\" /><path d=\"M6 21v-2a4 4 0 0 1 4 -4h4\" />",
}

def _get_workforce_member(request):
    return getattr(request, "workforce_member", None)


def _is_assigned_to_requester(guest, request):
    wf = _get_workforce_member(request)
    return bool(guest.assigned_to and wf and guest.assigned_to.workforce_member_id == wf.id)


def _membership_user(membership):
    if not membership:
        return None
    wf = membership.workforce_member
    if not wf or not wf.member:
        return None
    return wf.member.user


def _membership_label(membership):
    user = _membership_user(membership)
    if not user:
        return "Unknown"
    name = user.get_full_name() or user.username
    unit_name = membership.unit.name if membership.unit else ""
    return f"{name} ({unit_name})" if unit_name else name


def _get_guest_unit_ids(church):
    if not church:
        return set()

    cache_key = f"guest_unit_ids:{church.id}"

    def _build():
        unit_ids = set()
        roles = UnitRole.raw_objects.filter(church=church, is_active=True).select_related("unit")
        for role in roles:
            perms = role.permissions or {}
            for key, allowed in perms.items():
                if allowed and key.startswith("guests."):
                    unit_ids.add(role.unit_id)
                    break
        return list(unit_ids)

    return set(cache.get_or_set(cache_key, _build, 600))


def _get_guest_units(church):
    unit_ids = _get_guest_unit_ids(church)
    if not unit_ids:
        return ChurchUnit.raw_objects.none()
    return ChurchUnit.raw_objects.filter(id__in=unit_ids)


def _get_guest_memberships(church):
    unit_ids = _get_guest_unit_ids(church)
    if not unit_ids:
        return UnitMembership.raw_objects.none()
    return (
        UnitMembership.raw_objects
        .filter(
            church=church,
            unit_id__in=unit_ids,
            is_active=True,
            workforce_member__is_active=True,
            workforce_member__member__is_active=True,
            workforce_member__member__user__is_active=True,
        )
        .select_related("unit", "workforce_member__member__user")
    )

def safe_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).strftime("%Y-%m-%d")
        except Exception:
            for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
                except Exception:
                    pass
            return value
    return value.strftime("%Y-%m-%d")


@login_required
def guest_entry_summary(request):
    church = _get_church(request)
    if not church:
        return JsonResponse({'error': 'No church context'}, status=403)

    year = request.GET.get('year')
    try:
        year = int(year)
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid year'}, status=400)

    guests = _guest_queryset(request).filter(date_of_visit__year=year)

    total_count = guests.count()

    month_counts = (
        guests.annotate(month=ExtractMonth('date_of_visit'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )

    counts_dict = {month: 0 for month in range(1, 13)}
    for entry in month_counts:
        counts_dict[entry['month']] = entry['count']

    max_count = max(counts_dict.values()) if counts_dict else 0
    min_count = min(counts_dict.values()) if counts_dict else 0
    avg_count = sum(counts_dict.values()) // 12 if counts_dict else 0

    max_months = [calendar.month_name[m] for m, c in counts_dict.items() if c == max_count]
    min_months = [calendar.month_name[m] for m, c in counts_dict.items() if c == min_count]

    max_month = max_months[0] if max_months else "N/A"
    min_month = min_months[0] if min_months else "N/A"

    def percent(count):
        return round((count / max_count) * 100, 1) if max_count > 0 else 0

    data = {
        'max_month': max_month,
        'max_count': max_count,
        'max_percent': percent(max_count),
        'min_month': min_month,
        'min_count': min_count,
        'min_percent': percent(min_count),
        'avg_count': avg_count,
        'avg_percent': percent(avg_count),
        'total_count': total_count,
    }

    return JsonResponse(data)


@login_required
def top_services_data(request):
    qs = _guest_queryset(request)

    top_services = (
        qs.values('service_attended')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )

    data = list(top_services)
    total = sum(item['count'] for item in data) or 1

    for item in data:
        item['percent'] = round((item['count'] / total) * 100, 1)

    return JsonResponse({'services': data})


@login_required
def services_attended_chart(request):
    qs = _guest_queryset(request)

    stats = qs.values('service_attended').annotate(count=Count('id')).order_by('-count')
    labels = [item['service_attended'] or "Not Specified" for item in stats]
    counts = [item['count'] for item in stats]

    return JsonResponse({'labels': labels, 'counts': counts})


@login_required
def channel_breakdown(request):
    qs = _guest_queryset(request)

    stats = qs.values('channel_of_visit').annotate(count=Count('id')).order_by('-count')
    total = sum(item['count'] for item in stats)

    data = [
        {
            'label': item['channel_of_visit'] or 'Unknown',
            'count': item['count'],
            'percent': round((item['count'] / total) * 100, 2) if total else 0,
        }
        for item in stats
    ]

    return JsonResponse(data, safe=False)


@login_required
def guest_list_view(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    search_query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    channel_filter = request.GET.get('channel', '').strip()
    purpose_filter = request.GET.get('purpose', '').strip()
    service_filter = request.GET.get('service', '').strip()
    user_filter = request.GET.get('user_filter', '').strip()
    date_of_visit_filter = request.GET.get('date_of_visit', '').strip()
    view_type = request.GET.get('view', 'cards')

    qs = _guest_queryset(request)

    qs = qs.annotate(
        report_count=Count('reports', distinct=True),
        last_reported=Max('reports__report_date'),
        unread_reviews=Count('reviews', filter=Q(reviews__is_read=False), distinct=True),
    )

    filters = {
        "status__name__iexact": status_filter,
        "channel_of_visit__name__iexact": channel_filter,
        "purpose_of_visit__name__iexact": purpose_filter,
        "service_attended__name__iexact": service_filter,
        "date_of_visit": date_of_visit_filter,
    }
    qs = qs.filter(**{k: v for k, v in filters.items() if v})

    if search_query:
        qs = qs.filter(
            Q(full_name__icontains=search_query) |
            Q(phone_number__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(status__name__icontains=search_query) |
            Q(channel_of_visit__name__icontains=search_query) |
            Q(purpose_of_visit__name__icontains=search_query) |
            Q(assigned_to__workforce_member__member__user__full_name__icontains=search_query) |
            Q(assigned_to__workforce_member__member__user__username__icontains=search_query)
        )

    qs = qs.order_by('-custom_id').select_related('assigned_to__workforce_member__member__user')

    if user_filter:
        qs = qs.filter(assigned_to_id=user_filter)

    per_page = 50 if view_type == 'list' else 45
    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    excluded_fields = {
        "id", "custom_id", "title", "full_name", "gender",
        "picture", "phone_number", "assigned_to",
    }

    for guest in page_obj:
        fields = []
        for field in (f for f in guest._meta.fields if f.name not in excluded_fields):
            value = getattr(guest, field.name, None)
            display_value = getattr(guest, f"get_{field.name}_display")() if field.choices else value
            fields.append({
                "name": field.name,
                "verbose_name": getattr(field, "verbose_name", field.name).title(),
                "value": display_value,
                "icon": field.name,
                "svg": SVG_ICONS.get(field.name),
            })
        guest.field_data = fields
        guest.has_unread_reviews = guest.unread_reviews > 0
        guest.is_new = guest.assigned_at and (now() - guest.assigned_at <= timedelta(days=14))

    def cache_list(key, field):
        cache_key = f"{church.id}:{key}"
        return cache.get_or_set(
            cache_key,
            lambda: list(
                _guest_queryset(request)
                .values_list(field, flat=True)
                .distinct()
                .order_by(field)
            ),
            600,
        )

    channels = cache_list("guest_channels", 'channel_of_visit__name')
    purposes = cache_list("guest_purposes", 'purpose_of_visit__name')
    services = cache_list("guest_services", 'service_attended__name')
    statuses = list(GuestStatus.raw_objects.filter(church=church, is_active=True).values_list('name', flat=True).order_by('order'))

    params = request.GET.copy()
    params.pop('page', None)
    query_string = urlencode(params)

    guest_units = _get_guest_units(church)
    guest_memberships = _get_guest_memberships(church)
    guest_users = [
        {
            'id': m.id,
            'full_name': _membership_label(m),
            'unit_name': m.unit.name if m.unit else '',
        }
        for m in guest_memberships
    ]

    context = {
        'page_obj': page_obj,
        'view_type': view_type,
        'users': guest_users,
        'search_query': search_query,
        'status_filter': status_filter,
        'channel_filter': channel_filter,
        'purpose_filter': purpose_filter,
        'service_filter': service_filter,
        'user_filter': user_filter,
        'date_of_visit': date_of_visit_filter,
        'show_filters': True,
        'statuses': statuses,
        'channels': channels,
        'purposes': purposes,
        'services': services,
        'query_string': query_string,
        'svg_icons': SVG_ICONS,
        'excluded_fields': excluded_fields,
        'guest_unit': ({"id": guest_units.first().id, "name": guest_units.first().name} if guest_units.exists() else None),
        'guest_users': guest_users,
        'page_title': 'Guests',
        # Permission booleans — resolved server-side so templates stay clean
        'can_report': _can(request, 'guests.report') or _can(request, 'guests.manage_all'),
        'can_update_status': _can(request, 'guests.manage_all') or _can(request, 'guests.update_status'),
        'guest_statuses': GuestStatus.raw_objects.filter(church=church, is_active=True).order_by('order'),
        'assignable_members': _get_guest_memberships(church),
        # Chat room ID for attach-to-chat (first guests unit room)
        'GUESTS_ROOM_ID': (guest_units.first().chatroom_set.filter(is_default=True).values_list('id', flat=True).first() if guest_units.exists() else None),
    }
    return render(request, 'guests/guest_list.html', context)

@login_required
def guest_detail_api(request, guest_id):
    church = _get_church(request)
    if not church:
        return JsonResponse({"error": "No church context"}, status=403)

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), id=guest_id)

    social_accounts = [
        {
            "platform": account.platform,
            "handle": account.handle,
        }
        for account in guest.social_media_accounts.all()
    ]

    data = {
        "id": guest.id,
        "custom_id": guest.custom_id,
        "title": guest.title,
        "full_name": guest.full_name,
        "phone_number": guest.phone_number,
        "picture": guest.picture.url if guest.picture else None,
        "field_data": _build_field_data(guest),
        "social_media_accounts": social_accounts,
        "svg_icons": SVG_ICONS,
    }

    return JsonResponse(data)

@login_required
def create_guest(request):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    if not _can(request, "guests.manage_all"):
        return HttpResponseForbidden("You do not have permission to create guests.")

    if request.method == 'POST':
        form = GuestEntryForm(request.POST or None, request.FILES or None, user=request.user, church=church, permissions=request.permissions)
        social_media_types = request.POST.getlist('social_media_type[]')
        social_media_handles = request.POST.getlist('social_media_handle[]')
        social_media_entries = []
        errors = []

        for i, (platform, handle) in enumerate(zip(social_media_types, social_media_handles)):
            platform = platform.strip()
            handle = handle.strip()
            if platform and handle:
                if platform not in dict(SocialMediaEntry.SOCIAL_MEDIA_CHOICES):
                    errors.append(f"Invalid social media platform at entry {i+1}.")
                elif len(handle) > 255:
                    errors.append(f"Handle too long at entry {i+1}.")
                else:
                    social_media_entries.append({'platform': platform, 'handle': handle})
            elif platform or handle:
                errors.append(f"Both platform and handle must be provided at entry {i+1}.")

        if form.is_valid() and not errors:
            guest = form.save(commit=False)
            guest.church = church
            guest.save()

            for entry in social_media_entries:
                SocialMediaEntry.raw_objects.create(church=church, guest=guest, **entry)

            if 'save_add_another' in request.POST:
                return redirect('guests:create_guest')
            return redirect('guests:guest_list')

        return render(request, 'guests/guest_form.html', {
            'form': form,
            'social_media_errors': errors,
            'edit_mode': False,
        })

    form = GuestEntryForm(user=request.user, church=church, permissions=request.permissions)
    return render(request, 'guests/guest_form.html', {
        'form': form,
        'edit_mode': False,
        'page_title': 'Add New Guest',
        'can_save': True,
        'social_platform_choices': SocialMediaEntry.SOCIAL_MEDIA_CHOICES,
    })
@login_required
def edit_guest(request, pk):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), pk=pk)
    user = request.user

    if not (_can(request, "guests.manage_all") or _is_assigned_to_requester(guest, request)):
        messages.error(request, "You do not have permission to edit this guest.")
        return redirect('guests:guest_list')

    reassign_allowed = _can(request, "guests.assign_all") or _can(request, "guests.assign_unit")
    all_users = list(_get_guest_memberships(church)) if reassign_allowed else None

    social_media_entries = guest.social_media_accounts.all()

    if request.method == "POST":
        if "delete_guest" in request.POST:
            if not _can(request, "guests.delete"):
                messages.error(request, "You do not have permission to delete this guest.")
                return redirect('guests:guest_list')

            guest.delete()
            messages.success(request, f"{guest.full_name} was deleted successfully.")
            return redirect('guests:guest_list')

        form = GuestEntryForm(request.POST, request.FILES, instance=guest, user=request.user, church=church, permissions=request.permissions)

        social_media_types = request.POST.getlist('social_media_type[]')
        social_media_handles = request.POST.getlist('social_media_handle[]')
        social_media_data = []
        errors = []

        for i, (platform, handle) in enumerate(zip(social_media_types, social_media_handles)):
            platform = platform.strip()
            handle = handle.strip()
            if platform and handle:
                if platform not in dict(SocialMediaEntry.SOCIAL_MEDIA_CHOICES):
                    errors.append(f"Invalid social media platform at entry {i+1}.")
                elif len(handle) > 255:
                    errors.append(f"Handle too long at entry {i+1}.")
                else:
                    social_media_data.append({'platform': platform, 'handle': handle})
            elif platform or handle:
                errors.append(f"Both platform and handle must be provided at entry {i+1}.")

        if form.is_valid() and not errors:
            updated_guest = form.save(commit=False)
            if 'clear_picture' in request.POST and guest.picture:
                guest.picture.delete(save=False)
                updated_guest.picture = None

            updated_guest.save()

            guest.social_media_accounts.all().delete()
            for entry in social_media_data:
                SocialMediaEntry.raw_objects.create(church=church, guest=guest, **entry)

            if 'save_add_another' in request.POST:
                return redirect('guests:create_guest')
            return redirect('guests:guest_list')

    else:
        form = GuestEntryForm(instance=guest, user=request.user, church=church, permissions=request.permissions)

    return render(request, 'guests/guest_form.html', {
        'form': form,
        'guest': guest,
        'edit_mode': True,
        'can_reassign': reassign_allowed,
        'all_users': all_users,
        'show_delete': True,
        'social_media_entries': social_media_entries,
        'page_title': 'Guests',
    })


@login_required
def reassign_guest(request, guest_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), id=guest_id)

    if not (_can(request, "guests.assign_all") or _can(request, "guests.assign_unit")):
        return HttpResponseForbidden("You do not have permission to reassign guests.")

    if request.method == 'POST':
        assigned_to_id = request.POST.get('assigned_to')

        if assigned_to_id:
            assigned_membership = UnitMembership.raw_objects.filter(
                id=assigned_to_id,
                church=church,
                unit_id__in=_get_guest_unit_ids(church),
                is_active=True,
            ).select_related("workforce_member__member__user", "unit").first()
            if assigned_membership:
                guest.assigned_to = assigned_membership
                guest.save()
                user = _membership_user(assigned_membership)
                display_name = user.get_full_name() or user.username if user else "Unknown"
                messages.success(request, f"Guest {guest.full_name} reassigned to {display_name}.")
            else:
                messages.error(request, "Selected assignee does not exist or is inactive.")
        else:
            guest.assigned_to = None
            guest.save()
            messages.success(request, f"Assignment cleared for guest {guest.full_name}.")

    return redirect('guests:guest_list')
@login_required
def import_guests_csv(request):
    church = _get_church(request)
    if not church:
        return redirect('guests:guest_list')

    if not _can(request, "guests.import"):
        messages.error(request, "You do not have permission to import guests.")
        return redirect('guests:guest_list')

    if request.method != "POST" or not request.FILES.get("csv_file"):
        messages.error(request, "Please upload a valid CSV file.")
        return redirect('guests:guest_list')

    csv_file = request.FILES["csv_file"]
    decoded_file = csv_file.read().decode("utf-8").splitlines()
    reader = csv.DictReader(decoded_file)

    guests_to_create = []

    for row in reader:
        username = (row.get("assigned_to", "") or "").strip()
        membership = None
        if username:
            user = User.objects.filter(username=username, church_memberships__church=church).first()
            if user:
                unit_ids = _get_guest_unit_ids(church)
                membership = UnitMembership.raw_objects.filter(
                    church=church,
                    unit_id__in=unit_ids,
                    workforce_member__member__user=user,
                    is_active=True,
                ).select_related("unit", "workforce_member__member__user").first()

        dob = row.get("date_of_birth", "").strip() or None
        dov = parse_flexible_date(row.get("date_of_visit", "").strip() or "")

        status = _find_or_create_status(church, row.get("status", "").strip())
        purpose = _find_or_create_purpose(church, row.get("purpose_of_visit", "").strip())
        channel = _find_or_create_channel(church, row.get("channel_of_visit", "").strip())
        service = _find_or_create_service(church, row.get("service_attended", "").strip())

        guest = GuestEntry(
            church=church,
            full_name=row.get("full_name", "").strip(),
            title=row.get("title", "").strip() or None,
            gender=row.get("gender", "").strip() or None,
            phone_number=row.get("phone_number", "").strip() or None,
            email=row.get("email", "").strip() or None,
            date_of_birth=dob,
            marital_status=row.get("marital_status", "").strip() or None,
            home_address=row.get("home_address", "").strip() or None,
            occupation=row.get("occupation", "").strip() or None,
            date_of_visit=dov,
            purpose_of_visit=purpose,
            channel_of_visit=channel,
            service_attended=service,
            status=status,
            assigned_to=membership,
        )

        guests_to_create.append(guest)

    if guests_to_create:
        GuestEntry.raw_objects.bulk_create(guests_to_create)

    messages.success(request, f"Successfully imported {len(guests_to_create)} guests.")
    return redirect('guests:guest_list')


@login_required
def download_csv_template(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="guest_import_template.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'full_name', 'title', 'gender', 'phone_number', 'email',
        'date_of_birth', 'marital_status', 'home_address', 'occupation',
        'date_of_visit', 'purpose_of_visit', 'channel_of_visit',
        'service_attended', 'status', 'assigned_to'
    ])
    writer.writerow(['', '', '', '', '', '', '', '', '', '', '', '', '', '', ''])

    return response


@login_required
def export_csv(request):
    church = _get_church(request)
    if not church:
        return HttpResponseRedirect('/')

    if not _can(request, "guests.export"):
        return HttpResponseForbidden("You do not have permission to export guests.")

    filter_user_id = request.GET.get('user')
    filter_service = request.GET.get('service')
    search_query = request.GET.get('q')

    guests = _guest_queryset(request)

    if filter_user_id and filter_user_id.isdigit():
        guests = guests.filter(assigned_to__id=filter_user_id)

    if filter_service:
        guests = guests.filter(service_attended__name__iexact=filter_service)

    if search_query:
        guests = guests.filter(
            Q(full_name__icontains=search_query) |
            Q(phone_number__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="guest_entries.csv"'

    writer = csv.writer(response)
    writer.writerow([
        'Full Name', 'Phone Number', 'Email', 'Gender',
        'Date of Birth', 'Marital Status', 'Home Address',
        'Occupation', 'Date of Visit', 'Purpose of Visit',
        'Channel of Visit', 'Service Attended',
        'Status', 'Assigned To'
    ])

    for guest in guests:
        writer.writerow([
            guest.full_name,
            guest.phone_number,
            guest.email,
            guest.gender,
            guest.date_of_birth,
            guest.marital_status,
            guest.home_address,
            guest.occupation,
            guest.date_of_visit,
            guest.purpose_of_visit.name if guest.purpose_of_visit else '',
            guest.channel_of_visit.name if guest.channel_of_visit else '',
            guest.service_attended.name if guest.service_attended else '',
            guest.status.name if guest.status else '',
            _membership_user(guest.assigned_to).get_full_name() if _membership_user(guest.assigned_to) else '',
        ])

    return response


@login_required
def export_guests_excel(request):
    church = _get_church(request)
    if not church:
        return HttpResponseRedirect('/')

    if not _can(request, "guests.export"):
        return HttpResponseForbidden("You do not have permission to export guests.")

    filter_user_id = request.GET.get('user')
    filter_service = request.GET.get('service')
    search_query = request.GET.get('q')

    guests = _guest_queryset(request)

    if filter_user_id and filter_user_id.isdigit():
        guests = guests.filter(assigned_to__id=filter_user_id)

    if filter_service:
        guests = guests.filter(service_attended__name__iexact=filter_service)

    if search_query:
        guests = guests.filter(
            Q(full_name__icontains=search_query) |
            Q(phone_number__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Guests"

    headers = [
        "Full Name", "Phone Number", "Email", "Gender",
        "Date of Birth", "Marital Status", "Home Address",
        "Occupation", "Date of Visit", "Purpose of Visit",
        "Channel of Visit", "Service Attended", "Status", "Assigned To",
    ]
    ws.append(headers)

    for guest in guests:
        ws.append([
            guest.full_name,
            guest.phone_number,
            guest.email,
            guest.gender,
            safe_date(guest.date_of_birth),
            guest.marital_status,
            guest.home_address,
            guest.occupation,
            safe_date(guest.date_of_visit),
            guest.purpose_of_visit.name if guest.purpose_of_visit else '',
            guest.channel_of_visit.name if guest.channel_of_visit else '',
            guest.service_attended.name if guest.service_attended else '',
            guest.status.name if guest.status else '',
            _membership_user(guest.assigned_to).get_full_name() if _membership_user(guest.assigned_to) else '',
        ])

    for col in ws.columns:
        max_length = 0
        column = col[0].column
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[get_column_letter(column)].width = min(max_length + 2, 60)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename="guests.xlsx"'
    wb.save(response)
    return response


@login_required
def import_guests_excel(request):
    church = _get_church(request)
    if not church:
        return redirect('guests:guest_list')

    if not _can(request, "guests.import"):
        messages.error(request, "You do not have permission to import guests.")
        return redirect('guests:guest_list')

    if request.method != "POST" or not request.FILES.get("excel_file"):
        messages.error(request, "Please upload a valid Excel file.")
        return redirect('guests:guest_list')

    excel_file = request.FILES["excel_file"]
    wb = openpyxl.load_workbook(excel_file)
    ws = wb.active

    guests_to_create = []

    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    for row in ws.iter_rows(min_row=2, values_only=True):
        row_data = dict(zip(headers, row))

        username = (row_data.get("Assigned To") or "").strip()
        membership = None
        if username:
            user = User.objects.filter(username=username, church_memberships__church=church).first()
            if user:
                unit_ids = _get_guest_unit_ids(church)
                membership = UnitMembership.raw_objects.filter(
                    church=church,
                    unit_id__in=unit_ids,
                    workforce_member__member__user=user,
                    is_active=True,
                ).select_related("unit", "workforce_member__member__user").first()

        status = _find_or_create_status(church, (row_data.get("Status") or "").strip())
        purpose = _find_or_create_purpose(church, (row_data.get("Purpose of Visit") or "").strip())
        channel = _find_or_create_channel(church, (row_data.get("Channel of Visit") or "").strip())
        service = _find_or_create_service(church, (row_data.get("Service Attended") or "").strip())

        guest = GuestEntry(
            church=church,
            full_name=(row_data.get("Full Name") or "").strip(),
            phone_number=(row_data.get("Phone Number") or "").strip() or None,
            email=(row_data.get("Email") or "").strip() or None,
            gender=(row_data.get("Gender") or "").strip() or None,
            date_of_birth=(row_data.get("Date of Birth") or "").strip() or None,
            marital_status=(row_data.get("Marital Status") or "").strip() or None,
            home_address=(row_data.get("Home Address") or "").strip() or None,
            occupation=(row_data.get("Occupation") or "").strip() or None,
            date_of_visit=parse_flexible_date(row_data.get("Date of Visit")) or None,
            purpose_of_visit=purpose,
            channel_of_visit=channel,
            service_attended=service,
            status=status,
            assigned_to=membership,
        )

        guests_to_create.append(guest)

    if guests_to_create:
        GuestEntry.raw_objects.bulk_create(guests_to_create)

    messages.success(request, f"Successfully imported {len(guests_to_create)} guests.")
    return redirect('guests:guest_list')


@login_required
def followup_report_page(request, guest_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), id=guest_id)
    today = localdate()
    user = request.user

    if not (_can(request, "guests.manage_all") or _is_assigned_to_requester(guest, request)):
        messages.error(request, "You do not have permission to edit this guest.")
        return redirect('guests:guest_list')

    reports = FollowUpReport.raw_objects.filter(church=church, guest=guest).order_by('-report_date')
    paginator = Paginator(reports, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    report_to_edit = None
    edit_report_id = request.GET.get('edit_report_id')
    if edit_report_id:
        report_to_edit = get_object_or_404(FollowUpReport.raw_objects.filter(church=church), id=edit_report_id, guest=guest)

    if request.method == 'POST' and 'submit_report' in request.POST:
        if report_to_edit:
            form = FollowUpReportForm(request.POST, instance=report_to_edit, guest=guest, church=church)
        else:
            form = FollowUpReportForm(request.POST, guest=guest, church=church)

        if form.is_valid():
            form.save()
            messages.success(request, "Report updated successfully." if report_to_edit else "Follow-up report created successfully.")
            return redirect('followup_report_page', guest_id=guest.id)
    else:
        if report_to_edit:
            form = FollowUpReportForm(instance=report_to_edit, guest=guest, church=church)
        else:
            form = FollowUpReportForm(guest=guest, church=church)

    return render(request, 'guests/followup_report_page.html', {
        'guest': guest,
        'reports': reports,
        'page_obj': page_obj,
        'today': today,
        'report_to_edit': report_to_edit,
        'form': form,
    })


@login_required
def followup_history_view(request, guest_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), id=guest_id)
    reports = FollowUpReport.raw_objects.filter(church=church, guest=guest).order_by('-report_date')
    return render(request, 'guests/followup_history.html', {
        'guest': guest,
        'reports': reports,
    })


@login_required
def export_followup_reports_pdf(request, guest_id):
    church = _get_church(request)
    if not church:
        return HttpResponseForbidden("No church context")

    guest = get_object_or_404(GuestEntry.raw_objects.filter(church=church), id=guest_id)
    reports = FollowUpReport.raw_objects.filter(church=church, guest=guest).order_by('-report_date')

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title="Guest Reports")

    styles = getSampleStyleSheet()
    elements = []

    title_style = ParagraphStyle(
        name='Title',
        fontSize=16,
        leading=24,
        alignment=1,
        spaceAfter=20,
    )

    elements.append(Paragraph(f"Follow-Up Report for {guest.full_name}", title_style))
    elements.append(Spacer(1, 10))

    data = [['Date', 'Sunday', 'Midweek', 'Other', 'Message', 'Assigned To']]
    for report in reports:
        assignee = _membership_user(report.assigned_to)
        assigned_to_name = assignee.get_full_name() if assignee else 'Unknown'
        data.append([
            report.report_date.strftime("%Y-%m-%d"),
            'Yes' if report.service_sunday else '',
            'Yes' if report.service_midweek else '',
            'Yes' if report.service_others else '',
            report.note or '',
            assigned_to_name,
        ])

    table = Table(data, colWidths=[80, 60, 60, 60, 220, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor("#000000")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))

    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    return HttpResponse(buffer, content_type='application/pdf', headers={
        'Content-Disposition': f'attachment; filename="followup_reports_{guest.id}.pdf"'
    })


@login_required
def mark_attendance(request):
    return workforce_mark_attendance(request)