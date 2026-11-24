# Native artifact inventory

These are the exact inspected Linux CPython 3.12 wheels; they are not installed-image or Windows verification.

## psycopg2-binary 2.9.12

Artifact: `psycopg2_binary-2.9.12-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl`

SHA-256: `9fe06d93e72f1c048e731a2e3e7854a5bfaa58fc736068df90b352cefe66f03f`

- `psycopg2/_psycopg.cpython-312-x86_64-linux-gnu.so`
- `psycopg2_binary.libs/libcom_err-2abe824b.so.2.1`
- `psycopg2_binary.libs/libcrypt-13f4f5d0.so.1`
- `psycopg2_binary.libs/libcrypto-88208852.so.3`
- `psycopg2_binary.libs/libgssapi_krb5-497db0c6.so.2.2`
- `psycopg2_binary.libs/libk5crypto-b1f99d5c.so.3.1`
- `psycopg2_binary.libs/libkeyutils-dfe70bd6.so.1.5`
- `psycopg2_binary.libs/libkrb5-fcafa220.so.3.3`
- `psycopg2_binary.libs/libkrb5support-d0bcff84.so.0.1`
- `psycopg2_binary.libs/liblber-314cbfbf.so.2.0.200`
- `psycopg2_binary.libs/libldap-331dad9d.so.2.0.200`
- `psycopg2_binary.libs/libpcre-9513aab5.so.1.2.0`
- `psycopg2_binary.libs/libpq-f521cc7d.so.5.17`
- `psycopg2_binary.libs/libsasl2-84219a89.so.3.0.0`
- `psycopg2_binary.libs/libselinux-0922c95c.so.1`
- `psycopg2_binary.libs/libssl-fe1b61af.so.3`

## pillow 13.0.0

Audit snapshot only, not evidence of an installed runtime dependency. `server/requirements.txt` does not declare Pillow. The remote server Python import search found one `from PIL import Image` in `server/tests/flowgate/test_ai_base_select_browser_0031.py`; this test import is not proof of a runtime dependency. The current Docker build installs server requirements on `python:3.12-slim`; no resolved image dependency metadata is available to establish a transitive Pillow dependency. The checker therefore records Pillow as `not-installed` when absent, and still requires nonempty installed license files when present. The 26 entries below describe only the inspected wheel.

Artifact: `pillow-13.0.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`

SHA-256: `42d63be3af4f9b951b199b0a9d23365d37ab14687e6d77b74940432c8515c4ca`

- `PIL/_avif.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imaging.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imagingcms.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imagingft.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imagingmath.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imagingmorph.cpython-312-x86_64-linux-gnu.so`
- `PIL/_imagingtk.cpython-312-x86_64-linux-gnu.so`
- `PIL/_webp.cpython-312-x86_64-linux-gnu.so`
- `pillow.libs/libXau-154567c4.so.6.0.0`
- `pillow.libs/libavif-c6062d5f.so.16.4.2`
- `pillow.libs/libbrotlicommon-53534446.so.1.2.0`
- `pillow.libs/libbrotlidec-7e5462ba.so.1.2.0`
- `pillow.libs/libfreetype-57f805ba.so.6.20.6`
- `pillow.libs/libharfbuzz-fb49c9fc.so.0.61450.0`
- `pillow.libs/libjpeg-6dc8d115.so.62.4.0`
- `pillow.libs/liblcms2-88eaba2f.so.2.0.19`
- `pillow.libs/liblzma-de26b416.so.5.8.4`
- `pillow.libs/libopenjp2-e530e1bf.so.2.5.4`
- `pillow.libs/libpng16-1d1ba958.so.16.58.0`
- `pillow.libs/libsharpyuv-0066295b.so.0.1.2`
- `pillow.libs/libtiff-20b7e810.so.6.3.0`
- `pillow.libs/libwebp-51b0b3f7.so.7.2.0`
- `pillow.libs/libwebpdemux-9fe2abcc.so.2.0.17`
- `pillow.libs/libwebpmux-8fb1c9f6.so.3.1.2`
- `pillow.libs/libxcb-9065bb12.so.1.1.0`
- `pillow.libs/libzstd-40ad5e7e.so.1.5.7`

psycopg2: 16 native entries = extension plus 15 bundled shared libraries. Its wheel LICENSE is not a complete license ledger for those 15 libraries. Their exact source versions, license files and replacement/relinking evidence remain 미확정.

Pillow: 26 native entries; preserve the whole integrated LICENSE. This count includes extension modules and must not be reported as 26 separately identified third-party projects.

## Rust locks

Remote glob `**/Cargo.lock` returned zero paths. `node_modules`, `client_gui/node_modules`, `.venv` and `server/.venv` were absent (404) when queried directly. The audit HTML mentions four locks / 244 rows but does not include the paths or crate rows. No crate list or link mapping has been invented. R6-4 remains 미완료 until those exact locks and their build artifacts are available; then record each crate name/version/source/checksum/license and actual link target separately from build-only dependencies.
