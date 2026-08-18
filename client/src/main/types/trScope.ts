// TR work-scope verification result (flowgate.default.0299 D0004 §6).
// Expands what the server put in documents.meta['tr_scope'] within the document detail response.
// The shape matches api/inbox_routes.py _tr_scope_meta 1:1.

// The path list stores only the leading portion, to cap meta size. count is the true
// pre-truncation total, so the screen honestly shows "n건" (n items) and appends "외 n건" (n more) when items falls short.
export interface TrScopePathSlice {
  count: number
  items: string[]
}

export interface TrScopeVerdict {
  verdict: 'pass' | 'warn' | 'reject' | 'skipped' | string
  // 0390 TR0005 rev2 — when false, verification didn't run at submit time, so only the
  // body's self-reported list exists (server: tr_scope_service.unevaluated_verdict). Since
  // there is no detected list at all, the screen shows an unverified notice rather than "감지 0건" (0 detected).
  evaluated?: boolean
  stage?: 'observe' | 'warn' | 'enforce' | null
  codes?: string[]
  branch?: string | null
  scope_reason?: string | null
  reported?: TrScopePathSlice
  detected?: TrScopePathSlice
  unconfirmed?: TrScopePathSlice
  unreported?: TrScopePathSlice
  out_of_scope?: TrScopePathSlice
  format_errors?: TrScopePathSlice
}
