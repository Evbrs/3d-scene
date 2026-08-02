<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchHealth } from '@/api/client'

type BackendState = 'loading' | 'ok' | 'error'

const state = ref<BackendState>('loading')
const detail = ref<string>('')

onMounted(async () => {
  try {
    const health = await fetchHealth()
    state.value = health.status === 'ok' ? 'ok' : 'error'
    detail.value = `status = ${health.status}`
  } catch (error) {
    state.value = 'error'
    detail.value = error instanceof Error ? error.message : String(error)
  }
})
</script>

<template>
  <main class="shell">
    <h1>Éditeur de plan de rénovation 2D → 3D</h1>
    <p class="subtitle">
      Écran de test du scaffolding (ticket P0).
    </p>

    <section
      aria-labelledby="backend-status-title"
      class="card"
    >
      <h2 id="backend-status-title">
        État du backend
      </h2>
      <p
        aria-live="polite"
        :data-state="state"
      >
        <span v-if="state === 'loading'">Vérification de <code>GET /health</code>…</span>
        <span v-else-if="state === 'ok'">Backend joignable — {{ detail }}</span>
        <span v-else>Backend injoignable — {{ detail }}</span>
      </p>
    </section>
  </main>
</template>

<style scoped>
.shell {
  margin: 0 auto;
  max-width: 44rem;
  padding: 2rem 1rem;
  font-family: system-ui, sans-serif;
  line-height: 1.5;
}

.subtitle {
  color: #4a4a4a;
}

.card {
  border: 1px solid #d4d4d4;
  border-radius: 0.5rem;
  padding: 1rem;
}

[data-state='ok'] {
  color: #0b6b2f;
}

[data-state='error'] {
  color: #8a1010;
}
</style>
