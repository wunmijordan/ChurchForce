from django.urls import path
from core.support_views import support_bot

app_name = "support"

urlpatterns = [
    path("", support_bot, name="support_bot"),
]
