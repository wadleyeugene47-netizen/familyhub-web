from django.urls import path

from . import views

app_name = "leagues"

urlpatterns = [
    path("", views.league_list, name="list"),
    path("manage/", views.organizer_leagues, name="organizer_list"),
    path("captain/", views.captain_dashboard, name="captain"),
    path("captain/teams/<int:team_id>/roster/", views.captain_roster, name="captain_roster"),
    path("captain/teams/<int:team_id>/edit/", views.captain_team_edit, name="captain_team_edit"),
    path("captain/teams/<int:team_id>/players/add/", views.captain_player_add, name="captain_player_add"),
    path(
        "captain/teams/<int:team_id>/players/<int:membership_id>/remove/",
        views.captain_player_remove,
        name="captain_player_remove",
    ),
    path("create/", views.league_create, name="create"),
    path("<int:league_id>/", views.league_detail, name="detail"),
    path("<int:league_id>/chat/", views.league_chat, name="chat"),
    path("<int:league_id>/join/", views.league_join, name="join"),
    path("<int:league_id>/manage/", views.league_manage, name="manage"),
    path("<int:league_id>/edit/", views.league_update, name="update"),
    path("<int:league_id>/activate/", views.league_activate, name="activate"),
    path("<int:league_id>/teams/create/", views.team_create, name="team_create"),
    path("<int:league_id>/matches/create/", views.match_schedule, name="match_schedule"),
    path("<int:league_id>/matches/<int:match_id>/result/", views.match_result, name="match_result"),
]
