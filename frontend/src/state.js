import { reactive } from 'vue'
export const auth = reactive({ user: null, checked: false })
export const notices = reactive({ items: [] })
export function notify(message, kind = 'success') {
  const id = Date.now()
  notices.items.push({ id, message, kind })
  setTimeout(() => { notices.items = notices.items.filter(item => item.id !== id) }, 3500)
}
