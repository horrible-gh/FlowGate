import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.default.0560 T0012 §2.5 / §5-3 — DialogFooter contract.
//
// L0009 §5 "완료 조건" items proved here:
//   6  the semantic ordering is enforced by the footer, not by each screen
//   7  cancel always sits immediately left of the primary button
//   8  a separate danger action and the primary/auxiliary ones stay distinguishable
//  13  Type A (cancel) / Type B (stop) / Type C (dismiss) never collapse into one role
//  21  DialogFooter is the only thing that runs onSelect; `select` is an observation
//      signal that does not execute anything
//  22  ordering is deterministic with `order` unset, `order: 0`, and duplicate `order`
//  26  the DialogAction objects handed in as props are never mutated
//
// The failing arrangement R0001 reported — cancel drawn to the right of the primary
// button on one screen and to the left on the next — is impossible here because the
// caller's array position is only the third sort key.
import DialogFooter from '@main/components/dialogs/DialogFooter.vue'
import type { DialogAction, DialogActionRole } from '@main/components/dialogs/dialogTypes'

function action(id: string, role: DialogActionRole, extra: Partial<DialogAction> = {}): DialogAction {
  return { id, label: id, role, onSelect: () => {}, ...extra }
}

function mountFooter(actions: DialogAction[], props: Record<string, unknown> = {}) {
  return mount(DialogFooter as never, { props: { actions, ...props } })
}

function renderedOrder(actions: DialogAction[], props: Record<string, unknown> = {}): string[] {
  const wrapper = mountFooter(actions, props)
  const ids = wrapper.findAll('button').map((button) => button.attributes('data-dialog-action-id') ?? '')
  wrapper.unmount()
  return ids
}

let warnSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  warnSpy.mockRestore()
})

describe('DialogFooter — semantic ordering (DS0007 / L0009 §2, 완료조건 6/7/8/22)', () => {
  it('keeps an already-correct list in place', () => {
    expect(renderedOrder([action('aux', 'aux'), action('cancel', 'cancel'), action('primary', 'primary')])).toEqual([
      'aux',
      'cancel',
      'primary',
    ])
  })

  it('reorders a caller list that puts the primary button first', () => {
    expect(renderedOrder([action('primary', 'primary'), action('aux', 'aux'), action('cancel', 'cancel')])).toEqual([
      'aux',
      'cancel',
      'primary',
    ])
  })

  it('places a separate danger action left of cancel', () => {
    expect(
      renderedOrder([
        action('danger', 'danger'),
        action('primary', 'primary'),
        action('cancel', 'cancel'),
        action('aux', 'aux'),
      ]),
    ).toEqual(['aux', 'danger', 'cancel', 'primary'])
  })

  it('slots a stop action between danger and cancel', () => {
    expect(
      renderedOrder([
        action('primary', 'primary'),
        action('cancel', 'cancel'),
        action('stop', 'stop'),
        action('danger', 'danger'),
        action('aux', 'aux'),
      ]),
    ).toEqual(['aux', 'danger', 'stop', 'cancel', 'primary'])
  })

  it('renders a danger confirm as [cancel] [danger primary]', () => {
    expect(
      renderedOrder([
        action('confirm', 'primary', { tone: 'danger' }),
        action('cancel', 'cancel'),
      ]),
    ).toEqual(['cancel', 'confirm'])
  })

  it('keeps cancel immediately left of the primary button in every arrangement', () => {
    const arrangements: DialogAction[][] = [
      [action('primary', 'primary'), action('cancel', 'cancel')],
      [action('cancel', 'cancel'), action('aux', 'aux'), action('primary', 'primary')],
      [action('primary', 'primary'), action('danger', 'danger'), action('cancel', 'cancel')],
      [action('dismiss', 'dismiss'), action('primary', 'primary'), action('stop', 'stop'), action('cancel', 'cancel')],
    ]
    for (const actions of arrangements) {
      const order = renderedOrder(actions)
      expect(order.indexOf('cancel')).toBe(order.indexOf('primary') - 1)
    }
  })

  it('lets `order` decide between aux and dismiss, which share one role weight', () => {
    expect(renderedOrder([action('aux', 'aux', { order: 1 }), action('dismiss', 'dismiss', { order: 0 })])).toEqual([
      'dismiss',
      'aux',
    ])
  })

  it('treats order: 0 as a real value rather than as "unset"', () => {
    // `third` asks for position 0 from caller index 2. Read as a real value it sorts
    // level with `first` (implicit order 0) and ahead of `second` (implicit 1); read
    // with a falsy check it would fall back to its caller index and land last.
    expect(
      renderedOrder([action('first', 'aux'), action('second', 'aux'), action('third', 'aux', { order: 0 })]),
    ).toEqual(['first', 'third', 'second'])
  })

  it('is deterministic when two actions land on the same sort key', () => {
    const actions = [action('a', 'aux', { order: 5 }), action('b', 'aux', { order: 5 }), action('c', 'aux', { order: 5 })]
    expect(renderedOrder(actions)).toEqual(['a', 'b', 'c'])
    // Same keys, different caller order — the third tie-breaker follows the caller.
    expect(renderedOrder([actions[2], actions[0], actions[1]])).toEqual(['c', 'a', 'b'])
  })

  it('never lets role priority be overridden by order', () => {
    expect(
      renderedOrder([action('primary', 'primary', { order: -100 }), action('aux', 'aux', { order: 100 })]),
    ).toEqual(['aux', 'primary'])
  })
})

describe('DialogFooter — contract violations (L0009 §4)', () => {
  it('warns about two primary actions but still renders both', () => {
    const order = renderedOrder([
      action('p1', 'primary'),
      action('p2', 'primary'),
      action('cancel', 'cancel'),
    ])
    expect(order).toEqual(['cancel', 'p1', 'p2'])
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('role "primary"'))
  })

  it('warns about two cancel actions but still renders both', () => {
    const order = renderedOrder([action('c1', 'cancel'), action('c2', 'cancel')])
    expect(order).toEqual(['c1', 'c2'])
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('role "cancel"'))
  })

  it('warns about a duplicate action id', () => {
    renderedOrder([action('same', 'aux'), action('same', 'primary')])
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('duplicate action id "same"'))
  })
})

describe('DialogFooter — single execution owner (L0009 §0 규칙 3, 완료조건 21/26)', () => {
  it('runs onSelect once per click and emits select as an observation signal', async () => {
    const onSelect = vi.fn()
    const actions = [action('confirm', 'primary', { onSelect })]
    const wrapper = mountFooter(actions)

    await wrapper.find('[data-dialog-action-id="confirm"]').trigger('click')
    await flushPromises()

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('select')).toHaveLength(1)
    // Identity is not asserted: props arrive through Vue's reactive wrapper, so the
    // payload is a proxy of the caller's object rather than the object itself.
    expect((wrapper.emitted('select') as DialogAction[][])[0][0].id).toBe('confirm')
  })

  it('still runs onSelect exactly once when the feature also listens to select', async () => {
    const onSelect = vi.fn()
    const observer = vi.fn()
    const wrapper = mountFooter([action('confirm', 'primary', { onSelect })], { onSelect: observer })

    await wrapper.find('[data-dialog-action-id="confirm"]').trigger('click')
    await flushPromises()

    // The observer is a listener on `select`; it really did fire, and it still must not
    // become a second execution path.
    expect(observer).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('does not mutate the DialogAction objects it was given', async () => {
    let release: () => void = () => {}
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    const target: DialogAction = action('save', 'primary', { onSelect: () => pending })
    const snapshot = { ...target }

    const wrapper = mountFooter([target])
    await wrapper.find('[data-dialog-action-id="save"]').trigger('click')
    await flushPromises()

    // The button reports itself as busy…
    expect(wrapper.find('[data-dialog-action-id="save"]').attributes('disabled')).toBeDefined()
    // …while the caller's object is untouched.
    expect(target.loading).toBe(snapshot.loading)
    expect(target.disabled).toBe(snapshot.disabled)
    expect(target).toEqual(snapshot)

    release()
    await flushPromises()
    expect(target).toEqual(snapshot)
  })
})

describe('DialogFooter — async actions (L0009 §2 Async action)', () => {
  it('blocks re-entry while an action is running and releases it afterwards', async () => {
    let release: () => void = () => {}
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    const onSelect = vi.fn(() => pending)
    const wrapper = mountFooter([action('save', 'primary', { onSelect })])
    const button = wrapper.find('[data-dialog-action-id="save"]')

    await button.trigger('click')
    await flushPromises()
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(button.attributes('disabled')).toBeDefined()
    expect(button.classes()).toContain('is-loading')

    await button.trigger('click')
    await flushPromises()
    expect(onSelect).toHaveBeenCalledTimes(1)

    release()
    await flushPromises()
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.classes()).not.toContain('is-loading')
  })

  it('keeps the dialog open, shows the error and re-enables the button when onSelect rejects', async () => {
    const onSelect = vi.fn(() => Promise.reject(new Error('save failed')))
    const wrapper = mountFooter([action('save', 'primary', { onSelect })])

    await wrapper.find('[data-dialog-action-id="save"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.fg-dialog-footer__error').text()).toBe('save failed')
    expect(wrapper.find('[data-dialog-action-id="save"]').attributes('disabled')).toBeUndefined()
    // The footer is still on screen: a failed action never closes anything by itself.
    expect(wrapper.find('[data-dialog-action-id="save"]').exists()).toBe(true)
  })

  it('does not leave a button locked when the footer is destroyed mid-flight and remounted', async () => {
    let release: () => void = () => {}
    const pending = new Promise<void>((resolve) => {
      release = resolve
    })
    const actions = [action('save', 'primary', { onSelect: () => pending })]

    const first = mountFooter(actions)
    await first.find('[data-dialog-action-id="save"]').trigger('click')
    await flushPromises()
    first.unmount()
    release()
    await flushPromises()

    const second = mountFooter(actions)
    expect(second.find('[data-dialog-action-id="save"]').attributes('disabled')).toBeUndefined()
    second.unmount()
  })
})

describe('DialogFooter — busy (L0009 §2 Busy / Blocking, 완료조건 5/13)', () => {
  it('disables the finishing actions but keeps stop and dismiss live', () => {
    const wrapper = mountFooter(
      [
        action('aux', 'aux'),
        action('danger', 'danger'),
        action('stop', 'stop'),
        action('dismiss', 'dismiss'),
        action('cancel', 'cancel'),
        action('primary', 'primary'),
      ],
      { busy: true },
    )

    const disabledOf = (id: string) => wrapper.find(`[data-dialog-action-id="${id}"]`).attributes('disabled')
    expect(disabledOf('primary')).toBeDefined()
    expect(disabledOf('danger')).toBeDefined()
    expect(disabledOf('aux')).toBeDefined()
    expect(disabledOf('cancel')).toBeDefined()
    // Stopping the run is the one thing a user must still be able to do while busy.
    expect(disabledOf('stop')).toBeUndefined()
    expect(disabledOf('dismiss')).toBeUndefined()
    wrapper.unmount()
  })

  it('lets a caller disable one action without touching the rest', () => {
    const wrapper = mountFooter([
      action('primary', 'primary', { disabled: true }),
      action('cancel', 'cancel'),
    ])
    expect(wrapper.find('[data-dialog-action-id="primary"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-dialog-action-id="cancel"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('ignores clicks on a disabled action', async () => {
    const onSelect = vi.fn()
    const wrapper = mountFooter([action('primary', 'primary', { disabled: true, onSelect })])
    await wrapper.find('[data-dialog-action-id="primary"]').trigger('click')
    await flushPromises()
    expect(onSelect).not.toHaveBeenCalled()
    expect(wrapper.emitted('select')).toBeUndefined()
    wrapper.unmount()
  })
})
