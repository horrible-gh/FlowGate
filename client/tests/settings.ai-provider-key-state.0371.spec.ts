// flowgate.default.0371 T0010: AI provider API keys are encrypted at rest, so the one new
// state the screen can meet is "a key IS stored but the server can no longer decrypt it"
// (the master key changed). Rendering that as the ordinary "Key registered (…)" line —
// with an empty hint — would read as a healthy row and hide the fact that the operator has
// to type the key again.
//
// 0469 T4: key state is no longer shown inline on the row (§3 bans always-on connection/key
// text in the list) — it moved into the command/info-view dialog opened via the row's
// "view command" button, so these tests open that dialog before asserting on the text.
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiProviderListEditor from '@/settings/components/AiProviderListEditor.vue'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), putRequest: vi.fn() }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))

const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: ['claude'], api: ['claude'] } }

function apiProvider(extra = {}) {
  return {
    id: 'aip_abc123',
    name: 'openai api',
    exec_type: 'api',
    kind: 'claude',
    enabled: true,
    cli_command: null,
    api_base_url: null,
    api_model: 'gpt-5.6-sol',
    api_key_set: true,
    api_key_hint: 'J3zQ',
    ...extra,
  }
}

function mountEditor(providers) {
  return mount(AiProviderListEditor, {
    props: { providers, defaultIndex: 0, catalog: CATALOG },
    global: { plugins: [i18n] },
  })
}

async function openCommandView(wrapper) {
  await wrapper.find(`button[title="${i18n.global.t('settings.ai.view_command')}"]`).trigger('click')
}

describe('AI provider key state', () => {
  it('still shows the last-4 hint for a readable key', async () => {
    const wrapper = mountEditor([apiProvider()])
    await openCommandView(wrapper)
    const text = wrapper.text()
    expect(text).toContain('J3zQ')
    expect(text).not.toContain(i18n.global.t('settings.ai.key_unreadable'))
  })

  it('says the key is unreadable instead of pretending it is registered', async () => {
    const wrapper = mountEditor([
      apiProvider({ api_key_hint: null, api_key_unreadable: true }),
    ])
    await openCommandView(wrapper)
    const text = wrapper.text()
    expect(text).toContain(i18n.global.t('settings.ai.key_unreadable'))
    expect(text).not.toContain(i18n.global.t('settings.ai.key_set_hint', { hint: '' }))
  })

  it('reports a provider with no key as such', async () => {
    const wrapper = mountEditor([
      apiProvider({ api_key_set: false, api_key_hint: null }),
    ])
    await openCommandView(wrapper)
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai.key_none'))
  })
})
