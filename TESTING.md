# FlowGate 테스트 지침

flowgate.default.0394 — R0001 "테스트 코드 정리" §6·§7, NR0003 §12·§13-15.

이 문서는 두 가지를 정한다. **테스트를 어떻게 돌리는가**(실행 계층)와 **새 테스트를
언제 쓰는가**(추가 정책)다. 둘 다 R0001 이 정한 방향이고, 여기 적힌 수치는 NR0003 이
2026-08-08 에 실제로 돌려서 잰 값이다.

---

## 1. 실행 계층

### 왜 계층인가

전체 서버 테스트는 3,223 건에 **474 초**다. 그런데 가장 느린 40 건을 다 합쳐도 전체의
**15%**이고, 나머지 85% 는 3,223 건에 평균 147ms 씩 얇게 퍼져 있다. 즉 **느린 테스트를
지우거나 고쳐도 시간은 줄지 않는다.** 줄일 수 있는 것은 "지금 이 변경에 필요 없는 것을
안 돌리는" 쪽뿐이다. R0001 §6 이 "실행 시간이 길다는 이유로 테스트를 제거하지 말고
실행 계층을 분리하라"고 한 근거가 이것이다.

### 서버

```bash
cd server

python -m pytest tests --tier=fast       # 개발 중 빠른 회귀 확인
python -m pytest tests --tier=standard   # 작업 완료 검증
python -m pytest tests                   # release / 대규모 리팩터링 (= full, 기본값)
```

`--tier` 를 안 주면 전부 돈다. 기본값을 full 로 둔 것은 의도적이다 — 기존 명령이나
스크립트가 어느 날 조용히 더 적게 돌기 시작하는 일이 없어야 한다.

| 계층 | 목적 | 선택되는 테스트 | 실측 시간 |
|---|---|---:|---:|
| fast | 개발 중 | 1,614 | ~110초 |
| standard | 작업 완료 검증 | 2,822 | ~340초 |
| full | release·리팩터링·아키텍처 변경 | 3,244 | ~475초 |

위 표는 **2026-08 측정값(213개 파일)** 이며 다시 재지 않았다. 그 뒤 테스트가 크게 늘었고
(flowgate.v02.0113 회귀 기준선: 1800초에 550건 처리, 건당 약 3.3초), flowgate.v02.0114 는
전체 스위트를 다시 돌리지 않고 대표 선택의 전·후로만 쟀다(D 0006 9.1). 2026-10 측정값:

| 측정 (flowgate.v02.0114, Self-check 1회씩) | 전 | 후 |
|---|---:|---:|
| 대표 ① DB 준비(26건) — 템플릿 DB (T#1) | 60초 | 11초 |
| 대표 ② Git 준비(3건) — 같은 코드에서 직접 생성 / 템플릿 모드 (T#2) | 71초 | 50초 |
| Self-check 계열 회귀 923건, `-n 2 --dist loadfile` (T#4 기준선) | — | 459초 |
| 병렬 비교 233건(8개 파일) 직렬 / 처리기 2 / 처리기 4 — Self-check 승인 경로(예약·처리기 한정·슬롯 풀) (T#4) | 179초 | 124초 / 103초 |

병렬 비교의 CPU 시간 ÷ wall time 은 직렬 0.68, 처리기 2 에서 1.17, 처리기 4 에서 1.47 이었다(모두 예약 이하).
측정 중 같은 호스트의 다른 Self-check 실행은 관측되지 않았고, 승인 거절은 0건이었다.
이 묶음은 git·하위 프로세스 대기가 커서 worker 를 늘려도 처리기를 다 쓰지 못한다. worker
수만 늘리는 것은 시간 단축의 지표가 아니다.

계층 판정은 `server/tests/conftest.py` 의 `_tier_for_module` 이 한다. 순서는 이렇다.

1. `@pytest.mark.fast` / `.standard` / `.full` 을 직접 붙였으면 그것이 이긴다.
   테스트·클래스·모듈(`pytestmark`) 어디에 붙여도 된다.
2. 안 붙였으면 모듈 소스를 보고 기계적으로 정한다.
   * fast — `TestClient`, `subprocess`/git, `sleep(` 을 쓰지 않는다
   * standard — git/subprocess 를 쓰지 않고 파일명이 e2e·dialect·migration·worktree·git 계열이 아니다
   * full — 나머지

목록이 아니라 규칙인 이유가 있다. 계층을 파일 목록으로 관리하면 **새로 만든 파일이 어느
계층에도 안 들어가는 사고**가 난다. 규칙이면 새 파일도 쓰는 순간 같은 기준으로 분류되고,
예외가 필요한 파일만 맨 위에 한 줄 적으면 된다.

### 클라이언트

계층을 나누지 않는다. 1,192 건 전체가 **30 초**다. 항상 전부 돌린다.

```bash
cd client
npx vitest run
```

### 병렬 실행 (pytest-xdist)

조사 기계는 논리 코어 16 개인데 서버 테스트는 1 프로세스로 돈다. 테스트를 한 건도
지우지 않고 전체 시간을 줄일 수 있는 유일한 수단이라, 0394 T0004 에서 도입해
`requirements.txt` 에 넣고 안전성을 실제로 확인했다.

```bash
python -m pytest tests -n auto --dist loadfile
```

`--dist loadfile` **가 필수다.** 이 저장소의 테스트는 모듈/세션 스코프 임시 DB 와
`conn_mod.STORE` 치환을 쓴다. 기본값인 `--dist load` 는 한 파일의 케이스를 여러 워커에
흩뿌리므로 그 픽스처들이 서로 다른 프로세스로 찢어진다. `loadfile` 은 파일 단위로 묶어
보내므로 한 파일의 모든 케이스가 같은 워커에서 같은 픽스처를 본다.

### Self-check 에서의 병렬 — 처리기 예약과 호스트 상한 (flowgate.v02.0114 T#4)

Self-check 는 호스트 전체에 처리기(논리 프로세서) 상한을 둔다. 직렬 실행과 pytest 가 아닌
명령을 포함해 **모든** Self-check 실행이 처리기를 예약하고(최소 1), 같은 호스트(`node_key`)의
실행 중·복구 대기 실행의 예약 합은 상한을 넘지 않는다. TS 실행은 Self-check 가 아니므로 세지
않는다.

* 실행 요청의 `parallel_share` 가 희망 몫이다(기본 1). 받은 예약 R 은 응답의 `cpu_reservation` 이다.
* 실행의 프로세스 트리 전체는 **운영체제가 R개 처리기에만 묶는다**(Windows: 실행 Job 의 처리기
  한정, POSIX: supervisor 의 처리기 집합). 도구가 worker 를 몇 개 만들든 동시에 도는 것은 R개를
  넘지 않는다. 더 만들면 실패하지 않고 느려질 뿐이다.
* `server/tests` 의 pytest 는 실행 하나의 **worker 슬롯 풀**(크기 R)을 나눠 쓴다. `-n 8` 이나
  `-n auto` 를 줘도 실제 worker 는 빈 슬롯 수로 낮아지고, 테스트 안에서 띄운 pytest(중첩)는 남은
  슬롯만 쓰며 없으면 슬롯 없이 직렬로 돈다. 결과 블록의 `parallel` 에 요청값·적용값·슬롯 출처가 남는다.
* 예약이 모자라면 실행 기록 없이 **409 `selfcheck_host_capacity_exhausted`** 로 거절된다. 잠시 뒤
  같은 요청을 다시 보낸다(대기열 없음, 백그라운드로 넘기지 않음).

기본값(2026-10-31 승인 경로 측정 근거. 반영 뒤 운영 실행 기록의 호스트 예약 합·거절 건수로 다시 확인한다):
상한 = min(4, 논리 프로세서 수), 직렬 예비분 1, 희망 몫 기본 1.
1을 넘는 몫은 직렬 예비분을 남기는 범위에서만 주므로 빈 호스트에서 `parallel_share=4` 는 3 을
받는다. 상한은 서버 환경 변수 `FLOWGATE_SELFCHECK_HOST_LIMIT`(논리 프로세서 수를 넘으면 그 수),
예비분은 `FLOWGATE_SELFCHECK_SERIAL_RESERVE` 로 바꾼다.

```text
큰 선택:     pytest -q -n 2 --dist loadfile tests/...   + parallel_share 2
작은 선택:   pytest -q tests/test_x.py                    (parallel_share 생략 = 1)
```

고정 포트를 쓰는 연결 통합 테스트, 같은 소스에 산출물을 쓰는 빌드·타입 검사, 단일·소수 파일
선택은 병렬로 돌리지 않는다. 기준선과 최종 회귀는 같은 호스트·같은 `parallel_share`·같은 `-n`
으로 돌린다 — 적용 worker 나 처리기 한정 수가 다르면 기준선을 재사용할 수 없다.

### 큰 회귀를 1800초 안에 넣는 법 — 분할·`parallel_share`·시험 단위 timeout (flowgate.v02.0146 T#2)

Self-check 상한(1800초)은 올리지 않는다. 19~21개 파일 회귀가 상한을 넘은 까닭은 세 가지가 겹쳤기 때문이다
(2026-11-07 측정, Windows, Python 3.14, pytest 8.3.4 / xdist 3.6.1, 호스트 처리기 예약 1~3).

1. **`-n 4` 만 주고 `parallel_share` 를 안 주면 처리기 1개에서 worker 1개로 돈다.** 결과 블록의
   `parallel` 은 `requested 4 / applied 1` 이다. 같은 4개 파일 묶음이 `-n 4`(share 1) 264초, `-n 2`(share 2)
   203초, `-n 4`(share 4 → 예약 3) 193초였다. `-n` 과 `parallel_share` 는 반드시 같은 수로 준다.
2. **시간은 git 서브프로세스가 쓴다.** 직렬 1처리기에서 `test_fg_gate_publish_0085` 13건 499초,
   `test_fg_document_rejection_0126` 29건 388초, `test_fg_card_settings` 의 git 시험 2건 약 255초,
   `test_fg_provider_failure_retry_0121` 29건 150초다. 느린 시험을 지워도 줄지 않고, `--dist loadfile` 은
   파일 하나를 한 worker 에 주므로 큰 파일이 최소 시간을 정한다. 그래서 git 시험 2건을
   `test_fg_card_settings_merge_target_git_0146.py`, `test_fg_card_settings_no_worktree_git_0146.py` 로 옮겼다(본문 동일).
3. **일부 파일은 다른 파일이 먼저 남긴 전역 상태(문서 쓰기 게이트·포트 등록, run id allocator 계약, `tests/flowgate` import 경로)에 기댔다.**
   혼자 돌리거나 `--dist loadfile` 로 worker 배정이 달라지면 setup error 가 났다. 이제 각 시험 harness 가 자기 전제를
   스스로 만든다(제품 계약·단언은 그대로): 공유 harness(`test_fg_card_settings.env`)와 `test_fg_card_documents` 는
   `fg_harness_write_gate.install`(실제 게이트, harness 자신의 시드 행 쓰기만 통과), `tr2_approval` 은 서버 시작이 등록하는
   문서 쓰기 포트 4개, `card_settings_restart` 는 자기 SQLite 저장소와 초기화된 run id 계약 + 실제 게이트, `0136` 은
   자기 저장소의 run id 계약 초기화, `tests/conftest.py` 는 `tests/flowgate` import 경로.

권장 실행 계획(각 실행은 `pytest -q -rEf -n 3 --dist loadfile <파일들>` + `parallel_share 3`, `timeout_seconds` 1200):

| 묶음 | 파일 | 실측(2026-11-07, 마지막 변경 뒤, 예약 2 — 요청 3) |
|---|---|---|
| G1 카드 설정 | `test_fg_card_settings*.py`(7), `test_fg_settings_{capability_0126,context_0130,retry_0126,retry_completion_0126}.py` | 199 통과, 실패·error 0, 528초 |
| G2 git 게이트·반려 | `tests/test_fg_gate_intake_0085.py`, `tests/test_fg_gate_publish_0085.py`, `test_fg_document_rejection_0126.py` | 108 통과, 실패·error 0, 708초 |
| G3 검수·재작업 | `test_fg_provider_failure_retry_0121.py`, `test_review_loop_rework_binding_0136.py`, `test_fg_review_{duplicate_start,retry_verdict}_0128.py`, `test_fg_rewind_rework_0118.py`, `test_fg_rejection_history_0126.py`, `test_fg_card_documents.py` | 161 통과, 실패·error 0, 242초 |

한 Self-check 가 한 hop 에서 쓰는 시간은 가장 긴 G2 도 708초(상한의 39%)다. 재작업 hop 예산(`rework_timeout_sec` 3600)에는
1800초 상한이 2번밖에 들어가지 않으므로, 전체를 한 번에 돌리지 말고 변경과 직접 관련된 묶음부터 돌린다.
고정 `parallel_share` 를 요청해도 호스트가 바쁘면 예약이 더 적게 나온다(`cpu_reservation` 을 본다. 위 실측은 3을 요청해
2를 받았다. 예약 3일 때는 더 짧았다: G1 271초, G2 726초 → 예약에 따라 편차가 크므로 상한 대비 여유를 본다).

**시험 단위 timeout:** `tests/hang_guard.py` 가 모든 시험에 `FLOWGATE_TEST_TIMEOUT`(기본 900초, `0` 이면 끔) 또는
`@pytest.mark.timeout(초)` 를 적용한다. 한계를 넘기면 시험 id 와 모든 스레드 스택을 stderr 에 찍고 종료 상태 70 으로
끝난다(통과로 처리되지 않는다. xdist 에서는 worker 가 죽은 시험으로 실패 보고되고 나머지는 계속된다).
`pytest-timeout` 이 설치·로드돼 있어도 이 guard 는 물러나지 않는다: 그 플러그인은 `--timeout` / ini `timeout` /
`PYTEST_TIMEOUT` 으로 한계를 따로 주지 않으면 아무 시험도 끊지 않기 때문이다(물러나면 위 환경값·기본값·0 계약이 조용히
사라진다). 둘 다 켜져 있으면 먼저 닿는 한계가 이긴다. 한계는 가장 느린 정상 시험(약 200초)보다 높게만 두고, 느린 시험을 가리려고 올리지 않는다.

---

## 2. 새 테스트 정책 (v0.2 이후)

### 파일 배치 (NR0003 §7)

- 단일 소스 테스트는 `server/<소스 상대경로>/<모듈>.py`에 대응하는 `server/tests/<소스 상대경로>/test_<모듈>.py`에 둔다. 여러 파일로 나눌 때는 기능 또는 회귀 번호 접미사를 붙이고, 전체 테스트 트리에서 basename을 고유하게 유지한다.
- 여러 모듈 통합은 `tests/integration/<기능>/`, 실제 실행 흐름 E2E는 `tests/e2e/<기능>/`, 단일 모듈에 귀속되지 않는 교차 회귀는 `tests/regression/<기능>/`에 둔다.
- 소스 텍스트나 전역 구조를 검사하는 가드는 `tests/architecture/`에 둔다. 티켓 단위 회귀를 일괄 `regression/`으로 옮기지 않는다.
- 공통 fixture는 루트 `tests/conftest.py` 범위를 유지한다. 필요한 디렉터리만 만들고 중복 테스트 사본과 무관한 코드 변경은 추가하지 않는다.

### 무엇을 테스트할지는 위험도로 정한다

기존 비율을 맞추려고 테스트를 추가하지 않는다. **테스트 LOC 나 테스트/제품 비율에 대한
목표값을 두지 않는다.** (NR0003 §3.2 는 이 방침을 수치로도 뒷받침한다 — R0001 이 인용한
클라이언트 "테스트 25,371 / 제품 6,858" 은 화면 코드 `.vue` 39,945 줄이 제품에서 빠진
값이고, 실제 비율은 3.7 이 아니라 약 0.44 다. 비율은 재는 방법에 따라 이렇게 흔들린다.)

**우선 쓴다**

* 데이터 손실 가능성이 있다
* 상태 전이가 복잡하다
* 장애 시 복구가 어렵다
* 외부 API 계약
* 권한·보안
* 동시성
* workflow 핵심 로직
* 과거에 실제로 난 버그

**선택**

* 단순 UI, 표현 방식
* 구현 세부사항
* 수동으로 쉽게 확인되는 저위험 기능

### 어떻게 쓸지 — 0394 에서 얻은 규칙 5개

앞의 넷은 취향이 아니다. 상시 실패 44 건을 전수 분류한 결과, **28 건 중 21 건이 제품이
틀려서가 아니라 테스트가 구현에 묶여 있어서 깨진 것**이었고, 그 21 건이 아래 규칙 중
하나로 예방됐을 것이다.

**(1) 내부 함수를 직접 호출하거나 스텁하지 않는다. 엔드포인트나 공개 함수로 들어간다.**

`_handle_new` / `_handle_review` / `_handle_edit` / `_handle_test_run` 이 이벤트 루프
차단을 없애려고 `async def` 에서 평범한 `def` 로 바뀌고 `anyio.to_thread` 로 넘어갔다.
**POST /inbox 의 요청과 응답은 한 글자도 안 바뀌었는데** 그 함수를 `asyncio.run(...)` 으로
직접 부르던 테스트 8 건이 한꺼번에 죽었다. 엔드포인트로 들어갔다면 아무 일도 없었다.
서버 테스트에서 inbox 를 부를 때는 `tests/inbox_client.py` 의 `post_inbox()` 를 쓴다.

스텁도 같다. `storage_paths.src_root` 를 스텁한 테스트 6 건은 제품이 그 함수를 그만
부르고 `git_service.base_src_root` 로 옮겨간 순간 **스텁이 아무 데도 안 걸린 채** 실제
경로를 보게 됐고, 증상은 전부 404 라 원인이 겉으로 드러나지 않았다. 스텁이 빗나가도
아무도 알려주지 않는 것이 이 방식의 본질적 약점이다. 경로·설정처럼 **입력으로 바꿀 수
있는 것은 입력으로 바꾼다** (예: `FLOWGATE_STORAGE_DIR`).

**(2) 제품 소스를 문자열로 읽어 단언하는 테스트는 전역 규칙에만 쓴다.**

티켓 하나의 배치·순서·"어느 파일에 있는가" 를 고정하는 용도로는 쓰지 않는다.
`MainPanel.aiRunExclusive.0378.spec.ts` 는 `.vue` 파일 안에서 문자열이 나타나는 순서를
검사한다. 그 테스트를 추가한 바로 그 변경이 **실제 렌더링을 검증하는 테스트 11 건을
깨뜨렸는데, 이 소스 텍스트 테스트는 그때도 지금도 초록이다.** `.document-modal` 규칙이
`MainPanel.vue` 에서 `shared/app.css` 로 옮겨간 것만으로 깨진 테스트도 같은 종류다 —
스타일은 멀쩡했고 검사만 틀렸다.

전역 규칙(중복 정의 금지, 이벤트 루프 차단 금지, 한국어 유출 금지, 마이그레이션 번호
유일성)에 쓸 때는 **처음부터 전 범위를 검사한다.** 번호 중복이 8 묶음인데 빨간불이
1 건뿐이었던 이유가 이것이다 — 076 을 검사하는 가드가 076 만 보도록 쓰여 있었고, 앞선
7 묶음은 검사하는 테스트가 아예 없었다. 규칙이 전역이면 검사도 전역이어야 한다.

**(3) 스키마를 손으로 만들지 않는다.**

`test_auth.py` 는 87 개 마이그레이션 중 001/002/005 만 적용하는 픽스처를 갖고 있었고,
`067a_auth_sessions.sql` 이 들어온 날 7 건이 `no such table` 로 죽었다. 스키마가 필요하면
`conftest.py` 의 `migrated_sqlite_db(name, seed_sql)` 을 쓴다. 전부 적용하고, 실패를
삼키지 않는다. 제품 객체를 흉내 내는 double 도 같은 이유로 위험하다 — 같은 파일의
`_SqliteStore` 는 `transaction()` 이 없어서 세션 기능이 그 위를 지나가는 순간 깨졌다.

**(4) 건너뛸 수 있으면, 건너뛰었다는 사실이 결과에 남아야 한다.**

`test_git_integration_0115.py` 는 107 건이다. git 이 없는 호스트에서는 **107 건이 통째로
사라지고도 결과는 "실패 0"** 으로 나온다 — 전부 통과한 실행과 글자가 같다. 그래서
서버 테스트는 호스트 능력(git·심볼릭 링크·api 소스 트리·run.bat)과 그것 때문에 건너뛴
건수를 실행 끝에 보고한다. release 검증처럼 **정말로 전부 돌았어야 하는 실행**은

```bash
FLOWGATE_REQUIRE_FULL_SUITE=1 python -m pytest tests
```

로 돌린다. 능력이 없는 호스트면 조용히 건너뛰는 대신 실패한다.

**(5) 환경변수는 빌려 쓰고, 끝나면 원래 값으로 돌려준다. 지우지 않는다.**

많은 스위트가 **import 시점에** 자기 저장소 루트를 발행한다
(`os.environ["FLOWGATE_STORAGE_DIR"] = tempfile.mkdtemp(...)`). pytest 는 아무것도
실행하기 전에 모든 모듈을 import 하므로, 실행이 시작될 때 그 값들은 이미 다 올라와
있다. 그래서 어떤 테스트가 정리하면서 그 변수를 **지우면** 자기 것만 지우는 게 아니라
남의 것까지 지운다.

`test_numbering.py` 와 `test_outbound_single.py` 가 `finally` 에서
`del os.environ["FLOWGATE_STORAGE_DIR"]` 을 했고, 뒤에 오는
`test_worktree_resolution_e2e_0280.py` 는 자기 임시 루트를 잃은 채 `ensure_worktree` 가
엉뚱한 자리에 만들어 **7 건이 setup 에서 error** 로 죽었다. 그 파일만 따로 돌리면 8 건
전부 통과한다. 이 건이 오래 안 보인 이유가 두 가지인데, 둘 다 기억해 둘 만하다 —
pytest 는 **error 를 failure 와 따로 세고**, `-rf` 요약에는 error 가 안 찍힌다. 전체
실행은 `-rEf` 로 본다.

되돌리는 방법은 `monkeypatch.setenv` 를 쓰는 것이 가장 간단하다(원래 없었으면 없던
상태로, 있었으면 그 값으로 pytest 가 되돌린다). 직접 쓸 때는 이전 값을 먼저 받아 두고
`finally` 에서 그것을 복원한다. 안전망으로 `conftest.py` 가 모듈 경계에서 FLOWGATE_*
변수를 원래대로 되돌리고, 흘린 모듈 이름을 실행 끝에 찍는다 — 되돌려 주기는 하지만
그건 안전망이고, 고칠 자리는 그 모듈이다.

### 클라이언트에서 더

* **컴포넌트를 마운트하는 테스트는 준비가 끝난 트리를 본다.** `MainPanel` 은
  `v-if="aiRunBootstrapPending"` 로 시작하는 비동기 게이트를 갖고 있고, 이 한 줄 때문에
  `$nextTick()` 만 기다리던 테스트 11 건이 5 개 파일에서 동시에 깨졌다. MainPanel 을
  마운트할 때는 `tests/helpers/mountMainPanel.ts` 의 `mountMainPanel()` 을 쓴다 —
  부트스트랩을 기다려 주고 공용 스텁 목록도 갖고 있다.
* **"X 가 없다" 를 단언하기 전에 화면이 그려졌는지부터 확인한다.** 게이트가 닫혀 있으면
  아무것도 마운트되지 않으므로 그런 단언은 공짜로 통과한다. 실제로
  `MainPanel.reviewRequestMention.spec.ts` 의 통과 중이던 케이스는 심어 놓은
  `pending_review` 를 **한 번도 쓰지 않고** 통과하고 있었다.
  `expectDocumentBranchMounted(wrapper)` 를 먼저 부른다.
* **테스트는 진짜 서버에 접속하지 않는다.** `tests/setup/blockNetwork.ts` 가 XHR 과
  fetch 를 막는다. 응답이 필요하면 `@shared/api` 를 mock 한다. (막기 전에는 4 개 파일이
  개발 서버 주소로 진짜 요청을 보내고 있었고, 아무도 안 듣고 있어서 통과했다 —
  개발 서버가 떠 있는 기계에서는 결과가 달라진다.)

### 무엇을 지우는가

**테스트가 많다는 이유로 지우지 않는다.** 지우는 경우는 하나다: **제품이 그 기능을 더는
갖고 있지 않을 때.** 그때도 "고쳐서 통과시키기" 는 답이 아니다 — 그것은 걷어낸 기능을
되살리는 일이고, 테스트를 고치는 사람이 혼자 내릴 결정이 아니다. 지운 이유를 그 파일에
남긴다(0394 에서 `test_worker_token_hint_0238.py` 가 그 예다).

중복은 **지우기보다 통합을 먼저** 본다. 다만 파일 통합 자체를 목적으로 대규모 변경은
하지 않는다. 합칠 때는 **각 케이스가 어느 버그를 막고 있었는지(이름·주석)를 잃지 않는다** —
티켓 번호가 붙은 145 개 파일은 "파편화" 대상인 동시에 "실제 버그 회귀 테스트" 라서,
정보를 잃으면 R0001 §4.6 위반이 된다.
