from django.urls import path

from . import views

app_name = "allstars"

urlpatterns = [
    path("", views.game_list, name="list"),
    path("create/", views.game_create, name="create"),
    path("<int:game_id>/", views.game_detail, name="detail"),
    path("<int:game_id>/register/", views.game_register, name="register"),
    path("<int:game_id>/withdraw/", views.game_withdraw, name="withdraw"),
    path("<int:game_id>/manage/", views.game_manage, name="manage"),
    path("<int:game_id>/edit/", views.game_update, name="update"),
    path("<int:game_id>/status/", views.game_status, name="status"),
    path("<int:game_id>/ratings/", views.game_ratings, name="ratings"),
    path("<int:game_id>/balance/", views.game_balance, name="balance"),
    path("<int:game_id>/players/<int:registration_id>/move/", views.player_move, name="player_move"),
    path("<int:game_id>/players/<int:registration_id>/remove/", views.player_remove, name="player_remove"),
    path("<int:game_id>/teams/<int:team_id>/rename/", views.team_rename, name="team_rename"),
]
