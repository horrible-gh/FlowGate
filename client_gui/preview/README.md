# client_gui/preview — Standalone UI previews (fixtures)

flowgate.v02.0051: development pages for inspecting the Inspector components as
actually rendered (0051-R §3.5: distinguish fixtures/previews from real data and functionality).

- No server connection. All values are examples from mockup 0035 v7, not actual
  project data. Buttons are display-only, except the overflow menu's Copy ID
  action, which uses the real clipboard.
- Excluded from the production bundle (`vite build`); its only entry is `index.html`.
- Open after `npm run dev`: `http://127.0.0.1:3001/app/preview/inspector/index.html`.
  - Change language with `?locale=en|ja`.
  - Show one section with `?only=<section id>`: header, chips, slots, review,
    origin, blocks, run-settings, pane, or frame.

| Path | Contents |
| --- | --- |
| `inspector/index.html` | HTML entry |
| `inspector/main.ts` | Mounts with i18n (shared instance + GUI strings) and theme only |
| `inspector/fixtures.ts` | InspectorTarget, slot, and settings fixtures using v7 examples |
| `inspector/PreviewApp.vue` | Section gallery: shared components first, node-specific components and assembled screens in later stages |
