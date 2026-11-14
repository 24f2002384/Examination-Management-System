<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'
import './portal-addons.css'

const props = defineProps({ booking: { type: Object, required: true } })
const emit = defineEmits(['close', 'saved'])
const info = ref({ item: null, label: 'Examination work document', replace_allowed: true })
const file = ref(null)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const success = ref('')

async function load() {
  loading.value = true
  try { info.value = await api.request(`/student/bookings/${props.booking.id}/submission`) }
  catch (e) { error.value = e.message }
  finally { loading.value = false }
}

async function submit() {
  if (!file.value) { error.value = 'Choose a report or answer document first.'; return }
  saving.value = true; error.value = ''; success.value = ''
  const body = new FormData()
  body.append('file', file.value)
  try {
    await api.request(`/student/bookings/${props.booking.id}/submission`, { method: 'POST', body })
    file.value = null
    await load()
    success.value = 'Your document was submitted and the Examiner was notified.'
    emit('saved')
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}

async function download() {
  try { await api.download(info.value.item.download_url, info.value.item.filename) }
  catch (e) { error.value = e.message }
}

onMounted(load)
</script>

<template>
  <div class="modal-backdrop-custom" @click.self="emit('close')">
    <section class="form-modal work-submission-modal" role="dialog" aria-modal="true" aria-labelledby="work-submission-title">
      <div class="modal-head">
        <div><div class="eyebrow">STUDENT WORK</div><h2 id="work-submission-title">{{ info.label }}</h2><p class="muted">{{ booking.examination }} · {{ booking.course }}</p></div>
        <button type="button" aria-label="Close" @click="emit('close')">×</button>
      </div>
      <p v-if="loading" class="muted">Loading submission…</p>
      <template v-else>
        <div v-if="info.item" class="submission-file-card"><div><strong>{{ info.item.filename }}</strong><small>Submitted {{ new Date(info.item.submitted_at).toLocaleString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true }) }}</small></div><button type="button" class="btn btn-sm btn-outline-primary" @click="download">Download</button></div>
        <p v-else class="muted">No document submitted yet. Upload a report or answer document for the Examiner.</p>
        <div v-if="error" class="alert alert-danger py-2">{{ error }}</div>
        <div v-if="success" class="alert alert-success py-2">{{ success }}</div>
        <form v-if="info.replace_allowed" class="submission-upload-form" @submit.prevent="submit">
          <label>{{ info.item ? 'Replace document' : 'Choose document' }}<input type="file" class="form-control" accept=".pdf,.doc,.docx,.txt,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" @change="file=$event.target.files?.[0]||null"></label>
          <small>PDF, DOC, DOCX or TXT · 10 MB maximum. You can replace your document until the Examiner submits the evaluation.</small>
          <button class="btn btn-primary" :disabled="saving">{{ saving ? 'Uploading…' : 'Submit work' }}</button>
        </form>
        <p v-else class="muted">Submission is locked because the Examiner has submitted the evaluation.</p>
      </template>
    </section>
  </div>
</template>
