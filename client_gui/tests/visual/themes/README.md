# Four-theme evidence

Run `capture.mjs` from `client_gui` against a local test server with the current
build and an authenticated test account. Provide the existing Playwright package,
Chromium, and a storage-state file in scratch; never commit credentials.

```text
node tests/visual/themes/capture.mjs http://127.0.0.1:8088/app/designer <absolute-scratch-evidence-dir> <absolute-test-storage-state.json>
```

The script uses the real theme controls and server, captures all four styles at
1440×1000, checks PNG signatures, and records source/image hashes, browser version,
time, and browser errors in `evidence.json`. It restores the test account's original
theme even when capture fails. Use only a test account. No mocked UI or historical
screenshots count as current evidence. A partial manifest is not a complete pass.

Classic starts by opening the settings group before the theme card. Repeat the
command with a test account initially on Classic as well as FlowGate; verify all
four captures and restoration in each run. The script uses stable data attributes,
not translated menu labels.

For ordinary notice regression checks (Node 24+), install locked dependencies and
run `npm run verify:web-notices`. `npm run check:web-notices` checks an existing
build. It prints generated package sections and asserts the 15 emitted root-package
baseline and exact package set, installed versions, D3 closure and locales.
Review intentional bundle changes before updating that baseline.
`scripts/self-check-web.mjs` is only the F86-5 disposable Self-check runner adapter;
it installs dependencies and probes port 8088, and is not the normal CI entry point.

After R4 merge and rebuild, open the sidebar notices in the actual test server.
Click both links and verify HTTP 200, non-HTML content, and byte-for-byte equality
with `dist/THIRD_PARTY_NOTICES.txt` and `dist/LICENSE.txt`. Record the URLs, statuses,
body hashes and browser result alongside the screenshots. Source checks alone do
not satisfy this end-to-end acceptance step.

R7-3 uses an original F/flow-arrow/gate line mark for Modern, in the text color,
instead of the blue four-square mark. The theme preview uses the same geometry.
This visual change is not a determination of third-party design rights (U4).
