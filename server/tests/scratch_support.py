"""테스트 스크래치 — 저장소 밖에 만들고, 끝나면 지운다 (0382 B0001 / NR0003 제안 2·7).

사고의 1단계가 여기였다. 두 실행 스펙이 스크래치 기본값을 ``server/.test-tmp-<번호>``
로 두는 바람에, FlowGate 가 직접 돌리지 않는 모든 실행(사람·AI 가 손으로 pytest 를
돌릴 때)은 **저장소 안에** 폴더를 만들었다. 그 폴더는 화면에서 감춰지고, 마무리 커밋의
``git add -A`` 가 통째로 삼켜서 261개가 main 에 들어갔다.

두 가지를 여기서 끝낸다.

* 기본값은 저장소 밖 임시 폴더다. ``FLOWGATE_TEST_SCRATCH`` 가 있으면 지금처럼 그걸
  쓴다 — 그 경로는 FlowGate 가 만들고 FlowGate 가 지운다(test_run_service).
* 우리가 만든 것은 우리가 지운다. 기존 스펙이 케이스 폴더를 일부러 남겼던 이유는
  "윈도우에서 읽기 전용 git 개체 때문에 정리가 실패한다"였는데(제안 7), 그건
  ``shutil.rmtree`` 에 권한 복구 핸들러를 붙이면 사라지는 문제다.
"""
from __future__ import annotations

import atexit
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path


def _clear_readonly(func, path, _exc):
    """rmtree onerror: git 이 만든 읽기 전용 개체 파일에 쓰기 권한을 주고 한 번 더."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        # 정리 실패가 테스트 결과를 뒤집으면 안 된다. 남은 것은 저장소 밖이다.
        pass


def remove_tree(path) -> None:
    """읽기 전용 파일이 있어도 지워지는 rmtree (윈도우 git 개체 대응).

    파이썬 3.12 부터 ``onerror`` 는 폐기되고 ``onexc`` 가 정본이다. 두 훅은 세 번째
    인자만 다르고 우리 핸들러는 그걸 안 쓰므로, 부르는 이름만 갈라 준다.
    """
    if sys.version_info >= (3, 12):
        shutil.rmtree(str(path), onexc=_clear_readonly)
    else:
        shutil.rmtree(str(path), onerror=_clear_readonly)


def session_scratch(name: str) -> Path:
    """이 스펙이 쓸 스크래치 루트.

    ``FLOWGATE_TEST_SCRATCH`` 가 있으면 그 아래를 쓰고(주인은 FlowGate), 없으면 저장소
    **밖** 임시 폴더를 만들어 인터프리터 종료 시 지운다(주인은 우리).
    """
    provided = os.environ.get("FLOWGATE_TEST_SCRATCH")
    if provided:
        root = Path(provided)
        root.mkdir(parents=True, exist_ok=True)
        return root
    root = Path(tempfile.mkdtemp(prefix=f"flowgate-{name}-"))
    atexit.register(remove_tree, root)
    return root
