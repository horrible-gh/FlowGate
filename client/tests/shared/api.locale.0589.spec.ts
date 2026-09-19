/** 0589 T#1 — the rendered UI locale is the single source for X-Locale. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'

let restoreAdapter: (() => void) | null = null
const originalBrowserLanguage = navigator.language

function setBrowserLanguage(language: string) {
  Object.defineProperty(window.navigator, 'language', {
    configurable: true,
    value: language,
  })
}

async function localeStack(stored: string | null, browser: string) {
  localStorage.removeItem('preferred_locale')
  if (stored !== null) localStorage.setItem('preferred_locale', stored)
  setBrowserLanguage(browser)
  vi.resetModules()

  const { default: i18n } = await import('@shared/i18n')
  const { default: api } = await import('@shared/api')
  const originalAdapter = api.defaults.adapter
  const requestLocales: string[] = []

  api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    requestLocales.push(String(config.headers.get('X-Locale')))
    return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
  }
  restoreAdapter = () => {
    api.defaults.adapter = originalAdapter
  }

  return { api, i18n, requestLocales }
}

afterEach(() => {
  restoreAdapter?.()
  restoreAdapter = null
  localStorage.removeItem('preferred_locale')
  setBrowserLanguage(originalBrowserLanguage)
  vi.resetModules()
})

describe('shared/api locale SSOT', () => {
  it.each([
    {
      name: 'uses the Japanese browser locale when no preference is stored',
      stored: null,
      browser: 'ja-JP',
      expected: 'ja',
    },
    {
      name: 'uses the English browser locale when no preference is stored',
      stored: null,
      browser: 'en-US',
      expected: 'en',
    },
    {
      name: 'gives the stored locale priority over the browser locale',
      stored: 'ja',
      browser: 'en-US',
      expected: 'ja',
    },
    {
      name: 'falls an unsupported browser locale back to Korean',
      stored: null,
      browser: 'zh-CN',
      expected: 'ko',
    },
  ])('$name', async ({ stored, browser, expected }) => {
    const { api, i18n, requestLocales } = await localeStack(stored, browser)

    await api.get('/locale-probe')

    expect(i18n.global.locale.value).toBe(expected)
    expect(requestLocales).toEqual([expected])
  })

  it('uses a runtime locale switch on the very next request without a reload', async () => {
    const { api, i18n, requestLocales } = await localeStack('ko', 'en-US')

    await api.get('/before-switch')
    i18n.global.locale.value = 'ja'
    await api.get('/after-switch')

    expect(i18n.global.locale.value).toBe('ja')
    expect(requestLocales).toEqual(['ko', 'ja'])
  })
})