# Settings Feature

v0.2 Environment Settings (flowgate.v02.0020, approved mockups 11~14; reorganised by flowgate.v02.0076; laid
out after the MirageGlass deck `1jnmunhp` screen ⑩ "Environment Settings" by flowgate.v02.0077).

- Screen (flowgate.v02.0077, mockup ⑩): every settings screen is the settings navigation
  (`components/SettingsNav.vue`: Settings overview, the scopes of the shared menu with their reach, and
  under the scope shown the page's own sub-navigation) beside one content card — head (icon, title,
  one-line description, actions) and a body of sections / setting rows / tables (`settings.css`).
  The 0076 breadcrumb, scope tab row and sub-navigation title are gone. The overview uses the same
  navigation without `SettingsShell`. System's pages are listed under Operations / Execution resources / Accounts / Projects.

- Routes: every `/app/settings` screen is a lazily loaded child of `SettingsLayout`
  (`routing.ts`, registered in `app/router/index.ts`). The layout shows a neutral loading state
  while `/auth/me` is still out and mounts no settings view until then.
- Access (`access.ts`): Project / System screens are shown to system administrators only; anyone
  else is replaced onto the overview with a notice before the view mounts. The retired
  `/settings/workspaces[/…]` and `/settings/system/security` URLs, and any `/settings/*` path
  without a registered view, are replaced onto the overview (no query/hash kept). This is a
  GUI exposure rule only — the server's permission checks are unchanged.
- Menus (`settingsMenu.ts`): the hub cards, the scope headings of `SettingsNav` and the System
  sub-navigation list a screen only once its route is registered, so nothing is shown as
  "Coming soon" or disabled. Registered today: the overview, My settings › Account / Display / Security / Sessions,
  Project (selection + General information / Storage paths / Git) and System › General / System Agent / Users / All projects.
- System › General (`SystemGeneralSettingsView`; flowgate.v02.0076 T#3, D §2.5), administrators only:
  - Storage Root: GET `/api/v1/system/storage-root` (configured_value / effective_value /
    effective_source / env_override), read only — no input, save or move, and `storage_root` is
    never sent. `/system/settings/storage-root` is a different contract and is not used.
  - Scratch retention, AI attachment read limit and title AI from the rows of GET `/api/v1/system/settings`,
    saved separately on PATCH `/api/v1/system/settings`: `scratch_ttl_value` + `scratch_ttl_unit`
    together in one request (strings, never minutes; the server writes the pair in one
    transaction), `attach_read_max_mb` alone. The AI repeat limit is not on this screen (v02test.0096 F96-11):
    the max repeat count is a personal setting only (⚙ AI tab, 1~30). A save counts only when `updated` and a re-read GET both match; invalid input is never
    sent, a failure keeps the input. The Saved notice is for the saved value only: editing
    again takes it away.
  - Unsaved-change navigation guard (D §3.2): with an unsaved input or a save still out, a move to another route
    asks first (Keep editing cancels it, no request) and a refresh / tab close gets the browser's
    question. Losing the session or the administrator role is never held by it. The rule lives in
    `components/useUnsavedLeaveGuard.ts`, shared with System Agent.
- System › System Agent (`SystemAgentSettingsView`; flowgate.v02.0076 T#4, D §2.6), administrators
  only, on the single `/api/v1/system/agents` family (`api/v1/agent_routes.py`, the one the old
  `client/src/settings` AgentsView used):
  - Screen (flowgate.v02.0077): counts (Registered remote Agents / Online / Needs attention), filter chips (All /
    Online / Needs attention / Remote / Local) and a name · ID search over the one GET answer, then a table of
    Agent / Status / Capabilities. The state per row (`agentState.ts`) follows the server's order: local →
    Disabled → Unregistered / Enrollment pending → Revoked → Protocol incompatible → Online / Offline; Needs attention = an enabled
    remote Agent that is waiting, refused, offline or has a capability allowed but not reported.
    The capability tags read Available / Allowed but not reported / Reported but not allowed / Not allowed.
  - A row opens the Agent in a drawer above the list (Close, the backdrop and Esc close it; unsaved
    input is asked about first; closing drops the selection, the typed values and a shown token).
    The drawer holds the state notice, Connection / Execution (the GET's fields), Capabilities (Server permission switch ×
    Agent report = Result), Settings (name, local path), Enrollment / Credentials and, in its foot, Delete / Disabled ·
    Enable again / Save. New Agent opens in the same drawer. No form stays open beside the list.
  - `?agent=<id>` (the overview's Inspect) opens that Agent once the list is read; the query is dropped.
  - GET `/system/agents` (the list answer carries each Agent's detail); POST `/system/agents` for a
    local (`connection_mode` null, optional executable path) or remote (`agent_pull`, no path)
    Agent with the ai_cli / ai_api / storage capabilities (settings only — no storage, AI-job or
    personal-Agent control); PATCH `/system/agents/{aid}` with only the changed name / path /
    capabilities, or `enabled` alone for Disabled · Enable again (never `location`).
  - POST `…/enrollments` and `…/credential/revoke` send the route's Empty body `{}`; DELETE only for
    an Agent without registration history (the server answers 409 otherwise).
  - State shown as the GET reports it: enabled, registration / credential / enrollment status,
    online (local = not applicable), last heartbeat, protocol version · compatibility, agent
    version, OS / architecture, configured / reported / effective capabilities. Online is not
    "can run" or "running".
  - Issue token · Revoke credentials · Delete · Disabled are confirmed first with their impact (cancel = no
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
  - Unsaved name / path / capability or create input is guarded like System › General, also when
    switching to another Agent or starting New Agent inside the screen. Leave without saving throws
    the typed values away: cancelling New Agent shows the confirmed values again.
- Project (flowgate.v02.0076 T#5, D §2.3 / §2.4), administrators only, read only. Screen
  (flowgate.v02.0077): the picker (by project name) and Reload sit in the navigation; the Project
  Selection panel shows only while no listed project is open, so a section is the only card. General information no
  longer shows the project ID (an internal key, kept in the URL).
  - `ProjectSettingsLayout` is the parent of `/settings/project` and
    `/settings/project/:projectId/{general,paths,git}`. It reads GET `/api/v1/projects?status=all`
    once per entry (`components/useProjectList.ts`); the picker shows active projects by default and
    `Include inactive` adds the rest — a display rule, not a server permission filter. `__SYSTEM__` is dropped
    like every other list consumer does.
  - No project in the URL: a selection hint, no detail GET. A projectId missing from the list:
    Not found, no detail GET (navigation only, not a permission check). `/settings/project/p1`
    without a section is an unknown settings URL (the overview).
  - Only a listed project mounts its section view, keyed by the project, so switching project
    unmounts the old view and its late answers are dropped. Reload repeats the list GET only;
    a selected project gone from the new list clears the selection (notice, back to the picker).
  - General information (`ProjectGeneralReadView`): the list row's ID / name / description / active / modules;
    no request, no save.
  - Storage paths (`ProjectPathsReadView`): GET `/api/v1/projects/{id}/storage-root` only —
    configured_value / project_override / effective_value / effective_source / env_override as
    answered, null = Not configured. Never `/system/settings/storage-root`, settings or paths, not even as a
    fallback; no path is worked out on the client.
  - Git (`ProjectGitReadView`): GET `/api/v1/projects/{id}/git/config` only. configured=false /
    config=null is Not configured, apart from a failed read. Shows repo_url, provider, enabled, base_branch,
    default_finalize_action, author name / email and whether a secret is registered — the masked
    secret is not kept or shown. No secret input, author save, merge / push / wait edit, disconnect or
    connection test; the Designer's GitConnectWindow / DesignerGitPanel stay where they are.
  - 401 / 403 / 500 are shown as errors (Access denied / Failed) with a retry of the same GET — never as an
    empty list, Not configured or success. Requests live in an epoch (`components/useLiveRequests.ts`, the
    System Agent rule): leaving, sign-out, a lost administrator role or a lost token drops the data and
    every answer still out. A lost token is the whole project area's: whichever project view finds it
    (before sending — every GET asks first —, on an answer, or on the 1 s token check of a screen with
    nothing out) ends the list, the picker and every section view together and shows a notice; after
    that Reload / Retry send nothing.
  - Never sent from here: PATCH settings / paths, storage migrate, Git PUT / DELETE / test-connection /
    provision / reconnect / finalize, legacy `/projects` · `/list/projects` · `/project-settings/pick-folder`.
- System › All projects (`SystemProjectsReadView`; T#5, D §2.8): the same list GET and handling,
  active and inactive rows with a All / Active / Inactive display filter; a row opens
  `/settings/project/:projectId/general`. No Root / Git GET on entry, no create / archive / restore /
  delete. The server answers this list to any signed-in person (`get_current_user` only); the
  administrator-only GUI does not close that existing 0075 gap.
- System › Users (`SystemUsersView`, `/api/v1/system/users…`): T#3 removed the Workspace tile,
  filter, column, drawer chips and create-time picker; the create body has no `workspace_id`.
  After every change the list is read again; only when that GET succeeds is the change reported
  done. A failed re-read keeps its error above the last list (retry = that GET only, never the
  change), closes the drawer and shows a "not confirmed" warning instead of the success notice.
- My settings (`meApi.ts`, `components/meNav.ts`; flowgate.v02.0076 T#2), open to every signed-in person.
  Screen (flowgate.v02.0077): sections of setting rows; Security / Sessions lists the sessions as a table and
  opens the three password inputs with Change (Cancel closes them and forgets the input).
  - Account / Display (`MeAccountSettingsView`): `/auth/me` username/email read-only, Display language ko/ja/en
    (`preferred_locale`; `@shared/api` sends it as X-Locale). No request of its own. The completed-card
    retention row was removed in v02test.0055 (no v0.2 screen reads the value); `embedded` draws the panel
    without the page frame for the Designer window (`windows/MeAccountWindow.vue`).
  - Security / Sessions (`MeSecuritySettingsView`): GET `/auth/sessions`, DELETE `/auth/sessions/{sid}` for
    other sessions, POST `/auth/sessions/revoke-others` after a confirmation, the current session via
    POST `/auth/logout` (`core/auth/logout.ts`), POST `/auth/password/change`. No TOTP control: no
    self-service API reports the person's TOTP state and `/auth/totp/setup` would replace a secret.
    `embedded` draws the panel without the page frame for the Designer window (`windows/MeSecurityWindow.vue`).
- Overview › Needs attention (flowgate.v02.0077, mockup ⑩): for a system administrator the overview reads
  GET `/api/v1/system/agents` once and lists the Agents that need a look, each with Inspect (→ System
  Agent with that Agent open). A non-administrator's overview sends nothing, and an answer that
  arrives after leaving or after the role went is dropped.
- The Workspace and System › Security views and their API adapter functions stay in the source, but no
  route opens them any more.
