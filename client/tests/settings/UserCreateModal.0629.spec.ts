import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

import UserCreateModal from '@/settings/views/users/UserCreateModal.vue'

async function mountModal() {
  const wrapper = mount(UserCreateModal, {
    global: {
      plugins: [i18n],
      stubs: {
        DialogShell: {
          template: '<div><slot name="header" /><slot /><slot name="footer" /></div>',
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

async function fillForm(wrapper: Awaited<ReturnType<typeof mountModal>>) {
  await wrapper.get('input[placeholder="username"]').setValue('new_0629')
  await wrapper.get('input[type="email"]').setValue('new_0629@test.com')
  const passwords = wrapper.findAll('input[type="password"]')
  await passwords[0].setValue('Pass1234!')
  await passwords[1].setValue('Pass1234!')
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockResolvedValue({
    data: { projects: [{ project_id: 'proj_001', project_name: 'TestProject' }] },
  })
})

describe('UserCreateModal 0629', () => {
  it('creates the selected system and project roles in one request, then emits created', async () => {
    postRequest.mockResolvedValue({ data: { user_id: 'usr_created' } })
    const wrapper = await mountModal()
    await fillForm(wrapper)
    await wrapper.findAll('select')[0].setValue('role_admin')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest).toHaveBeenCalledWith('/api/v1/users', {
      username: 'new_0629',
      email: 'new_0629@test.com',
      password: 'Pass1234!',
      is_active: true,
      role_id: 'role_admin',
      project_roles: [{ project_id: 'proj_001', role_id: 'role_worker' }],
    })
    expect(wrapper.emitted('created')).toHaveLength(1)
  })

  it('shows a localized conflict and hides the raw database detail', async () => {
    postRequest.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'username_already_exists',
            message: 'duplicate key value violates unique constraint users_username_key',
          },
        },
      },
    })
    const wrapper = await mountModal()
    await fillForm(wrapper)
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('이미 사용 중인 사용자 이름입니다.')
    expect(wrapper.text()).not.toContain('duplicate key value')
    expect(wrapper.text()).not.toContain('users_username_key')
    expect(wrapper.emitted('created')).toBeUndefined()
  })

  it('validates password confirmation without a script-scope translation error', async () => {
    const wrapper = await mountModal()
    await wrapper.get('input[placeholder="username"]').setValue('new_0629')
    const passwords = wrapper.findAll('input[type="password"]')
    await passwords[0].setValue('Pass1234!')
    await passwords[1].setValue('different')
    await wrapper.get('[data-dialog-action-id="submit-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('비밀번호가 일치하지 않습니다.')
    expect(postRequest).not.toHaveBeenCalled()
  })
})
