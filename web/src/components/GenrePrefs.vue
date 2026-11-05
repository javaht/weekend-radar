<template>
  <section class="space-y-4">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h2 class="text-[16px] font-semibold tracking-tight text-ink">推送品类偏好</h2>
        <p class="mt-1 text-[12px] text-muted">
          勾选你关心的品类。取消勾选的品类仍会正常入库并在「活动情报」页展示，
          只是不再计入推送判定，避免被不感兴趣的活动打扰。
        </p>
      </div>
      <button
        @click="savePrefs"
        :disabled="saving || !dirty"
        class="btn-primary shrink-0"
      >
        <span v-if="saving">保存中…</span>
        <span v-else-if="!dirty">已保存</span>
        <span v-else>保存偏好</span>
      </button>
    </div>

    <div v-if="loading" class="py-8 text-center text-[12px] text-muted-soft">正在读取品类偏好…</div>

    <div v-else class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
      <button
        v-for="g in genres"
        :key="g.id"
        @click="toggle(g.id)"
        class="flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left transition"
        :class="g.enabled
          ? 'border-hairline bg-surface hover:border-border-specular'
          : 'border-hairline bg-surface-soft opacity-60'"
      >
        <span
          class="flex h-4 w-4 shrink-0 items-center justify-center rounded border transition"
          :class="g.enabled ? 'border-ink bg-ink' : 'border-border-strong bg-surface'"
        >
          <svg v-if="g.enabled" class="h-3 w-3 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
            <path d="M20 6 9 17l-5-5" />
          </svg>
        </span>
        <span class="text-[12px] font-medium" :class="g.enabled ? 'text-ink' : 'text-muted'">{{ g.label }}</span>
      </button>
    </div>

    <div class="flex items-center gap-3 pt-1">
      <button @click="setAll(true)" class="text-[12px] text-muted underline-offset-2 hover:text-ink hover:underline">全选</button>
      <span class="text-[12px] text-hairline">|</span>
      <button @click="setAll(false)" class="text-[12px] text-muted underline-offset-2 hover:text-ink hover:underline">全不选</button>
      <span class="ml-auto text-[11px] text-muted-soft">
        已选 {{ enabledCount }} / {{ genres.length }} 个品类
      </span>
    </div>

    <p v-if="message" class="text-[12px]" :class="messageOk ? 'text-accent-green' : 'text-accent'">
      {{ message }}
    </p>
  </section>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { api, type GenrePref } from '../api/client'

const genres = ref<GenrePref[]>([])
const loading = ref(false)
const saving = ref(false)
const dirty = ref(false)
const message = ref('')
const messageOk = ref(true)

const enabledCount = computed(() => genres.value.filter(g => g.enabled).length)

async function load() {
  loading.value = true
  try {
    const res = await api.getGenres()
    genres.value = res.genres
    dirty.value = false
  } catch (e: any) {
    message.value = `读取失败: ${e.message}`
    messageOk.value = false
  } finally {
    loading.value = false
  }
}

function toggle(id: string) {
  const g = genres.value.find(x => x.id === id)
  if (!g) return
  g.enabled = !g.enabled
  dirty.value = true
  message.value = ''
}

function setAll(on: boolean) {
  genres.value.forEach(g => { g.enabled = on })
  dirty.value = true
  message.value = ''
}

async function savePrefs() {
  saving.value = true
  message.value = ''
  try {
    const disabled = genres.value.filter(g => !g.enabled).map(g => g.id)
    const res = await api.updateGenres(disabled)
    genres.value = res.genres
    dirty.value = false
    message.value = res.message
    messageOk.value = true
  } catch (e: any) {
    message.value = `保存失败: ${e.message}`
    messageOk.value = false
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
