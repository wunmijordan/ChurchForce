from datetime import datetime, timedelta
import pytz

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.contrib.auth.models import Group
from django.contrib.auth.views import LoginView
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.utils.timezone import localtime, now

from .forms import CustomUserCreationForm, CustomUserChangeForm, ProfileEditForm
from .models import CustomUser, ChurchMember
from units.models import ChurchUnit, UnitMembership
from workforce.models import AttendanceRecord, ClockRecord
from permissions.models import UnitRole

try:
    from services.models import Event
except ImportError:
    Event = None


User = get_user_model()

DAY_QUOTES = {
    'Monday':    "Start strong — the harvest is plenty!",
    'Tuesday':   "God doesn't call the qualified — He qualifies the called.",
    'Wednesday': "It's the hump of the week. You're not alone in this mission.",
    'Thursday':  "Midweek recharge: reload your artillery.",
    'Friday':    "The weekend is here — prepare for His people.",
    'Saturday':  "Pray. Plan. Prepare for the Sunday harvest.",
    'Sunday':    "Today is the Lord's day — souls are waiting!",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def has_perm(request, perm):
    """Delegate to PermissionResolver via request.permissions."""
    if request.user.is_superuser:
        return True
    perms = getattr(request, "permissions", None)
    return bool(perms and perms.can(perm))


def _church_coordinates(church):
    """
    Return (lat, lon) for a church from Church.latitude / Church.longitude.
    Both fields are mandatory on the Church model — no fallback needed.
    """
    return float(church.latitude), float(church.longitude)


def haversine_distance(lat1, lon1, lat2, lon2):
    import math
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lon / 2) ** 2
    )
    return R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

class CustomLoginForm(AuthenticationForm):
    pass


# apps/accounts/views.py

class CustomLoginView(LoginView):
    form_class = CustomLoginForm
    template_name = "accounts/login.html"

    def form_valid(self, form):
        remember_me = self.request.POST.get("remember_me")
        if not remember_me:
            self.request.session.set_expiry(0)
        else:
            self.request.session.set_expiry(60 * 60 * 24 * 28)
        return super().form_valid(form)

    def get_success_url(self):
        # 1. Get the current church from the request context
        church = getattr(self.request, 'church', None)
        
        # 2. Check if the welcome screen is enabled in settings
        if church and hasattr(church, 'settings'):
            if church.settings.enable_welcome_screen:
                return reverse_lazy("post_login_redirect")
        
        # 3. If disabled or church not found, go straight to dashboard logic
        if has_perm(self.request, "dashboard.admin"):
            return reverse_lazy("dashboard:admin_dashboard")
        return reverse_lazy("dashboard:dashboard")



def post_login_redirect(request):
    church = getattr(request, 'church', None)
    
    # Safety: If admin turned it off, bypass the modal even if they visit the URL
    if church and hasattr(church, 'settings') and not church.settings.enable_welcome_screen:
        if has_perm(request, "dashboard.admin"):
            return redirect("dashboard:admin_dashboard")
        return redirect("dashboard:dashboard")

    tz = pytz.timezone(getattr(church, 'timezone', 'Africa/Lagos'))
    now_in_wat = localtime(now(), timezone=tz)
    today_str = now_in_wat.strftime("%Y-%m-%d")
    day_name = now_in_wat.strftime("%A")
    time_str = now_in_wat.strftime("%I:%M %p")

    if request.session.get("welcome_shown") != today_str:
        request.session["welcome_shown"] = today_str
        request.session.modified = True
        quote = DAY_QUOTES.get(day_name, "Stay faithful — your work in the Kingdom is never in vain.")

        if has_perm(request, "dashboard.admin"):
            dashboard_url = reverse("dashboard:admin_dashboard")
        else:
            dashboard_url = reverse("dashboard:dashboard")

        return render(request, "accounts/welcome_modal.html", {
            "day_name": day_name,
            "time_str": time_str,
            "quote": quote,
            "dashboard_url": dashboard_url,
            "dashboard_label": "Proceed to Dashboard",
        })

    if has_perm(request, "dashboard.admin"):
        return redirect("dashboard:admin_dashboard")
    return redirect("dashboard:dashboard")


# ─────────────────────────────────────────────────────────────────────────────
# Member list
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def user_list(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    search_query = request.GET.get("q", "")

    # Scoped by explicit church filter — safe for multi-tenant
    base_users = CustomUser.objects.filter(
        church_memberships__church=church
    ).distinct()

    if has_perm(request, "accounts.view_all_users"):
        users = base_users
    else:
        user_units = ChurchUnit.raw_objects.filter(
            church=church,
            memberships__workforce_member__member__user=request.user,
        ).distinct()

        users = base_users.filter(
            church_memberships__workforce_profiles__unit_memberships__unit__in=user_units
        ).distinct()

    if search_query:
        users = users.filter(
            Q(full_name__icontains=search_query)
            | Q(username__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(church_memberships__workforce_profiles__unit_memberships__unit__name__icontains=search_query)
        ).distinct()

    view_type = request.GET.get("view", "cards")
    per_page = 50 if view_type == "list" else 45
    paginator = Paginator(users, per_page)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Build enriched item list — role label and units computed server-side
    # so templates never need group/role resolution logic.
    from permissions.services.resolver import PermissionResolver
    from units.models import UnitMembership

    def _role_label(user):
        if user.is_superuser:
            return "Superuser"
        resolver = PermissionResolver(user, church)
        if resolver.can("dashboard.admin"):
            return "Admin"
        # Try to derive from unit roles
        first_role = UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user=user,
            is_active=True,
            is_unit_head=True,
        ).first()
        return "Unit Head" if first_role else "Member"

    def _unit_names(user):
        return list(
            UnitMembership.raw_objects.filter(
                church=church,
                workforce_member__member__user=user,
                is_active=True,
            ).values_list("unit__name", flat=True)
        )

    def _guest_count(user):
        from guests.models import GuestEntry
        membership = UnitMembership.raw_objects.filter(
            church=church,
            workforce_member__member__user=user,
            is_active=True,
        ).first()
        if not membership:
            return 0
        return GuestEntry.raw_objects.filter(
            church=church, assigned_to=membership, is_deleted=False
        ).count()

    enriched_page = [
        {
            "user":        u,
            "role_label":  _role_label(u),
            "units":       _unit_names(u),
            "guest_count": _guest_count(u) if _unit_names(u) else 0,
        }
        for u in page_obj.object_list
    ]

    # Attach enriched list to page_obj for pagination context
    page_obj.enriched = enriched_page

    # Available units for invite modal
    from units.models import ChurchUnit
    available_units = ChurchUnit.raw_objects.filter(
        church=church, is_active=True
    ).order_by("name")

    return render(request, "accounts/user_list.html", {
        "page_obj":        page_obj,
        "view_type":       view_type,
        "search_query":    search_query,
        "page_title":      "Team",
        "available_units": available_units,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Member create / edit
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def manage_user(request, user_id=None):
    """
    Combined create / edit view for church members.

    On creation:
        - form.save() generates credentials (username + temp password)
          if admin left them blank.
        - Credentials are stored in the session and the admin is redirected
          to the credential display page (show_credentials).
        - Credentials are cleared from the session immediately after display.

    On edit:
        - Structural fields (roles, units) are updated by the form.
        - Password change handled separately via SetPasswordForm.
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You don't have permission to manage users.")
        return redirect("accounts:user_list")

    is_edit = user_id is not None
    user_obj = get_object_or_404(CustomUser, pk=user_id) if is_edit else None

    if is_edit:
        in_church = ChurchMember.raw_objects.filter(
            user=user_obj, church=church
        ).exists()
        if not in_church:
            return HttpResponseForbidden("User not in this church.")

    FormClass = CustomUserChangeForm if is_edit else CustomUserCreationForm
    form_kwargs = {
        "data": request.POST or None,
        "files": request.FILES or None,
        "instance": user_obj,
        "current_user": request.user,
        "church": church,
    }
    if is_edit:
        form_kwargs["edit_mode"] = True

    form = FormClass(**form_kwargs)
    password_form = SetPasswordForm(user_obj) if is_edit else None

    if request.method == "POST":

        # ── Delete ────────────────────────────────────────────────────
        if "delete_user" in request.POST and is_edit:
            if user_obj.is_superuser:
                messages.error(request, "Cannot delete a superuser.")
            else:
                name = user_obj.full_name
                user_obj.delete()
                messages.success(request, f"{name} has been removed.")
            return redirect("accounts:user_list")

        # ── Activate / deactivate ─────────────────────────────────────
        elif "deactivate_user" in request.POST and is_edit:
            if user_obj.is_superuser:
                messages.error(request, "Cannot deactivate a superuser.")
            else:
                user_obj.is_active = not user_obj.is_active
                user_obj.save(update_fields=["is_active"])
                status = "activated" if user_obj.is_active else "deactivated"
                messages.success(request, f"{user_obj.full_name} {status}.")
            return redirect("accounts:user_list")

        # ── Password change (edit only) ───────────────────────────────
        elif "change_password" in request.POST and is_edit:
            password_form = SetPasswordForm(user_obj, request.POST)
            if password_form.is_valid():
                password_form.save()
                messages.success(request, f"Password updated for {user_obj.full_name}.")
                return redirect("accounts:user_list")
            else:
                messages.error(request, "Please correct the password errors.")

        # ── Create / update ───────────────────────────────────────────
        else:
            if not request.user.is_superuser:
                form.instance.is_superuser = False

            if form.is_valid():
                saved_user = form.save()

                if is_edit:
                    messages.success(request, f"{saved_user.full_name} updated successfully.")

                    if "save_return" in request.POST:
                        return redirect("accounts:user_list")
                    elif "save_add_another" in request.POST:
                        return redirect("accounts:create_user")

                else:
                    # ── New member: store credentials in session ──────
                    # Credentials are shown ONCE on the next page, then
                    # cleared. Never stored in a message or the URL.
                    # Fetch the ChurchMember to get the custom_id
                    from accounts.models import ChurchMember as CM
                    cm = CM.raw_objects.filter(church=church, user=saved_user).first()

                    request.session["new_member_credentials"] = {
                        "full_name":     saved_user.full_name,
                        "member_id":     cm.custom_id if cm else None,
                        "username":      form.generated_username,
                        "temp_password": form.generated_password,
                    }

                    if "save_add_another" in request.POST:
                        # Show credentials then loop back to create another
                        request.session["credentials_next"] = reverse("accounts:create_user")
                    else:
                        request.session["credentials_next"] = reverse("accounts:user_list")

                    return redirect("accounts:show_credentials")

            else:
                messages.error(request, "Please correct the errors below.")

    # ── Context for GET / invalid POST ───────────────────────────────
    units = ChurchUnit.raw_objects.filter(church=church, is_active=True)

    unit_roles_data = [
        {
            "id": unit.id,
            "name": unit.name,
            "color_class": unit.color_class,
            "color_hex": unit.color_hex,
        }
        for unit in units
    ]

    memberships = (
        list(
            UnitMembership.raw_objects.filter(
                church=church,
                workforce_member__member__user=user_obj,
            ).values("unit_id", "unit__name")
        )
        if is_edit
        else []
    )

    preloadedTeamRoles = {
        "teams": unit_roles_data,
        "userMemberships": memberships,
    }

    return render(request, "accounts/user_form.html", {
        "form": form,
        "edit_mode": is_edit,
        "user_obj": user_obj,
        "password_form": password_form,
        "preloadedTeamRoles": preloadedTeamRoles,
        "page_title": "Team",
    })


@login_required
def show_credentials(request):
    """
    One-time credentials display page shown after creating a new member.

    Reads from session, renders the credentials, then clears them
    immediately so they cannot be seen again by refreshing the page.

    The admin copies/screenshots these to share with the new member
    verbally, via WhatsApp, or any out-of-band channel.

    Template: accounts/credentials.html
    """
    credentials = request.session.pop("new_member_credentials", None)
    next_url = request.session.pop("credentials_next", reverse("accounts:user_list"))

    if not credentials:
        # Credentials already viewed or session expired
        messages.info(request, "No credentials to display — they may have already been viewed.")
        return redirect("accounts:user_list")

    return render(request, "accounts/credentials.html", {
        "credentials": credentials,
        "next_url": next_url,
        "page_title": "New Member Credentials",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Attendance
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def attendance_summary(request):
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    user = request.user
    user_id = request.GET.get("user_id")
    today = timezone.localdate()
    last_30_days = today - timedelta(days=30)

    if has_perm(request, "attendance.view_all") and not user_id:
        total_users = CustomUser.objects.filter(
            church_memberships__church=church,
            is_active=True,
            is_superuser=False,
        ).distinct().count() or 1

        records = AttendanceRecord.objects.filter(
            church=church,
            user__is_superuser=False,
            date__gte=last_30_days,
        )

        present = records.filter(status="present").count()
        excused = records.filter(status="excused").count()
        absent  = records.filter(status="absent").count()

        def pct(v):
            return round((v / total_users) * 100, 1)

        total_clock_in  = ClockRecord.objects.filter(church=church, clock_in__isnull=False,  user__is_superuser=False).count()
        total_clock_out = ClockRecord.objects.filter(church=church, clock_out__isnull=False, user__is_superuser=False).count()

        return JsonResponse({
            "summary": {
                "present": f"{pct(present)}%",
                "excused": f"{pct(excused)}%",
                "absent":  f"{pct(absent)}%",
            },
            "today":  {"clocked_in": False, "clock_in": None, "clock_out": None},
            "totals": {"clock_in": f"{pct(total_clock_in)}%", "clock_out": f"{pct(total_clock_out)}%"},
        })

    target_user = user
    if user_id and has_perm(request, "attendance.view_all"):
        target_user = get_object_or_404(CustomUser, id=user_id)

    records = AttendanceRecord.objects.filter(
        church=church, user=target_user, date__gte=last_30_days
    )
    present = records.filter(status="present").count()
    excused = records.filter(status="excused").count()
    absent  = records.filter(status="absent").count()

    today_clock = ClockRecord.objects.filter(
        church=church, user=target_user, date=today
    ).first()
    clocked_in = today_clock.is_clocked_in if today_clock else False

    total_clock_in  = ClockRecord.objects.filter(church=church, user=target_user, clock_in__isnull=False).count()
    total_clock_out = ClockRecord.objects.filter(church=church, user=target_user, clock_out__isnull=False).count()

    return JsonResponse({
        "summary": {"present": present, "excused": excused, "absent": absent},
        "today": {
            "clocked_in":        clocked_in,
            "attendance_marked": today_clock is not None,
            "clock_in":  today_clock.clock_in.strftime("%H:%M")  if today_clock and today_clock.clock_in  else None,
            "clock_out": today_clock.clock_out.strftime("%H:%M") if today_clock and today_clock.clock_out else None,
        },
        "totals": {"clock_in": total_clock_in, "clock_out": total_clock_out},
    })





@login_required
def clock_action(request):
    church = getattr(request, "church", None)
    if not church:
        return JsonResponse({"success": False, "error": "No church context."}, status=403)

    if not Event:
        return JsonResponse({"success": False, "error": "Events module not available."}, status=500)

    user      = request.user
    today     = timezone.localdate()
    event_id  = request.POST.get("event_id")
    latitude  = request.POST.get("latitude")
    longitude = request.POST.get("longitude")
    action    = request.POST.get("action")

    if action not in ("clock_in", "clock_out"):
        return JsonResponse({"success": False, "error": "Invalid or missing action."}, status=400)

    event = Event.objects.filter(church=church, id=event_id).first()
    if not event:
        return JsonResponse({"success": False, "error": "Event not found."}, status=404)

    clock, _ = ClockRecord.objects.get_or_create(
        church=church, user=user, date=today, event=event
    )

    if action == "clock_out":
        if event.attendance_mode.lower() == "physical":
            try:
                church_lat, church_lon = _church_coordinates(church)
                distance_km = haversine_distance(
                    float(latitude), float(longitude), church_lat, church_lon
                )
                radius_km = float(church.attendance_radius_km)
                if distance_km > radius_km:
                    return JsonResponse({
                        "success": False,
                        "error": "You are outside the allowed range for clock-out.",
                    })
            except Exception:
                return JsonResponse({"success": False, "error": "Unable to verify your location."})

        if clock.clock_out:
            return JsonResponse({"success": False, "error": "You have already clocked out today."})
        clock.mark_clock_out()

    elif action == "clock_in":
        if clock.clock_in:
            return JsonResponse({"success": False, "error": "You have already clocked in today."})
        clock.mark_clock_in()

    return JsonResponse({
        "success":   True,
        "action":    action,
        "clock_in":  clock.clock_in.strftime("%H:%M")  if clock.clock_in  else None,
        "clock_out": clock.clock_out.strftime("%H:%M") if clock.clock_out else None,
    })


def attendance_check(request):
    church   = getattr(request, "church", None)
    user     = request.user
    event_id = request.GET.get("event_id")

    if not church:
        return JsonResponse({"clock_in": False, "clock_out": False})

    clock = ClockRecord.objects.filter(
        church=church, user=user, event_id=event_id
    ).first()

    return JsonResponse({
        "clock_in":  bool(clock and clock.clock_in),
        "clock_out": bool(clock and clock.clock_out),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Invitation system
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def send_invitation(request):
    """
    Send a member invitation email.
    Requires: accounts.manage_users permission.

    POST fields:
        email       — required
        full_name   — optional pre-fill
        suggested_unit — optional unit PK

    On success → creates MemberInvitation, sends email, redirects to user_list.
    """
    church = getattr(request, "church", None)
    if not church:
        return HttpResponseForbidden("No church context.")

    if not has_perm(request, "accounts.manage_users"):
        messages.error(request, "You do not have permission to invite members.")
        return redirect("accounts:user_list")

    if request.method != "POST":
        return redirect("accounts:user_list")

    from django.utils import timezone
    from datetime import timedelta
    from accounts.models import MemberInvitation
    from units.models import ChurchUnit

    email      = request.POST.get("email", "").strip().lower()
    full_name  = request.POST.get("full_name", "").strip()
    unit_id    = request.POST.get("suggested_unit", "").strip()
    msg_text   = request.POST.get("message", "").strip()

    if not email:
        messages.error(request, "An email address is required.")
        return redirect("accounts:user_list")

    # Check if already a member
    if CustomUser.objects.filter(email__iexact=email,
                                 church_memberships__church=church).exists():
        messages.warning(request, f"{email} is already a member of this church.")
        return redirect("accounts:user_list")

    # Check for existing unused invitation
    existing = MemberInvitation.objects.filter(
        church=church, email=email, accepted_at__isnull=True
    ).first()
    if existing and existing.is_valid:
        messages.warning(request, f"A valid invitation already exists for {email}.")
        return redirect("accounts:user_list")

    unit = None
    if unit_id and unit_id.isdigit():
        unit = ChurchUnit.raw_objects.filter(
            church=church, id=int(unit_id), is_active=True
        ).first()

    invitation = MemberInvitation.objects.create(
        church=church,
        email=email,
        full_name=full_name,
        invited_by=request.user,
        suggested_unit=unit,
        message=msg_text,
        expires_at=timezone.now() + timedelta(days=7),
    )

    _send_invitation_email(invitation, request)

    messages.success(request, f"Invitation sent to {email}.")
    return redirect("accounts:user_list")


def accept_invitation(request, token):
    """
    Public view — no login required.
    Handles the invitation link click. Shows and processes the registration form.

    GET  → pre-filled signup form
    POST → create user + ChurchMember, mark invitation accepted
    """
    from accounts.models import MemberInvitation

    try:
        invitation = MemberInvitation.objects.select_related("church", "suggested_unit").get(
            token=token
        )
    except MemberInvitation.DoesNotExist:
        messages.error(request, "This invitation link is invalid.")
        return redirect("tenants:signup")

    if not invitation.is_valid:
        if invitation.is_used:
            messages.info(request, "This invitation has already been used. Please sign in.")
            return redirect("login")
        messages.error(request, "This invitation has expired. Please ask for a new one.")
        return redirect("tenants:signup")

    church = invitation.church
    request.church = church  # allow middleware-dependent logic to work

    if request.method == "POST":
        from accounts.forms import CustomUserCreationForm
        from django.contrib.auth import login
        from django.utils import timezone

        form = CustomUserCreationForm(
            data=request.POST,
            files=request.FILES,
            current_user=None,
            church=church,
        )

        if form.is_valid():
            saved_user = form.save()

            # Mark invitation used
            from django.utils import timezone as tz
            invitation.accepted_at = tz.now()
            invitation.accepted_by = saved_user
            invitation.save(update_fields=["accepted_at", "accepted_by"])

            # Store credentials for one-time display
            request.session["new_member_credentials"] = {
                "full_name":     saved_user.full_name,
                "username":      form.generated_username,
                "temp_password": form.generated_password,
            }
            request.session["credentials_next"] = "/"

            login(request, saved_user,
                  backend="django.contrib.auth.backends.ModelBackend")

            messages.success(
                request, f"Welcome to {church.name}! Your account has been created."
            )
            return redirect("accounts:show_credentials")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = None  # template uses invitation data to pre-fill

    return render(request, "accounts/accept_invitation.html", {
        "invitation": invitation,
        "church":     church,
        "form":       form,
        "page_title": "Accept Invitation",
    })


@login_required
def resend_invitation(request, token):
    """Resend an existing invitation email."""
    church = getattr(request, "church", None)
    if not church or not has_perm(request, "accounts.manage_users"):
        return HttpResponseForbidden("Not allowed.")

    from accounts.models import MemberInvitation
    from django.utils import timezone
    from datetime import timedelta

    try:
        invitation = MemberInvitation.objects.get(token=token, church=church)
    except MemberInvitation.DoesNotExist:
        messages.error(request, "Invitation not found.")
        return redirect("accounts:user_list")

    if invitation.is_used:
        messages.warning(request, "This invitation has already been accepted.")
        return redirect("accounts:user_list")

    # Extend expiry
    invitation.expires_at = timezone.now() + timedelta(days=7)
    invitation.save(update_fields=["expires_at"])

    _send_invitation_email(invitation, request)
    messages.success(request, f"Invitation resent to {invitation.email}.")
    return redirect("accounts:user_list")


def _send_invitation_email(invitation, request):
    """
    Send the invitation email. Uses Django's send_mail.
    Falls back silently if email is not configured.
    """
    try:
        from django.core.mail import send_mail
        from django.conf import settings as django_settings

        accept_url = request.build_absolute_uri(invitation.get_accept_url())
        church_name = invitation.church.name

        body_lines = [
            f"You've been invited to join {church_name} Workforce.",
        ]
        if invitation.message:
            body_lines.append(f"\nMessage from {invitation.invited_by.full_name or 'the admin'}:")
            body_lines.append(invitation.message)

        body_lines += [
            f"\nClick the link below to create your account (expires in 7 days):",
            f"\n{accept_url}",
            f"\nThis link can only be used once.",
        ]

        send_mail(
            subject=f"You're invited to join {church_name} Workforce",
            message="\n".join(body_lines),
            from_email=getattr(django_settings, "DEFAULT_FROM_EMAIL",
                               "noreply@churchforce.io"),
            recipient_list=[invitation.email],
            fail_silently=True,
        )
    except Exception:
        pass  # Email is best-effort — don't crash the request


# ─────────────────────────────────────────────────────────────────────────────
# Member self-service profile edit
# ─────────────────────────────────────────────────────────────────────────────


@login_required
def profile_edit(request):
    """
    Self-service profile editing for the authenticated member.
    Only safe fields are exposed — no access-control fields.
    """
    church = getattr(request, "church", None)
    if not church:
        return redirect("login")

    if request.method == "POST":
        form = ProfileEditForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your profile has been updated.")
            return redirect("accounts:profile_edit")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProfileEditForm(instance=request.user)

    return render(request, "accounts/profile_edit.html", {
        "form":       form,
        "page_title": "Edit Profile",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Self-service profile edit
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def edit_profile(request):
    """
    Member self-service profile editing.
    Only exposes safe fields — never username, email, etc.
    """
    from django import forms as dforms

    church = getattr(request, "church", None)

    class ProfileForm(dforms.ModelForm):
        class Meta:
            model  = CustomUser
            fields = [
                "full_name", "title", "phone_number",
                "date_of_birth", "marital_status", "address", "image",
            ]
            widgets = {
                "full_name":    dforms.TextInput(attrs={"class": "form-control"}),
                "title":        dforms.TextInput(attrs={"class": "form-control"}),
                "phone_number": dforms.TextInput(attrs={"class": "form-control"}),
                "date_of_birth": dforms.DateInput(attrs={"class": "form-control", "type": "date"}),
                "marital_status": dforms.Select(attrs={"class": "form-select"}),
                "address":      dforms.Textarea(attrs={"class": "form-control", "rows": 3}),
                "image":        dforms.ClearableFileInput(attrs={"class": "form-control"}),
            }

    if request.method == "POST":
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:edit_profile")
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = ProfileForm(instance=request.user)

    return render(request, "accounts/edit_profile.html", {
        "form":       form,
        "church":     church,
        "page_title": "Edit Profile",
    })
