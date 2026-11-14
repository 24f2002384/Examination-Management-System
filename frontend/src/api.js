const API = import.meta.env.VITE_API_URL || 'http://localhost:5000/api/v1'

async function request(path, options = {}) {
  const isFormData = typeof FormData !== 'undefined' && options.body instanceof FormData
  const headers = { ...(!isFormData ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
  if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) {
    const token = await fetch(`${API}/auth/csrf`, { credentials: 'include' }).then(r => r.json()).then(d => d.csrf_token).catch(() => null)
    if (token) headers['X-CSRFToken'] = token
  }
  const response = await fetch(`${API}${path}`, { ...options, headers, credentials: 'include' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.error?.message || 'Request failed')
  return data
}

async function download(path, filename) {
  const response = await fetch(`${API}${path}`, { credentials: 'include' })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.error?.message || 'Download failed')
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const api = {
  request,
  download,
  me: () => request('/auth/me'),
  login: (email, password, role) => request('/auth/login', { method: 'POST', body: JSON.stringify({ email, password, role }) }),
  register: payload => request('/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => request('/auth/logout', { method: 'POST' }),
  dashboard: () => request('/dashboard'),
  profile: () => request('/profile'),
  updateProfile: payload => request('/profile', { method: 'PATCH', body: JSON.stringify(payload) }),
}
