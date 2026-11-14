<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { auth, notices, notify } from '../state.js'
import { api } from '../api.js'
const route = useRoute(), router = useRouter()
const notifications = ref([]), notificationOpen = ref(false)
const menus = {
  admin: [['Dashboard', '/dashboard'], ['Courses', '/admin/courses'], ['Examinations', '/admin/examinations'], ['Rubrics', '/admin/rubrics'], ['Examiners', '/admin/examiners'], ['Students', '/admin/students'], ['Slots', '/admin/slots'], ['Bookings', '/admin/bookings'], ['Evaluations', '/admin/evaluations'], ['Reports', '/admin/reports'], ['Activity history', '/admin/audit-log'], ['Settings', '/admin/settings']],
  examiner: [['Dashboard', '/dashboard'], ['My Examinations', '/examiner/my-examinations'], ['My Slots', '/examiner/my-slots'], ['Booked Students', '/examiner/booked-students'], ['Evaluations', '/examiner/evaluations']],
  student: [['Dashboard', '/dashboard'], ['Browse Examinations', '/student/browse-examinations'], ['My Bookings', '/student/my-bookings'], ['Examination History', '/student/examination-history'], ['Results', '/student/results']],
}
const links = computed(() => [...(menus[auth.user?.role] || []), ['Profile', auth.user?.role === 'examiner' ? '/examiner/profile' : auth.user?.role === 'student' ? '/student/profile' : '/profile']])
async function loadNotifications() { try { notifications.value = (await api.request('/notifications?per_page=10')).items } catch { /* The page remains usable when notifications are temporarily unavailable. */ } }
async function markNotificationRead(item) { if (item.read_at) return; try { const result = await api.request(`/notifications/${item.id}/read`, { method: 'POST' }); item.read_at = result.item.read_at } catch (e) { notify(e.message, 'danger') } }
onMounted(loadNotifications)
watch(() => route.fullPath, loadNotifications)
async function logout() { try { await api.logout(); auth.user = null; auth.checked = true; router.push('/login') } catch (e) { notify(e.message, 'danger') } }
</script>
<template>
  <div class="app-frame">
    <aside class="sidebar"><div class="brand"><span class="brand-icon">E</span><span>Exam<span class="brand-light">wise</span><small>MANAGEMENT PORTAL</small></span></div>
      <div class="workspace-label">WORKSPACE</div><nav class="side-nav"><RouterLink v-for="[label, href] in links" :key="href" :to="href" :class="{ active: route.path === href }"><span class="nav-mark">{{ label[0] }}</span>{{ label }}</RouterLink></nav>
      <div class="sidebar-bottom"><div class="help-panel"><strong>Need a hand?</strong><p>Visit help and documentation</p><a href="#">Open help centre <span>↗</span></a></div><div class="user-mini"><div class="avatar">{{ auth.user?.full_name?.[0] || 'U' }}</div><div class="user-info"><strong>{{ auth.user?.full_name }}</strong><small>{{ auth.user?.role }}</small></div><button class="signout-sidebar" @click="logout">Sign out</button></div></div>
    </aside>
    <main class="main-panel"><header class="topbar"><button class="mobile-menu" data-bs-toggle="offcanvas" data-bs-target="#mobileNav">☰</button><div class="breadcrumb">Workspace <span>/</span> <strong>{{ route.name || (route.path === '/dashboard' ? 'Dashboard' : route.path.split('/').pop()?.replaceAll('-', ' ')) }}</strong></div><div class="top-actions"><span class="status-dot"></span><span class="role-label">{{ auth.user?.role }}</span><div class="notification-control"><button class="notification-button" :aria-expanded="notificationOpen" aria-label="Notifications" @click="notificationOpen=!notificationOpen">♧<span v-if="notifications.some(n=>!n.read_at)" class="notification-count">{{notifications.filter(n=>!n.read_at).length}}</span></button><section v-if="notificationOpen" class="notification-panel"><div class="notification-heading"><strong>Notifications</strong><button class="btn btn-sm btn-light" @click="loadNotifications">Refresh</button></div><button v-for="item in notifications" :key="item.id" class="notification-item" :class="{'notification-unread':!item.read_at}" @click="markNotificationRead(item)"><span>{{item.message}}</span><small>{{new Date(item.created_at).toLocaleString()}}</small></button><p v-if="!notifications.length" class="notification-empty">You’re all caught up.</p></section></div><button class="btn btn-sm btn-outline-secondary signout-button" @click="logout">Sign out</button><button class="avatar avatar-small" aria-label="Open profile" @click="router.push(auth.user?.role==='examiner'?'/examiner/profile':auth.user?.role==='student'?'/student/profile':'/profile')">{{ auth.user?.full_name?.[0] }}</button></div></header>
      <div class="offcanvas offcanvas-start mobile-drawer" id="mobileNav"><div class="offcanvas-header"><h5>Examwise</h5><button class="btn-close" data-bs-dismiss="offcanvas"></button></div><div class="offcanvas-body"><RouterLink v-for="[label, href] in links" :key="href" class="drawer-link" :to="href">{{ label }}</RouterLink></div></div>
      <section class="page-content"><slot /></section>
    </main>
    <div class="toast-stack"><div v-for="n in notices.items" :key="n.id" class="alert shadow-sm" :class="`alert-${n.kind}`">{{ n.message }}</div></div>
  </div>
</template>

<style scoped>
.signout-button { font-size: 10px; padding: 5px 9px; white-space: nowrap; }
.signout-sidebar { border: 1px solid #56708b; border-radius: 5px; background: transparent; color: #cbd9e8; font-size: 9px; padding: 5px 7px; white-space: nowrap; }
.signout-sidebar:hover { background: #ffffff12; color: #fff; }
@media (max-width: 360px) { .top-actions { gap: 6px; } .signout-button { padding: 4px 6px; } }
</style>
