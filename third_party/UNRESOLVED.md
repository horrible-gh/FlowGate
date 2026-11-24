# Unresolved notice evidence

This file is an evidence gap register, not a replacement license text.

## Required follow-up card handoff (not registered or completed)

The integration/planning owner must create and link follow-up cards from these work packages. These local handoff keys are not FlowGate card IDs. No follow-up card creation API was used in this worker.

| Handoff key | Required input and work | Completion evidence |
| --- | --- | --- |
| FOLLOWUP-R6-RUST | Obtain the audit's four original Cargo.lock files, paths and hashes plus matching build outputs; enumerate each crate/version/source/license and actual link target. | Reconcile all four locks and the audit's 244 rows; link original license texts and actual linked artifacts, or document evidence-backed exclusions. |
| FOLLOWUP-R6-NOTICES | Obtain exact-version permission texts for the three npm packages below and actual Rollup module graph; obtain versions, notices and corresponding source/relinking evidence for the 15 psycopg2 bundled libraries. | Each distributed component has all five ledger fields and original full text; development-only exclusions use build evidence. |
| FOLLOWUP-VERIFY | Restore self-check and run `python3 server/tests/architecture/test_0086_third_party_notices.py`; use the root ledger's Docker distribution procedure in an authorized Docker/Syft environment. | TS/TSR records actual exit codes, build logs, source/output IDs, image LICENSE/notice reads and three SBOM evidence JSON files. No PASS from source review alone. |

Until these are closed, the all-row/full-text criterion and actual-image SBOM acceptance criterion remain unmet.


- `@napi-rs/lzma-linux-x64-gnu@1.5.1`: npm gitHead `f164df92d83e095f195d628b1a68a141ae2eb638`, https://github.com/Brooooooklyn/lzma/tree/f164df92d83e095f195d628b1a68a141ae2eb638 . Published tarball contains no license text; recursive source tree has no LICENSE/COPYING/NOTICE. Metadata MIT is not fabricated into a copyright grant.
- `de-indent@1.0.2`: npm gitHead `5861bd7a39c09f0056fd361d82e570e56bd2c275`, https://github.com/yyx990803/de-indent/tree/5861bd7a39c09f0056fd361d82e570e56bd2c275 . Tree contains `.gitignore`, `index.js`, `package.json`, `test.js`; no full permission notice.
- `stackback@0.0.2`: npm has no gitHead. Upstream tag `v0.0.2` resolves to `2963095372abf7b75ba55f01cef08ab1e62c2ff4`, https://github.com/defunctzombie/node-stackback/tree/2963095372abf7b75ba55f01cef08ab1e62c2ff4 . Tarball has no license file; tag-to-published-bytes correspondence and full notice remain unverified. Do not silently substitute current HEAD.

All three have `dev: true` in client_gui/package-lock.json. This is evidence of declared development scope, not a measured runtime module graph. Before redistributing development dependencies, obtain exact original permission notices or replace them with approved alternatives. Before claiming exclusion from a web release, check its actual bundler module graph.

The Python artifact snapshots do not close target-specific locking, all psycopg2 native notices, source/relinking obligations, or actual image verification. Cargo lock evidence is missing; see NATIVE_INVENTORY.md. U1–U5 operator decisions are recorded in the root ledger (F86-7); they are decision records, not legal conclusions.

## Voice engine native notices (F86-7, rechecked 2026-11-24)

Secured at pinned versions and listed in the catalog `notices[]`: whisper.cpp 1.8.2, SDL2 2.28.5, sherpa-onnx 1.12.20, ONNX Runtime 1.17.1 (LICENSE and ThirdPartyNotices from the official release wheel), cargs 1.0.3, Vosk API 0.3.45, GCC Runtime Library Exception 3.1 (with GPLv3). Still open, so every voice artifact keeps `redistribution_allowed=false` and stays out of offline bundles:

- `whisper-server.exe`: vendored cpp-httplib and nlohmann/json notices are in upstream headers at v1.8.2 and have not been extracted into separate files.
- `sherpa-onnx-c-api.dll`: statically linked dependencies (kaldi-native-fbank, OpenFst, kaldi-decoder, etc.) not enumerated against the v1.12.20 build.
- `libvosk.dll`: statically linked Kaldi, OpenFst and BLAS/LAPACK code; notices and exact revisions not enumerated.
- `libwinpthread-1.dll`: mingw-w64 version not provable from the binary; notice not secured.
- `libgcc_s_seh-1.dll`, `libstdc++-6.dll`: build string `GCC: (GNU) 10-win32 20210110`; the exception text is taken from releases/gcc-10.2.0, exact patch level not proven.
- `github.com/microsoft/onnxruntime` returned 404 from this environment; the ONNX Runtime texts therefore come from the PyPI release wheel (hash in license_sources.json), whose build date and version match the DLL's PE version 1.17.20240223.5.8f5c79c.

The three npm packages above were rechecked on 2026-11-24 against their exact npm tarballs and pinned upstream trees: still no license text, so they remain blocked as described.
