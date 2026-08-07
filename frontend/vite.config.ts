import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
  },
  build: {
    rollupOptions: {
      output: {
        /**
         * Découpage manuel des trois moteurs.
         *
         * Le 3D (three + TresJS) et le 2D (Konva) ne servent jamais sur le même écran, et aucun
         * des deux ne sert sur la page de connexion ni sur `/partage/:token` — la vitrine, ouverte
         * au téléphone par le client de l'artisan. Les laisser fusionner avec le socle Vue les
         * rendait obligatoires partout.
         *
         * Priorités décroissantes : le groupe `vue` capture tout ce qui commence par `vue`, il
         * doit donc passer après `vue-konva`.
         */
        codeSplitting: {
          groups: [
            { name: 'three', test: /node_modules[\\/](three|@tresjs)[\\/]/, priority: 3 },
            { name: 'konva', test: /node_modules[\\/](konva|vue-konva)[\\/]/, priority: 2 },
            { name: 'vue', test: /node_modules[\\/](@?vue|vue-router|pinia)[\\/]/, priority: 1 },
          ],
        },
      },
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.spec.ts'],
  },
})
