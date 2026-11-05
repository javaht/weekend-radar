<template>
  <header class="sticky top-0 z-50 w-full border-b border-hairline bg-white/95 backdrop-blur-sm">
    <div class="mx-auto flex h-16 max-w-[880px] items-center justify-between gap-4 px-4 sm:px-6">
      <!-- 品牌标识：圆形 logo + 文字标 -->
      <div class="flex flex-shrink-0 items-center gap-2.5">
        <div class="flex h-9 w-9 items-center justify-center rounded-full bg-ink">
          <Radar class="text-white" :size="18" />
        </div>
        <span class="hidden text-[15px] font-semibold tracking-tight text-ink sm:inline">Weekend Radar</span>
      </div>

      <!-- 标签栏：激活项为黑底圆角块 -->
      <nav class="-mx-1 flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto px-1 sm:flex-none sm:gap-2">
        <button
          v-for="tab in tabs"
          :key="tab.id"
          @click="$emit('update:activeTab', tab.id)"
          class="tactile-button inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xl px-2.5 py-2 text-[12px] font-medium transition-colors sm:px-3.5 sm:text-[13px]"
          :class="activeTab === tab.id
            ? 'bg-ink text-white'
            : 'border border-hairline bg-white text-muted hover:border-ink/25 hover:text-ink'"
        >
          <component :is="tab.icon" :size="14" class="hidden sm:block" />
          <span>{{ tab.label }}</span>
        </button>
      </nav>

      <!-- 右侧：城市选择胶囊 + 状态点 -->
      <div class="flex flex-shrink-0 items-center gap-2">
        <div class="relative">
          <MapPin class="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" :size="13" />
          <select
            :value="currentCity"
            @change="$emit('update:currentCity', ($event.target as HTMLSelectElement).value)"
            class="tactile-button cursor-pointer appearance-none rounded-xl border border-hairline bg-white py-1.5 pl-7 pr-6 text-[12px] font-medium text-ink outline-none transition-colors hover:border-ink/25 focus:border-ink/30 sm:pr-7"
          >
            <option v-for="c in cities" :key="c" :value="c">{{ c }}</option>
          </select>
          <ChevronDown class="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 text-muted sm:block" :size="12" />
        </div>

        <div class="hidden items-center gap-1.5 rounded-xl border border-hairline bg-white px-2.5 py-1.5 sm:flex">
          <span class="relative flex h-1.5 w-1.5">
            <span v-if="taskStatus.running" class="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent-green opacity-60"></span>
            <span class="relative inline-flex h-1.5 w-1.5 rounded-full" :class="taskStatus.running ? 'bg-accent-green' : 'bg-hairline'"></span>
          </span>
          <span class="whitespace-nowrap text-[11px] font-medium" :class="taskStatus.running ? 'text-accent-green' : 'text-muted'">
            {{ taskStatus.running ? `${(taskStatus.task_type || '任务').toUpperCase()} 中` : '就绪' }}
          </span>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
import { Radar, Terminal, SlidersHorizontal, MapPin, ChevronDown } from 'lucide-vue-next'

defineProps<{
  activeTab: string
  currentCity: string
  cities: string[]
  taskStatus: {
    running: boolean
    task_type: string | null
    city: string | null
    started_at: string | null
  }
}>()

defineEmits<{
  (e: 'update:activeTab', tab: string): void
  (e: 'update:currentCity', city: string): void
}>()

const tabs = [
  { id: 'intel', label: '活动情报', icon: Radar },
  { id: 'console', label: '任务中控', icon: Terminal },
  { id: 'settings', label: '配置渠道', icon: SlidersHorizontal },
]
</script>
