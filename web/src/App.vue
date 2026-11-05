<template>
  <div class="flex min-h-screen flex-col bg-white font-sans text-ink">
    <!-- 顶部导航 -->
    <HeaderNav
      v-model:activeTab="activeTab"
      v-model:currentCity="currentCity"
      :cities="cityList"
      :taskStatus="taskStatus"
    />

    <!-- 主体内容 -->
    <main class="flex-1 pb-16">
      <KeepAlive>
        <component
          :is="currentViewComponent"
          :city="currentCity"
          :taskStatus="taskStatus"
          @task-finished="fetchStatus"
        />
      </KeepAlive>
    </main>

    <!-- 底部版权与架构声明 -->
    <footer class="border-t border-hairline py-6 text-center text-[11px] text-muted">
      <div class="mx-auto flex max-w-[880px] flex-col items-center justify-between gap-2 px-4 sm:flex-row sm:px-6">
        <div class="flex items-center gap-2">
          <span>Weekend Radar 周末活动雷达</span>
          <span>·</span>
          <span>Vue 3 + FastAPI 全栈架构</span>
        </div>
        <div class="flex items-center gap-3 font-mono text-[11px] text-muted-soft">
          <span>零重型爬虫框架</span>
          <span>·</span>
          <span>13 城预设联动</span>
          <span>·</span>
          <span>SQLite 状态差分</span>
        </div>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import HeaderNav from './components/HeaderNav.vue'
import IntelView from './views/IntelView.vue'
import ConsoleView from './views/ConsoleView.vue'
import SettingsView from './views/SettingsView.vue'
import { api, type TaskStatus } from './api/client'

const activeTab = ref('intel')
const currentCity = ref('北京')
const cityList = ref<string[]>([
  '北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '南京', '西安', '重庆', '苏州', '天津', '长沙'
])

const taskStatus = ref<TaskStatus>({
  running: false,
  task_type: null,
  city: null,
  started_at: null,
})

const currentViewComponent = computed(() => {
  if (activeTab.value === 'console') return ConsoleView
  if (activeTab.value === 'settings') return SettingsView
  return IntelView
})

async function fetchStatus() {
  try {
    const s = await api.getStatus()
    taskStatus.value = s
  } catch (e) {
    // 静默失败
  }
}

async function initPresets() {
  try {
    const res = await api.getPresets()
    if (res.cities && res.cities.length > 0) {
      cityList.value = res.cities.map(c => c.name)
    }
    const cfg = await api.getConfig()
    if (cfg.sections?.radar?.city) {
      currentCity.value = cfg.sections.radar.city
    }
  } catch (e) {
    console.error('初始化配置预设失败', e)
  }
}

let timer: number | null = null

onMounted(() => {
  initPresets()
  fetchStatus()
  timer = window.setInterval(fetchStatus, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>
