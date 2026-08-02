import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import App from '@/App.vue'

describe('App (écran de test P0)', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('affiche le backend comme joignable quand /health répond ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'ok' }) }),
    )

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('Backend joignable')
  })

  it('affiche une erreur quand /health échoue', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503 }))

    const wrapper = mount(App)
    await flushPromises()

    expect(wrapper.text()).toContain('Backend injoignable')
  })
})
