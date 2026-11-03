# CA-23 routing and legacy review — 3 November 2026

Reviewed against the production route registry and Python/Jinja references in
B-Snap 2.6.6. This review resolves the duplicate documentation entry, consolidates
Swagger routing, and records deprecation decisions without enabling old views.
Static references do not prove whether a downstream custom deployment imports a
file; retained legacy templates/modules are explicitly marked before deletion.

| URL | Result in 2.6.6 | Access/compatibility |
| --- | --- | --- |
| `/docs` | 307 to `/docs/environment` | One registration; preserves the bookmarked documentation entry |
| `/docs/environment` | Environment reference | Existing documentation access policy; static configuration metadata only |
| `/docs/api` | API reference | Existing documentation page; links to canonical Swagger URL |
| `/docs/security` | Security reference | Existing documentation page |
| `/docs/openapi` | Existing `swagger_embed.html` | Admin dependency checks current user; canonical Swagger URL |
| `/developer/docs` | 307 to `/docs/openapi` | Preserved admin-only bookmark and `embedded_docs` route name |
| `/openapi.json` | FastAPI-generated schema | Existing schema policy preserved |
| `/docs/openapi.json` | 307 to `/openapi.json` | Compatibility with the URL published in older docs |
| `/documentation-index` | 307 to `/documentation/index.html` | Existing generated-documentation bookmark preserved |
| `/documentation/*` | Optional bundled Sphinx output | Static mount when bundled; authenticated 404 when absent, as before |

Previously `/docs` was registered both by `app/routes/docs.py` and `app/main.py`.
The environment handler won by registration order, shadowing a Swagger handler
whose `/static/swagger-ui-bundle.js` file was absent. The duplicate handler/import
is removed. The active `app/routes/dev_docs.py` implementation is consolidated
into the documentation router and the redundant module is removed. Its URL remains
an alias; the existing embedded Swagger template and remote assets remain in use.
Environment docs now describe IP/CIDR proxy peers rather than the old CORS/domain
example, plus PUBLIC_ORIGIN and cookie settings from the shared security loader.

`/stats` still returns a 301 to `/analytics?tab=snapshots`, retaining camera/days
filters with proper URL encoding. Its unreachable legacy rendering block after
`return` was removed. `/insights` still redirects to `/analytics?tab=overview`,
GET `/change-password` to `/profile`, and the legacy password POST remains active.
Stats and Insights data/report APIs remain registered.

| Retained legacy file | Evidence and disposition |
| --- | --- |
| `templates/user_management2.html` | No static production render/include reference. Active UI uses `user_management.html`. Deprecated; do not activate without auth/UI review. |
| `templates/change_password.html` | No active render reference; GET `/change-password` redirects to `/profile`. Retain template for downstream review; preserve password POST compatibility. |
| `templates/insights.html` | No active render reference; `/insights` redirects to Analytics. Insights APIs/reports are active and are not deprecated. |
| `templates/stats.html` | Its sole reference was in the unreachable block removed from `/stats`. Analytics and `/stats/*` data APIs remain active. Retain template pending downstream review. |
| `templates/recipients_table.html` | No static Python/Jinja reference. Active recipient UI uses `recipients.html`. Deprecated partial retained for external/template review. |
| `templates/_orphaned_files_list.html` | No static Python/Jinja reference. Orphan/media APIs remain active; retain this partial pending access/UI review before reuse/removal. |
| `app/routes/2fa.py` | No endpoints registered in the production app. Production MFA enrollment is in `auth.py`. Deprecated standalone flow remains unregistered; do not reactivate it. |
| `app/middleware/real_ip_fix.py` | Not in the production middleware stack and no active import. Deprecated unguarded IP-header override retained for downstream review; use explicit proxy trust. |
| `app/middleware/http_proxy_fix.py` | Not in the production stack and no active import. Deprecated unguarded scheme override retained for downstream review. |

This is not a list of files safe to delete immediately. Templates receive Jinja
deprecation comments and the inactive Python modules receive module docstrings.
No DB schema, historical migration, email flow, evidence retention, or production
data is deleted. Future deletion requires a downstream-reference review and updated
template/route regressions; do not resurrect these views to satisfy a static scan.

Two apparent candidates are **active**: `map_live.py` and `map_heatmap.py` are
included through `maps.router`. `audit.py` and `audit_log.py` also remain active
at distinct API/compatibility paths. They are not classified as unused simply
because `main.py` does not name every nested router or exposes alternate URLs.

Verification includes unique production method/path pairs, documentation redirect
chains, canonical Swagger HTML/schema URLs, anonymous/non-admin denial, retained
bookmarks, encoded legacy stats filters, inactive `/2fa/enable`, nested map routes,
navigation, template compilation, and existing analytics/security regressions.
Tests use dependency overrides and isolated fixtures; application DB startup and
camera/notifier jobs are not run. Serving the existing remote Swagger assets still
depends on the browser's network access, as it did before this consolidation.
Chrome verification loaded those assets and the schema successfully through the
legacy alias; environment reference light/dark and the canonical API link at
390px were inspected without page errors. Generated docs also link this review,
the rollout guide and audit/benchmark reports in their navigation.
