// TR 작업범위 검증 결과 (flowgate.default.0299 D0004 §6).
// 서버가 documents.meta['tr_scope'] 에 넣은 것을 문서 상세 응답에서 펼쳐 준다.
// 형태는 api/inbox_routes.py _tr_scope_meta 와 1:1로 맞춘다.

// 경로 목록은 meta 크기를 묶기 위해 앞부분만 저장한다. count 는 잘리기 전 진짜
// 개수이므로 화면은 "n건"을 정직하게 쓰고, items 가 모자라면 "외 n건"을 덧붙인다.
export interface TrScopePathSlice {
  count: number
  items: string[]
}

export interface TrScopeVerdict {
  verdict: 'pass' | 'warn' | 'reject' | 'skipped' | string
  // 0390 TR0005 rev2 — false 면 제출 시점에 검증이 돌지 않아 본문의 신고 목록만
  // 있는 상태다(서버 tr_scope_service.unevaluated_verdict). 감지 목록이 아예
  // 없으므로 화면은 "감지 0건"이 아니라 미검증 안내를 보여준다.
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
