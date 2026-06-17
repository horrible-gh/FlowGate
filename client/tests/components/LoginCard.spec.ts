import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import LoginCard from '@login/views/components/LoginCard.vue'

beforeEach(() => {
  i18n.global.locale.value = 'en'
})

describe('LoginCard', () => {
  it('renders login form', () => {
    const wrapper = mount(LoginCard, {
      props: {
        loading: false,
        error: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    expect(wrapper.text()).toContain(i18n.global.t('auth.login.title'))
    expect(wrapper.find('input[autocomplete="username"]').exists()).toBe(true)
    expect(wrapper.find('input[autocomplete="current-password"]').exists()).toBe(true)
  })

  it('emits submit event', async () => {
    const wrapper = mount(LoginCard, {
      props: {
        loading: false,
        error: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    await wrapper.find('input[autocomplete="username"]').setValue('alice')
    await wrapper.find('input[autocomplete="current-password"]').setValue('secret')
    await wrapper.find('button.btn-primary').trigger('click')

    expect(wrapper.emitted('submit')).toEqual([
      [
        {
          username: 'alice',
          password: 'secret',
          remember_me: false,
        },
      ],
    ])
  })
})
