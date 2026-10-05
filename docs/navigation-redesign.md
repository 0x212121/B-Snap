# Navigation audit and redesign

The existing application uses a global header, not a sidebar. The redesign keeps
that layout so operational pages retain their content width.

## Existing navigation

The original header gave six dropdowns the same visual priority:

| Group | Destinations (second level) |
| --- | --- |
| Monitoring | Maps, Snapshots, Videos |
| Devices | Cameras, NVRs, Record Checks, Status |
| Administration | Users, Camera Groups, WA Whitelist, WhatsApp Bot, Email Recipients, Email Templates, Configuration, Job Management, Power BI Record Checks, Trash Management |
| Logs | Logs Viewer, Audit Logs, Email Logs |
| Developer | API Docs, Environment, Documentation |
| Analytics | Dashboard |

The account dropdown contained Profile Settings, API Keys, and Logout. Theme
and language controls were permanently visible beside it.

Icons repeated on both dropdown triggers and destinations; active states used
blue backgrounds, text, icons, and border indicators together. Administration
mixed device organization, notifications, and system settings. Analytics required
opening a dropdown for a single destination. There were no duplicated destination
URLs, but Camera Groups overlapped the Devices task and email logs overlapped
notification configuration. Logs remain together to preserve a clear audit task.

## Implemented hierarchy

| Priority | Location | Destinations |
| --- | --- | --- |
| Primary | Always visible on desktop | Maps, Snapshots, Videos, Analytics |
| Secondary operations | More → Devices | Cameras, NVRs, Camera Groups, Record Checks, Status |
| Notification configuration | More → Notifications | WhatsApp Bot, WA Whitelist, Email Recipients, Email Templates |
| Administration | More → Administration | Users, Configuration, Job Management, Power BI Record Checks, Trash Management |
| Secondary investigation | More → Logs | Logs Viewer, Audit Logs, Email Logs |
| Rare utilities | More → Help & developer | API Docs, Environment, Documentation |
| Account utilities | Account dropdown | Profile Settings, API Keys, theme, language, Logout |
| Contextual actions | Existing page content | Refresh, create/edit/delete, filters, export, report generation |

All entries retain their original URLs and role restrictions. Dashboard is labeled
Analytics to identify its destination without an unnecessary single-item dropdown.
Primary priority is based on the product's monitoring workflow; usage telemetry
was not available for this audit.

The More panel uses group headings and direct links, with no third navigation
level or nested flyouts. Below 1024px, the header exposes a menu button. Its
scrollable menu places primary links first, then secondary accordions and account
utilities. Only the active secondary group opens initially; opening another closes
the previous group. The active destination remains a focusable link with
`aria-current="page"` and a restrained neutral highlight.

## Components and preservation

- `app/utils/template_helper.py`: one destination definition and existing role filtering;
  active routes match the destination or a slash-delimited descendant.
- `templates/macros/navigation.html`: shared `nav_link`, desktop `render_nav`, and
  accordion `render_mobile_nav`.
- `templates/base.html`: existing global header and account menu; preferences move
  inside the desktop account menu. Menu controls expose expanded state and panel
  IDs. Escape closes panels and returns keyboard focus to their triggers.
- `assets/css/tailwind.css`: shared 44px targets, restrained active state, focus
  outlines, light/dark colors, and viewport-constrained panel height.
- `static/js/i18n.js`: translations for the new navigation labels and controls.

Routing, backend authorization, authentication, API integration, data fetching,
page actions, notifications, and page-specific layouts are preserved. No new
frontend dependency is introduced. Dashboard content and page containers remain
outside this global navigation refactor.

## Validation

Seven regression cases check destination coverage for all three roles, control
targets, active links, and route boundaries. Browser checks exercise the rendered
header at 1440, 1024, 768, and 390px, menu toggling, Escape/focus return, accordion
exclusivity, English/Indonesian labels, and light/dark rendering. The preview uses
the actual header template with a minimal page body, without a live database or
camera connection. CSS is rebuilt with `npm run build`.
