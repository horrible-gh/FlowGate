# core

transport / auth / router / i18n 등 기반시설을 담당합니다 (T0005 §4).

- transport / auth / i18n: 새로 만들지 않고 `@shared` (`client/shared/api.ts`,
  `client/shared/auth.ts`, `client/shared/i18n`)를 그대로 재사용합니다. 이 경계가
  기존 로그인/인증 정책을 복제하지 않는 이유입니다.
- router: `core/router/guard.ts`가 인증 되지 않은 접근을 공용 `/login`으로
  보내고(`next` 복귀 경로 포함), 실제 라우트 테이블 구성(화면 조립)은 app/*이
  소유합니다.
- auth 상태: `core/auth/currentUser.ts`가 부팅 때 한 번 받은 `/auth/me` 결과(로그인
  사용자)를 보관합니다. 화면(Topbar 등)은 이 상태를 읽기만 하고 인증 API를 직접
  부르지 않습니다 (T0011 §6).
