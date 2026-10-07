# B-Snap shared UI standard

## Audit before implementation

| Representative page | Existing inconsistencies | Shared pattern |
| --- | --- | --- |
| Analytics dashboard | Nested container padding; icon beside title; shadow cards; amber report action | Shared shell/header, KPI surfaces, neutral supporting actions |
| Cameras list | Decorated h2 title; blue/green/purple actions; rounded-xl cards; separate table styling | Semantic h1 header, one primary action, shared cards/controls/table |
| User Profile detail | Narrow centered workspace; gradient avatar; colored role categories; custom tabs/forms | Shared header, intentional readable detail width, neutral role badge, shared controls |
| Change Password form | Large top gap; title inside elevated card; emoji heading; bespoke success/error panels | Header before form, readable form width, shared validation and feedback |
| Configuration settings | Custom page padding, control shadows, section icons at 32px, independent tab styles | Shared shell/header/control tokens; preserve its bounded scrolling workspace |

Other lists follow broadly similar patterns but differ in button colors, radii,
table density, and headers. Galleries and documentation add another sticky header
and extra horizontal padding inside the global shell. The map and log viewer are
special workspaces; their canvas/console behavior should remain intact.

## Tokens and layout

`static/css/design-system.css` owns the `--ui-*` tokens and component rules.
Tailwind remains available for layout. New pages should use these shared classes
and `templates/macros/ui.html` rather than inventing competing component styles.

- Space: 4, 8, 12, 16, 24, 32, 40, 48, 64px.
- Shell: maximum 1536px; horizontal padding 16px on mobile, 20px at 768px,
  24px at 1024px, and 32px at 1440px.
- Titles: 26px/semibold for pages; 18px/semibold for sections; 14–16px for
  content; 12px for metadata. KPI numbers may use 28px.
- Radius: 8px for surfaces/dialogs; 6px for controls; 4px for badges.
- Density: 44px controls and minimum 44px table rows. Text-heavy rows may grow.
- Neutral surfaces with thin borders; shadows reserved for floating layers.

Page order: global navigation, page header, contextual tabs, optional summary,
filters, primary content, secondary content, pagination. Operational summaries
may precede filters so critical status remains visible. Forms use a readable
width without narrowing every detail/list page. Maps and console workspaces keep
their interaction-specific layout.

## Shared components

- `page_header(title, description)` uses a caller slot for existing actions;
  `page_heading` also supports metadata. Primary actions belong in that slot.
- `section_header`, `card`, and `kpi_card` provide reusable content grouping.
  `kpi_card(label, value, description='', value_id='', tone='neutral')` keeps
  existing AJAX counter IDs through `value_id`; its caller slot accepts an icon
  from `_icons.html`. Use neutral icons at 20px, with semantic color on the value
  for health/success, warning, and failure. Counts of roles, groups, or locations
  stay neutral. Keep date/time metadata at metadata size rather than KPI number size.
- Buttons: `ui-button` plus `ui-button-primary`, `ui-button-secondary`,
  `ui-button-ghost`, `ui-button-danger`, or `ui-button-danger-quiet`. Use solid red
  for destructive command deletion and red outlines for removing draft nodes/messages.
  One primary per action group;
  export/import/refresh are supporting actions unless they are the main task.
- Status: `status_badge(label, tone)` and `ui-badge-*` for neutral, info, success,
  warning, and critical. Healthy/success = green, warning = amber, error/critical
  = red, inactive = neutral. Role categories are neutral, not health signals.
- Tables: `ui-table` inside `ui-table-scroll`. Preserve sort/selection handlers,
  alignment, bulk controls, and existing pagination renderers. Row actions remain
  on the right. Preserve IDs and data attributes used by scripts.
  Numbered pagination uses `ui-button ui-pagination-button` with a primary active
  page (`aria-current="page"`) and ghost inactive pages. Reuse the shared chevron
  macros for first/previous/next/last controls. Keep the result range above the
  table and the page indicator below it; wrap controls on mobile.
- Filters: `ui-filter-bar` and `ui-control`. Search first, then primary selectors,
  date range and reset when available. Existing immediate/apply behavior remains;
  controls must state when an explicit Apply action is required.
  Cameras and audit logs reuse `more_filters()` for secondary selectors. Native
  disclosure supports keyboard access; `static/js/ui.js` shows a nonzero active
  count and opens prefiltered sections without changing values or sending requests.
- Forms: labels above 44px `ui-control` fields, consistent helper/error text,
  visible focus and `aria-invalid` styling. Textareas may grow. Checkbox/radio,
  file controls, hidden inputs, and third-party internals are not text fields.
- Tabs: `ui-tabs`/`ui-tab`, 44px targets, horizontal scrolling on narrow screens,
  restrained underline. Preserve existing tab state and keyboard handlers.
- Feedback: `empty_state`, `error_state`, and `loading_skeleton` macros. Empty
  states explain the next step; errors have a recovery slot; loaders preserve
  structure. Existing AJAX errors use Toast; confirmation uses themed SweetAlert2.

Reusable macros escape labels/descriptions by default. Trusted template markup
belongs in caller blocks, not `safe` filters applied to user content. Components
never invoke APIs or change permissions. Status labels always accompany colors.

## Migration and exceptions

Shared classes cover static markup and existing HTML generated by page scripts.
Page-specific IDs, event handlers, form names/actions, tab bindings, route URLs,
and pagination behavior are preserved. Complex editors, previews, galleries,
map popups, authentication screens, and operational alerts retain specialized
structure where it serves the workflow. Do not flatten their semantic colors or
style third-party Leaflet/SweetAlert internals with general control rules.

Sign-in, verification, MFA setup, and initial admin setup retain focused centered
layouts while sharing typography, control, button, card, and focus tokens.

The unused `user_management2.html` legacy view is outside the migration; it is not
routed and already has malformed Jinja and duplicate scripts blocks. The active
user-management view uses the shared system. The changelog/version utility is in
account navigation so a fixed badge cannot obscure form actions.

Validate representative dashboard, list, detail, form, and settings pages in
light/dark mode and at desktop/tablet/mobile sizes after changes. Template
rendering and browser previews supplement component regression checks; these
do not replace end-to-end validation against live cameras and production data.

## Creating a page

```jinja
{% extends "base.html" %}
{% from "macros/ui.html" import page_header, status_badge, more_filters %}
{% block content %}
  {% call page_header("Devices", "Manage device availability.") %}
    <button type="button" class="ui-button ui-button-primary">Add device</button>
  {% endcall %}
  <div class="ui-filter-bar">
    <label for="device-search" class="ui-label block">Search</label>
    <input id="device-search" type="search" class="ui-control w-full">
    {% call more_filters() %}
      <select class="ui-control" aria-label="Location">
        <option value="">All Locations</option>
      </select>
    {% endcall %}
  </div>
  {{ status_badge("Healthy", "success") }}
{% endblock %}
```

Connect existing page handlers to controls explicitly; the shared macros never
perform application actions. Use the existing numbered pagination renderer for
paginated results instead of creating a separate previous/next-only control.

## WhatsApp Bot Builder

The command editor uses four keyboard-accessible tabs: command settings, action/API, responses, and preview/execution. The footer stays visible while the form scrolls. Saving a command persists it immediately; the separate API settings button saves the selected token. Unsaved command edits require confirmation when closing, and pending command/token changes trigger the browser navigation warning.

Sample previews use the production template renderer with editable JSON data. The preview endpoint never executes a bot action, calls an API node, looks up snapshot files, or delivers media. Chat bubbles show configured processing messages followed by success or fallback, with media source metadata. Processing messages can be skipped at runtime when an action finishes before its delay. Live execution requires a whitelisted sender and an explicit confirmation with the action, arguments, and API paths.

Command filters cover names, triggers, aliases, status, and action. Access summaries show roles and chat scope. Message monitor filters operate on the latest 100 messages, using phone, command, status, and local time bounds. Details show full messages/errors as escaped text. Refresh failures preserve the previous rows and display the last successful update time.

Conversation previews appear automatically alongside the response editor (below it on mobile), and update after edits. The same chat preview is also available in the execution tab. Editable sample JSON is collapsed by default.
