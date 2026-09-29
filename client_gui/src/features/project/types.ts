// Project domain model owned by features/project (WP0005 T#1 §2). Transport snake_case
// from `/api/v1/projects*` never spreads past api.ts's normalizer — every component/store
// in this feature (and any future consumer of the public surface) only ever sees this shape.

export interface ProjectModule {
  name: string
  title: string
}

export interface Project {
  id: string
  name: string
  description: string
  color: string | null
  // Archive/active flag (backend `is_active`). This is NOT "is this the current project" —
  // that meaning lives entirely in currentProjectId/currentProjectStorage (NR0003 §5 row
  // "is_active", T#1 §2 "is_active는 archive/active 여부이고 current project 의미가 아님").
  isActive: boolean
  createdAt: string | null
  // "최종 수정" 의미만 허용된다 — 최근 사용/최근 열기 의미로 쓰지 않는다 (D0004 §2.3).
  updatedAt: string | null
  modules: ProjectModule[]
}

export interface CreateProjectInput {
  name: string
  // Required at the v0.2 frontend contract boundary (T0012 §3.3/§3.1) — the v0.1 create
  // screen never treated this as optional, and the backend's own ability to slugify a
  // missing project_id is a backend capability only, not grounds to relax this contract.
  id: string
  description?: string
  color?: string
}
