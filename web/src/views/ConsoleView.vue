<template>
  <div class="mx-auto max-w-[880px] space-y-5 px-4 pb-20 pt-5 sm:px-6">
    <!-- 定时任务 -->
    <section class="panel p-5">
      <div>
        <h2 class="text-[16px] font-semibold tracking-tight text-ink">定时任务</h2>
        <p class="mt-1 text-[12px] text-muted">
          到点自动执行一次抓取 + 入库 + 按需推送；依赖本服务常驻运行
        </p>
      </div>

      <!-- 已有定时：按城市列出，可逐条删除 -->
      <div v-if="schedules.length" class="mt-4 space-y-2">
        <div
          v-for="s in schedules"
          :key="s.id"
          class="flex items-center justify-between gap-3 rounded-xl border border-hairline bg-surface-soft px-3 py-2.5"
        >
          <div class="min-w-0 overflow-hidden">
            <div class="flex flex-wrap items-center gap-x-2 text-[12px]">
              <span class="font-semibold text-ink">{{ s.effective_city }}</span>
              <span class="text-muted">{{ freqLabel(s) }} {{ s.time }}</span>
              <span class="text-muted-soft">下次 {{ nextRunText(s.next_run) }}</span>
            </div>
          </div>
          <button
            @click="deleteSchedule(s)"
            :disabled="scheduleSaving"
            class="tactile-button flex-shrink-0 rounded-lg border border-hairline bg-white px-2.5 py-1 text-[11px] text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-50"
          >
            删除
          </button>
        </div>
      </div>
      <p v-else class="mt-4 text-[12px] text-muted-soft">还没有定时任务，保存后立即生效</p>

      <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <label class="field-label">频率</label>
          <select v-model="schedule.mode" class="field">
            <option value="daily">每天</option>
            <option value="weekly">每周指定星期</option>
          </select>
        </div>
        <div>
          <label class="field-label">时间</label>
          <input v-model="schedule.time" type="time" class="field" />
        </div>
        <div>
          <label class="field-label">执行城市</label>
          <select v-model="scheduleCity" class="field">
            <option v-for="c in cityOptions" :key="c" :value="c">{{ c }}</option>
          </select>
          <p class="mt-1.5 truncate text-[11px] text-muted-soft">默认跟随右上角城市，也可单独选择</p>
        </div>
      </div>

      <div v-if="schedule.mode === 'weekly'" class="mt-3 flex flex-wrap gap-2">
        <button
          v-for="d in 7"
          :key="d"
          type="button"
          @click="toggleWeekday(d)"
          class="tactile-button rounded-lg border px-2.5 py-1 text-[12px] transition-colors"
          :class="schedule.weekdays.includes(d)
            ? 'border-ink bg-ink text-white'
            : 'border-hairline bg-white text-muted hover:border-ink/30 hover:text-ink'"
        >
          周{{ '一二三四五六日'[d - 1] }}
        </button>
      </div>

      <div class="mt-4 flex items-center justify-between gap-4 border-t border-hairline pt-4">
        <p class="min-w-0 text-[12px] text-muted-soft">
          {{ schedules.length ? '同城已存在的定时会被覆盖更新' : '保存后按设定时间自动执行' }}
        </p>
        <div class="flex flex-shrink-0 items-center gap-2">
          <button
            @click="saveSchedule"
            :disabled="scheduleLoading || scheduleSaving"
            class="tactile-button flex h-[38px] items-center gap-1.5 rounded-xl border border-hairline bg-surface-soft px-3.5 text-[12px] font-medium text-ink transition-colors hover:border-ink/30 disabled:opacity-50"
          >
            <span v-if="scheduleSaving" class="h-3 w-3 animate-spin rounded-full border-2 border-muted border-t-transparent"></span>
            <span>{{ scheduleSaving ? '保存中…' : (scheduleSaved ? '已保存' : '保存定时任务') }}</span>
          </button>
          <button
            @click="handleRun"
            :disabled="isBusy || props.taskStatus.running"
            class="btn-primary tactile-button flex h-[38px] items-center disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span v-if="isBusy || props.taskStatus.running" class="mr-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent align-middle"></span>
            立即执行
          </button>
        </div>
      </div>
    </section>

    <!-- 实时终端 -->
    <section class="panel overflow-hidden">
      <div class="flex items-center justify-between border-b border-hairline bg-surface-soft px-4 py-2.5">
        <span class="font-mono text-[11px] text-muted">运行日志</span>
        <div class="flex items-center gap-3">
          <button
            v-if="props.taskStatus.running && props.taskStatus.task_type === 'run'"
            @click="handleStop"
            :disabled="stopping"
            :title="stopping ? '当前数据源结束后终止，本轮不入库' : '中止本轮任务（当前数据源结束后生效）'"
            class="font-mono text-[11px] text-accent transition-colors hover:underline disabled:opacity-60"
          >
            {{ stopping ? '正在停止…' : '停止任务' }}
          </button>
          <button
            @click="autoScroll = !autoScroll"
            class="font-mono text-[11px] text-muted transition-colors hover:text-ink"
          >
            {{ autoScroll ? '滚屏锁定: 开' : '滚屏锁定: 关' }}
          </button>
          <button
            @click="clearLogs"
            class="font-mono text-[11px] text-muted transition-colors hover:text-ink"
          >
            清屏
          </button>
        </div>
      </div>

      <div
        ref="terminalRef"
        class="h-96 select-text space-y-0.5 overflow-y-auto bg-surface-soft p-4 font-mono text-[12px] leading-relaxed"
      >
        <div v-if="logs.length === 0" class="py-8 text-center italic text-muted-soft">
          暂无日志输出。点击「正式执行并入库」开始。
        </div>
        <div
          v-for="(log, idx) in logs"
          :key="idx"
          class="whitespace-pre-wrap break-all"
          :class="getLogLineClass(log)"
        >
          {{ log }}
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { api, type ScheduleItem } from '../api/client'

const props = defineProps<{
  city: string
  taskStatus: { running: boolean; task_type: string | null }
}>()

const emit = defineEmits<{
  (e: 'task-finished'): void
}>()

const isBusy = ref(false)
const stopping = ref(false)
let statusTimer: number | null = null
const logs = ref<string[]>([])
const autoScroll = ref(true)
const terminalRef = ref<HTMLDivElement | null>(null)

const schedules = ref<ScheduleItem[]>([])
// 定时执行城市：默认跟随右上角，用户在下拉里改过之后就不再被顶部联动覆盖
const scheduleCity = ref(props.city)
const cityOptions = ref<string[]>([props.city])
let lastTopCity = props.city
const schedule = ref({
  mode: 'daily' as 'daily' | 'weekly',
  time: '09:00',
  weekdays: [1, 2, 3, 4, 5, 6, 7] as number[],
})
const scheduleLoading = ref(false)
const scheduleSaving = ref(false)
const scheduleSaved = ref(false)
let scheduleSavedTimer: number | null = null

function toggleWeekday(d: number) {
  const list = schedule.value.weekdays
  schedule.value.weekdays = list.includes(d)
    ? list.filter(x => x !== d)
    : [...list, d].sort((a, b) => a - b)
}

// 顶部城市变化时联动，但用户在下拉里单独选过之后不再覆盖
watch(() => props.city, (v) => {
  if (!scheduleCity.value || scheduleCity.value === lastTopCity) {
    scheduleCity.value = v
  }
  lastTopCity = v
  if (v && !cityOptions.value.includes(v)) {
    cityOptions.value = [...cityOptions.value, v]
  }
})

async function loadCities() {
  try {
    const res = await api.getPresets()
    const names = (res.cities || []).map(c => c.name).filter(Boolean)
    if (names.length) {
      cityOptions.value = names
      if (!names.includes(scheduleCity.value)) scheduleCity.value = names[0]
    }
  } catch {
    // 拉不到城市预设就保留当前城市，不阻塞保存
  }
}

function freqLabel(s: ScheduleItem) {
  if (s.mode !== 'weekly') return '每天'
  return '每周' + s.weekdays.map(d => '周' + '一二三四五六日'[d - 1]).join('、')
}

function nextRunText(iso?: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} 周${'日一二三四五六'[d.getDay()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadSchedules() {
  scheduleLoading.value = true
  try {
    const res = await api.getSchedules()
    schedules.value = res.items || []
  } catch {
    // 读取失败保持空列表，不影响手动执行
  } finally {
    scheduleLoading.value = false
  }
}

async function saveSchedule() {
  scheduleSaving.value = true
  scheduleSaved.value = false
  try {
    const res = await api.saveSchedule({
      mode: schedule.value.mode,
      time: schedule.value.time,
      weekdays: schedule.value.weekdays,
      city: scheduleCity.value,
    })
    schedules.value = res.items || []
    scheduleSaved.value = true
    if (scheduleSavedTimer) clearTimeout(scheduleSavedTimer)
    scheduleSavedTimer = window.setTimeout(() => { scheduleSaved.value = false }, 2000)
  } catch (e: any) {
    alert(e.message)
  } finally {
    scheduleSaving.value = false
  }
}

async function deleteSchedule(item: ScheduleItem) {
  if (!window.confirm(`删除「${item.effective_city}」${freqLabel(item)} ${item.time} 的定时任务？`)) return
  scheduleSaving.value = true
  try {
    const res = await api.deleteSchedule(item.id)
    schedules.value = res.items || []
  } catch (e: any) {
    alert(e.message)
  } finally {
    scheduleSaving.value = false
  }
}

let eventSource: EventSource | null = null

function setupSSE() {
  if (eventSource) {
    eventSource.close()
  }
  eventSource = new EventSource('/api/radar/logs/stream')
  eventSource.onmessage = (event) => {
    if (event.data) {
      logs.value.push(event.data)
      if (logs.value.length > 800) {
        logs.value.shift()
      }
      if (autoScroll.value) {
        nextTick(() => {
          if (terminalRef.value) {
            terminalRef.value.scrollTop = terminalRef.value.scrollHeight
          }
        })
      }
    }
  }
}

async function handleRun() {
  isBusy.value = true
  try {
    // 页面不再提供 Dry-Run / Force-Push 开关，统一按「正式执行」调用:
    // 入库、写报告、按 push 配置的规则决定是否推送。
    await api.triggerRun({ city: scheduleCity.value })
    // 父组件的状态轮询是 10s 粒度：这里自己快轮询，running 一翻转就通知父级，
    // 避免「已开始跑但停止按钮要等 10 秒才出现」。
    emit('task-finished')
    if (statusTimer) clearInterval(statusTimer)
    let tries = 0
    statusTimer = window.setInterval(async () => {
      tries += 1
      try {
        const s = await api.getStatus()
        if (s.running || tries > 20) {
          if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
          isBusy.value = false
          emit('task-finished')
        }
      } catch {}
    }, 500)
  } catch (e: any) {
    isBusy.value = false
    alert(e.message)
    emit('task-finished')
  }
}

async function handleStop() {
  stopping.value = true
  try {
    await api.stopRun()
    // 立刻刷新一次全局状态，让顶栏从「RUN 中」切到「停止中」
    emit('task-finished')
    // 后端只置停止标记：当前数据源跑完才真正结束，这里轮询到状态翻转
    if (statusTimer) clearInterval(statusTimer)
    statusTimer = window.setInterval(async () => {
      try {
        const s = await api.getStatus()
        if (!s.running) {
          if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
          stopping.value = false
          emit('task-finished')
        }
      } catch {}
    }, 1500)
  } catch (e: any) {
    stopping.value = false
    alert(e.message)
  }
}

// 任务自然结束（或停止生效）后，清掉本地「正在停止」状态
watch(() => props.taskStatus.running, (running) => {
  if (!running) {
    stopping.value = false
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null }
  }
})

async function clearLogs() {
  logs.value = []
  await api.clearLogs()
}

function getLogLineClass(line: string) {
  if (line.includes('[PASS]') || line.includes('成功') || line.includes('✅')) {
    return 'text-accent-green'
  }
  if (line.includes('[失败]') || line.includes('[FAIL]') || line.includes('❌') || line.includes('异常')) {
    return 'font-semibold text-accent'
  }
  if (line.includes('[警告]') || line.includes('[!]')) {
    return 'text-accent-amber'
  }
  if (line.includes('模式:')) {
    return 'font-semibold text-accent-blue'
  }
  return 'text-muted'
}

onMounted(() => {
  setupSSE()
  loadSchedules()
  loadCities()
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
  }
  if (statusTimer) clearInterval(statusTimer)
  if (scheduleSavedTimer) clearTimeout(scheduleSavedTimer)
})
</script>
