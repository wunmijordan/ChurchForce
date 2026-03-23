from django.urls import path
from tenants import views

app_name = "tenants"

urlpatterns = [
    # Public self-serve signup
    path("signup/", views.signup, name="signup"),

    # Subscription status walls
    # Linked from TenantMiddleware when a subscription lapses.
    # Both are exempt from the subscription guard so lapsed tenants
    # can always reach the upgrade/billing flow.
    path("trial-expired/",          views.trial_expired,          name="trial_expired"),
    path("subscription-inactive/",  views.subscription_inactive,  name="subscription_inactive"),
    # Church admin settings
    path("settings/church/",  views.church_settings, name="church_settings"),
]