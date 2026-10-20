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
  "준비 중" or disabled. Registered today: the overview and 시스템 › 사용자 (`/api/v1/system/users`).
- The Workspace and 시스템 › 보안 views and their API adapter functions stay in the source, but no
  route opens them any more.
