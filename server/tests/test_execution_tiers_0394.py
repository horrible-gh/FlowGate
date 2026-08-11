"""실행 계층(Fast/Standard/Full)과 병렬 실행의 계약 — 0394 T0014 (NR0003 §13-12 / §13-13).

T0004 가 계층 선택과 pytest-xdist 도입을 만들었지만, **그 둘을 지키는 테스트는 한 건도
없었다.** 계층은 설정 파일 하나(`server/pytest.ini`)와 conftest 의 훅 몇 개에 얹혀 있고,
병렬 안전성은 requirements.txt 의 주석과 TESTING.md 의 문장으로만 존재했다. 그런 것은
지워져도 아무 불이 켜지지 않는다 — NR0003 §5.3 이 "규칙은 전역인데 검사는 국소" 라고
지적한 자리와 정확히 같은 모양이고, 여기서는 아예 검사가 없었다.

이 파일이 고정하는 계약은 다섯이다.

1. 계층을 걸 자리(설정 파일과 마커 등록)가 있다.
2. 계층 판정은 목록이 아니라 규칙에서 나오고, 명시 마커가 그 규칙을 이긴다.
3. 계층 때문에 건너뛴 건수는 능력 부족 skip 과 섞이지 않고, 실행 끝에 반드시 보고된다.
4. 병렬 실행은 파일을 쪼개는 분배로는 시작조차 되지 않는다.
5. 병렬 실행에서도 워커가 본 것(환경변수 누출)이 컨트롤러 보고에 합쳐진다.

3·5 는 실제 결함에서 나왔다. `--tier=fast -n 2` 는 117 건을 건너뛰고도 "이 실행은 전체가
아닙니다" 를 한 줄도 찍지 않았다. 수집 훅이 워커 안에서 돌아 그 사실이 컨트롤러에
도달하지 못했기 때문이다. 병렬로 돌리는 순간 계층 경고가 사라진다면, 그것은 NR0003 §5.2
가 없애려던 조용한 skip 이 다른 문으로 돌아온 것이다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SERVER_DIR = _TESTS_DIR.parent
_REPO_ROOT = _SERVER_DIR.parent
_CONFTEST_PATH = _TESTS_DIR / "conftest.py"
_PYTEST_INI = _SERVER_DIR / "pytest.ini"
_REQUIREMENTS = _SERVER_DIR / "requirements.txt"
_TESTING_MD = _REPO_ROOT / "TESTING.md"


@pytest.fixture(scope="module")
def conf(pytestconfig):
    """지금 이 실행을 움직이고 있는 conftest 모듈 그 자체.

    새로 import 하면 사본이 생겨, 정작 훅이 쓰는 전역과 다른 것을 검사하게 된다.
    """
    for plugin in pytestconfig.pluginmanager.get_plugins():
        source = getattr(plugin, "__file__", None)
        if source and Path(source).resolve() == _CONFTEST_PATH:
            return plugin
    pytest.fail(f"{_CONFTEST_PATH} 가 플러그인으로 등록돼 있지 않다")


class _RecordingParser:
    """pytest_addoption 이 무엇을 등록하는지만 받아 적는다."""

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
    """계층 판정에 먹일 가짜 테스트 모듈 (tmp_path 안, 저장소 밖)."""
    module_file = directory / name
    module_file.write_text(body, encoding="utf-8")
    return module_file


# ── 1. 계층을 걸 자리 ────────────────────────────────────────────────────────


class TestTierConfigurationExists:
    def test_pytest_ini_exists_and_registers_three_tiers(self):
        """NR0003 §10.4: 저장소에 설정 파일이 하나도 없어 계층을 붙일 자리부터 없었다."""
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
        """기본값이 full 이 아니면, 아무 것도 모르는 실행이 조용히 덜 돈다."""
        parser = _RecordingParser()
        conf.pytest_addoption(parser)

        registered = {args[0]: kwargs for args, kwargs in parser.options}
        assert "--tier" in registered, "--tier 옵션이 등록되지 않았다"
        option = registered["--tier"]
        assert option["default"] == "full"
        assert list(option["choices"]) == ["fast", "standard", "full"]

    def test_tier_order_is_cheapest_first(self, conf):
        assert conf._TIER_ORDER == ("fast", "standard", "full")


# ── 2. 계층 판정은 규칙에서 나온다 ───────────────────────────────────────────


class TestTierIsDerivedNotListed:
    """NR0003 §10.4: 목록으로 관리하면 새 파일이 어느 계층에도 안 들어간다."""

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
        """어느 계층에도 안 들어가는 파일은 존재할 수 없어야 한다."""
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
        """규칙이 틀린 파일을 위한 탈출구 — 마커 한 줄이 이긴다."""
        assert conf._declared_tier(_FakeItem({"fast"})) == "fast"
        assert conf._declared_tier(_FakeItem({"full"})) == "full"
        assert conf._declared_tier(_FakeItem(set())) is None

    def test_full_tier_changes_nothing_about_an_ordinary_run(self, conf):
        """기본 실행에 손대지 않는다 — 수집 훅이 즉시 돌아 나와야 한다."""
        class _Config:
            def getoption(self, name, default=None):
                assert name == "--tier"
                return "full"

        items = [_FakeItem(set())]
        conf.pytest_collection_modifyitems(_Config(), items)   # 마커를 못 달아도 통과해야 한다


# ── 3. 계층 유예는 능력 부족 skip 과 섞이지 않는다 ───────────────────────────


class TestTierSkipsAreAccountedSeparately:
    def test_tier_reason_is_classified_as_tier(self, conf):
        reason = f"tier full > requested fast {conf._TIER_SKIP_MARK}"
        assert conf._classify_skip(reason) == "tier"

    def test_capability_reasons_still_classify(self, conf):
        assert conf._classify_skip("git executable not found") == "git"
        assert conf._classify_skip("symlink support required") == "symlinks"
        assert conf._classify_skip("무슨 다른 사유") == "other"

    def test_skip_reason_written_by_the_hook_uses_the_same_mark(self, conf):
        """꼬리표가 한쪽만 바뀌면 유예 건수가 조용히 0 이 된다."""
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        assert 'reason=f"tier {tier} > requested {requested} {_TIER_SKIP_MARK}"' in source, (
            "수집 훅이 쓰는 skip 사유와 _TIER_SKIP_MARK 가 어긋났다"
        )

    def test_deferral_is_counted_from_reports_not_from_collection(self, conf):
        """병렬에서도 남으려면 집계가 리포트 쪽이어야 한다.

        수집 훅은 워커 안에서 돌고, 거기서 만든 전역은 요약을 찍는 컨트롤러에게
        보이지 않는다. 그래서 유예 건수는 `pytest_runtest_logreport` 가 센다.
        """
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        report_hook = source.split("def pytest_runtest_logreport", 1)[-1].split("\ndef ", 1)[0]
        assert "_tier_deferred" in report_hook
        assert "_items_started" in report_hook

        summary = source.split("def pytest_terminal_summary", 1)[-1]
        assert "_tier_deferred" in summary, "요약이 유예 건수를 읽지 않는다"
        assert "전체 통과가 아닙니다" in summary, "계층 실행의 경고 문구가 사라졌다"

    def test_executed_count_excludes_every_kind_of_skip(self, conf, monkeypatch):
        """실행 건수는 계층 유예만이 아니라 **건너뛴 것 전부**를 뺀 값이다.

        실측에서 어긋났다. `--tier=fast` 는 요약에 "1614건 실행" 을 찍었는데 pytest 는
        `1613 passed, 1654 skipped` 를 찍었다. 차이 한 건은 run.bat 이 없어 빠진
        테스트였고, 계층 유예가 아니라는 이유로 실행 건수 쪽에 남아 있었다.
        조용히 빠진 테스트를 드러내려고 만든 줄이 스스로 한 건을 감춘 셈이다.
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

        # 실제로 나왔던 숫자 그대로: 3,267 건 중 1,653 건은 계층 유예,
        # 1 건은 run.bat 이 없어 빠졌다 → 실행된 것은 1,613 건이다.
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
        """다른 skip 이 없으면 군더더기 문구도 붙지 않는다."""
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


# ── 4. 병렬 실행은 파일을 쪼개지 않는다 ──────────────────────────────────────


class TestParallelExecutionIsFileWise:
    def test_xdist_is_pinned_and_installed(self):
        """NR0003 §10.3: 테스트를 한 건도 지우지 않고 전체 시간을 줄이는 유일한 지렛대."""
        requirements = _REQUIREMENTS.read_text(encoding="utf-8")
        assert "pytest-xdist==" in requirements, "requirements.txt 에 pytest-xdist 핀이 없다"
        assert importlib.util.find_spec("xdist") is not None, "pytest-xdist 가 설치돼 있지 않다"

    def test_safe_distributions_are_allowed(self, conf):
        assert conf._dist_mode_problem(None, "no") is None       # 순차 실행
        assert conf._dist_mode_problem(0, "load") is None        # -n0 은 병렬이 아니다
        assert conf._dist_mode_problem(8, "loadfile") is None
        assert conf._dist_mode_problem(8, "loadscope") is None

    @pytest.mark.parametrize("dist", ["load", "worksteal", "loadgroup", "each"])
    def test_file_splitting_distributions_are_refused(self, conf, dist):
        problem = conf._dist_mode_problem(8, dist)
        assert problem, f"--dist {dist} 는 한 파일을 여러 워커로 찢는데 통과했다"
        assert "loadfile" in problem, "거절 메시지가 올바른 사용법을 알려 주지 않는다"

    def test_configure_refuses_an_unsafe_parallel_run(self, conf):
        """문서가 아니라 실행이 막아야 한다 — `-n auto` 만 친 사람은 문서를 안 읽는다."""
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
        """워커는 자기 몫을 순차로 돈다. 거기서 dist 를 따지면 실행이 통째로 죽는다."""
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


# ── 5. 워커가 본 것은 컨트롤러 보고에 합쳐진다 ───────────────────────────────


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
        """xdist 없는 호스트에서도 conftest 는 그대로 읽혀야 한다."""
        options = getattr(conf.pytest_testnodedown, "pytest_impl", {})
        assert options.get("optionalhook") is True, (
            "optionalhook 이 아니면 pytest-xdist 가 없는 환경에서 conftest 가 "
            "unknown hook 으로 죽는다"
        )


# ── 6. 사용법이 남아 있다 ────────────────────────────────────────────────────


class TestUsageIsWrittenDown:
    def test_testing_md_documents_both_tiers_and_parallel(self):
        text = _TESTING_MD.read_text(encoding="utf-8")
        for needle in ("--tier=fast", "--tier=standard", "--dist loadfile"):
            assert needle in text, f"TESTING.md 에 {needle} 사용법이 없다"


# ── 7. 공용 연결은 다음 사람에게 깨끗하게 넘어간다 ───────────────────────────
#
# 0394 T0014 — 병렬 실행이 실제로 드러낸 결함의 회귀 테스트.
#
# `--tier=standard -n auto --dist loadfile` 에서
# `test_auto_approved_locale_0099.py::test_migration_051_adds_column_and_round_trips`
# 한 건이 FOREIGN KEY 로 죽었다. 원인은 병렬이 아니었다. 세션 스코프 연결 하나를 네
# 파일이 공유하는데 그 중 하나가 INSERT 만 하고 commit 도 정리도 하지 않아, 다음 파일이
# **쓰기 트랜잭션이 열린 연결**을 물려받는다. sqlite 의 `PRAGMA foreign_keys` 는
# 트랜잭션 안에서 조용히 무시되므로, FK 를 끄고 도는 테스트가 FK 가 켜진 채 돌았다.
#
# 순차 실행에서는 알파벳 순서가 우연히 이 둘을 안전한 쪽으로 놓아 한 번도 안 보였다.
# 파일을 워커에 나눠 주는 순간 그 우연이 사라진 것뿐이다. 그래서 이 계약을 고정한다 —
# 병렬을 쓰든 안 쓰든, 한 테스트가 받은 연결에는 남의 트랜잭션이 열려 있으면 안 된다.
#
# 두 케이스는 이 순서로 붙어 있어야 의미가 있다. 앞은 일부러 더럽히고, 뒤는 다음 사람이
# 무엇을 받는지 본다.

_PROBE_TOKEN_SQL = (
    "INSERT INTO tokens (token_id, hash, pepper_id, project, action_scope, "
    "issued_to, created_at, expires_at, continuation_review_mode) "
    "VALUES ('tok_tier_probe','h','pep_1','__SYSTEM__','new','usr_admin',"
    "'2026-08-10T00:00:00+09:00','2026-08-11T00:00:00+09:00',0)"
)


class TestSharedConnectionIsHandedOverClean:
    def test_a_test_may_leave_a_write_transaction_open(self, test_db):
        """정리하지 않는 테스트는 실제로 있다 — 그것이 다음 사람을 깨면 안 된다."""
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

        # 죽은 자리가 정확히 여기였다: 트랜잭션이 열려 있으면 이 PRAGMA 는 아무 일도
        # 하지 않고, 그런데도 예외 하나 나지 않는다.
        test_db.execute("PRAGMA foreign_keys = OFF")
        assert test_db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        test_db.execute("PRAGMA foreign_keys = ON")

    def test_the_fixture_rolls_back_on_both_sides(self, conf):
        """빌려줄 때와 돌려받을 때 양쪽에서 닫는다 — 한쪽만이면 첫 테스트가 못 받는다."""
        source = _CONFTEST_PATH.read_text(encoding="utf-8")
        # 이름을 `body` 로 두면 안 된다. 저장소 쓰기 가드(0382)는 __file__ 에서 유도된
        # 이름을 파일 전체에서 전이적으로 추적하는데, 위쪽 `_write_module(..., body)` 가
        # 같은 이름을 쓰고 있어 `module_file` 까지 저장소 경로로 물들고, tmp_path 에
        # 쓰는 줄이 저장소 쓰기로 신고된다.
        fixture_source = source.split("def test_db(all_migrations_db)", 1)[-1]
        assert fixture_source.count("all_migrations_db.rollback()") == 2, (
            "test_db 픽스처가 yield 앞뒤에서 트랜잭션을 닫지 않는다"
        )
