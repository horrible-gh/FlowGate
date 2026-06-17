import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import TotpInput from '@login/views/components/TotpInput.vue'

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

describe('TotpInput', () => {
  it('renders otp screen', () => {
    const wrapper = mount(TotpInput, {
      props: {
        loading: false,
        error: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    expect(wrapper.text()).toContain('Two-factor authentication (OTP)')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
  })

  it('emits submit when 6 digits entered', async () => {
    const wrapper = mount(TotpInput, {
      props: {
        loading: false,
        error: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    const inputs = wrapper.findAll('input[inputmode="numeric"]')
    expect(inputs).toHaveLength(6)

    for (let i = 0; i < 6; i++) {
      await inputs[i].setValue(String(i + 1))
    }

    expect(wrapper.emitted('submit')).toEqual([['123456']])
  })

  it('emits back action', async () => {
    const wrapper = mount(TotpInput, {
      props: {
        loading: false,
        error: null,
      },
      global: {
        plugins: [i18n],
      },
    })

    const buttons = wrapper.findAll('button')
    await buttons[1].trigger('click')

    expect(wrapper.emitted('back')).toHaveLength(1)
  })
})
