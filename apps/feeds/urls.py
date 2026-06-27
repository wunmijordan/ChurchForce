from django.urls import path
from feeds import views

app_name = "feeds"

urlpatterns = [
    path("list/",                      views.feed_list,           name="list"),
    path("create/",                    views.feed_create,         name="create"),
    path("<int:feed_id>/react/",       views.feed_react,          name="react"),
    path("<int:feed_id>/comment/",     views.feed_comment,        name="comment"),
    path("<int:feed_id>/comments/",    views.feed_comments,       name="comments"),
    path("<int:feed_id>/repost/",      views.feed_repost,         name="repost"),
    path("<int:feed_id>/pin/",         views.feed_pin,            name="pin"),
    path("<int:feed_id>/delete/",      views.feed_delete,         name="delete"),
    path("notifications/",             views.feed_notifications,  name="notifications"),
    path("notifications/count/",       views.feed_notif_count,    name="notif_count"),
    path("units/",                     views.feed_units,          name="units"),
]
