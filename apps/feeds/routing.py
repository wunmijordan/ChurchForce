from django.urls import re_path
from feeds import consumers

websocket_urlpatterns = [
    re_path(r"ws/feeds/$", consumers.FeedConsumer.as_asgi()),
]
