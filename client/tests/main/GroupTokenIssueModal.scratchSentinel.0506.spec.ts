/**
 * 0506 T0004 §11-16 — group-token copy mention sentinel regression.
 *
 * TR0005 rev0 was rejected for missing sentinel coverage on the group-token mention
 * surface. `buildGroupMention()` used to append a
 * `${t('main.group_token_mention.scratch_dir')} ${token.scratch_dir}` line; this mounts the
 * real modal, issues a token whose scratch_dir is a sentinel, and asserts the rendered
 * mention never contains it.
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GroupTokenIssueModal from '@main/components/GroupTokenIssueModal.vue'

const { postRequest } = vi.hoisted(() => ({ postRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest,
}))

const SENTINEL = 'C:\\FLOWGATE_SECRET_SCRATCH\\TOKEN_123'

function mountModal() {
  return mount(GroupTokenIssueModal, {
    props: {
      visible: true,
      groupId: 'flowgate.default.0506',
      groupName: 'scratch sentinel group',
      projectId: 'flowgate',
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
}

beforeEach(() => {
  postRequest.mockReset()
  postRequest.mockResolvedValue({
    data: {
      raw_token: 'raw-token',
      token_id: 'tk_1',
      expires_at: '2026-09-05T00:00:00+09:00',
      scratch_dir: SENTINEL,
      action_scope: 'new',
    },
  })
})

describe('GroupTokenIssueModal group-token mention — scratch sentinel', () => {
  it('the issued group mention never contains the token scratch_dir', async () => {
    const wrapper = mountModal()

    await wrapper.find('.gti-issue-row button').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/token/issue', expect.objectContaining({
      project: 'flowgate',
      module: 'default',
      group: '0506',
    }))

    const mentionText = wrapper.find('.gti-mention').text()
    expect(mentionText.length).toBeGreaterThan(0)
    expect(mentionText).not.toContain(SENTINEL)

    wrapper.unmount()
  })
})
