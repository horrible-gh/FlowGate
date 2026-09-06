// flowgate.default.0241 (B0001): saving a provider failed with a bare "check each entry"
// toast, so the real cause (cli_command over the length cap) never reached the screen.
// Covers both halves of the fix: the 422 `errors` array now renders, and the form rejects
// over-limit/duplicate input before the request.
//
// 0469 T4: the provider list is now a row list + dialogs (no <table>) and AiSettingsView
// saves immediately on every row operation instead of via a bottom Save button — the save
// trigger in the tests below is a dialog confirm/save click, not `button.btn-primary` on the
// view itself.
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

async function openAddForm(wrapper) {
  const add = wrapper.findAll('button').find(
    (b) => b.text().includes(i18n.global.t('settings.ai.add_provider')),
  )
  await add.trigger('click')
}

async function openEditForm(wrapper, row = 0) {
  const buttons = wrapper.findAll(`button[title="${i18n.global.t('common.edit')}"]`)
  await buttons[row].trigger('click')
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

describe('AiSettingsView immediate save', () => {
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

  async function saveViaEditDialog(wrapper, row = 0) {
    const editButtons = wrapper.findAll(`button[title="${i18n.global.t('common.edit')}"]`)
    await editButtons[row].trigger('click')
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
  }

  async function renameViaEditDialog(wrapper, name, row = 0) {
    const editButtons = wrapper.findAll(`button[title="${i18n.global.t('common.edit')}"]`)
    await editButtons[row].trigger('click')
    await wrapper.get('.modal-bg input.form-ctrl').setValue(name)
    await wrapper.get('.modal-bg button.btn-primary').trigger('click')
    await flushPromises()
  }

  async function addViaDialog(wrapper, name) {
    const add = wrapper.findAll('button').find(
      (b) => b.text().includes(i18n.global.t('settings.ai.add_provider')),
    )
    await add.trigger('click')
    await wrapper.get('.modal-bg input.form-ctrl').setValue(name)
    await wrapper.get('.modal-bg input.mono').setValue('claude -p')
    await wrapper.get('.modal-bg button.btn-primary').trigger('click')
    await flushPromises()
  }

  /** Holds a PUT open so the next operation lands while that save is still in flight. */
  function deferNextPut() {
    let settle
    putRequest.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve }))
    return (data) => settle({ data })
  }

  it('saves immediately when a row is edited, with no separate provider save button', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockResolvedValue({
      data: { providers: [ROW], default_provider_id: ROW.id, catalog: CATALOG },
    })
    // The only unconditional .btn-primary left on the page belongs to the execution policy
    // card (data-test="ai-execution-policy-save") — there is no provider list save button.
    expect(wrapper.findAll('button.btn-primary')).toHaveLength(1)
    expect(wrapper.find('[data-test="ai-execution-policy-save"]').exists()).toBe(true)
    expect(wrapper.find('.badge-yellow').exists()).toBe(false)

    await saveViaEditDialog(wrapper)

    expect(putRequest).toHaveBeenCalledWith('/api/v1/system/ai-settings', expect.any(Object))
  })

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

    await saveViaEditDialog(wrapper)

    const alert = wrapper.get('.alert-danger').text()
    expect(alert).toContain('claude sandbox')
    expect(alert).toContain(String(CLI_COMMAND_MAX))
  })

  it('clears stale errors once a later save succeeds', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockRejectedValueOnce({
      response: { status: 422, data: { detail: { errors: [{ index: 0, field: 'name', reason: 'required' }] } } },
    })
    await saveViaEditDialog(wrapper)
    expect(wrapper.find('.alert-danger').exists()).toBe(true)

    putRequest.mockResolvedValueOnce({
      data: { providers: [ROW], default_provider_id: ROW.id, catalog: CATALOG },
    })
    await saveViaEditDialog(wrapper)
    expect(wrapper.find('.alert-danger').exists()).toBe(false)
  })

  it('shows no error list for a non-422 failure', async () => {
    const wrapper = await mountView([ROW])
    putRequest.mockRejectedValue({ response: { status: 500, data: {} } })

    await saveViaEditDialog(wrapper)

    expect(wrapper.find('.alert-danger').exists()).toBe(false)
  })

  it('keeps the api_key write-only merge contract in the immediate-save payload', async () => {
    const apiRow = {
      id: 'aip_key1', name: 'openai api', exec_type: 'api', kind: 'claude', enabled: true,
      cli_command: null, api_base_url: null, api_model: 'gpt-5.6-sol',
      api_key_set: true, api_key_hint: 'J3zQ',
    }
    const wrapper = await mountView([apiRow])
    putRequest.mockResolvedValue({
      data: { providers: [apiRow], default_provider_id: apiRow.id, catalog: CATALOG },
    })

    // Editing without touching the key field omits api_key entirely (keep).
    await saveViaEditDialog(wrapper)
    let payload = putRequest.mock.calls.at(-1)[1]
    expect(payload.providers[0].api_key).toBeUndefined()

    // Ticking "clear key" sends an explicit empty string (delete).
    const editButtons = wrapper.findAll(`button[title="${i18n.global.t('common.edit')}"]`)
    await editButtons[0].trigger('click')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
    payload = putRequest.mock.calls.at(-1)[1]
    expect(payload.providers[0].api_key).toBe('')
  })

  // Consecutive operations are serialized, and the later one must survive: the first save's
  // response comes back describing the state as it was BEFORE the second operation, so
  // applying it wholesale would make the queued save re-send the first state and drop the
  // second edit entirely.
  it('keeps an edit made while an earlier save is still in flight', async () => {
    const wrapper = await mountView([ROW])
    const finishFirst = deferNextPut()

    await renameViaEditDialog(wrapper, 'renamed A')
    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1].providers[0].name).toBe('renamed A')

    // Operation B arrives before the first PUT answers, so its save is queued behind it.
    await renameViaEditDialog(wrapper, 'renamed B')
    expect(putRequest).toHaveBeenCalledTimes(1)

    putRequest.mockResolvedValue({
      data: { providers: [{ ...ROW, name: 'renamed B' }], default_provider_id: ROW.id },
    })
    finishFirst({ providers: [{ ...ROW, name: 'renamed A' }], default_provider_id: ROW.id })
    await flushPromises()

    expect(putRequest).toHaveBeenCalledTimes(2)
    expect(putRequest.mock.calls[1][1].providers[0].name).toBe('renamed B')
    expect(wrapper.get('.ai-name').text()).toBe('renamed B')
  })

  it('gives a row created by an in-flight save the id it was issued', async () => {
    const wrapper = await mountView([ROW])
    const finishFirst = deferNextPut()

    await addViaDialog(wrapper, 'second cli')
    expect(putRequest.mock.calls[0][1].providers[1].id).toBeNull()

    // The first response is stale by the time it lands, but the id it issued to the new row
    // still has to reach that row — otherwise the queued save asks for a second one.
    await renameViaEditDialog(wrapper, 'renamed while saving')
    const created = { ...ROW, id: 'aip_new999', name: 'second cli' }
    putRequest.mockResolvedValue({
      data: { providers: [ROW, created], default_provider_id: ROW.id },
    })
    finishFirst({ providers: [ROW, created], default_provider_id: ROW.id })
    await flushPromises()

    const queued = putRequest.mock.calls[1][1]
    expect(queued.providers).toHaveLength(2)
    expect(queued.providers[1].id).toBe('aip_new999')
    expect(queued.providers[0].name).toBe('renamed while saving')
  })

  it('rolls a failed queued save back to the state the server confirmed', async () => {
    const wrapper = await mountView([ROW])
    const finishFirst = deferNextPut()

    await renameViaEditDialog(wrapper, 'renamed A')
    await renameViaEditDialog(wrapper, 'renamed B')

    putRequest.mockRejectedValue({ response: { status: 500, data: {} } })
    finishFirst({ providers: [{ ...ROW, name: 'renamed A' }], default_provider_id: ROW.id })
    await flushPromises()

    // Not 'renamed B' (never saved) and not the pre-A name: the rollback target is the last
    // state the server actually confirmed, which is A's response.
    expect(wrapper.get('.ai-name').text()).toBe('renamed A')
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
