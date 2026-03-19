from django.urls import path
from billing import views, webhook

app_name = "billing"

urlpatterns = [
    path("pay/",             views.initiate_payment, name="initiate_payment"),
    path("callback/",        views.payment_callback, name="payment_callback"),
    path("pricing/",          views.pricing,                    name="pricing"),
    path("upgrade/",          views.upgrade,                    name="upgrade"),
    path("portal/",           views.portal,                     name="portal"),

    # Paystack webhook — CSRF exempt (verified by HMAC signature)
    path("webhook/paystack/", webhook.paystack_webhook,         name="webhook"),
]