<script setup>
import './portal-addons.css'
defineProps({ items: { type: Array, default: () => [] } })
const emit = defineEmits(['capacity', 'cancel'])
function date(value) {
  return value ? new Date(value).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'
}
function time(value) {
  return value ? new Date(value).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true }) : '—'
}
</script>

<template>
  <table class="table admin-table examiner-slots-table">
    <thead><tr><th>Examination</th><th>Date</th><th>Time</th><th>Capacity used</th><th>Seats left</th><th>Status</th><th>Meeting</th><th>Actions</th></tr></thead>
    <tbody>
      <tr v-for="slot in items" :key="slot.id">
        <td>{{ slot.examination }}</td>
        <td>{{ date(slot.starts_at) }}</td>
        <td>{{ time(slot.starts_at) }}–{{ time(slot.ends_at) }}</td>
        <td>{{ slot.booked_seats }} / {{ slot.capacity }}</td>
        <td>{{ slot.available_seats }}</td>
        <td><span class="status-pill" :class="`status-${slot.status}`">{{ slot.status }}</span></td>
        <td><a v-if="slot.meeting_link" :href="slot.meeting_link" target="_blank" rel="noopener noreferrer">Open link</a><span v-else>—</span></td>
        <td class="actions-cell">
          <button class="btn btn-sm btn-light" @click="emit('capacity', slot)">Capacity</button>
          <button class="btn btn-sm btn-outline-warning" :disabled="slot.booked_seats>0 || ['cancelled','completed'].includes(slot.status)" @click="emit('cancel', slot)">Cancel</button>
        </td>
      </tr>
      <tr v-if="!items.length"><td colspan="8" class="text-center text-secondary">No examination slots found.</td></tr>
    </tbody>
  </table>
</template>
