# tests/visual/gitSettingsMockup — Browser measurements for the Git settings box

flowgate.v02test.0002 R0001 requirement 2, 0010-T §5. Renders the actual
`CheckSettings.vue` Git box and the approved `flowgate_git_settings_mockup.html`
(R0001 attachment, sha256 `effa4c1d…caeb3`) vertically in the same viewport,
then measures dimensions, overlap, and layout. This is not a Vitest spec and
does not run under `npm run test`. It is also excluded from the production bundle.

## Run

```
cd client_gui
node tests/visual/gitSettingsMockup/measure.mjs <output-folder-outside-repository> <flowgate_git_settings_mockup.html>
```

- Bundles `harness.ts` in production mode using this package's vite and
  @vitejs/plugin-vue (the same template compilation as `vite build`), then opens
  it in headless Chromium (Edge, or Chrome if Edge is unavailable).
- No server connection. Values come from the component's `view` prop fixtures.
  Three states: timeout without a cached list (refresh/error, selection disabled),
  successful query, and timeout with a cached list (T-b, 0006-T: selection enabled,
  refresh/error shown).
- Outputs `results.json` (measurements) and `capture_<case>.png` (mockup above,
  the two current implementation states below).
- Exits with code 1 for overlap in the actual box or controls outside its contents.

## Widths

| Case | Outer Git box width | Mockup viewport | Corresponding Inspector width |
| --- | --- | --- | --- |
| wide1026 | 1026 | 1080 | Mockup reference width |
| normal910 | 910 | 964 | 938px (1920px window, default dock) |
| mid570 | 570 | 600 | 598px (approximately 1440px window) |
| narrow392 | 392 | 422 | 420px |
| min236 | 236 | 266 | 264px (minimum) |

The outer Git box width is the Inspector width minus 14px of
`.designer-inspector-body` padding on each side.

## Results in this folder

`results.json` and `capture_*.png` were produced during TR0012 rework by Self-check
`scr_bca5012ec1d64ad7ad4a71ad`, attempt `att_1f97bf3d53458a45e55969c7`, starting
commit `34c096025e234b486c5def4cd8176151bf9a4874` (including uncommitted changes).
Measured file hashes are in `results.json` under `hashes`; that harness used two states.
T-b (TR0013 rev 1) remeasured three states after combining with T-a (Self-check
`scr_067776f9c5a44bc78430a265`, final-tree remeasurement
`scr_45e9f35dd87641639b1aa0c1`). Those artifacts are outside the repository;
measurements and hashes are in TR0013 §4.4.
