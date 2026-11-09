# Family Hub

One website, one account per person, and many communities inside it. Built as a Django modular monolith for events, registrations, soccer leagues and championships, and Stripe Connect payments.

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

Browse `/` for the homepage. `migrate` creates starter event categories, and you can rename or add more in `/admin/`.

Run the test suite:

```powershell
python manage.py test apps.hub apps.accounts apps.events apps.registrations apps.leagues apps.organizers apps.payments apps.notifications apps.championships apps.messaging apps.communities apps.allstars
```

## Project structure

This repository contains the Django app and frontend assets for the Family Hub platform.

- `apps/` contains the feature modules.
- `templates/` holds page templates.
- `static/` contains CSS and JavaScript assets.
- `manage.py` runs the Django project.

## Deployment

The project is prepared for Render-style deployment with environment configuration and production settings. Review the configuration before shipping to production.

## Notes

This README is intentionally kept valid Markdown for GitHub Pages/Jekyll builds.
