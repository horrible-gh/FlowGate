# core

Infrastructure for transport, authentication, routing, and i18n (T0005 §4).

- Transport/auth/i18n use `@shared` (`src/shared/api.ts`, `src/shared/auth.ts`,
  `src/shared/i18n`). /app and the login page (`src/login`, login.html) share
  these modules, keeping login/authentication policy in one place.
- `core/router/guard.ts` sends unauthenticated access to the shared `/login`,
  including the `next` return path. app/* owns the route table and screen assembly.
- `core/auth/currentUser.ts` holds the signed-in user returned by the single
  `/auth/me` call during bootstrap. Screens such as Topbar read this state
  without calling the authentication API themselves (T0011 §6).
