<<<<<<< HEAD
# FAMILYHUB.github.io.
=======
# Family Hub

One website, one account per person, and many communities inside it. Built as a Django modular monolith: events, registrations, soccer leagues and championships, and Stripe Connect payments.

## Local development

Local development uses SQLite and Django's console email backend. Stripe credentials and SMTP are optional locally.

Activate the virtual environment in PowerShell, install dependencies, and initialize the local database:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py check
python manage.py runserver
```

Browse `/` for the homepage. `migrate` creates six starter event categories (Soccer & Sports, Parties, and so on); rename or add more in `/admin/`. To fill a local database with sample events, run `python manage.py seed_demo` (and `python manage.py seed_demo --clear` to remove them). Run the tests with:

```powershell
python manage.py test apps.hub apps.accounts apps.events apps.registrations apps.leagues apps.organizers apps.payments apps.notifications apps.championships apps.messaging apps.communities apps.allstars
```

## Owner dashboard

Create your owner account with `python manage.py createsuperuser` and log in: you land in the **Host panel** (`/owner/`), a separate area with its own sidebar that members never see. From there you can:

- **Switch features on or off.** Payments, the platform fee and custom domains are built but start switched off. While payments are off, checkout and Stripe setup are hidden and blocked.
- **Edit site settings:** the site name shown on every page, the tagline, the default platform fee (3%) and the support email.
- **Run community leagues** (`/owner/leagues/`), such as Brothers and Goals: add teams and what each captain pays, put accepted members on teams, create the next round automatically (round-robin), and type in scores; the standings update by themselves. Members see their team, the rosters, supporters (members not playing) and a league group chat.
- **Chat with members** (`/owner/messages/`): each member has a private chat with you, opened from **Message the host** in their account menu. New messages appear without reloading.
- **Approve members** (`/owner/members/`): new sign-ups wait on a "waiting for approval" page until you accept them; you get an email for each one.
- **Pre-approve people by phone** (`/owner/phones/`): whoever signs up first with a number on your list joins right away.
- **Approve events** (`/owner/events/`): events members publish wait for you to approve or decline them, and you can cancel any upcoming event. Your own events publish directly.
- **Edit option lists:** tournament formats, who can play, gender and positions. Add, rename, reorder or hide options; an option's stored value never changes once created, so past answers stay valid.

### Adding a module (plugin-ready)

A new feature lives in its own Django app under `apps/`. To make it switchable, register it in the app's `AppConfig.ready()`:

```python
from apps.hub.registry import register_feature
register_feature("my_feature", "My feature", "What it does.")
```

After `migrate`, it appears switched off in the owner dashboard. Check it in code with `apps.hub.features.is_enabled("my_feature")` or in templates with `{% if "my_feature" in features_on %}`.

## Accounts

Each person signs up once with first and last name, email, phone and a username, and can log in with either the username or the email. An email address can only belong to one account.

## Soccer championships

`apps/championships` runs tournaments end to end: group stage, knockout, or groups followed by knockout. After pulling this feature, apply its migration:

```powershell
python manage.py migrate championships
```

Organizer workflow (`/championships/manage/`):

1. Create a championship: format, team and group counts, qualifiers per group, points (3/1/0 by default), tie-breaker order, how level knockout matches are decided, kickoff schedule, fields, venue and optional entry fee.
2. Register teams (one at a time or several at once). Optionally set seeds or pick groups; **Distribute into groups** snake-drafts by seed.
3. **Confirm teams & generate fixtures** builds the round-robin group schedule and the whole knockout bracket. Bracket slots point at group positions ("Group A · 1st") or earlier matches ("Winner of Semi-final 1").
4. Publish, then enter scores after each match. Goal and card details are optional.

How the moving parts work:

- `standings.py` calculates every table from completed match rows on each request; there are no stored totals. Cancelled matches are ignored, postponed ones keep a group open.
- `services.py` is the only place results change. Each write runs in a transaction, records a `ResultAudit` row (who, when, before/after, optional reason) and then calls `sync_progression`, which fills knockout slots once their source group is complete or their source match has a winner.
- Corrections that would change the teams in a knockout match that already has a result are refused until the organizer confirms. Those results are then cleared and audited, and the change flows through to later rounds.
- Level knockout matches need penalties, extra time then penalties, or an explicit organizer decision, depending on the championship's rule. The winner is never inferred from a level score.
- Teams level on every tie-breaker are flagged, and their bracket slot stays empty until the organizer records a drawing-of-lots rank on each team.
- Entry fees reuse the existing paid-event checkout: link a paid event as the entry fee event, and teams register through that event's Stripe flow.
- The public page polls `/championships/<id>/live/` every 30 seconds and offers a refresh when results change.

Only the organizer, superusers and users with the `championships.manage_any_championship` permission can change a championship. The Django admin shows matches and the audit log read-only so that every result change goes through the services.

## Communities

`apps/communities` gives each group a public page (`/communities/<slug>/`) listing its leagues, tournaments, All-Star games and upcoming events. Any approved member can start one. Its organizer and the site owner run it from the community dashboard (`/communities/<slug>/manage/`). Leagues, tournaments and events join a community through the optional **Community** field on their forms, which only offers the organizer's own communities.

## Captain dashboard

`/leagues/captain/` shows each team a member captains: table position, record, team fee, squad, next games and recent results. While the league is running, captains can add members who aren't on a team in that league, remove players, set shirt numbers, and change the team's name and short name. Team fees, the paid mark, choosing the captain and deleting a team stay with the host.

## All-Star games

`apps/allstars` runs registration and team balancing. The organizer's workflow, from `/all-star/<id>/manage/`:

1. Create the game in a community and open registration. Members sign up with a position and gender.
2. Close registration and rate every player from 1 to 5. Ratings are only ever shown to the organizer and the site owner.
3. **Balance teams.** `balancer.py` keeps team sizes within one player, spreads each position evenly, then swaps like-for-like players until the rating totals are as close as those rules allow. The same players always produce the same teams.
4. Rename teams or move players by hand if needed, then **Announce teams**. Signed-in members can then see the rosters.

## Design system

All pages extend `templates/base.html`, which holds the navigation, toast messages and footer.

- `static/gatherplay/css/gatherplay.css`: design tokens (colors, type, spacing, radii, shadows) and every component, grouped by numbered section.
- `static/gatherplay/js/gatherplay.js`: mobile menu, scroll reveals, card tilt, hero parallax, toast dismissal and submit loading states. Motion is disabled for visitors who prefer reduced motion.
- `templates/partials/`: the SVG icon sprite, event card and form field partials.
- `apps/events/themes.py`: maps each category to a color theme and icon (sports, party, outdoor, social, community, other) by keywords in its slug or name.

## Deployment preparation

`render.yaml` defines a staging web service and managed PostgreSQL database. Review the plan, region, database size, and cost in your Render account before approving resource creation. Staging is not deployed by this repository.

Before deployment, configure the generated hostname in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`; add Stripe test-mode keys using the hosting provider's secret settings; and run `python manage.py check --deploy` against the real staging environment. Do not commit `.env` or credentials.

See [LAUNCH_READINESS.md](LAUNCH_READINESS.md) for staging acceptance tests, operational gaps, and production go/no-go requirements. Images still require persistent object storage, and Stripe staging flows have not been tested with live service credentials.
>>>>>>> 0454ae5 (initial project upload)
