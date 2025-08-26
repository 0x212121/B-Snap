# Maintenance & Troubleshooting

## Database
- Backup with `pg_dump`.
- Restore with `psql`.

## Logs
- B-SNAP logs in `logs`.
- n8n logs via its UI.

## Common Issues
- **DB migration error** → check Alembic `versions/`.
- **WhatsApp not receiving messages** → check n8n webhook URL & logs.
