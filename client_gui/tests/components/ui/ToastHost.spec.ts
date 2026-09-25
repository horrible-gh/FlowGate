import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import ToastHost from '@/components/ui/ToastHost.vue'
import ModalShell from '@/components/ui/ModalShell.vue'

// T0008 §12: caller-owned toast list, stacked in a shared teleported region without
// overlap, and no global queue/store/event-bus invented on top.
describe('components/ui/ToastHost (T0008 §12)', () => {
  afterEach(() => {
    document.body.querySelectorAll('.ui-toast-host').forEach((el) => el.remove())
    document.body.querySelectorAll('.ui-modal-overlay').forEach((el) => el.remove())
  })

  it('renders nothing when there are no toasts', () => {
    mount(ToastHost, { props: { toasts: [] } })
    expect(document.querySelector('.ui-toast-host')).toBeNull()
  })

  it('teleports multiple toasts into one shared stacking region, each rendering its own message', () => {
    mount(ToastHost, {
      props: {
        toasts: [
          { id: 1, message: 'First', duration: 0 },
          { id: 2, message: 'Second', duration: 0 },
        ],
      },
    })
    const host = document.querySelector('.ui-toast-host') as HTMLElement
    expect(host).not.toBeNull()
    // vitest's jsdom environment does not inject scoped-CSS <style> tags, so the "no
    // overlap" guarantee (T0008 §5.3, layout defined in ToastHost.vue's flex-column CSS)
    // can only be checked structurally here: both toasts must be direct children of one
    // shared host, not two independently teleported/positioned regions.
    expect(host.children).toHaveLength(2)
    const items = host.querySelectorAll(':scope > .ui-toast')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain('First')
    expect(items[1].textContent).toContain('Second')
  })

  it('re-emits dismiss with the dismissed item id when a child Toast closes', async () => {
    const wrapper = mount(ToastHost, {
      props: {
        toasts: [
          { id: 'a', message: 'First' },
          { id: 'b', message: 'Second' },
        ],
      },
    })
    const closeButtons = document.querySelectorAll('.ui-toast-close')
    expect(closeButtons).toHaveLength(2)
    ;(closeButtons[1] as HTMLElement).dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('dismiss')?.[0]).toEqual(['b'])
  })

  // Regression for the rework rejection: a dependency-free `computed` only allocated a
  // z-index once, so a Modal opened after the host was already showing would render
  // above it forever, and a later toast never recovered the top spot. Host -> Modal ->
  // new Toast must land the host above the Modal again (T0008 §6/§10 shared "나중에 뜬
  // 것이 위" counter, core/overlay/zIndex.ts).
  it('reallocates its z-index above a later-opened ModalShell once a new toast is added', async () => {
    const hostWrapper = mount(ToastHost, { props: { toasts: [{ id: 1, message: 'First', duration: 0 }] } })
    const hostBefore = document.querySelector('.ui-toast-host') as HTMLElement
    const zBefore = Number(hostBefore.style.zIndex)

    const modalWrapper = mount(ModalShell, { props: { open: true }, attachTo: document.body })
    const modalOverlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    const modalZ = Number(modalOverlay.style.zIndex)
    expect(modalZ).toBeGreaterThan(zBefore)

    await hostWrapper.setProps({
      toasts: [
        { id: 1, message: 'First', duration: 0 },
        { id: 2, message: 'Second', duration: 0 },
      ],
    })
    const hostAfter = document.querySelector('.ui-toast-host') as HTMLElement
    const zAfter = Number(hostAfter.style.zIndex)
    expect(zAfter).toBeGreaterThan(modalZ)

    modalWrapper.unmount()
    hostWrapper.unmount()
  })

  // Regression for the second rework rejection: the length-growth check missed same-
  // length queue updates. An atomic replace of the toasts array (still length 1, but a
  // new id) must still be detected as "a new toast became relevant" and reallocate
  // above a Modal opened in between, not just an appended item.
  it('reallocates its z-index when a same-length toast replacement introduces a new id', async () => {
    const hostWrapper = mount(ToastHost, { props: { toasts: [{ id: 1, message: 'First', duration: 0 }] } })
    const hostBefore = document.querySelector('.ui-toast-host') as HTMLElement
    const zBefore = Number(hostBefore.style.zIndex)

    const modalWrapper = mount(ModalShell, { props: { open: true }, attachTo: document.body })
    const modalOverlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    const modalZ = Number(modalOverlay.style.zIndex)
    expect(modalZ).toBeGreaterThan(zBefore)

    // Atomic replace, not append: length stays 1, only the id changes.
    await hostWrapper.setProps({ toasts: [{ id: 2, message: 'Second', duration: 0 }] })
    const hostAfter = document.querySelector('.ui-toast-host') as HTMLElement
    const zAfter = Number(hostAfter.style.zIndex)
    expect(zAfter).toBeGreaterThan(modalZ)

    modalWrapper.unmount()
    hostWrapper.unmount()
  })

  // Same rejection, the max-size-queue variant: a same-tick push+shift (drop the oldest,
  // add one new item) nets out to the same length too.
  it('reallocates its z-index when a same-tick push+shift keeps the queue length constant', async () => {
    const hostWrapper = mount(ToastHost, {
      props: {
        toasts: [
          { id: 1, message: 'First', duration: 0 },
          { id: 2, message: 'Second', duration: 0 },
        ],
      },
    })
    const hostBefore = document.querySelector('.ui-toast-host') as HTMLElement
    const zBefore = Number(hostBefore.style.zIndex)

    const modalWrapper = mount(ModalShell, { props: { open: true }, attachTo: document.body })
    const modalOverlay = document.querySelector('.ui-modal-overlay') as HTMLElement
    const modalZ = Number(modalOverlay.style.zIndex)
    expect(modalZ).toBeGreaterThan(zBefore)

    // Drop the oldest (id 1) and push a new one (id 3) in one update: length stays 2.
    await hostWrapper.setProps({
      toasts: [
        { id: 2, message: 'Second', duration: 0 },
        { id: 3, message: 'Third', duration: 0 },
      ],
    })
    const hostAfter = document.querySelector('.ui-toast-host') as HTMLElement
    const zAfter = Number(hostAfter.style.zIndex)
    expect(zAfter).toBeGreaterThan(modalZ)

    modalWrapper.unmount()
    hostWrapper.unmount()
  })
})
