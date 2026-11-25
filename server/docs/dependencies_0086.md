# F86 R3 / R6-1: transliteration and Python locks

## Decision and provenance

Replace Unidecode with **AnyAscii 0.3.3**, a pure-Python Unicode-to-ASCII
implementation under ISC. It covers Korean, Japanese, Latin and mixed text
without copying Unidecode's GPL implementation or adding native runtime code.
Copyright: 2020-2025 Hunter WB <hunterwb.com>.

- Release: https://pypi.org/project/anyascii/0.3.3/
- Upstream: https://github.com/anyascii/anyascii
- License evidence: `anyascii-0.3.3.dist-info/licenses/LICENSE` in the release wheel.
- Wheel SHA256: `f5ab5e53c8781a36b5a40e1296a0eeda2f48c649ef10c3921c1381b1d00dee7a`.
- Distribution location: server Python environment, including package dist-info.
- The final integration card must add this component and the license below to
  the root notice register and packaged notices. Those files belong to another card.

## Compatibility

Only the new-suggestion function `romanize_to_slug` changes. The existing ASCII
validation pipeline, stored IDs, project creation handler and storage paths do
not change. No data migration or existing-ID regeneration is performed.

`tests/test_slug_0086.py` embeds the old output as constants. Before the library
replacement, the nine corpus rows plus seven invalid-input cases passed against
the original implementation (self-check `scr_fcb4688ca77c46bbba1d1c4e`).
The corpus covers Korean, Japanese, English, mixed text, accented Latin and
punctuation. Actual execution with installed AnyAscii 0.3.3 confirms three changed
suggestions: `한국어 프로젝트`: `hangugeo-peurojegteu` -> `hangugeo-peulojegteu`
(raw transliteration: `HanGugEo PeuLoJegTeu`); `日本語`: `ri-ben-yu` -> `ribenyu`;
`東京`: `dong-jing` -> `dongjing`. Korean changes r to l in `프로젝트`, while
Han-character spacing is removed in the two Japanese examples. The other six
corpus outputs are unchanged. Legacy constants remain unchanged.

The actual create rule returns **422**, with `project_id_conflict` or
`project_archived_conflict`, on a duplicate suggestion. It does not automatically
append a suffix. Regression cases exercise the real handler against a fake store,
reject duplicate active/archived IDs, then accept an explicit `-2` suffix and
assert all saved rows remain unchanged. All three changed rows now participate
in both active/archived cases (six collision/suffix cases). A read-path test
retains a legacy ID. The complete source suite has 23 cases.

## Exact versions and target locks

Direct requirements are exact pins, retaining Windows-only markers:
`jinja2==3.1.6`, `psutil==7.2.2`, `anyascii==0.3.3`.
Unidecode is removed. An existing environment is not cleaned by pip installing
requirements; deployment should use a fresh environment, rather than claiming
that changing a manifest uninstalls previously installed packages.

The locks cover **CPython 3.12 x86_64**, Linux GNU and Windows MSVC, including
transitive packages and SHA256 hashes for release artifacts. They do not cover
ARM64, Alpine/musl, or other Python versions. Generated with uv 0.9.11 against
PyPI on 2026-11-24. Reproduce from repository root:

```sh
uv pip compile server/requirements.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes --no-header -o server/requirements/linux-py312.lock
uv pip compile server/requirements.txt --python-version 3.12 --python-platform x86_64-pc-windows-msvc --generate-hashes --no-header -o server/requirements/windows-py312.lock
```

The generation host had Python 3.13, and uv warned it would use that interpreter
if build metadata were needed; resolution targets were explicitly Python 3.12.
These are cross-target resolution results, not native Python 3.12 install proofs.
For reproducible deployment use the matching lock in a fresh Python 3.12 venv:

```sh
python -m pip install --require-hashes -r server/requirements/linux-py312.lock
# On Windows, use server/requirements/windows-py312.lock instead.
```

A later regeneration may select newer transitive versions; review its diff.
Hashes accept listed release artifacts; they are not just the host's wheel hash.
Linux includes uvloop; Windows includes pywinpty and psutil instead.

## Direct-import guard and limits

`tests/test_dependency_policy_0086.py` parses runtime Python files with AST,
including `import`, `from`, and literal dynamic imports. It checks known restricted
modules and installed distribution license metadata, with an explicit psycopg2
exception requested by R3-3. It checks the classifier with GPL/LGPL and permissive
alternatives, and validates exact pins, SHA256 syntax and target marker membership.
Tests, virtualenvs and dependency directories are excluded from runtime scanning.

This is a regression guard, not a complete license audit: missing/unclassified
package metadata, computed dynamic imports, vendored/native code and redistribution
notices require separate review. Run it in a fully installed environment on each
target; the current Windows host alone cannot establish Linux package coverage.

## Verification and follow-up

The original official self-check history is retained below; the full test suite was not run:

- Original implementation: 16 passed in 8.98 s, Windows CPython 3.14.2.
- Updated slug tests: collection failed (`ModuleNotFoundError: anyascii`), run
  `scr_403027b6848b4b4f98f9a800`, exit 2. This was the initial run, before the supplemental verification below.
- Dependency/import policy: 8 passed in 19.31 s, run
  `scr_d20c5ffd4eae497f8ee8ad7c`, exit 0, no skips, no source drift.
- Required-dependency setup through self-check (`python -m pip install anyascii==0.3.3`)
  failed with exit 3: `Could not find an activated virtualenv (required)`, run
  `scr_f6fdff9200934869b05b1bf7`. The host policy was not bypassed.

Revision rework repeated the official installation command, run
`scr_3f95c512ed6a406db377eab6`: exit 3, the same virtualenv-required error;
no source/worktree drift and no timeout. It did not install AnyAscii on that host.

To fulfill the review's request for actual execution in a separate environment,
a fresh virtualenv in this job's scratch directory installed `anyascii==0.3.3`
and `pytest==8.3.4` on Linux x86_64, CPython 3.13.5. A scratch harness loaded
`slug.py` and the corrected test file verbatim from remote/read JSON responses
into memory, without editing or copying project source into a local source tree.
Command: `PYTHONDONTWRITEBYTECODE=1 verify-venv/bin/python -m pytest -q -s -p no:cacheprovider test_remote_corpus.py`.
Result: **17 passed in 0.03 s**, exit 0: all nine original corpus test invocations,
all seven original invalid-input test invocations, and one parameter-membership
check confirming the three changed inputs times two active states. This is
supplemental execution evidence, not an official self-check or a full 23-case pass.
The harness did not execute the six create-handler collision cases or the
legacy-ID read case. Those seven integration cases still require the managed
server environment; membership coverage alone does not prove handler behavior.

Verified source SHA256:
- `server/modules/flow_gate/utils/slug.py`: `3e96e4a12206def639efff0b96fba6e3b0f01a5d363b586bfd8fac2f77a9c841`
- `server/tests/test_slug_0086.py`: `cfa32ba4334acd17ed01e7da13e92511ef50cfae59329da54f1624683b77d0ee`

Run `pytest -q tests/test_slug_0086.py tests/test_dependency_policy_0086.py`
from `server` in an activated managed virtualenv with the new requirements.
Fresh hash-enforced installs on both Python 3.12 targets and confirmation that
Unidecode is absent remain pending. R6-2 native notices and final packaged
notices remain the integration card's responsibility.

## ISC license copied from the pinned release wheel

```text
ISC License

Copyright (c) 2020-2025, Hunter WB <hunterwb.com>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```
