from django.urls import re_path
from core.support_consumer import SupportBotConsumer

websocket_urlpatterns = [
    re_path(r"ws/support/$", SupportBotConsumer.as_asgi()),
]
