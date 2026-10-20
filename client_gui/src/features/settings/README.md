# Settings Feature

v0.2 환경 설정 (flowgate.v02.0020, approved mockups 11~14; reorganised by flowgate.v02.0076).

- Routes: every `/app/settings` screen is a lazily loaded child of `SettingsLayout`
  (`routing.ts`, registered in `app/router/index.ts`). The layout shows a neutral loading state
  while `/auth/me` is still out and mounts no settings view until then.
- Access (`access.ts`): 프로젝트 / 시스템 screens are shown to system administrators only; anyone
  else is replaced onto the overview with a notice before the view mounts. The retired
  `/settings/workspaces[/…]` and `/settings/system/security` URLs, and any `/settings/*` path
  without a registered view, are replaced onto the overview (no query/hash kept). This is a
  GUI exposure rule only — the server's permission checks are unchanged.
- Menus (`settingsMenu.ts`): the hub cards, the scope tabs of `SettingsShell` and the 시스템
  sub-navigation list a screen only once its route is registered, so nothing is shown as
  "준비 중" or disabled. Registered today: the overview, 내 설정 › 계정 · 화면 / 보안 · 세션 and
  시스템 › 사용자 (`/api/v1/system/users`).
- 내 설정 (`meApi.ts`, `components/meNav.ts`; flowgate.v02.0076 T#2), open to every signed-in person:
  - 계정 · 화면 (`MeAccountSettingsView`): `/auth/me` username/email read-only, 표시 언어 ko/ja/en
    (`preferred_locale`; `@shared/api` sends it as X-Locale), 완료 카드 유지 시간 on
    GET/PATCH `/api/v1/me/ui-settings` (shared domain -1, 0, 30 … 1440; a failed save restores the
    last confirmed value).
  - 보안 · 세션 (`MeSecuritySettingsView`): GET `/auth/sessions`, DELETE `/auth/sessions/{sid}` for
    other sessions, POST `/auth/sessions/revoke-others` after a confirmation, the current session via
    POST `/auth/logout` (`core/auth/logout.ts`), POST `/auth/password/change`. No TOTP control: no
    self-service API reports the person's TOTP state and `/auth/totp/setup` would replace a secret.
- The Workspace and 시스템 › 보안 views and their API adapter functions stay in the source, but no
  route opens them any more.
