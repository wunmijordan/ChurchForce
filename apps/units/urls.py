from django.urls import path
from units import views

app_name = "units"

urlpatterns = [
    path("",                                                views.unit_list,               name="list"),
    path("create/",                                         views.unit_create,             name="create"),
    path("<int:unit_id>/",                                  views.unit_detail,             name="detail"),
    path("<int:unit_id>/edit/",                             views.unit_edit,               name="edit"),
    path("<int:unit_id>/delete/",                           views.unit_delete,             name="delete"),
    path("<int:unit_id>/members/add/",                      views.add_member_to_unit,      name="add_member"),
    path("<int:unit_id>/members/<int:membership_id>/remove/", views.remove_member_from_unit, name="remove_member"),
    path("<int:unit_id>/members/<int:membership_id>/head/", views.set_unit_head,           name="set_unit_head"),
    path("transfer/",                                       views.transfer_member,         name="transfer_member"),
    path("membership/<int:membership_id>/probation/on/",    views.set_probation,           name="set_probation"),
    path("membership/<int:membership_id>/probation/off/",   views.clear_probation,         name="clear_probation"),
    path("membership/<int:membership_id>/review/clear/",    views.clear_for_review,        name="clear_for_review"),
    path("<int:unit_id>/attendance/",                       views.mark_unit_attendance,    name="mark_attendance"),
    path("campuses/",                                       views.campus_list,             name="campus_list"),
    path("campuses/create/",                                views.campus_create,           name="campus_create"),
    path("campuses/transfer/",                              views.transfer_to_campus,      name="transfer_to_campus"),
    path("<int:unit_id>/ajax/members/",                     views.ajax_unit_members,       name="ajax_members"),
]
