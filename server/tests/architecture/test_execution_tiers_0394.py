"""Execution-tier/parallel contracts: the earlier implementation (sections 13-12/13-13).

added tier selection and pytest-xdist without tests protecting either.
Tier behavior depended on server/pytest.ini and conftest hooks; parallel safety
existed only in requirements.txt comments and TESTING.md. Removing those rules
would trigger no failure, the global-rule/local-check gap noted in the review 5.3.

Protect five contracts: tier configuration/markers exist; rules classify modules
and explicit markers override them; tier deferrals are reported separately from
capability skips; parallel execution cannot start with file-splitting distribution;
and worker observations of environment leakage reach the controller report.

Contracts 3/5 came from a real bug: --tier=fast -n 2 skipped 117 tests without
warning that the run was incomplete, because collection hooks ran inside workers
and their state never reached the controller. Parallelism must not bring back
the silent skips the review 5.2 intended to eliminate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[2]
_TESTS_DIR = _SERVER_DIR / "tests"
_REPO_ROOT = _SERVER_DIR.parent
_CONFTEST_PATH = _TESTS_DIR / "conftest.py"
_PYTEST_INI = _SERVER_DIR / "pytest.ini"
_REQUIREMENTS = _SERVER_DIR / "requirements.txt"
_TESTING_MD = _REPO_ROOT / "TESTING.md"


@pytest.fixture(scope="module")
def conf(pytestconfig):
    """The conftest module driving this very run.

    A new import creates a copy with globals different from those used by hooks.
    """
    for plugin in pytestconfig.pluginmanager.get_plugins():
        source = getattr(plugin, "__file__", None)
        if source and Path(source).resolve() == _CONFTEST_PATH:
            return plugin
    pytest.fail(f"{_CONFTEST_PATH} 가 플러그인으로 등록돼 있지 않다")


class _RecordingParser:
    """Record only what pytest_addoption registers."""

    def __init__(self) -> None:
        self.options: list[tuple[tuple, dict]] = []

    def addoption(self, *args, **kwargs) -> None:
        self.options.append((args, kwargs))


class _FakeItem:
    def __init__(self, markers: set[str]) -> None:
        self._markers = markers

    def get_closest_marker(self, name: str):
        return object() if name in self._markers else None


def _write_module(directory: Path, name: str, body: str) -> Path:
    """A fake module for tier classification, under tmp_path outside the repository."""
    module_file = directory / name
    module_file.write_text(body, encoding="utf-8")
    return module_file


# 1. Configuration and registered tier markers.


class TestTierConfigurationExists:
    def test_pytest_ini_exists_and_registers_three_tiers(self):
        """10.4: originally no configuration file existed to define tiers."""
        assert _PYTEST_INI.is_file(), (
            "server/pytest.ini 가 없다. 계층 마커를 등록할 자리가 사라지면 "
            "--tier 는 남아도 `@pytest.mark.fast` 는 unknown marker 경고가 된다."
        )
        text = _PYTEST_INI.read_text(encoding="utf-8")
        assert "[pytest]" in text
        markers = text.split("markers", 1)[-1]
        for tier in ("fast", "standard", "full"):
            assert f"{tier}:" in markers, f"pytest.ini 에 {tier} 마커 등록이 없다"

    def test_tier_option_defaults_to_full(self, conf):
        """Defaulting to anything but full silently reduces an uninformed run."""
        parser = _RecordingParser()
        conf.pytest_addoption(parser)

        registered = {args[0]: kwargs for args, kwargs in parser.options}
        assert "--tier" in registered, "--tier 옵션이 등록되지 않았다"
        option = registered["--tier"]
        assert option["default"] == "full"
        assert list(option["choices"]) == ["fast", "standard", "full"]

    def test_tier_order_is_cheapest_first(self, conf):
        assert conf._TIER_ORDER == ("fast", "standard", "full")


# 2. Rules determine tiers.


class TestTierIsDerivedNotListed:
    """10.4: a file-list policy can leave new files outside every tier."""

    def test_plain_module_is_fast(self, conf, tmp_path):
        module_file = _write_module(
            tmp_path,
            "test_plain.py",
            "def test_one():\n    assert 1 + 1 == 2\n",
        )
        assert conf._tier_for_module(module_file) == "fast"

    def test_testclient_module_is_not_fast(self, conf, tmp_path):
        module_file = _write_module(
            tmp_path,
            "test_api.py",
            "from fastapi.testclient import TestClient\n\n\ndef test_two():\n    assert TestClient\n",
        )
        assert conf._tier_for_module(module_file) == "standard"

    def test_subprocess_module_is_full(self, conf, tmp_path):
        module_file = _write_module(
            tmp_path,
            "test_shells_out.py",
            "import subprocess\n\n\ndef test_three():\n    assert subprocess\n",
        )
        assert conf._tier_for_module(module_file) == "full"

    def test_dialect_family_stays_out_of_standard(self, conf, tmp_path):
        module_file = _write_module(
            tmp_path,
            "test_dialect_shapes.py",
            "from fastapi.testclient import TestClient\n\n\ndef test_four():\n    assert TestClient\n",
        )
        assert conf._tier_for_module(module_file) == "full"

    def test_a_brand_new_file_always_lands_somewhere(self, conf, tmp_path):
        """No file may fall outside every tier."""
        for index, body in enumerate(
            (
                "def test_a():\n    pass\n",
                "import time\n\n\ndef test_b():\n    time.sleep(0)\n",
                "import subprocess\n\n\ndef test_c():\n    assert subprocess\n",
            )
        ):
            module_file = _write_module(tmp_path, f"test_new_{index}.py", body)
            assert conf._tier_for_module(module_file) in conf._TIER_ORDER

    def test_explicit_marker_beats_the_rule(self, conf):
        """An explicit marker overrides the rule for exceptional files."""
        assert conf._declared_tier(_FakeItem({"fast"})) == "fast"
        assert conf._declared_tier(_FakeItem({"full"})) == "full"
        assert conf._declared_tier(_FakeItem(set())) is None

    def test_full_tier_changes_nothing_about_an_ordinary_run(self, conf):
        """Leave default runs untouched: the collection hook must return immediately."""
        class _Config:
            def getoption(self, name, default=None):
                assert name == "--tier"
                return "full"

        items = [_FakeItem(set())]
        conf.pytest_collection_modifyitems(_Config(), items)   # This must pass even without marker support.


# 3. Tier deferrals remain distinct from capability skips.


class TestTierSkipsAreAccountedSeparately:
    def test_tier_reason_is_classified_as_tier(self, conf):
        reason = f"tier full > requested fast {conf._TIER_SKIP_MARK}"
        assert conf._classify_skip(reason) == "tier"

    def test_capability_reasons_still_classify(self, conf):
        assert conf._classify_skip("git executable not found") == "git"
        assert conf._classify_skip("symlink support required") == "symlinks"
        assert conf._classify_skip("무슨 다른 사유") == "other"

    def test_skip_reason_written_by_the_hook_uses_the_same_mark(self, conf):
        """Changing only one side of a marker contract silently zeroes deferred counts."""
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        assert 'reason=f"tier {tier} > requested {requested} {_TIER_SKIP_MARK}"' in source, (
            "수집 훅이 쓰는 skip 사유와 _TIER_SKIP_MARK 가 어긋났다"
        )

    def test_deferral_is_counted_from_reports_not_from_collection(self, conf):
        """Aggregate through reports so parallel runs retain the count.

        Collection hooks run in workers; their globals are invisible to the
        controller summary. pytest_runtest_logreport counts deferrals instead.
        """
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        report_hook = source.split("def pytest_runtest_logreport", 1)[-1].split("\ndef ", 1)[0]
        assert "_tier_deferred" in report_hook
        assert "_items_started" in report_hook

        summary = source.split("def pytest_terminal_summary", 1)[-1]
        assert "_tier_deferred" in summary, "요약이 유예 건수를 읽지 않는다"
        assert "전체 통과가 아닙니다" in summary, "계층 실행의 경고 문구가 사라졌다"

    def test_executed_count_excludes_every_kind_of_skip(self, conf, monkeypatch):
        """Executed counts exclude all skipped tests, not only tier deferrals.

        A real --tier=fast run reported 1614 executed while pytest reported
        1613 passed, 1654 skipped. The difference was a missing-run.bat skip,
        still counted as executed because it was not a tier deferral. A line
        intended to expose skipped tests must not conceal one itself.
        """
        class _Reporter:
            def __init__(self):
                self.lines: list[str] = []

            def write_sep(self, _sep, title, **_kw):
                self.lines.append(f"== {title}")

            def write_line(self, line, **_kw):
                self.lines.append(line)

        class _Option:
            numprocesses = None
            dist = "no"

        class _Config:
            option = _Option()

            def getoption(self, name, default=None):
                return "fast" if name == "--tier" else default

        # Actual figures: 3,267 tests, 1,653 tier deferrals, and one missing-run.bat
        # skip leave 1,613 tests executed.
        monkeypatch.setattr(conf, "_items_started", 3267)
        monkeypatch.setattr(conf, "_tier_deferred", 1653)
        monkeypatch.setattr(conf, "_skips_by_capability", {"tier": 1653, "run_bat": 1})

        reporter = _Reporter()
        conf.pytest_terminal_summary(reporter, 0, _Config())

        tier_line = next((line for line in reporter.lines if "--tier=fast" in line), None)
        assert tier_line is not None, "계층 실행인데 요약에 계층 줄이 없다"
        assert "1613건 실행" in tier_line, tier_line
        assert "1653건은 상위 계층" in tier_line, tier_line
        assert "그 밖의 사유로 1건" in tier_line, (
            "능력 부족으로 빠진 건수가 요약에 드러나지 않는다: " + tier_line
        )

    def test_the_count_is_exact_when_nothing_else_was_skipped(self, conf, monkeypatch):
        """Do not add redundant wording when there are no other skips."""
        class _Reporter:
            def __init__(self):
                self.lines: list[str] = []

            def write_sep(self, _sep, title, **_kw):
                self.lines.append(f"== {title}")

            def write_line(self, line, **_kw):
                self.lines.append(line)

        class _Option:
            numprocesses = None
            dist = "no"

        class _Config:
            option = _Option()

            def getoption(self, name, default=None):
                return "standard" if name == "--tier" else default

        monkeypatch.setattr(conf, "_items_started", 100)
        monkeypatch.setattr(conf, "_tier_deferred", 40)
        monkeypatch.setattr(conf, "_skips_by_capability", {"tier": 40})

        reporter = _Reporter()
        conf.pytest_terminal_summary(reporter, 0, _Config())

        tier_line = next(line for line in reporter.lines if "--tier=standard" in line)
        assert "60건 실행" in tier_line, tier_line
        assert "그 밖의 사유로" not in tier_line, tier_line


# 4. Parallel distribution must not split files.


class TestParallelExecutionIsFileWise:
    def test_xdist_is_pinned_and_installed(self):
        """10.3: reduce total runtime without deleting tests through parallelism."""
        requirements = _REQUIREMENTS.read_text(encoding="utf-8")
        assert "pytest-xdist==" in requirements, "requirements.txt 에 pytest-xdist 핀이 없다"
        assert importlib.util.find_spec("xdist") is not None, "pytest-xdist 가 설치돼 있지 않다"

    def test_safe_distributions_are_allowed(self, conf):
        assert conf._dist_mode_problem(None, "no") is None       # Serial execution.
        assert conf._dist_mode_problem(0, "load") is None        # -n0 is not parallel.
        assert conf._dist_mode_problem(8, "loadfile") is None
        assert conf._dist_mode_problem(8, "loadscope") is None

    @pytest.mark.parametrize("dist", ["load", "worksteal", "loadgroup", "each"])
    def test_file_splitting_distributions_are_refused(self, conf, dist):
        problem = conf._dist_mode_problem(8, dist)
        assert problem, f"--dist {dist} 는 한 파일을 여러 워커로 찢는데 통과했다"
        assert "loadfile" in problem, "거절 메시지가 올바른 사용법을 알려 주지 않는다"

    def test_configure_refuses_an_unsafe_parallel_run(self, conf):
        """Enforce this at execution: someone typing only -n auto may not read the docs."""
        state_before = dict(conf._capability_state)
        try:
            class _Option:
                numprocesses = 8
                dist = "load"

            class _Config:
                option = _Option()

            with pytest.raises(pytest.UsageError) as raised:
                conf.pytest_configure(_Config())
            assert "loadfile" in str(raised.value)
        finally:
            conf._capability_state.clear()
            conf._capability_state.update(state_before)

    def test_configure_leaves_workers_alone(self, conf):
        """Each worker runs its share serially; enforcing dist there kills the whole run."""
        state_before = dict(conf._capability_state)
        try:
            class _Option:
                numprocesses = 8
                dist = "load"

            class _Config:
                option = _Option()
                workerinput = {"workerid": "gw0"}

            conf.pytest_configure(_Config())
        finally:
            conf._capability_state.clear()
            conf._capability_state.update(state_before)


# 5. Worker observations reach the controller report.


class TestWorkerObservationsReachTheController:
    def test_worker_ships_its_env_leaks(self, conf):
        shipped: dict = {}

        class _Config:
            workeroutput = shipped

        class _Session:
            config = _Config()

        conf._env_leaks.append("test_fake_module.py: FLOWGATE_STORAGE_DIR 를 지웠다")
        try:
            conf.pytest_sessionfinish(_Session(), 0)
        finally:
            conf._env_leaks.remove("test_fake_module.py: FLOWGATE_STORAGE_DIR 를 지웠다")

        assert "test_fake_module.py: FLOWGATE_STORAGE_DIR 를 지웠다" in shipped["flowgate_env_leaks"]

    def test_controller_merges_a_finished_worker(self, conf):
        line = "test_other_module.py: FLOWGATE_SRC_DIR 를 바꿔 놓았다"

        class _Node:
            workeroutput = {"flowgate_env_leaks": [line, line]}

        try:
            conf.pytest_testnodedown(_Node(), None)
            assert conf._env_leaks.count(line) == 1, "워커 보고가 누락되거나 중복됐다"
        finally:
            while line in conf._env_leaks:
                conf._env_leaks.remove(line)

    def test_the_xdist_hook_is_optional(self, conf):
        """conftest must still import on a host without xdist."""
        options = getattr(conf.pytest_testnodedown, "pytest_impl", {})
        assert options.get("optionalhook") is True, (
            "optionalhook 이 아니면 pytest-xdist 가 없는 환경에서 conftest 가 "
            "unknown hook 으로 죽는다"
        )


# 6. Usage instructions remain available.


class TestUsageIsWrittenDown:
    def test_testing_md_documents_both_tiers_and_parallel(self):
        text = _TESTING_MD.read_text(encoding="utf-8")
        for needle in ("--tier=fast", "--tier=standard", "--dist loadfile"):
            assert needle in text, f"TESTING.md 에 {needle} 사용법이 없다"


# 7. Shared connections are clean for the next borrower.
#
# regression for a defect exposed by parallel execution.
#
# During --tier=standard -n auto --dist loadfile,
# `test_auto_approved_locale_0099.py::test_migration_051_adds_column_and_round_trips`
# one case failed with FOREIGN KEY, but parallelism was not the cause.
# Four files shared one session connection; one inserted without committing
# or cleaning up, leaving a write transaction for the next file. SQLite
# silently ignores PRAGMA foreign_keys inside a transaction, leaving FKs on.
#
# Serial alphabetical order accidentally placed the files in a safe order.
# Worker assignment removed that coincidence. With or without parallelism,
# a borrowed connection must not carry another test's open transaction.
#
# Keep these two cases adjacent and ordered: the first deliberately dirties
# the connection, and the second inspects what the next borrower receives.

_PROBE_TOKEN_SQL = (
    "INSERT INTO tokens (token_id, hash, pepper_id, project, action_scope, "
    "issued_to, created_at, expires_at, continuation_review_mode) "
    "VALUES ('tok_tier_probe','h','pep_1','__SYSTEM__','new','usr_admin',"
    "'2026-08-10T00:00:00+09:00','2026-08-11T00:00:00+09:00',0)"
)


class TestSharedConnectionIsHandedOverClean:
    def test_a_test_may_leave_a_write_transaction_open(self, test_db):
        """Tests without cleanup exist; they must not break the next borrower."""
        test_db.execute(_PROBE_TOKEN_SQL)
        assert test_db.in_transaction, "이 케이스의 전제(쓰기 트랜잭션이 열린다)가 깨졌다"

    def test_the_next_test_gets_a_clean_connection(self, test_db):
        assert not test_db.in_transaction, (
            "앞 테스트의 트랜잭션이 열린 채 넘어왔다. 이 상태에서는 PRAGMA 가 무시되고, "
            "워커 배분이 바뀔 때마다 다른 파일이 무작위로 빨간불이 된다."
        )
        leftover = test_db.execute(
            "SELECT 1 FROM tokens WHERE token_id = 'tok_tier_probe'"
        ).fetchone()
        assert leftover is None, "앞 테스트가 공용 DB 에 행을 남겼다"

        # This was the failure point: inside a transaction the PRAGMA silently does
        # nothing, without raising an exception.
        test_db.execute("PRAGMA foreign_keys = OFF")
        assert test_db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        test_db.execute("PRAGMA foreign_keys = ON")

    def test_the_fixture_rolls_back_on_both_sides(self, conf):
        """Clean both before lending and after return; otherwise the first test loses."""
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        # Do not name this body. The 0382 write guard transitively tracks names derived
        # from __file__ across the file. _write_module(..., body) above shares that
        # name, tainting module_file as a repository path and falsely reporting the
        # tmp_path write as a repository mutation.
        fixture_source = source.split("def test_db(all_migrations_db)", 1)[-1]
        assert fixture_source.count("all_migrations_db.rollback()") == 2, (
            "test_db 픽스처가 yield 앞뒤에서 트랜잭션을 닫지 않는다"
        )
