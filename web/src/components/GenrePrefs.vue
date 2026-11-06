<template>
  <section class="space-y-4">
    <div class="flex items-start justify-between gap-4">
      <div>
        <h2 class="text-[16px] font-semibold tracking-tight text-ink">推送品类偏好</h2>
        <p class="mt-1 text-[12px] text-muted">
          勾选你关心的品类。取消勾选的品类仍会正常入库并在「活动情报」页展示，
          只是不再计入推送判定，避免被不感兴趣的活动打扰。修改后自动保存。
        </p>
      </div>

      <span class="shrink-0 text-[11px] text-muted-soft">修改自动保存</span>
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

    <SaveFlash :state="saveFlash" />
  </section>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api, type GenrePref } from '../api/client'
import SaveFlash from './SaveFlash.vue'

const genres = ref<GenrePref[]>([])
const loading = ref(false)
const saveFlash = ref('')   // '' | 'ok' | 'error'
let saveTimer: number | null = null
let flashTimer: number | null = null
// 基线：加载与保存回填不应触发自动保存
let baseline = ''
let suspendBaselineSync = false

const enabledCount = computed(() => genres.value.filter(g => g.enabled).length)
const stateKey = () => genres.value.map(g => g.enabled).join(',')

function flash(kind: 'ok' | 'error') {
  saveFlash.value = kind
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = window.setTimeout(() => { saveFlash.value = '' }, kind === 'ok' ? 600 : 2000)
}

async function load() {
  loading.value = true
  suspendBaselineSync = true
  try {
    const res = await api.getGenres()
    genres.value = res.genres
  } catch (e: any) {
    saveFlash.value = 'error'
    flashTimer = window.setTimeout(() => { saveFlash.value = '' }, 2000)
  } finally {
    baseline = stateKey()
    loading.value = false
    suspendBaselineSync = false
  }
}

function toggle(id: string) {
  const g = genres.value.find(x => x.id === id)
  if (!g) return
  g.enabled = !g.enabled
}

function setAll(on: boolean) {
  genres.value.forEach(g => { g.enabled = on })
}

async function savePrefs() {
  try {
    const disabled = genres.value.filter(g => !g.enabled).map(g => g.id)
    const res = await api.updateGenres(disabled)
    suspendBaselineSync = true
    genres.value = res.genres
    baseline = stateKey()
    suspendBaselineSync = false
    flash('ok')
  } catch (e: any) {
    suspendBaselineSync = false
    saveFlash.value = 'error'
    flashTimer = window.setTimeout(() => { saveFlash.value = '' }, 2000)
  }
}

// 勾选/全选后 debounce 500ms 自动保存
watch(stateKey, () => {
  if (suspendBaselineSync || stateKey() === baseline) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = window.setTimeout(savePrefs, 500)
})

onMounted(load)

onUnmounted(() => {
  if (saveTimer) clearTimeout(saveTimer)
  if (flashTimer) clearTimeout(flashTimer)
})
</script>
