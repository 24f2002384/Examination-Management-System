<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const API = import.meta.env.VITE_API_URL || 'http://localhost:5000/api/v1'
const connection = ref({ connected: false, configured: false, email: null, status: 'loading' })
const loading = ref(true)
const busy = ref(false)
const message = ref('')
const messageType = ref('success')

async function loadStatus() {
  loading.value = true
  try {
    connection.value = await api.request('/admin/google-mail/status')
  } catch (error) {
    messageType.value = 'danger'
    message.value = error.message
  } finally {
    loading.value = false
  }
}

function connect() {
  window.location.assign(`${API}/admin/google-mail/connect`)
}

async function disconnect() {
  busy.value = true
  message.value = ''
  try {
    await api.request('/admin/google-mail/disconnect', { method: 'POST' })
    await loadStatus()
    messageType.value = 'success'
    message.value = 'Gmail has been disconnected.'
  } catch (error) {
    messageType.value = 'danger'
    message.value = error.message
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  const result = new URLSearchParams(window.location.search).get('gmail')
  const messages = {
    connected: ['Gmail account connected successfully.', 'success'],
    cancelled: ['Google authorization was cancelled.', 'warning'],
    error: ['Gmail could not be connected. Check the server configuration and try again.', 'danger'],
    scope_missing: ['Gmail sending permission was not granted. Reconnect and approve Gmail sending access.', 'danger'],
    setup: ['Add the Gmail OAuth settings to backend/.env and restart Flask before connecting.', 'warning'],
  }
  if (result && messages[result]) {
    ;[message.value, messageType.value] = messages[result]
    window.history.replaceState({}, '', window.location.pathname)
  }
  await loadStatus()
})
</script>

<template>
  <section class="panel-card gmail-settings-card">
    <div class="panel-heading">
      <div><h3>Gmail email delivery</h3><p>Connect a Google account for server-sent examination notifications.</p></div>
      <span class="gmail-state" :class="connection.connected ? 'is-connected' : ''">{{ loading ? 'Checking…' : connection.connected ? 'Connected' : connection.status === 'reauthorization_required' ? 'Reconnect required' : 'Not connected' }}</span>
    </div>
    <div v-if="message" class="alert" :class="`alert-${messageType}`" role="status">{{ message }}</div>
    <div class="gmail-settings-body">
      <p v-if="connection.connected"><strong>Connected account:</strong> {{ connection.email }}</p>
      <p v-else-if="!connection.configured" class="muted">Gmail connection is not configured on the server yet.</p>
      <p v-else-if="connection.status === 'reauthorization_required'" class="muted">Google authorization expired or was revoked. Reconnect Gmail to resume Gmail API delivery.</p>
      <p v-else class="muted">No Gmail account is connected. SMTP remains in use unless the server email provider is changed.</p>
      <button v-if="connection.connected" class="btn btn-outline-danger" :disabled="busy || loading" @click="disconnect">{{ busy ? 'Disconnecting…' : 'Disconnect Gmail' }}</button>
      <button v-else class="btn btn-primary" :disabled="!connection.configured || loading" @click="connect">Connect Gmail</button>
    </div>
  </section>
</template>
