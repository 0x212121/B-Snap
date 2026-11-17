# Command Center theme

Choose **Theme → Command Center** in the account menu, or in the mobile navigation.
Light and Dark remain available. The browser stores the choice in `localStorage.theme`;
open tabs synchronize it through the storage event. If storage is unavailable, the
selection still works for the current page.

The theme uses the existing shared components and dark utility variants. It is loaded
through `_branding_theme.html`, including authentication/setup pages. The two MFA
pages retain their light/dark shortcut and use the same theme state.

## Design and maintenance

- Direction: surveillance monitoring for operators; ENERGY 1 / RHYTHM 1 / MOTION 1.
- Navy background, recessed navigation and slate panels separate information levels.
- Cyan identifies primary actions, active navigation and keyboard focus. Green, red,
  amber and neutral status colors retain their meanings and visible text labels.
- Flat borders and 4px panel corners keep dense tables legible. Desktop rows are
  compact; controls keep their existing touch targets. Existing typography is retained
  for readable labels, with tabular numerals for table values and KPIs.
- No new imagery, decorative glow, font dependency, navigation destination or metrics.
- `static/css/command-center.css` defines `--cc-*` tokens and maps them to the existing
  `--ui-*` system. All rules are scoped to `data-theme="command-center"`.
- `static/js/theme.js` owns selection, persistence and `bsnap:themechange`.
- `static/js/theme-charts.js` themes Chart.js labels, axes, grid and tooltips. Dataset
  colors remain intact. Original options are restored by path because Chart.js rebuilds
  scale objects during updates. Load this plugin after Chart.js on new chart pages.

## Verification and delivery gate

Verified on 17 November 2026 using Chrome and isolated Jinja template rendering.
API responses and WebSocket connections were mocked; no database, capture, scheduler,
notification delivery or live camera interaction was exercised. This is scoped frontend
verification, not a claim that the full application suite or every existing workflow passed.

- Build PASS: `npm run build` completed.
- Regression PASS: `node --test app/tests/theme.test.cjs`, four tests covering saved/system
  preferences, desktop/mobile synchronization, storage denial, invalid input, cross-tab
  changes and chart restoration after scale objects are replaced.
- R-02/17/18/23/24/28/36/38 PASS: new source introduces no marketing copy, em dashes,
  fabricated data, assets, FAQ or destinations; existing navigation and branding are reused.
- R-03 PASS: component fixture fits 320/390/768/1440px after Chart.js resize settles;
  actual Cameras template also fits 320px with table scrolling contained.
- R-25 PASS: rendered status contrast: Online 9.28:1, Offline 7.63:1, Warning 10.72:1,
  Unknown 7.08:1. Normal text uses light text on dark panels and dark text on cyan actions.
- R-26/32/39 PASS: labeled native theme selectors change the root theme and synchronize;
  text input accepts typing with visible focus; SweetAlert opens and Escape closes it;
  disabled controls retain disabled states; reduced-motion rules stop decorative animation.
- R-27 PASS: shared empty/error/loading components render with theme tokens; Cameras
  empty state appears for an empty mocked response.
- R-33 PASS: implementation lives in CSS, JavaScript and Jinja source; no source-rewriting
  script is shipped.
- R-34 PASS: desktop selections Command Center → Dark → Light → Command Center work;
  reload restores Command Center; actual chart tick colors restore from cyan-theme muted
  text to Chart.js defaults; dataset color stays unchanged. Login and both MFA pages retain
  Command Center, and MFA shortcuts return to Light.
- R-35 PASS: Chrome click-through covers desktop account menu, all theme options, mobile
  navigation and theme options, language EN → ID → EN, dialog open/type/Escape, Cameras
  More filters, Add Camera, location tab and modal close. Isolated run reports no JavaScript
  exceptions or console errors with API/WebSocket mocks.
- R-37 PASS: user's surveillance-control-room brief supplied the direction before edits.
- R-40 PASS: no new media or third-party dependency is loaded; local theme initialization
  runs before body paint. Performance scores were not measured or claimed.
- Purpose gate PASS (R-01/04/06/07/08/09/10/12/13/14/19/22): flat surfaces support hierarchy;
  existing icons, font, camera content and status badges are retained; the theme adds no
  illustration, gradient, background pattern, glass panel or decorative animation.
- Liveliness PASS: declared 1/1/1 dials match stable layout; page titles and primary actions
  establish hierarchy; spacing separates filters and data; cyan navigation/focus marks and
  precise bordered panels repeat the surveillance-console identity.
- Craftsmanship/quality locks PASS (C-1–C-5; R-05/11/15/16/20/21/29/30/31): existing
  operational composition and workflows are retained, component corners vary by purpose,
  the palette follows the requested identity, and no marketing sections or claims are added.
