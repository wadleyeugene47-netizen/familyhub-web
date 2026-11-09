"""State changes for championships. Every write that affects standings or the bracket goes through here."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Championship, Match, MatchIncident, ResultAudit
from .scheduling import (
    ROUND_ORDER,
    Scheduler,
    configuration_errors,
    create_group_fixtures,
    create_knockout_bracket,
    distribute_teams,
    ensure_groups,
    team_errors,
)
from .standings import championship_tables


class ProgressionConflict(Exception):
    """A change would alter the teams of knockout matches that already have results."""

    def __init__(self, matches):
        self.matches = matches
        names = ", ".join(f"{match.stage_label} ({match.home_label} vs {match.away_label})" for match in matches)
        super().__init__(f"This change affects knockout results that were already recorded: {names}.")


def _require_manager(championship, user):
    if not championship.can_manage(user):
        raise PermissionDenied("Only the championship organizer or an administrator can change it.")


def _lock(championship_id):
    return Championship.objects.select_for_update().get(pk=championship_id)


def snapshot(match):
    return {
        "status": match.status,
        "home_team": match.home_team.name if match.home_team_id else None,
        "away_team": match.away_team.name if match.away_team_id else None,
        "home_score": match.home_score,
        "away_score": match.away_score,
        "home_penalties": match.home_penalties,
        "away_penalties": match.away_penalties,
        "decided_by": match.decided_by,
        "winner": match.winner.name if match.winner_id else None,
        "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
        "field_name": match.field_name,
    }


def audit(championship, *, action, summary, user=None, match=None, before=None, after=None, note=""):
    return ResultAudit.objects.create(
        championship=championship,
        match=match,
        user=user if user is not None and user.is_authenticated else None,
        action=action,
        summary=summary[:255],
        before=before or {},
        after=after or {},
        note=note[:255],
    )


def score_line(match):
    if match.home_score is None:
        return f"{match.home_label} vs {match.away_label}"
    line = f"{match.home_label} {match.home_score}–{match.away_score} {match.away_label}"
    if match.home_penalties is not None:
        line += f" ({match.home_penalties}–{match.away_penalties} on penalties)"
    elif match.decided_by == Match.Decision.EXTRA_TIME:
        line += " (after extra time)"
    elif match.decided_by == Match.Decision.ORGANIZER and match.winner_id:
        line += f" ({match.winner.name} advance by organizer decision)"
    return line


def readiness_errors(championship):
    errors = configuration_errors(championship) + team_errors(championship)
    if not championship.matches.exists():
        errors.append("Generate the fixtures.")
    if championship.location_id is None:
        errors.append("Add the field location.")
    return errors


# Fixtures ---------------------------------------------------------------


@transaction.atomic
def generate_fixtures(championship, *, user):
    championship = _lock(championship.pk)
    _require_manager(championship, user)
    if championship.status in (Championship.Status.COMPLETED, Championship.Status.CANCELLED):
        raise ValidationError("Fixtures cannot be regenerated for a completed or cancelled championship.")
    if championship.matches.filter(status=Match.Status.COMPLETED).exists():
        raise ValidationError("Results have been recorded. Clear them before regenerating fixtures.")
    errors = configuration_errors(championship)
    if championship.first_kickoff_at is None:
        errors.append("Set the first kickoff date and time.")
    if errors:
        raise ValidationError(errors)
    if championship.has_groups:
        distribute_teams(championship)
    else:
        ensure_groups(championship)
    errors = team_errors(championship)
    if errors:
        raise ValidationError(errors)

    championship.matches.all().delete()
    scheduler = Scheduler(championship)
    created = []
    if championship.has_groups:
        created += create_group_fixtures(championship, scheduler)
    if championship.has_knockout:
        created += create_knockout_bracket(championship, scheduler)
    audit(
        championship,
        user=user,
        action=ResultAudit.Action.FIXTURES_GENERATED,
        summary=f"Generated {len(created)} fixtures.",
    )
    sync_progression(championship, user=user)
    return created


@transaction.atomic
def clear_fixtures(championship, *, user):
    championship = _lock(championship.pk)
    _require_manager(championship, user)
    if championship.matches.filter(status=Match.Status.COMPLETED).exists():
        raise ValidationError("Results have been recorded, so the fixtures cannot be cleared.")
    count = championship.matches.count()
    championship.matches.all().delete()
    audit(championship, user=user, action=ResultAudit.Action.FIXTURES_CLEARED, summary=f"Cleared {count} fixtures.")


@transaction.atomic
def publish(championship, *, user):
    championship = _lock(championship.pk)
    _require_manager(championship, user)
    if championship.status != Championship.Status.DRAFT:
        raise ValidationError("Only draft championships can be published.")
    errors = readiness_errors(championship)
    if errors:
        raise ValidationError(errors)
    championship.status = Championship.Status.PUBLISHED
    championship.save(update_fields=("status", "updated_at"))
    audit(championship, user=user, action=ResultAudit.Action.PUBLISHED, summary="Championship published.")
    return championship


@transaction.atomic
def update_team(form, *, user):
    """Save a team edit. Seeds and drawing-of-lots ranks can reorder standings, so progression re-runs."""
    championship = _lock(form.championship.pk)
    _require_manager(championship, user)
    tracked = [name for name in ("name", "seed", "lots_rank", "group") if name in form.changed_data]
    team = form.save()
    if tracked:
        audit(
            championship,
            user=user,
            action=ResultAudit.Action.TEAM_UPDATED,
            summary=f"{team.name}: changed {', '.join(name.replace('_', ' ') for name in tracked)}.",
            after={name: str(form.cleaned_data.get(name)) for name in tracked},
        )
    sync_progression(championship, user=user)
    return team


# Results ----------------------------------------------------------------


def _as_score(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{label} must be a whole number of zero or more.")
    return value


def _decide(match, *, home_score, away_score, extra_time, home_penalties, away_penalties, winner_side):
    """Return (decided_by, winner, home_penalties, away_penalties) after validating knockout rules."""
    has_penalties = home_penalties is not None or away_penalties is not None
    if has_penalties and (home_penalties is None or away_penalties is None):
        raise ValidationError("Enter the penalty shoot-out score for both teams.")
    if has_penalties:
        _as_score(home_penalties, "Home penalties")
        _as_score(away_penalties, "Away penalties")

    if not match.is_knockout:
        if has_penalties or extra_time or winner_side:
            raise ValidationError("Group matches can end in a draw; extra time and penalties are not used.")
        if home_score == away_score:
            return Match.Decision.REGULAR_TIME, None, None, None
        return Match.Decision.REGULAR_TIME, match.home_team if home_score > away_score else match.away_team, None, None

    rule = match.championship.knockout_tie_rule
    if extra_time and rule == Championship.KnockoutTieRule.PENALTIES:
        raise ValidationError("This championship goes straight to penalties; extra time is not played.")
    if home_score != away_score:
        if has_penalties:
            raise ValidationError("Penalties are only used when the score is level.")
        if winner_side:
            raise ValidationError("The winner is decided by the score; leave the organizer decision empty.")
        winner = match.home_team if home_score > away_score else match.away_team
        decided_by = Match.Decision.EXTRA_TIME if extra_time else Match.Decision.REGULAR_TIME
        return decided_by, winner, None, None

    if rule == Championship.KnockoutTieRule.ORGANIZER:
        if has_penalties:
            raise ValidationError("This championship does not use penalties. Choose which team advances.")
        if winner_side not in ("home", "away"):
            raise ValidationError("The score is level. Choose which team advances by organizer decision.")
        return Match.Decision.ORGANIZER, match.home_team if winner_side == "home" else match.away_team, None, None

    if winner_side:
        raise ValidationError("A level knockout match is decided on penalties under this championship's rules.")
    if rule == Championship.KnockoutTieRule.EXTRA_TIME_PENALTIES and not extra_time:
        raise ValidationError(
            "A level knockout match goes to extra time first. Tick 'Extra time played' and enter the score after extra time."
        )
    if not has_penalties:
        raise ValidationError("The score is level. Enter the penalty shoot-out result to decide the winner.")
    if home_penalties == away_penalties:
        raise ValidationError("A penalty shoot-out cannot end level.")
    winner = match.home_team if home_penalties > away_penalties else match.away_team
    return Match.Decision.PENALTIES, winner, home_penalties, away_penalties


def _save_incidents(match, incidents):
    team_ids = {match.home_team_id, match.away_team_id}
    goals = {match.home_team_id: 0, match.away_team_id: 0}
    rows = []
    for item in incidents:
        team_id = item["team_id"]
        if team_id not in team_ids:
            raise ValidationError("Goal and card details must belong to one of the two teams.")
        minute = item.get("minute")
        if minute is not None and (isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute <= 150):
            raise ValidationError("Minutes must be between 0 and 150.")
        incident = MatchIncident(
            match=match,
            team_id=team_id,
            kind=item.get("kind") or MatchIncident.Kind.GOAL,
            player_name=(item.get("player_name") or "").strip(),
            minute=minute,
            assist_name=(item.get("assist_name") or "").strip(),
        )
        incident.full_clean(exclude=["match", "team"])
        if incident.is_goal:
            goals[team_id] += 1
        rows.append(incident)
    if goals[match.home_team_id] > match.home_score or goals[match.away_team_id] > match.away_score:
        raise ValidationError("More goals are listed in the details than the final score allows.")
    match.incidents.all().delete()
    MatchIncident.objects.bulk_create(rows)


@transaction.atomic
def record_result(
    match,
    *,
    user,
    home_score,
    away_score,
    extra_time=False,
    home_penalties=None,
    away_penalties=None,
    winner_side="",
    incidents=None,
    allow_reset=False,
    note="",
):
    championship = _lock(match.championship_id)
    _require_manager(championship, user)
    match = Match.objects.select_for_update().select_related("home_team", "away_team").get(pk=match.pk)
    match.championship = championship
    if championship.status not in (Championship.Status.PUBLISHED, Championship.Status.COMPLETED):
        raise ValidationError("Publish the championship before entering results.")
    if not match.teams_known:
        raise ValidationError("Both teams must be known before a result can be entered.")
    if match.status == Match.Status.CANCELLED:
        raise ValidationError("This match is cancelled. Reinstate it before entering a result.")
    home_score = _as_score(home_score, "Home score")
    away_score = _as_score(away_score, "Away score")
    decided_by, winner, home_penalties, away_penalties = _decide(
        match,
        home_score=home_score,
        away_score=away_score,
        extra_time=bool(extra_time),
        home_penalties=home_penalties,
        away_penalties=away_penalties,
        winner_side=winner_side,
    )

    was_completed = match.is_completed
    before = snapshot(match)
    match.home_score = home_score
    match.away_score = away_score
    match.home_penalties = home_penalties
    match.away_penalties = away_penalties
    match.decided_by = decided_by
    match.winner = winner
    match.status = Match.Status.COMPLETED
    match.result_recorded_at = timezone.now()
    match.result_recorded_by = user
    match.full_clean()
    match.save()
    if incidents is not None:
        _save_incidents(match, incidents)

    after = snapshot(match)
    if was_completed and before == after:
        action = None
    else:
        action = ResultAudit.Action.RESULT_CORRECTED if was_completed else ResultAudit.Action.RESULT_RECORDED
    if action:
        audit(
            championship,
            user=user,
            match=match,
            action=action,
            summary=f"{match.stage_label}: {score_line(match)}",
            before=before,
            after=after,
            note=note,
        )
    reset = sync_progression(championship, user=user, allow_reset=allow_reset)
    return match, reset


@transaction.atomic
def clear_result(match, *, user, allow_reset=False, note=""):
    championship = _lock(match.championship_id)
    _require_manager(championship, user)
    match = Match.objects.select_for_update().select_related("home_team", "away_team").get(pk=match.pk)
    match.championship = championship
    if not match.is_completed:
        raise ValidationError("This match has no result to clear.")
    before = snapshot(match)
    _reset_result(match)
    audit(
        championship,
        user=user,
        match=match,
        action=ResultAudit.Action.RESULT_CLEARED,
        summary=f"{match.stage_label}: result cleared ({before['home_score']}–{before['away_score']}).",
        before=before,
        after=snapshot(match),
        note=note,
    )
    reset = sync_progression(championship, user=user, allow_reset=allow_reset)
    return match, reset


def _reset_result(match):
    match.home_score = None
    match.away_score = None
    match.home_penalties = None
    match.away_penalties = None
    match.decided_by = ""
    match.winner = None
    match.status = Match.Status.SCHEDULED
    match.result_recorded_at = None
    match.result_recorded_by = None
    match.save()
    match.incidents.all().delete()


@transaction.atomic
def update_schedule(match, *, user, kickoff_at, field_name, status, allow_reset=False, note=""):
    championship = _lock(match.championship_id)
    _require_manager(championship, user)
    match = Match.objects.select_for_update().select_related("home_team", "away_team").get(pk=match.pk)
    match.championship = championship
    if status == Match.Status.COMPLETED:
        raise ValidationError("Enter the score to complete a match.")
    if match.is_completed and status != Match.Status.COMPLETED:
        status = Match.Status.COMPLETED
    if status not in Match.Status.values:
        raise ValidationError("Choose a valid match status.")
    before = snapshot(match)
    match.kickoff_at = kickoff_at
    match.field_name = field_name
    match.status = status
    match.full_clean()
    match.save()
    after = snapshot(match)
    if before["status"] != after["status"]:
        audit(
            championship,
            user=user,
            match=match,
            action=ResultAudit.Action.STATUS_CHANGED,
            summary=f"{match.stage_label}: {match.home_label} vs {match.away_label} is now {match.get_status_display().lower()}.",
            before=before,
            after=after,
            note=note,
        )
    elif before != after:
        audit(
            championship,
            user=user,
            match=match,
            action=ResultAudit.Action.SCHEDULE_CHANGED,
            summary=f"{match.stage_label}: {match.home_label} vs {match.away_label} rescheduled.",
            before=before,
            after=after,
            note=note,
        )
    sync_progression(championship, user=user, allow_reset=allow_reset)
    return match


# Progression ------------------------------------------------------------


def _resolve(match, side, tables, matches_by_pk):
    group_id = getattr(match, f"{side}_source_group_id")
    if group_id is not None:
        table = tables.get(group_id)
        return table.team_at(getattr(match, f"{side}_source_rank")) if table else None
    source_id = getattr(match, f"{side}_source_match_id")
    if source_id is not None:
        source = matches_by_pk[source_id]
        if not source.is_completed or source.winner_id is None:
            return None
        if getattr(match, f"{side}_source_outcome") == Match.Outcome.LOSER:
            return source.loser
        return source.winner
    return getattr(match, f"{side}_team")


def _knockout_matches(championship):
    matches = list(
        championship.matches.filter(stage=Match.Stage.KNOCKOUT).select_related("home_team", "away_team", "winner")
    )
    matches.sort(key=lambda match: (ROUND_ORDER.index(match.knockout_round), match.bracket_position))
    for match in matches:
        match.championship = championship
    return matches


def sync_progression(championship, *, user=None, allow_reset=False):
    """Fill knockout slots from final group positions and completed matches.

    Teams are only placed once their source is final. If a slot's team would change on a match that
    already has a result, nothing is changed unless allow_reset is True; then that result is cleared,
    audited, and the change cascades through the later rounds.
    """
    reset = []
    if championship.has_knockout:
        for _attempt in range(len(ROUND_ORDER) + 2):
            tables = {table.group.pk: table for table in championship_tables(championship)} if championship.has_groups else {}
            matches = _knockout_matches(championship)
            by_pk = {match.pk: match for match in matches}
            conflicts = []
            changed = False
            for match in matches:
                home = _resolve(match, "home", tables, by_pk)
                away = _resolve(match, "away", tables, by_pk)
                if home == match.home_team and away == match.away_team:
                    continue
                if match.is_completed:
                    conflicts.append((match, home, away))
                    continue
                match.home_team = home
                match.away_team = away
                match.save(update_fields=("home_team", "away_team", "updated_at"))
                changed = True
            if conflicts and not allow_reset:
                raise ProgressionConflict([match for match, _home, _away in conflicts])
            for match, home, away in conflicts:
                before = snapshot(match)
                _reset_result(match)
                match.home_team = home
                match.away_team = away
                match.save(update_fields=("home_team", "away_team", "updated_at"))
                audit(
                    championship,
                    user=user,
                    match=match,
                    action=ResultAudit.Action.RESULT_INVALIDATED,
                    summary=f"{match.stage_label}: result {before['home_team']} {before['home_score']}–"
                    f"{before['away_score']} {before['away_team']} cleared because an earlier result changed.",
                    before=before,
                    after=snapshot(match),
                )
                reset.append(match)
                changed = True
            if not changed:
                break
    _update_completion(championship)
    return reset


def champion(championship):
    if championship.has_knockout:
        final = championship.matches.filter(
            stage=Match.Stage.KNOCKOUT, knockout_round=Match.Round.FINAL
        ).select_related("winner").first()
        return final.winner if final and final.is_completed else None
    tables = championship_tables(championship)
    if len(tables) == 1 and tables[0].is_complete:
        return tables[0].team_at(1)
    return None


def _is_finished(championship):
    if not championship.matches.exists():
        return False
    if championship.has_knockout:
        return champion(championship) is not None and not championship.matches.filter(
            stage=Match.Stage.KNOCKOUT
        ).exclude(status=Match.Status.COMPLETED).exists()
    return all(table.is_complete for table in championship_tables(championship))


def _update_completion(championship):
    if championship.status not in (Championship.Status.PUBLISHED, Championship.Status.COMPLETED):
        return
    target = Championship.Status.COMPLETED if _is_finished(championship) else Championship.Status.PUBLISHED
    if championship.status != target:
        championship.status = target
        championship.save(update_fields=("status", "updated_at"))
