import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GroupInfoModal, { type GroupInfoDoc } from '@main/components/GroupInfoModal.vue'

// flowgate.default.0410 T0008: the group document list shows an "AI · {provider}" pill for
// documents with an origin_provider_name snapshot and a dashed "unknown" pill for legacy rows
// that predate the feature (both fields null). Both states must render side by side in the
// same list without throwing or logging a console error.

const t = i18n.global.t

let wrapper: ReturnType<typeof mount> | null = null

function mountModal(documents: GroupInfoDoc[]) {
  wrapper = mount(GroupInfoModal, {
    props: {
      visible: true,
      groupId: 'flowgate.default.0410',
      groupName: 'Test Group',
      documents,
    },
    global: { plugins: [i18n] },
  })
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.restoreAllMocks()
})

const SNAPSHOT_DOC: GroupInfoDoc = {
  id: 'flowgate.default.0410.0002-T',
  typeCode: 'T',
  shortId: 'T0002',
  title: 'AI-authored step',
  originProviderName: 'Claude Sonnet 5',
  originAiRunId: 'run_abc123',
}

const LEGACY_DOC: GroupInfoDoc = {
  id: 'flowgate.default.0410.0001-R',
  typeCode: 'R',
  shortId: 'R0001',
  title: 'Legacy root',
  originProviderName: null,
  originAiRunId: null,
}

describe('GroupInfoModal — AI authorship badges (T0008)', () => {
  it('renders an AI snapshot row and a legacy unknown row together without console errors', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mountModal([SNAPSHOT_DOC, LEGACY_DOC])

    const rows = document.body.querySelectorAll('.gi-doc-row')
    expect(rows.length).toBe(2)

    const snapshotBadge = rows[0].querySelector('.gi-doc-ai') as HTMLElement
    expect(snapshotBadge.textContent).toBe(
      t('main.group_actions.info_doc_author_ai', { provider: 'Claude Sonnet 5' }),
    )
    expect(snapshotBadge.classList.contains('is-unknown')).toBe(false)
    expect(snapshotBadge.getAttribute('title')).toBe(
      t('main.group_actions.info_doc_author_run_id', { runId: 'run_abc123' }),
    )

    const legacyBadge = rows[1].querySelector('.gi-doc-ai') as HTMLElement
    expect(legacyBadge.textContent).toBe(t('main.group_actions.info_doc_author_unknown'))
    expect(legacyBadge.classList.contains('is-unknown')).toBe(true)
    expect(legacyBadge.getAttribute('title')).toBeFalsy()

    expect(errorSpy).not.toHaveBeenCalled()
  })

  it('treats a whitespace-only provider name as unknown', () => {
    mountModal([{ ...SNAPSHOT_DOC, originProviderName: '   ' }])

    const badge = document.body.querySelector('.gi-doc-ai') as HTMLElement
    expect(badge.textContent).toBe(t('main.group_actions.info_doc_author_unknown'))
    expect(badge.classList.contains('is-unknown')).toBe(true)
  })

  it('keeps the run id in the title even when the provider name is missing', () => {
    mountModal([{ ...LEGACY_DOC, originAiRunId: 'run_only_no_provider' }])

    const badge = document.body.querySelector('.gi-doc-ai') as HTMLElement
    expect(badge.textContent).toBe(t('main.group_actions.info_doc_author_unknown'))
    expect(badge.getAttribute('title')).toBe(
      t('main.group_actions.info_doc_author_run_id', { runId: 'run_only_no_provider' }),
    )
  })

  it('gives a very long provider name a bounded, single-line badge that does not push the id/type columns', () => {
    const longName = 'A'.repeat(120)
    mountModal([{ ...SNAPSHOT_DOC, originProviderName: longName }])

    const row = document.body.querySelector('.gi-doc-row') as HTMLElement
    const badge = row.querySelector('.gi-doc-ai') as HTMLElement
    const docId = row.querySelector('.gi-doc-id') as HTMLElement
    const docTag = row.querySelector('.doc-tag') as HTMLElement

    // The badge carries the full name in the DOM (CSS ellipsis truncates the paint,
    // not the content) and stays out-of-flow-shrinkable so the id/type columns —
    // whose own CSS is flex-shrink:0 — are never the ones that give up space.
    expect(badge.textContent).toBe(t('main.group_actions.info_doc_author_ai', { provider: longName }))
    expect(docId.textContent).toBe('T0002')
    expect(docTag.textContent).toBe('T')
  })

  it('renders the empty state when there are no documents', () => {
    mountModal([])

    expect(document.body.querySelector('.gi-doc-list')).toBeNull()
    expect(document.body.querySelector('.gi-empty')).not.toBeNull()
  })
})
