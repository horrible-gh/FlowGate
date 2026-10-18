// flowgate.v02.0072 T#1 — a work plan read answer (`GET …/work-plan`, documents/routers/work_plan.py _read_view) for the
// edit window tests: a design sheet that already started, two work sets (one with a review and a pre-instruction, one
// on an AI that is no longer registered), a TS set whose result (TSR) the server builds, and fields the window has no
// control for (provider candidates, a top-level x_* extension, an attachment reference) that a save must keep.
export const FLOW = 'flowgate.v02.0072'
export const DOC = `${FLOW}.0006-WP`

export function planBody(): Record<string, unknown> {
  return {
    wp_version: 2,
    binding: 'advisory',
    counted_types: ['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'T2', 'TS'],
    quantities: {
      DS: { unit: 'sheet', count: 0 },
      D: { unit: 'sheet', count: 1 },
      P: { unit: 'sheet', count: 0 },
      L: { unit: 'sheet', count: 0 },
      DB: { unit: 'sheet', count: 0 },
      N: { unit: 'set', count: 0 },
      T: { unit: 'set', count: 2 },
      T2: { unit: 'set', count: 0 },
      TS: { unit: 'set', count: 1 },
    },
    provider_candidates: [{ provider_id: 'aip_old', display_name: 'Old AI', group_label: null }],
    defaults: { provider_id: 'aip_opus', note: '기본 메모' },
    x_origin_note: { kept: true },
    steps: [
      step({ key: 'D#1', card_id: 'c_d1', type: 'D', ordinal: 1, pair_role: 'single', provider_id: 'aip_opus', provider_display_name: 'Opus', note: '설계' }),
      step({
        key: 'T#1',
        card_id: 'c_t1',
        type: 'T',
        ordinal: 1,
        pair_key: 'TR#1',
        pair_role: 'instruction',
        provider_id: null,
        review_count: 1,
        reviewer_provider_id: 'aip_sonnet',
        reviewer_provider_display_name: 'Sonnet',
        pre_instruction_text: '사전 지시',
        pre_instruction_attachment: { doc_id: DOC, filename: 'r_1.txt', original_filename: 'brief.txt', content_sha256: 'a'.repeat(64) },
      }),
      step({ key: 'TR#1', card_id: 'c_t1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: 'aip_opus', provider_display_name: 'Opus' }),
      step({ key: 'T#2', card_id: 'c_t2', type: 'T', ordinal: 2, pair_key: 'TR#2', pair_role: 'instruction', provider_id: 'aip_old', provider_display_name: 'Old AI' }),
      step({ key: 'TR#2', card_id: 'c_t2', type: 'TR', ordinal: 2, pair_key: 'T#2', pair_role: 'result' }),
      step({ key: 'TS#1', card_id: 'c_ts1', type: 'TS', ordinal: 1, pair_key: 'TSR#1', pair_role: 'instruction', provider_id: 'aip_sonnet', provider_display_name: 'Sonnet' }),
      step({ key: 'TSR#1', card_id: 'c_ts1', type: 'TSR', ordinal: 1, pair_key: 'TS#1', pair_role: 'result', locked: true, locked_reason: 'server_assembled', origin: 'system' }),
    ],
  }
}

export function step(fields: Record<string, unknown>): Record<string, unknown> {
  return {
    key: '',
    card_id: null,
    type: '',
    ordinal: 1,
    pair_key: null,
    pair_role: 'single',
    provider_id: null,
    provider_display_name: null,
    note: null,
    review_count: 0,
    reviewer_provider_id: null,
    reviewer_provider_display_name: null,
    pre_instruction_text: null,
    pre_instruction_attachment: null,
    locked: false,
    locked_reason: null,
    origin: 'human',
    ...fields,
  }
}

export function readAnswer(over: Record<string, unknown> = {}): Record<string, unknown> {
  const body = (over.body as Record<string, unknown> | undefined) ?? planBody()
  return {
    ok: true,
    doc_id: DOC,
    doc_type: 'WP',
    title: '작업계획 — 설계 1장 · 작업 2세트',
    group_id: FLOW,
    status: 'open',
    doc_review_status: 'pending_review',
    editable: true,
    edit_locked_reason: null,
    revision_no: 2,
    body,
    registered_providers: [
      { id: 'aip_opus', name: 'Opus', group_label: null },
      { id: 'aip_sonnet', name: 'Sonnet', group_label: null },
    ],
    step_execution_status: [{ step_key: 'D#1', card_id: 'c_d1', started: true }],
    review_count_choices: [0, 1, 2, 3, -1],
    limits: { note_max_chars: 200, pre_instruction_text_max_chars: 20000, pre_instruction_attachment_max_bytes: 1048576 },
    ...over,
  }
}
