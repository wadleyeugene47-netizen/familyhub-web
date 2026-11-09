from django.urls import path

from apps.accounts import owner_views as member_views
from apps.events import owner_views as event_views
from apps.leagues import host_views as league_views
from apps.messaging import views as message_views

from . import views

app_name = "hub"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("settings/", views.site_settings, name="settings"),
    path("features/<slug:key>/", views.toggle_feature, name="toggle_feature"),
    path("lists/<slug:key>/", views.choice_list, name="choice_list"),
    path("members/", member_views.members, name="members"),
    path("members/<int:user_id>/decide/", member_views.member_decide, name="member_decide"),
    path("phones/", member_views.phones, name="phones"),
    path("phones/<int:pk>/delete/", member_views.phone_delete, name="phone_delete"),
    path("events/", event_views.event_review, name="event_review"),
    path("events/<uuid:event_id>/decide/", event_views.event_decide, name="event_decide"),
    path("leagues/", league_views.league_list, name="leagues"),
    path("leagues/<int:league_id>/", league_views.league_panel, name="league"),
    path("leagues/<int:league_id>/edit/", league_views.league_edit, name="league_edit"),
    path("leagues/<int:league_id>/start/", league_views.league_start, name="league_start"),
    path("leagues/<int:league_id>/teams/", league_views.team_add, name="league_team_add"),
    path("leagues/<int:league_id>/teams/<int:team_id>/", league_views.team_edit, name="league_team_edit"),
    path("leagues/<int:league_id>/teams/<int:team_id>/delete/", league_views.team_delete, name="league_team_delete"),
    path("leagues/<int:league_id>/players/", league_views.player_add, name="league_player_add"),
    path("leagues/<int:league_id>/requests/<int:request_id>/", league_views.join_request_decide, name="league_join_decide"),
    path("leagues/<int:league_id>/players/<int:membership_id>/captain/", league_views.player_captain, name="league_player_captain"),
    path("leagues/<int:league_id>/players/<int:membership_id>/remove/", league_views.player_remove, name="league_player_remove"),
    path("leagues/<int:league_id>/rounds/", league_views.round_generate, name="league_round"),
    path("leagues/<int:league_id>/games/<int:match_id>/score/", league_views.match_score, name="league_score"),
    path("leagues/<int:league_id>/games/<int:match_id>/cancel/", league_views.match_cancel, name="league_match_cancel"),
    path("leagues/<int:league_id>/chat/<int:message_id>/delete/", league_views.chat_delete, name="league_chat_delete"),
    path("messages/", message_views.inbox, name="messages"),
    path("messages/<int:user_id>/", message_views.thread, name="message_thread"),
    path("messages/<int:user_id>/poll/", message_views.thread_poll, name="message_poll"),
]
