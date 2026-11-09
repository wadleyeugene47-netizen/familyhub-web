from django.urls import path

from . import views

app_name = "messaging"

urlpatterns = [
    path("", views.member_chat, name="chat"),
    path("poll/", views.member_poll, name="poll"),
]
