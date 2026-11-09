from django.urls import path

from . import views

app_name = "communities"

urlpatterns = [
    path("", views.community_list, name="list"),
    path("create/", views.community_create, name="create"),
    path("<slug:slug>/", views.community_detail, name="detail"),
    path("<slug:slug>/edit/", views.community_update, name="update"),
    path("<slug:slug>/manage/", views.community_manage, name="manage"),
]
