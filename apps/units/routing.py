from django.urls import re_path
from units.consumers import UnitConsumer

websocket_urlpatterns = [
    re_path(r"^ws/units/(?P<unit_id>\d+)/$", UnitConsumer.as_asgi()),
]