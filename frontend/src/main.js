import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import 'bootstrap/dist/css/bootstrap.min.css'
import 'bootstrap/dist/js/bootstrap.bundle.min.js'
import './style.css'
import App from './App.vue'
import { routes } from './router/index.js'
import './typography.css'
import { auth } from './state.js'
import { api } from './api.js'

const router = createRouter({ history: createWebHistory(), routes })
router.beforeEach(async (to) => {
  if (!auth.checked) {
    try { auth.user = (await api.me()).user } catch { auth.user = null }
    auth.checked = true
  }
  if (to.meta.publicOnly && auth.user) return '/dashboard'
  if (to.meta.auth && !auth.user) return '/login'
  if (to.meta.role && auth.user?.role !== to.meta.role) return '/dashboard'
})
createApp(App).use(router).mount('#app')
