import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import ForbiddenNotice from '@/app/components/ForbiddenNotice.vue'
import { registerAppMessages } from '@/app/i18n'
import { dismissForbidden, reportForbidden, useForbidden } from '@shared/permissions/forbidden'

registerAppMessages(i18n)

async function mountNotice() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }, { path: '/designer', component: { template: '<div />' } }],
  })
  await router.push('/designer')
  await router.isReady()
  return { router, wrapper: mount(ForbiddenNotice, { global: { plugins: [i18n, router] } }) }
}

beforeEach(() => { i18n.global.locale.value = 'ko'; dismissForbidden() })
afterEach(() => dismissForbidden())

describe('ForbiddenNotice — the one "no permission" box', () => {
  it('is not drawn until a 403 is reported', async () => {
    const { wrapper } = await mountNotice()
    expect(wrapper.find('[data-test="forbidden-notice"]').exists()).toBe(false)
  })

  it('shows the title, the explanation and the refused path — not a blank screen', async () => {
    const { wrapper } = await mountNotice()
    reportForbidden('/api/v1/projects/p2/groups?x=1', 'project_access_denied')
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-test="forbidden-notice"]').attributes('role')).toBe('alert')
    expect(wrapper.get('[data-test="forbidden-title"]').text()).toBe('권한 없음 (403)')
    expect(wrapper.get('[data-test="forbidden-body"]').text()).toContain('프로젝트 관리자')
    expect(wrapper.get('[data-test="forbidden-detail"]').text()).toContain('/api/v1/projects/p2/groups')
    expect(wrapper.get('[data-test="forbidden-detail"]').text()).not.toContain('x=1')
  })

  it('[내 프로젝트로 돌아가기] closes it and leaves the refused screen for the dashboard', async () => {
    const { wrapper, router } = await mountNotice()
    reportForbidden('/api/v1/projects/p2/groups', null)
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-test="forbidden-back"]').trigger('click')
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(router.currentRoute.value.path).toBe('/')
    expect(useForbidden().event.value).toBeNull()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="forbidden-notice"]').exists()).toBe(false)
  })

  it('[닫기] only closes it', async () => {
    const { wrapper, router } = await mountNotice()
    reportForbidden('/api/v1/x', null)
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-test="forbidden-close"]').trigger('click')
    expect(router.currentRoute.value.path).toBe('/designer')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-test="forbidden-notice"]').exists()).toBe(false)
  })

  it.each([['ko', '권한 없음 (403)'], ['en', 'No permission (403)'], ['ja', '権限がありません (403)']] as const)('speaks %s', async (lang, title) => {
    i18n.global.locale.value = lang
    const { wrapper } = await mountNotice()
    reportForbidden('/api/v1/x', null)
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-test="forbidden-title"]').text()).toBe(title)
    for (const test of ['forbidden-body', 'forbidden-back', 'forbidden-close']) {
      expect(wrapper.get(`[data-test="${test}"]`).text()).not.toContain('gui.permissions')
    }
  })
})
