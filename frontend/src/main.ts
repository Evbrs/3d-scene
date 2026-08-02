import { createPinia } from 'pinia'
import { createApp } from 'vue'
import VueKonva from 'vue-konva'

import App from '@/App.vue'
import { router } from '@/router'

createApp(App).use(createPinia()).use(router).use(VueKonva).mount('#app')
