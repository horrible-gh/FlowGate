// flowgate.default.0241 (B0001): saving a provider failed with a bare "check each entry"
// toast, so the real cause (cli_command over the length cap) never reached the screen.
// Covers both halves of the fix: the 422 `errors` array now renders, and the form rejects
// over-limit/duplicate input before the request.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiProviderListEditor from '@/settings/components/AiProviderListEditor.vue'
import { CLI_COMMAND_MAX, formatErrors } from '@/settings/components/aiProviderLimits'

const { getRequest, putRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, putRequest }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))

const i18nApi = { t: i18n.global.t, te: i18n.global.te }

const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: ['claude'], api: ['claude'] } }

function mountEditor(providers = []) {
  return mount(AiProviderListEditor, {
    props: { providers, defaultIndex: providers.length ? 0 : -1, catalog: CATALOG },
    global: { plugins: [i18n] },
  })
}

// Row actions are .btn-secondary too, so target by position rather than by class/title:
// "Add provider" is the last one, and edit is the 3rd action in a row (up, down, edit, delete).
async function openAddForm(wrapper) {
  const buttons = wrapper.findAll('button.btn-secondary')
  await buttons[buttons.length - 1].trigger('click')
}

async function openEditForm(wrapper, row = 0) {
  await wrapper.findAll('tbody tr')[row].findAll('button')[2].trigger('click')
}

function formError(wrapper) {
  return wrapper.find('p.text-sm').exists() ? wrapper.get('p.text-sm').text() : ''
}

describe('AI provider 422 error formatting', () => {
  it('names the offending row, field and reason', () => {
    const providers = [{ name: 'claude cli' }, { name: 'codex' }]
    const messages = formatErrors(
      [{ index: 1, field: 'cli_command', reason: 'too_long' }],
      providers,
      i18nApi,
    )

    expect(messages).toHaveLength(1)
    // Row number is 1-based, and the limit reaches the user rather than a bare "too_long".
    expect(messages[0]).toContain('2')
    expect(messages[0]).toContain('codex')
    expect(messages[0]).toContain(String(CLI_COMMAND_MAX))
  })

  it('formats form-level errors that carry no row index', () => {
    const messages = formatErrors([{ field: 'providers', reason: 'too_many' }], [], i18nApi)
    expect(messages[0]).toContain('20')
  })

  it('falls back to the raw wire value for a reason the client does not know yet', () => {
    const messages = formatErrors(
      [{ index: 0, field: 'cli_command', reason: 'brand_new_reason' }],
      [{ name: 'claude cli' }],
      i18nApi,
    )
    expect(messages[0]).toContain('brand_new_reason')
  })

  it('ignores a malformed or absent errors payload', () => {
    expect(formatErrors(undefined, [], i18nApi)).toEqual([])
    expect(formatErrors([null, {}], [], i18nApi)).toEqual([])
  })
})

describe('AiSettingsView save failure', () => {
  beforeEach(() => {
    getRequest.mockReset()
    putRequest.mockReset()
  })

  async function mountView(providers) {
    getRequest.mockResolvedValue({
      data: { providers, default_provider_id: providers[0]?.id ?? null, catalog: CATALOG },
    })
    const AiSettingsView = (await import('@/settings/views/system/AiSettingsView.vue')).default
    const wrapper = mount(AiSettingsView, { global: { plugins: [i18n] } })
    await flushPromises()
    return wrapper
  }

  const ROW = {
    id: 'aip_abc123',
    name: 'claude sandbox',
    exec_type: 'cli',
    kind: 'claude',
    enabled: true,
    cli_command: 'claude -p',
  }

  it('renders the reason for each rejected row instead of only a generic toast', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: {
            code: 'validation_failed',
            errors: [{ index: 0, field: 'cli_command', reason: 'too_long' }],
          },
        },
      },
    })

    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    const alert = wrapper.get('.alert-danger').text()
    expect(alert).toContain('claude sandbox')
    expect(alert).toContain(String(CLI_COMMAND_MAX))
  })

  it('clears stale errors once the settings reload', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockRejectedValue({
      response: { status: 422, data: { detail: { errors: [{ index: 0, field: 'name', reason: 'required' }] } } },
    })
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
    expect(wrapper.find('.alert-danger').exists()).toBe(true)

    // Reset is the view's last .btn-secondary — the editor's row actions use that class too.
    const secondary = wrapper.findAll('button.btn-secondary')
    await secondary[secondary.length - 1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.alert-danger').exists()).toBe(false)
  })

  it('shows no error list for a non-422 failure', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockRejectedValue({ response: { status: 500, data: {} } })

    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    expect(wrapper.find('.alert-danger').exists()).toBe(false)
  })
})

describe('AI provider form pre-flight validation', () => {
  it('rejects a cli_command past the limit instead of letting the save 422', async () => {
    const wrapper = mountEditor()
    await openAddForm(wrapper)
    await wrapper.get('input.form-ctrl').setValue('claude cli')
    await wrapper.get('input.mono').setValue('c'.repeat(CLI_COMMAND_MAX + 1))
    await wrapper.get('button.btn-primary').trigger('click')

    expect(formError(wrapper)).toContain(String(CLI_COMMAND_MAX))
    expect(wrapper.emitted('update:providers')).toBeUndefined()
  })

  it('accepts a cli_command exactly at the limit', async () => {
    const wrapper = mountEditor()
    await openAddForm(wrapper)
    await wrapper.get('input.form-ctrl').setValue('claude cli')
    await wrapper.get('input.mono').setValue('c'.repeat(CLI_COMMAND_MAX))
    await wrapper.get('button.btn-primary').trigger('click')

    expect(formError(wrapper)).toBe('')
    expect(wrapper.emitted('update:providers')).toHaveLength(1)
  })

  it('rejects a duplicate name differing only in letter case', async () => {
    const wrapper = mountEditor([
      { name: 'Claude CLI', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p' },
    ])
    await openAddForm(wrapper)
    await wrapper.get('input.form-ctrl').setValue('claude cli')
    await wrapper.get('input.mono').setValue('claude -p')
    await wrapper.get('button.btn-primary').trigger('click')

    expect(formError(wrapper)).not.toBe('')
    expect(wrapper.emitted('update:providers')).toBeUndefined()
  })

  it('lets a row keep its own name while editing', async () => {
    const wrapper = mountEditor([
      { name: 'claude cli', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p' },
    ])
    await openEditForm(wrapper)
    await wrapper.get('input.mono').setValue('claude -p --verbose')
    await wrapper.get('button.btn-primary').trigger('click')

    expect(formError(wrapper)).toBe('')
    expect(wrapper.emitted('update:providers')).toHaveLength(1)
  })
})
