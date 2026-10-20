# Settings Feature

v0.2 환경 설정 (flowgate.v02.0020, approved mockups 11~14; reorganised by flowgate.v02.0076; laid
out after the MirageGlass deck `1jnmunhp` screen ⑩ "환경 설정" by flowgate.v02.0077).

- Screen (flowgate.v02.0077, mockup ⑩): every settings screen is the settings navigation
  (`components/SettingsNav.vue`: 환경 설정 개요, the scopes of the shared menu with their reach, and
  under the scope shown the page's own sub-navigation) beside one content card — head (icon, title,
  one-line description, actions) and a body of sections / setting rows / tables (`settings.css`).
  The 0076 breadcrumb, scope tab row and sub-navigation title are gone. The overview uses the same
  navigation without `SettingsShell`. 시스템's pages are listed under 운영 / 실행 자원 / 계정 · 프로젝트.

- Routes: every `/app/settings` screen is a lazily loaded child of `SettingsLayout`
  (`routing.ts`, registered in `app/router/index.ts`). The layout shows a neutral loading state
  while `/auth/me` is still out and mounts no settings view until then.
- Access (`access.ts`): 프로젝트 / 시스템 screens are shown to system administrators only; anyone
  else is replaced onto the overview with a notice before the view mounts. The retired
  `/settings/workspaces[/…]` and `/settings/system/security` URLs, and any `/settings/*` path
  without a registered view, are replaced onto the overview (no query/hash kept). This is a
  GUI exposure rule only — the server's permission checks are unchanged.
- Menus (`settingsMenu.ts`): the hub cards, the scope headings of `SettingsNav` and the 시스템
  sub-navigation list a screen only once its route is registered, so nothing is shown as
  "준비 중" or disabled. Registered today: the overview, 내 설정 › 계정 · 화면 / 보안 · 세션,
  프로젝트 (selection + 기본 정보 / 저장 경로 / Git) and 시스템 › 일반 / 시스템 Agent / 사용자 / 전체 프로젝트.
- 시스템 › 일반 (`SystemGeneralSettingsView`; flowgate.v02.0076 T#3, D §2.5), administrators only:
  - 저장 Root: GET `/api/v1/system/storage-root` (configured_value / effective_value /
    effective_source / env_override), read only — no input, save or move, and `storage_root` is
    never sent. `/system/settings/storage-root` is a different contract and is not used.
  - Scratch 보존 기간 and AI 반복 상한 from the rows of GET `/api/v1/system/settings`, saved
    separately on PATCH `/api/v1/system/settings`: `scratch_ttl_value` + `scratch_ttl_unit`
    together in one request (strings, never minutes; the server writes the pair in one
    transaction), `ai_repeat_count_max` alone (1~30; lowering it shows the re-evaluation impact
    first). A save counts only when `updated` and a re-read GET both match; invalid input is never
    sent, a failure keeps the input. The 저장되었습니다 notice is for the saved value only: editing
    again takes it away.
  - 미저장 이탈 보호 (D §3.2): with an unsaved input or a save still out, a move to another route
    asks first (계속 편집 cancels it, no request) and a refresh / tab close gets the browser's
    question. Losing the session or the administrator role is never held by it. The rule lives in
    `components/useUnsavedLeaveGuard.ts`, shared with 시스템 Agent.
- 시스템 › 시스템 Agent (`SystemAgentSettingsView`; flowgate.v02.0076 T#4, D §2.6), administrators
  only, on the single `/api/v1/system/agents` family (`api/v1/agent_routes.py`, the one the old
  `client/src/settings` AgentsView used):
  - Screen (flowgate.v02.0077): counts (등록된 원격 Agent / 온라인 / 확인 필요), filter chips (전체 /
    온라인 / 확인 필요 / 원격 / 로컬) and a name · ID search over the one GET answer, then a table of
    Agent / 상태 / 기능. The state per row (`agentState.ts`) follows the server's order: local →
    사용 중지 → 미등록 / 등록 대기 → 해지 → 프로토콜 비호환 → 온라인 / 오프라인; 확인 필요 = an enabled
    remote Agent that is waiting, refused, offline or has a capability allowed but not reported.
    The capability tags read 사용 가능 / 허용했지만 보고 안 함 / 보고했지만 허용 안 함 / 허용 안 함.
  - A row opens the Agent in a drawer above the list (닫기, the backdrop and Esc close it; unsaved
    input is asked about first; closing drops the selection, the typed values and a shown token).
    The drawer holds the state notice, 연결 / 실행 (the GET's fields), 기능 (서버 허용 switch ×
    Agent 보고 = 결과), 설정 (name, local path), 등록 · 자격 증명 and, in its foot, 삭제 / 사용 중지 ·
    다시 사용 / 저장. 새 Agent opens in the same drawer. No form stays open beside the list.
  - `?agent=<id>` (the overview's 확인) opens that Agent once the list is read; the query is dropped.
  - GET `/system/agents` (the list answer carries each Agent's detail); POST `/system/agents` for a
    local (`connection_mode` null, optional executable path) or remote (`agent_pull`, no path)
    Agent with the ai_cli / ai_api / storage capabilities (settings only — no storage, AI-job or
    personal-Agent control); PATCH `/system/agents/{aid}` with only the changed name / path /
    capabilities, or `enabled` alone for 사용 중지 · 다시 사용 (never `location`).
  - POST `…/enrollments` and `…/credential/revoke` send the route's Empty body `{}`; DELETE only for
    an Agent without registration history (the server answers 409 otherwise).
  - State shown as the GET reports it: enabled, registration / credential / enrollment status,
    online (local = not applicable), last heartbeat, protocol version · compatibility, agent
    version, OS / architecture, configured / reported / effective capabilities. Online is not
    "can run" or "running".
  - 토큰 발급 · 자격 해지 · 삭제 · 사용 중지 are confirmed first with their impact (cancel = no
    request; no second request while one is out). A change is done only when its answer and the
    list re-read agree; a refusal (403 / 409 / 503 …) keeps its message and the re-read list.
  - The enrollment token is held in the view's memory from the 201 answer only, and dropped on
    another Agent, a change, revoke, delete or leaving. Never logged, stored, put in the URL or a
    toast. `/agent/enrollments` and `/agent/heartbeat` (the Agent runtime) are never called.
  - Every request belongs to the epoch it was sent in; leaving the screen, a lost token, another
    person or a lost administrator role ends it. An answer arriving after that is dropped: no
    token kept, no re-read, no notice.
  - The form keeps the confirmed values apart from the typed ones. A re-read moves untouched fields
    to the server's new value (another administrator's change is never sent back) and keeps the
    touched ones; PATCH carries the touched fields only.
  - Unsaved name / path / capability or create input is guarded like 시스템 › 일반, also when
    switching to another Agent or starting 새 Agent inside the screen. 저장하지 않고 나가기 throws
    the typed values away: cancelling 새 Agent shows the confirmed values again.
- 프로젝트 (flowgate.v02.0076 T#5, D §2.3 / §2.4), administrators only, read only. Screen
  (flowgate.v02.0077): the picker (by project name) and 다시 불러오기 sit in the navigation; the 프로젝트
  선택 panel shows only while no listed project is open, so a section is the only card. 기본 정보 no
  longer shows the project ID (an internal key, kept in the URL).
  - `ProjectSettingsLayout` is the parent of `/settings/project` and
    `/settings/project/:projectId/{general,paths,git}`. It reads GET `/api/v1/projects?status=all`
    once per entry (`components/useProjectList.ts`); the picker shows active projects by default and
    `비활성 포함` adds the rest — a display rule, not a server permission filter. `__SYSTEM__` is dropped
    like every other list consumer does.
  - No project in the URL: a selection hint, no detail GET. A projectId missing from the list:
    찾을 수 없음, no detail GET (navigation only, not a permission check). `/settings/project/p1`
    without a section is an unknown settings URL (the overview).
  - Only a listed project mounts its section view, keyed by the project, so switching project
    unmounts the old view and its late answers are dropped. 다시 불러오기 repeats the list GET only;
    a selected project gone from the new list clears the selection (notice, back to the picker).
  - 기본 정보 (`ProjectGeneralReadView`): the list row's ID / name / description / active / modules;
    no request, no save.
  - 저장 경로 (`ProjectPathsReadView`): GET `/api/v1/projects/{id}/storage-root` only —
    configured_value / project_override / effective_value / effective_source / env_override as
    answered, null = 미설정. Never `/system/settings/storage-root`, settings or paths, not even as a
    fallback; no path is worked out on the client.
  - Git (`ProjectGitReadView`): GET `/api/v1/projects/{id}/git/config` only. configured=false /
    config=null is 미설정, apart from a failed read. Shows repo_url, provider, enabled, base_branch,
    default_finalize_action, author name / email and whether a secret is registered — the masked
    secret is not kept or shown. No secret input, author save, merge / push / wait edit, disconnect or
    connection test; the Designer's GitConnectWindow / DesignerGitPanel stay where they are.
  - 401 / 403 / 500 are shown as errors (접근 불가 / 실패) with a retry of the same GET — never as an
    empty list, 미설정 or success. Requests live in an epoch (`components/useLiveRequests.ts`, the
    시스템 Agent rule): leaving, sign-out, a lost administrator role or a lost token drops the data and
    every answer still out. A lost token is the whole project area's: whichever project view finds it
    (before sending — every GET asks first —, on an answer, or on the 1 s token check of a screen with
    nothing out) ends the list, the picker and every section view together and shows a notice; after
    that 다시 불러오기 / 다시 시도 send nothing.
  - Never sent from here: PATCH settings / paths, storage migrate, Git PUT / DELETE / test-connection /
    provision / reconnect / finalize, legacy `/projects` · `/list/projects` · `/project-settings/pick-folder`.
- 시스템 › 전체 프로젝트 (`SystemProjectsReadView`; T#5, D §2.8): the same list GET and handling,
  active and inactive rows with a 전체 / 활성 / 비활성 display filter; a row opens
  `/settings/project/:projectId/general`. No Root / Git GET on entry, no create / archive / restore /
  delete. The server answers this list to any signed-in person (`get_current_user` only); the
  administrator-only GUI does not close that existing 0075 gap.
- 시스템 › 사용자 (`SystemUsersView`, `/api/v1/system/users…`): T#3 removed the Workspace tile,
  filter, column, drawer chips and create-time picker; the create body has no `workspace_id`.
  After every change the list is read again; only when that GET succeeds is the change reported
  done. A failed re-read keeps its error above the last list (retry = that GET only, never the
  change), closes the drawer and shows a "not confirmed" warning instead of the success notice.
- 내 설정 (`meApi.ts`, `components/meNav.ts`; flowgate.v02.0076 T#2), open to every signed-in person.
  Screen (flowgate.v02.0077): sections of setting rows; 보안 · 세션 lists the sessions as a table and
  opens the three password inputs with 변경 (취소 closes them and forgets the input).
  - 계정 · 화면 (`MeAccountSettingsView`): `/auth/me` username/email read-only, 표시 언어 ko/ja/en
    (`preferred_locale`; `@shared/api` sends it as X-Locale), 완료 카드 유지 시간 on
    GET/PATCH `/api/v1/me/ui-settings` (shared domain -1, 0, 30 … 1440; a failed save restores the
    last confirmed value).
  - 보안 · 세션 (`MeSecuritySettingsView`): GET `/auth/sessions`, DELETE `/auth/sessions/{sid}` for
    other sessions, POST `/auth/sessions/revoke-others` after a confirmation, the current session via
    POST `/auth/logout` (`core/auth/logout.ts`), POST `/auth/password/change`. No TOTP control: no
    self-service API reports the person's TOTP state and `/auth/totp/setup` would replace a secret.
- 개요 › 확인 필요 (flowgate.v02.0077, mockup ⑩): for a system administrator the overview reads
  GET `/api/v1/system/agents` once and lists the Agents that need a look, each with 확인 (→ 시스템
  Agent with that Agent open). A non-administrator's overview sends nothing, and an answer that
  arrives after leaving or after the role went is dropped.
- The Workspace and 시스템 › 보안 views and their API adapter functions stay in the source, but no
  route opens them any more.
