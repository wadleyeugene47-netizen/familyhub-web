from django.urls import path

from . import views

app_name = "championships"

urlpatterns = [
    path("", views.championship_list, name="list"),
    path("manage/", views.organizer_championships, name="organizer_list"),
    path("create/", views.championship_create, name="create"),
    path("<int:championship_id>/", views.championship_detail, name="detail"),
    path("<int:championship_id>/live/", views.championship_live, name="live"),
    path("<int:championship_id>/manage/", views.championship_manage, name="manage"),
    path("<int:championship_id>/edit/", views.championship_update, name="update"),
    path("<int:championship_id>/publish/", views.championship_publish, name="publish"),
    path("<int:championship_id>/teams/add/", views.team_add, name="team_add"),
    path("<int:championship_id>/teams/<int:team_id>/edit/", views.team_update, name="team_update"),
    path("<int:championship_id>/teams/<int:team_id>/delete/", views.team_delete, name="team_delete"),
    path("<int:championship_id>/groups/distribute/", views.groups_distribute, name="groups_distribute"),
    path("<int:championship_id>/fixtures/generate/", views.fixtures_generate, name="fixtures_generate"),
    path("<int:championship_id>/fixtures/clear/", views.fixtures_clear, name="fixtures_clear"),
    path("<int:championship_id>/matches/<int:match_id>/result/", views.match_result, name="match_result"),
    path("<int:championship_id>/matches/<int:match_id>/clear/", views.match_clear, name="match_clear"),
    path("<int:championship_id>/matches/<int:match_id>/schedule/", views.match_schedule, name="match_schedule"),
]
