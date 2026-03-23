# One Codebase
# Multiple Churches
# Isolated Data
# Configurable Modules

churchforce/
    core/          (org, subscription, permissions)
    workforce/     (teams, roles, members)
    guests/        (pipeline, statuses)
    reporting/
    automation/


class Church(models.Model):
    name = models.CharField(max_length=255)
    subdomain = models.CharField(max_length=100, unique=True)
    plan = models.CharField(max_length=20, choices=[
        ("trial", "Trial"),
        ("saas", "SaaS"),
        ("white_label", "White Label"),
    ])
    trial_ends_at = models.DateTimeField(null=True, blank=True)

class Organization(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True)
    subdomain = models.SlugField(unique=True, null=True, blank=True)
    custom_domain = models.CharField(max_length=255, null=True, blank=True)
    is_trial = models.BooleanField(default=True)
    trial_expires_at = models.DateTimeField(null=True, blank=True)
    logo = models.ImageField(upload_to="org_logos/", blank=True, null=True)
    primary_color = models.CharField(max_length=20, default="#206bc4")
    is_white_label = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    plan = models.ForeignKey(SubscriptionPlan, choices=PLAN_CHOICES)
    subscription_active = models.BooleanField(default=False)

    @property
    def is_founder(self):
        return self.plan in [
            "founders_pro_lifetime",
            "founders_white_label_lifetime",
        ]


# Permission JSON
{
  "can_edit_guest": true,
  "can_move_guest_stage": true,
  "can_view_reports": false
}

{
    "guests.view": true,
    "guests.assign": true,
    "guests.delete": false
}


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=50)
    max_workers = models.IntegerField()
    has_guest_module = models.BooleanField(default=True)
    has_reporting = models.BooleanField(default=False)
    has_automation = models.BooleanField(default=False)

PLAN_CHOICES = [
    ("basic", "Basic"),
    ("growth", "Growth"),
    ("pro", "Pro"),
    ("founders_pro_lifetime", "Founders Pro Lifetime"),
    ("founders_white_label_lifetime", "Founders White Label Lifetime"),
]

PLAN_FEATURES = {
    "basic": {
        "max_members": 50,
        "multi_campus": False,
        "advanced_reports": False,
        "sms": False,
        "api_access": False,
        "white_label": False,
    },
    "growth": {
        "max_members": 200,
        "multi_campus": False,
        "advanced_reports": True,
        "sms": False,
        "api_access": False,
        "white_label": False,
    },
    "pro": {
        "max_members": None,
        "multi_campus": True,
        "advanced_reports": True,
        "sms": True,
        "api_access": True,
        "white_label": False,
    },
    "founders_pro_lifetime": {
        "max_members": None,
        "multi_campus": True,
        "advanced_reports": True,
        "sms": True,
        "api_access": True,
        "white_label": False,
    },
    "founders_white_label_lifetime": {
        "max_members": None,
        "multi_campus": True,
        "advanced_reports": True,
        "sms": True,
        "api_access": True,
        "white_label": True,  # 🚀 Enables custom domain & branding
    },
}

🧱 Founder-Level Decision

You can structure white-label setup in tiers:

Tier 1 – Standard White Label ($1,500)

Domain

Branding

Basic onboarding

Tier 2 – Advanced White Label ($3,000+)

Data migration

Workflow customization

Dedicated DB

Extended support

# Feature Check Helper
def has_feature(org, feature):
    return PLAN_FEATURES.get(org.plan, {}).get(feature, False)

# Usage
if not has_feature(request.organization, "advanced_reports"):
    return redirect("upgrade_page")


# White-Label Detection Helper
def is_white_label(org):
    return PLAN_FEATURES.get(org.plan, {}).get("white_label", False)

# Usage in Middleware
if request.host == org.custom_domain:
    if not is_white_label(org):
        raise PermissionDenied("White-label feature not enabled.")
    

# Members Limit Enforcement
max_members = PLAN_FEATURES[org.plan]["max_members"]

if max_members and org.members.count() >= max_members:
    raise ValidationError("Member limit reached. Please upgrade.")


#Middleware Logic
class OrganizationMiddleware:
    def __call__(self, request):
        request.organization = resolved_org

if request.host == "churchforce.com":
    # main domain
    if user is logged in:
        org = request.user.organization
    else:
        show marketing / trial signup

elif request.host ends with ".churchforce.com":
    subdomain = extract_subdomain(request.host)
    org = Organization.objects.get(subdomain=subdomain)

    subdomain = request.get_host().split('.')[0]
    organization = Organization.objects.get(subdomain=subdomain)
    request.organization = organization

if request.host matches organization.custom_domain:
    route to org

    organization = Organization.objects.get(custom_domain=request.host)

from billing.services import validate_white_label_access

validate_white_label_access(org, request.get_host())

# Subscription Bypass Logic
def subscription_valid(org):
    if org.is_founder:
        return True
    return org.subscription_active


<style>
  :root {
    --tblr-primary: {{ request.user.organization.primary_color }};
  }
</style>

<img src="{{ request.user.organization.logo.url }}">


class GuestEntry(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    ...

#Filter
GuestEntry.objects.filter(organization=request.organization)


class Status(models.Model):
    organization = models.ForeignKey(Organization, ...)
    name = models.CharField(...)
    color = models.CharField(...)
    module = models.CharField(...)  # guest, workforce, etc.


from notifications.services import notify_team

notify_team(
    team="media",
    message=f"Setlist for {service.title} is ready. Please prepare slides."
)


# billing/services.py

from django.core.exceptions import PermissionDenied, ValidationError
from .constants import (
    PLAN_FEATURES,
    PLAN_FOUNDERS_PRO,
    PLAN_FOUNDERS_WHITE_LABEL,
)


# ---------------------------------------
# Core Feature Access
# ---------------------------------------

def has_feature(org, feature: str) -> bool:
    """
    Check whether an organization has access to a specific feature.
    """
    return PLAN_FEATURES.get(org.plan, {}).get(feature, False)


# ---------------------------------------
# Founder Plan Helpers
# ---------------------------------------

def is_founder(org) -> bool:
    """
    Check if organization is on any founders lifetime plan.
    """
    return org.plan in [
        PLAN_FOUNDERS_PRO,
        PLAN_FOUNDERS_WHITE_LABEL,
    ]


def is_white_label(org) -> bool:
    """
    Check if organization has white-label capability.
    """
    return has_feature(org, "white_label")


# ---------------------------------------
# Subscription Validation
# ---------------------------------------

def subscription_valid(org) -> bool:
    """
    Determines whether organization subscription is valid.
    Founders plans automatically bypass billing.
    """
    if is_founder(org):
        return True

    return org.subscription_active


def require_active_subscription(org):
    """
    Enforce active subscription at view level.
    """
    if not subscription_valid(org):
        raise PermissionDenied("Active subscription required.")


# ---------------------------------------
# Member Limit Enforcement
# ---------------------------------------

def check_member_limit(org):
    """
    Enforce max member limit based on plan.
    """
    max_members = PLAN_FEATURES.get(org.plan, {}).get("max_members")

    if max_members and org.members.count() >= max_members:
        raise ValidationError("Member limit reached. Please upgrade.")


# ---------------------------------------
# White Label Domain Protection
# ---------------------------------------

def validate_white_label_access(org, request_host):
    """
    Ensure custom domain access is allowed.
    """
    if org.custom_domain and request_host == org.custom_domain:
        if not is_white_label(org):
            raise PermissionDenied("White-label feature not enabled.")






Tenant Isolation
Queue Prioritization
Tenant Caching
Rate Limiting
Task Sharding
Connection Pooling



<!--{% if request.user.is_superuser %}
              <li class="nav-item ms-1">
                <a class="nav-link text-white" href="{% url 'accounts:manage_groups' %}">
                  <span class="nav-link-icon d-md-none d-lg-inline-block">
                    <svg  xmlns="http://www.w3.org/2000/svg"  width="24"  height="24"  viewBox="0 0 24 24"  fill="none"  stroke="currentColor"  stroke-width="2"  stroke-linecap="round"  stroke-linejoin="round"  class="icon icon-tabler icons-tabler-outline icon-tabler-hierarchy">
                      <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
                      <path d="M12 5m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" />
                      <path d="M5 19m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" /><path d="M19 19m-2 0a2 2 0 1 0 4 0a2 2 0 1 0 -4 0" />
                      <path d="M6.5 17.5l5.5 -4.5l5.5 4.5" /><path d="M12 7l0 6" />
                    </svg>
                  </span>
                  <span class="nav-link-title"> Groups </span>
                </a>
              </li>
            {% endif %}-->








class GuestEntry(models.Model):
  
  PURPOSE_CHOICES = [
    ('Home Church', 'Home Church'), ('Occasional Visit', 'Occasional Visit'),
    ('One-Time Visit', 'One-Time Visit'), ('Special Programme Visit', 'Special Programme Visit'),
  ]
  CHANNEL_CHOICES = [
    ('Billboard (Grammar School)', 'Billboard (Grammar School)'),
    ('Billboard (Kosoko)', 'Billboard (Kosoko)'),
    ('Billboard (Ojodu)', 'Billboard (Ojodu)'),
    ('Facebook', 'Facebook'), ('Family & Friends', 'Family & Friends'), ('Flyer', 'Flyer'), ('Instagram', 'Instagram'),
    ('Referral', 'Referral'), ('Self', 'Self'), ('Visit', 'Visit'),
    ('YouTube', 'YouTube'), ('Others', 'Others'),
  ]
  SERVICE_CHOICES = [
    ('Black Ball', 'Black Ball'), ('Breakthrough Campaign', 'Breakthrough Campaign'),
    ('Breakthrough Festival', 'Breakthrough Festival'), ('Code Red. Revival', 'Code Red. Revival'),
    ('Cross Over', 'Cross Over'), ('Deep Dive', 'Deep Dive'), ('Family Hangout', 'Family Hangout'),
    ('Forecasting', 'Forecasting'), ('Life Masterclass', 'Life Masterclass'), ('Love Lounge', 'Love Lounge'),
    ('Midweek Recharge', 'Midweek Recharge'), ('Midyear Praise Party', 'Midyear Praise Party'),
    ('Outreach', 'Outreach'), ('Praise Party', 'Praise Party'), ('Quantum Leap', 'Quantum Leap'),
    ('Recalibrate Marathon', 'Recalibrate Marathon'), ('Singles Connect', 'Singles Connect'),
    ('Supernatural Encounter', 'Supernatural Encounter'),
  ]
  STATUS_CHOICES = [
    ('Planted', 'Planted'),
    ('Planted Elsewhere', 'Planted Elsewhere'), ('Relocated', 'Relocated'),
    ('Work in Progress', 'Work in Progress'),
  ]

  church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='guests', db_index=True)
  picture = CloudinaryField('image', blank=True, null=True)
  custom_id = models.CharField(max_length=20, unique=True, blank=True, null=True, editable=False)
  title = models.CharField(max_length=20, choices=TITLE_CHOICES, blank=False)
  full_name = models.CharField(max_length=100)
  gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=False)
  phone_number = models.CharField(max_length=20, blank=True, null=True)
  email = models.EmailField(blank=True)
  date_of_birth = models.CharField(blank=True, null=True)
  age_range = models.CharField(max_length=20, choices=AGE_RANGE_CHOICES, blank=True)
  marital_status = models.CharField(max_length=20, choices=MARITAL_STATUS_CHOICES, blank=True)
  home_address = models.TextField(blank=True)
  occupation = models.CharField(max_length=100, blank=True)
  date_of_visit = models.DateField(default=localdate)
  purpose_of_visit = models.CharField(max_length=30, choices=PURPOSE_CHOICES, blank=True)
  channel_of_visit = models.CharField(max_length=30, choices=CHANNEL_CHOICES, blank=True)
  service_attended = models.CharField(max_length=50, choices=SERVICE_CHOICES, blank=False)
  referrer_name = models.CharField(max_length=100, blank=True)
  referrer_phone_number = models.CharField(max_length=20, blank=True)
  message = models.TextField(blank=True)
  status = models.ForeignKey(GuestStatus, on_delete=models.PROTECT)

  assigned_to = models.ForeignKey(
    settings.AUTH_USER_MODEL,
    null=True, blank=True,
    on_delete=models.SET_NULL,
    related_name='assigned_guests'
  )
  assigned_at = models.DateTimeField(null=True, blank=True, editable=False)