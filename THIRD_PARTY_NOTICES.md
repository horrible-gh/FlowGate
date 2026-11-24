# Third-party notices

This ledger covers F86-4 (R4, R5-3, R6-2–R6-4). It is not commercial release approval.
Exact upstream texts are preserved without rewriting; acquisition URLs, archive members and SHA-256 hashes are in [license_sources.json](third_party/license_sources.json).
Versioned Python artifacts below are audit snapshots, not Linux/Windows dependency locks or proof of what a future image installs. R6-1 belongs to the integration work.

| Component / file | Original project / version | Copyright holder | License / full text | Distribution location |
| --- | --- | --- | --- | --- |
| Phosphor: `client_gui/src/features/designer/inspector/parts/InspectorAiButton.vue`<br>`client_gui/src/features/documentWindow/DocumentIoButtons.vue`<br>`client_gui/src/features/workPlanWindow/WorkPlanEditWindow.vue` | [Phosphor](https://raw.githubusercontent.com/phosphor-icons/core/2b75f3ad12b420c9504ef05df8d2564a28f8500e/LICENSE) — notice snapshot `2b75f3ad12b420c9504ef05df8d2564a28f8500e`; original icon import revision unknown | Phosphor Icons | MIT; [full text](third_party/licenses/phosphor-LICENSE.txt) | Source; inline browser SVG; Docker `/app/third_party/licenses/`. Web notice aggregation is R5-1 integration work. |
| Lucide: `client_gui/src/features/workPlanWindow/WorkPlanEditWindow.vue`<br>`client_gui/src/features/workPlanWindow/WorkPlanEditBody.vue`<br>`client_gui/src/features/workPlanWindow/WorkPlanStepEditWindow.vue`<br>`client_gui/src/components/ui/LineIcon.vue`<br>`client_gui/src/features/conversation/ConversationComposer.vue` | [Lucide](https://raw.githubusercontent.com/lucide-icons/lucide/c26ce5515667640ca2753e095ba319d225fc0d8d/LICENSE) — notice snapshot `c26ce5515667640ca2753e095ba319d225fc0d8d`; original icon import revision unknown | Lucide Icons and Contributors; Cole Bemis (upstream bundled notices) | ISC + bundled MIT notices; [full text](third_party/licenses/lucide-LICENSE.txt) | Source; inline browser SVG; Docker `/app/third_party/licenses/`. Web notice aggregation is R5-1 integration work. |
| Feather: `client_gui/src/login/components/LoginForm.vue` | [Feather](https://raw.githubusercontent.com/feathericons/feather/1b002399e8758fb2bccea4d07312dc9e0f43dcb8/LICENSE) — notice snapshot `1b002399e8758fb2bccea4d07312dc9e0f43dcb8`; original icon import revision unknown | Cole Bemis | MIT; [full text](third_party/licenses/feather-LICENSE.txt) | Source; inline browser SVG; Docker `/app/third_party/licenses/`. Web notice aggregation is R5-1 integration work. |
| `server/modules/flow_gate/services/voice_sherpa_abi.py` | sherpa-onnx v1.12.20, c-api.h; commit `5ce3d6d93a5f4fe11657bf11a6bf3a022eeef22f` | Xiaomi Corporation, 2022–2025 | [Apache-2.0](third_party/licenses/sherpa-onnx-LICENSE.txt) | Source; Docker `/app/server/` and `/app/third_party/licenses/` |
| psycopg2-binary | [psycopg2-binary 2.9.12](https://files.pythonhosted.org/packages/b0/55/44df3965b5f297c50cc0b1b594a31c67d6127a9d133045b8a66611b14dfb/psycopg2_binary-2.9.12-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl) | psycopg authors; individual holders retained in LICENSE | LGPL-3.0-or-later with OpenSSL exception and file-specific notices; [full text](third_party/licenses/psycopg2-binary-2.9.12-LICENSE.txt) | Python site-packages and dist-info; source and Docker notice directory |
| certifi | [certifi 2026.11.20](https://files.pythonhosted.org/packages/6d/39/5eec3296860b94ac20bb8ed807725b88f6adfab3f343df592afd975f5f86/certifi-2026.11.20-py3-none-any.whl) | Mozilla contributors / certifi authors; original notice retained | MPL-2.0; [full text](third_party/licenses/certifi-2026.11.20-LICENSE.txt) | Python site-packages and dist-info; source and Docker notice directory |
| pillow | [pillow 13.0.0](https://files.pythonhosted.org/packages/e6/99/fdf65cc539e266ab93546c261bdf98411fe165c6e4e13f9dbf597097192b/pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl) | Secret Labs AB; Fredrik Lundh; Jeffrey Alex Clark and bundled component holders | MIT-CMU plus all bundled native notices; [full text](third_party/licenses/pillow-13.0.0-LICENSE.txt) | Audit wheel snapshot only; installed runtime distribution is unconfirmed. Preserved license: source and Docker notice directory. Inspect site-packages/dist-info only if Pillow is installed. |
| passlib | [passlib 1.7.4](https://files.pythonhosted.org/packages/3b/a4/ab6b7589382ca3df236e03faa71deac88cae040af60c071a78d254a62172/passlib-1.7.4-py2.py3-none-any.whl) | Assurance Technologies, LLC and individual component holders | BSD plus Beerware, ISC and EFF data notices; [full text](third_party/licenses/passlib-1.7.4-LICENSE.txt) | Python site-packages and dist-info; source and Docker notice directory |
| marked | [marked 18.0.5](https://registry.npmjs.org/marked/-/marked-18.0.5.tgz) | MarkedJS; Christopher Jeffrey; John Gruber | MIT plus Markdown BSD notice; [full text](third_party/licenses/marked-18.0.5-LICENSE.txt) | Browser bundle; source and Docker notice directory |
| @napi-rs/lzma-linux-x64-gnu | npm 1.5.1; upstream `f164df92d83e095f195d628b1a68a141ae2eb638` | Brooooooklyn; exact copyright notice unresolved | metadata declares MIT; full permission notice 미확정, see [gaps](third_party/UNRESOLVED.md) | package-lock marks dev=true; development installation only by declaration. Actual Rollup module inclusion remains unverified. |
| de-indent | npm 1.0.2; upstream `5861bd7a39c09f0056fd361d82e570e56bd2c275` | Evan You; exact copyright notice unresolved | metadata declares MIT; full permission notice 미확정, see [gaps](third_party/UNRESOLVED.md) | package-lock marks dev=true; development installation only by declaration. Actual Rollup module inclusion remains unverified. |
| stackback | npm 0.0.2; upstream `2963095372abf7b75ba55f01cef08ab1e62c2ff4` | Roman Shtylman; exact copyright notice unresolved | metadata declares MIT; full permission notice 미확정, see [gaps](third_party/UNRESOLVED.md) | package-lock marks dev=true; development installation only by declaration. Actual Rollup module inclusion remains unverified. |

## Scope and preservation

- Lucide's complete upstream file also contains inherited notices; retain the entire file.
- Icon notice snapshot commits identify the retrieved permission text, not a proven original import revision. Original SVG import revisions remain 미확정.
- sherpa-onnx v1.12.20 recursive upstream tree (not truncated) contains no NOTICE file. No NOTICE was invented. The original Xiaomi copyright, generated ctypes origin and ABI instructions remain in the source.
- psycopg2's exception is preserved in its original LICENSE. Also retain [LGPLv3](third_party/licenses/LGPL-3.0.txt) and [GPLv3](third_party/licenses/GPL-3.0.txt). Native libraries do not all inherit psycopg2's license; their exact notices and source/relinking evidence remain open.
- certifi's original notice and source location are retained, with the [MPL 2.0 full text](third_party/licenses/MPL-2.0.txt).
- Pillow's entire 70 KB integrated license is retained, including its bundled native components. See [native inventory](third_party/NATIVE_INVENTORY.md) for the inspected wheel, not a universal platform claim.
- Installed dist-info files must remain in the image. Run `python3 tools/build_notice_sbom.py SOURCE_IMAGE OUTPUT_IMAGE --output-dir SCRATCH_DIRECTORY` after the normal Docker build. This requires existing Docker and Syft, installs neither, scans OS and language packages, verifies installed notices and embeds the SBOM into OUTPUT_IMAGE. Publish only after unresolved release prerequisites are closed.

## Docker distribution procedure

Use this procedure in the release build environment with existing Docker and Syft. A normal Dockerfile build does not produce an SBOM. Keep source and output tags distinct and select a new empty scratch directory outside the repository.

```sh
docker build -t flowgate:notice-source .
python3 tools/build_notice_sbom.py flowgate:notice-source flowgate:notice-output --output-dir /absolute/outside-repository/empty-notice-scratch
docker image inspect --format '{{.Id}}' flowgate:notice-output
docker run --rm --network none --entrypoint cat flowgate:notice-output /app/LICENSE
docker run --rm --network none --entrypoint cat flowgate:notice-output /app/THIRD_PARTY_NOTICES.md
docker run --rm --network none --entrypoint cat flowgate:notice-output /app/sbom/installed-notices.json
docker run --rm --network none --entrypoint cat flowgate:notice-output /app/sbom/scan-provenance.json
```

Stop on any nonzero command exit. The postprocessor checks installed notices in both images, OS/Python SBOM coverage, and equality of the embedded `/app/sbom/image.spdx.json` to the scan result. Archive its stdout, source/output image IDs, build logs and all three JSON evidence files with TS/TSR. Pillow absence is recorded as `not-installed`; presence requires a nonempty dist-info license. Neither case resolves other native license gaps. Select the output image for distribution only after the unresolved prerequisites below are closed; the source image alone does not satisfy the SBOM step. This procedure is documented but has not been executed or connected to CI in this card.

## Integration placeholders (not completed notices)

| Component / file | Original project / version | Copyright holder | License / evidence | Distribution location |
| --- | --- | --- | --- | --- |
| Unidecode replacement | Integration card to fill R3 implementation/version | 미확정 | 미확정; replacement decision and text pending | Server slug path; integration pending |
| SenseVoice | Integration card to fill R2 model hashes/version | 미확정 | Applicability 미확정; do not describe as unlicensed or prohibited | Direct upstream download vs redistribution to be filled by integration card |
| KSS samples | Integration card to fill R1 removal evidence | 미확정 | Removal evidence pending; no permission text fabricated | Archive/offline exclusion to be confirmed by integration card |
| FFmpeg | Integration card to fill R5-4 build/commit | 미확정 | Corresponding source and component notices pending | Direct download vs redistributed offline package to be filled by integration card |

## U1–U5 rights register

| ID | Status | Required evidence / next action |
| --- | --- | --- |
| U1 | 미확정 | SenseVoice converted weights and tokens: obtain fixed rights-holder scope evidence; do not open commercial redistribution before resolution. |
| U2 | 미확정 | If a Korean sample is needed, obtain a rights-cleared recording and permission evidence. |
| U3 | 미확정 | FlowGate name: define countries and goods/services, then check official trademark registers against VMware Project Flowgate and Endress+Hauser FLOWgate. |
| U4 | 미확정 | Check current screen designs in J-PlatPat/GrIP and the four candidates listed in R revision 2. |
| U5 | 미확정 | Obtain creator/acquisition, AI-generation and font permission evidence for promotional assets. |

These entries are release prerequisites, not legal conclusions. No external contact or production deployment has been made by this card.
