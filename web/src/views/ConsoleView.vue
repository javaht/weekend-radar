<template>
  <div class="mx-auto max-w-[880px] space-y-5 px-4 pb-20 pt-5 sm:px-6">
    <!-- 操作中枢：只保留正式执行 -->
    <section class="panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 class="text-[16px] font-semibold tracking-tight text-ink">任务调度</h2>
          <p class="mt-1 text-[12px] text-muted">
            全源抓取 + 状态差分 + 按需推送，日志实时输出在下方
          </p>
        </div>

        <button
          @click="handleRun"
          :disabled="isBusy || props.taskStatus.running"
          class="btn-primary tactile-button disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span v-if="isBusy || props.taskStatus.running" class="mr-2 inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent align-middle"></span>
          正式执行并入库
        </button>
      </div>
    </section>

    <!-- 实时终端 -->
    <section class="panel overflow-hidden">
      <div class="flex items-center justify-between border-b border-hairline bg-surface-soft px-4 py-2.5">
        <span class="font-mono text-[11px] text-muted">运行日志</span>
        <div class="flex items-center gap-3">
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
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api/client'

const props = defineProps<{
  city: string
  taskStatus: { running: boolean; task_type: string | null }
}>()

const emit = defineEmits<{
  (e: 'task-finished'): void
}>()

const isBusy = ref(false)
const logs = ref<string[]>([])
const autoScroll = ref(true)
const terminalRef = ref<HTMLDivElement | null>(null)

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
    await api.triggerRun({ city: props.city })
  } catch (e: any) {
    alert(e.message)
  } finally {
    isBusy.value = false
    emit('task-finished')
  }
}

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
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
  }
})
</script>
