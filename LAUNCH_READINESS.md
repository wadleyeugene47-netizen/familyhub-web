# Controlled Launch Readiness

Status: the code is prepared for a first launch with payments switched off, but nothing has been deployed. This checklist is for staging verification and a later controlled release; no deployment or live payment action has been performed.

## Going live (payments off)

1. Put the project in a private GitHub repository. `.gitignore` already keeps out `db.sqlite3`, `.env` and uploaded images.
2. Create an account with an email provider, verify your sending domain, and note its SMTP host, username and password.
3. Create an S3-compatible bucket for uploaded images (for example Cloudflare R2) and an access key for it.
4. In Render, create a Blueprint from the repository. It reads `render.yaml`, shows the monthly cost, and asks for the email and storage values. Approve only after reviewing the cost.
5. Open a shell on the `family-hub` service and run `python manage.py check --deploy`. Warnings `hub.W001` to `hub.W003` mean email or image storage is unfinished. `security.W004` is expected until HSTS is switched on.
6. In the same shell, run `python manage.py createsuperuser` to create the owner account.
7. Log in, then paste your reviewed Terms of service, Privacy policy and support email into **Site settings**. The Terms and Privacy pages stay hidden until they have text.
8. Run the staging acceptance tests below that don't involve payments, including a real password reset email and an image upload that survives a redeploy.
9. To use your own domain, add it in Render and add it to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` (with `https://`).

Leave the Payments, Platform fee and Custom domains switches off. The Platform fee and Custom domains switches are not connected to any behavior yet.

## Staging setup

- [ ] Connect this repository to the Render account and review `render.yaml` plan, region, database size, and monthly cost before approving resource creation.
- [ ] Confirm the generated service hostname. Set `ALLOWED_HOSTS` to that exact hostname and `CSRF_TRUSTED_ORIGINS` to its HTTPS origin.
- [ ] Confirm `DEBUG=false`, a generated `DJANGO_SECRET_KEY`, a managed PostgreSQL `DATABASE_URL`, and HTTPS redirect/session/CSRF cookie settings in the deployed environment.
- [ ] Run `python manage.py check --deploy` against the real staging environment. Decide about HSTS subdomains/preload only after confirming every affected hostname is HTTPS-only.
- [ ] Configure database backups and perform a restore test, not just a backup-status check.
- [ ] Configure persistent object storage for event images. Local filesystem uploads on an ephemeral web service do not survive replacement/redeploy reliably.
- [ ] Configure production-grade error reporting, uptime checks, and alerts for application errors, database availability, webhook failures, and email delivery.
- [ ] Replace the console email backend with a verified transactional email provider and test sender-domain authentication and delivery.

## Staging acceptance tests

- [ ] Register a participant, verify login throttling, password reset, logout, and account data isolation.
- [ ] Create an organizer, create/publish/edit/cancel an event, and confirm attendees receive correct update/cancellation messages.
- [ ] Verify unpublished drafts are private and an organizer cannot read or change another organizer's events, payments, or participants.
- [ ] Register for a free event and confirm it creates no payment record.
- [ ] Run two clients against a nearly-full event and verify capacity is never exceeded on PostgreSQL. SQLite testing does not prove PostgreSQL row-lock behavior.
- [ ] Complete Stripe test-mode Connect onboarding for an eligible staging organizer. Verify account status is synchronized and paid events remain blocked until `charges_enabled` is true.
- [ ] Complete Stripe test-mode Checkout. Verify the backend amount/fee/destination, signed webhook confirmation, duplicate webhook idempotency, async failure, and expiry behavior.
- [ ] Test full and partial refunds, transfer reversal, application-fee refund policy, disputes, and payout status events using Stripe test tools.
- [ ] Verify webhook retries/reconciliation and alerting after a deliberately failed test delivery.
- [ ] Upload an event image and confirm it survives an application redeploy using configured object storage.
- [ ] Check mobile and desktop event discovery, registration, organizer dashboard, and payment-return pages for basic usability and accessibility.

## Business and launch approvals

- [ ] Confirm platform country, Stripe Connect availability, account/business eligibility, payment model, processing fees, platform fee, taxes/reporting, refund rules, chargeback responsibility, and payout timing with qualified payment, legal, and accounting professionals.
- [ ] Publish reviewed Terms of Service, Privacy Policy, cancellation/refund policy, organizer eligibility/moderation rules, and data retention/account deletion process.
- [ ] Verify venue authorization, participant safety requirements, and who is responsible for each event operationally.
- [ ] Complete a security review covering file uploads, secrets, access controls, dependency updates, backups, logs, retention, and incident response.
- [ ] Agree on a limited initial cohort, support contact, rollback procedure, and measurable go/no-go criteria before any production traffic.
- [ ] Keep Stripe in test mode throughout staging. Do not add live keys until all preceding payment and business approvals are complete.

## Current limitations to resolve before public launch

- Uploaded images need a bucket configured through the `MEDIA_S3_*` settings. The code path has not been tested against a real bucket.
- The app has only been run on SQLite. PostgreSQL behavior is first exercised on staging.
- There is no way yet for members to edit their profile or delete their account themselves.
- Staging has not been deployed or tested on Render; the hosting account and service hostname are user-controlled.
- Stripe test-mode onboarding, Checkout, and webhook delivery have not been tested against Stripe because credentials are not configured here.
- Payment reconciliation is not yet a scheduled operational process; an operator workflow and alerting must be agreed before handling real payments.
