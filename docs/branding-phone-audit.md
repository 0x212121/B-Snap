# Initial branding and telephone impact audit

This is an initial source audit, not a complete inventory or a deployment plan.
No production database was inspected or modified.

| Priority | Area and locations | Current behavior and required follow-up |
| --- | --- | --- |
| High | `app/models/whitelist.py`, `app/schemas/whitelist_schema.py`, `app/routes/whitelist.py` | Unique whitelist values are stored as strings; create and CSV import restrict Indonesian formats. Introduce canonical E.164 only with collision detection and compatible lookup for existing values. |
| High | `app/routes/wa_webhook.py`, `app/api/whatsapp_routes.py`, `app/utils/wa_bot_commands.py`, `app/utils/wa_gateway.py` | Sender and scheduled execution identity are used for authorization. GoWA uses digit-only destinations; preserve that boundary and test legacy and canonical lookups before changing storage. Bot whitelist actions have a separate Indonesian regex. |
| High | `templates/whitelist.html`, `templates/wa_bot_builder.html`, `templates/config.html` | Inputs, JavaScript validation, import guidance and scheduled/default recipients need a country-aware interface. Group JIDs must remain distinct from telephone numbers. |
| High | `templates/base.html`, `templates/login.html`, `templates/setup.html`, `templates/login_2fa.html`, `templates/force_mfa_setup.html` | These page shells now use central branding for their logos and custom favicon. Authentication and setup still require end-to-end browser testing. |
| High | `app/routes/config.py`, `app/core/branding.py`, `app/core/branding_assets.py`, `docker-compose.yml` | Name, login tagline and selected application logo use validated settings with administrator access and auditing. Map and application uploads have separate files/previews in the persistent branding volume; legacy map/report files are preserved. Other branding settings need follow-up. |
| Medium | `templates/maps.html`, page title blocks, `templates/sla_report.html` | The map heading deliberately uses its own map logo and map title, independent of application branding. Other page/report identity and page-specific theme colors still need follow-up. |
| Medium | `app/utils/email_helper.py`, `app/utils/wa_monitor.py`, `app/utils/record_check.py`, `app/utils/insights_report_locale.py` | GoWA outage emails and Insights/health PDF identity are configurable. Other email, WhatsApp messages, report translations and filenames still contain branding. Changing them can affect user-managed templates and external consumers. |
| Medium | `app/main.py`, `app/core/security.py`, `pyproject.toml` | OpenAPI metadata and package/project links use product identity; security origin and proxy configuration are separate operational settings and must not be silently changed by rebranding. |

`app/utils/phone_numbers.py` provides a libphonenumber-based E.164 parser. New
whitelist entries, CSV imports and bot whitelist actions use it and store new
numbers with `+`. Existing digit-only entries remain readable by webhook and
whitelist authorization paths; GoWA delivery continues to use digit-only values.
The whitelist form supplies an ISO country; CSV may use a `country` column
(default `ID`). Local numbers use that region. For compatibility, digit-only
international input is accepted when it is not valid as a local number; use `+`
to remove ambiguity. CSV exports preserve the stored format. Existing
allow-check, whitelist management, camera resolver and webhook sender checks
accept common formatting while rejecting letters, misplaced plus signs and
invalid `+0` prefixes. Webhook device/JID suffixes retain their existing handling.
This lookup does not use libphonenumber validity to reject legacy records.
Existing database values are not rewritten. Migration `20261103_wa_phone_unique`
adds a unique index across digit-only and plus-prefixed values. Back up the target
database before upgrading; inspect duplicates with
`SELECT ltrim(phone_number, '+'), count(*) FROM whatsapp_whitelist GROUP BY 1 HAVING count(*) > 1`.
Resolve any collisions manually before retrying: the migration fails instead of
merging records. Its downgrade drops the index and leaves all values untouched.
Old application versions may not recognize new plus-prefixed entries; restore a
compatible database backup or separately plan their conversion before rolling back
the application. Existing API and CSV consumers must handle both formats. Test
GoWA inbound and outbound behavior with the deployed gateway version; isolated
tests do not send live messages.

The audit has not established all integrations, documents, assets, metadata,
database contents or authentication flows. Full rebranding, remaining phone selectors, data
migration, broader API changes and end-to-end verification remain outstanding.
