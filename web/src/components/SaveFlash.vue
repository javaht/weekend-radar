<template>
  <!-- 固定在视口右下角：滚到 [sources] 等长页面底部时也能看到保存反馈 -->
  <Transition name="save-flash">
    <div
      v-if="state"
      class="fixed bottom-6 right-6 z-50 inline-flex items-center gap-1.5 rounded-full border px-3.5 py-2 text-[12px] font-medium shadow-sm"
      :class="state === 'error'
        ? 'border-accent/25 bg-accent/10 text-accent'
        : 'border-accent-green/25 bg-accent-green/10 text-accent-green'"
      role="status"
    >
      <svg v-if="state === 'ok'" class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 6 9 17l-5-5" />
      </svg>
      <svg v-else class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
        <path d="M12 8v4M12 16h.01" /><circle cx="12" cy="12" r="9" />
      </svg>
      {{ state === 'error' ? '保存失败' : '已保存' }}
    </div>
  </Transition>
</template>

<script setup lang="ts">
defineProps<{ state: '' | 'ok' | 'error' }>()
</script>
