from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("create/", views.event_create, name="create"),
    path("manage/", views.organizer_event_list, name="organizer_list"),
    path("<uuid:event_id>/cancel/", views.event_cancel, name="cancel"),
    path("<uuid:event_id>/register/", views.event_register, name="register"),
    path("<uuid:event_id>/", views.event_detail, name="detail"),
    path("<uuid:event_id>/edit/", views.event_update, name="update"),
]
