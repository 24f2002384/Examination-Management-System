import Login from '../views/Login.vue'
import Register from '../views/Register.vue'
import Dashboard from '../views/Dashboard.vue'
import Profile from '../views/Profile.vue'
import Placeholder from '../views/Placeholder.vue'
import AdminPage from '../views/AdminPage.vue'
import PortalPage from '../views/PortalPage.vue'
const rolePages = {
  admin: ['Courses', 'Examinations', 'Rubrics', 'Examiners', 'Students', 'Slots', 'Bookings', 'Reports', 'Settings'],
  examiner: ['My Examinations', 'My Slots', 'Booked Students', 'Evaluations'],
  student: ['Browse Examinations', 'My Bookings', 'Examination History', 'Results'],
}
export const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/login', component: Login, meta: { publicOnly: true } },
  { path: '/register', component: Register, meta: { publicOnly: true } },
  { path: '/dashboard', component: Dashboard, meta: { auth: true } },
  { path: '/admin/courses', component: AdminPage, props: { title: 'Courses', section: 'courses' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/examinations', component: AdminPage, props: { title: 'Examinations', section: 'examinations' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/rubrics', component: AdminPage, props: { title: 'Rubrics', section: 'rubrics' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/examiners', component: AdminPage, props: { title: 'Examiners', section: 'examiners' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/students', component: AdminPage, props: { title: 'Students', section: 'students' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/slots', component: AdminPage, props: { title: 'Slots', section: 'slots' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/bookings', component: AdminPage, props: { title: 'Bookings', section: 'bookings' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/reports', component: AdminPage, props: { title: 'Reports', section: 'reports' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/settings', component: AdminPage, props: { title: 'Settings', section: 'settings' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/audit-log', component: AdminPage, props: { title: 'Activity history', section: 'audit-log' }, meta: { auth: true, role: 'admin' } },
  { path: '/admin/evaluations', component: AdminPage, props: { title: 'Evaluations & results', section: 'evaluations' }, meta: { auth: true, role: 'admin' } },
  { path: '/examiner/profile', component: PortalPage, props: { title: 'Profile', section: 'profile' }, meta: { auth: true, role: 'examiner' } },
  { path: '/student/profile', component: PortalPage, props: { title: 'Profile', section: 'profile' }, meta: { auth: true, role: 'student' } },
  { path: '/profile', component: Profile, meta: { auth: true } },
  ...Object.entries(rolePages).flatMap(([role, pages]) => pages.map(name => ({ path: `/${role}/${name.toLowerCase().replaceAll(' ', '-')}`, component: role === 'admin' ? AdminPage : PortalPage, props: { title: name, section: name.toLowerCase().replaceAll(' ', '-') }, meta: { auth: true, role } }))),
  { path: '/:pathMatch(.*)*', component: Placeholder, props: { title: 'Page not found', missing: true } },
]
