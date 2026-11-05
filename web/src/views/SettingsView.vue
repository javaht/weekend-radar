<template>
  <div class="mx-auto max-w-[880px] space-y-5 px-4 pb-20 pt-5 sm:px-6">
    <!-- Webhook 实时测试调试台 -->
    <section class="panel p-5">
      <div>
        <h2 class="text-[16px] font-semibold tracking-tight text-ink">Webhook 推送通道调试台</h2>
        <p class="mt-1 text-[12px] text-muted">测试目标交付通道连通性与加签校验，零副作用发送单条测试卡片</p>
      </div>

      <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <label class="mb-1.5 block text-[12px] font-medium text-muted">推送渠道 (Channel)</label>
          <select v-model="testChannel" class="field">
            <option v-for="ch in supportedChannels" :key="ch" :value="ch">{{ ch }}</option>
          </select>
        </div>

        <div>
          <label class="mb-1.5 block text-[12px] font-medium text-muted">Webhook URL / Endpoint</label>
          <input v-model="testWebhook" type="text" placeholder="留空则读取 config.ini" class="field" />
        </div>

        <div>
          <label class="mb-1.5 block text-[12px] font-medium text-muted">Secret / Key（可选加签）</label>
          <input v-model="testSecret" type="password" placeholder="留空则读取 config.ini" class="field" />
        </div>
      </div>

      <div class="mt-4 flex items-center justify-between gap-4 border-t border-hairline pt-4">
        <div class="min-w-0 text-[12px]">
          <span
            v-if="testResult"
            :class="testResult.ok ? 'text-accent-green' : 'font-medium text-accent'"
          >
            {{ testResult.ok ? '测试推送发送成功' : `推送失败: ${testResult.err || testResult.body}` }}
          </span>
          <span v-else class="text-muted-soft">尚未测试</span>
        </div>

        <button @click="handleTestPush" :disabled="testing" class="btn-primary flex-shrink-0">
          <span v-if="testing" class="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
          <span>触发单次测试</span>
        </button>
      </div>
    </section>

    <!-- 在线配置编辑器 -->
    <section class="panel p-5">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-hairline pb-4">
        <div>
          <h2 class="text-[16px] font-semibold tracking-tight text-ink">集中化参数配置 (config.ini)</h2>
          <p class="mt-1 text-[12px] text-muted">修改后直接持久化至磁盘文件，热载入生效</p>
        </div>

        <button @click="handleSaveConfig" :disabled="saving" class="btn-primary">
          <span v-if="saving" class="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
          <span>保存配置</span>
        </button>
      </div>

      <!-- CATEGORY_PUSH_PREFS_SECTION -->
      <div class="pt-4">
        <GenrePrefs />
      </div>

      <div v-if="loadingConfig" class="py-12 text-center text-[12px] text-muted-soft">
        正在读取配置...
      </div>

      <div v-else class="space-y-6 pt-4">
        <!-- 1. [radar] 核心规则 -->
        <div>
          <h3 class="mb-3 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">
            [radar] 引擎规则
          </h3>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">默认城市 (city)</label>
              <input v-model="configSections.radar.city" type="text" class="field" />
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">时间窗口天数 (window_days)</label>
              <input v-model="configSections.radar.window_days" type="number" class="field" />
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">仅保留周末 (weekend_only)</label>
              <select v-model="configSections.radar.weekend_only" class="field">
                <option value="true">true (仅看周末及多日跨周末)</option>
                <option value="false">false (包含工作日活动)</option>
              </select>
            </div>
          </div>
        </div>

        <!-- 2. [llm] 客户端配置 -->
        <div class="border-t border-hairline pt-4">
          <h3 class="mb-3 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">
            [llm] 大语言模型网关
          </h3>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">Provider 预设</label>
              <select v-model="configSections.llm.provider" @change="onProviderChange" class="field">
                <option v-for="p in providers" :key="p.name" :value="p.name">{{ p.name }}</option>
              </select>
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">接口地址 (base_url)</label>
              <input v-model="configSections.llm.base_url" type="text" class="field" />
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">模型名称 (model)</label>
              <input v-model="configSections.llm.model" type="text" class="field" />
            </div>
          </div>
        </div>

        <!-- 3. [push] 推送策略 -->
        <div class="border-t border-hairline pt-4">
          <h3 class="mb-3 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">
            [push] 推送策略与防扰门槛
          </h3>
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-4">
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">启用推送 (enabled)</label>
              <select v-model="configSections.push.enabled" class="field">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">默认渠道 (channel)</label>
              <input v-model="configSections.push.channel" type="text" placeholder="如 feishu 或 pushdeer,bark" class="field" />
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">新增触发门槛 (min_new_changes)</label>
              <input v-model="configSections.push.min_new_changes" type="number" class="field" />
            </div>
            <div>
              <label class="mb-1 block text-[12px] font-medium text-muted">源失败推告警</label>
              <select v-model="configSections.push.notify_on_source_failure" class="field">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </div>
          </div>
        </div>

        <!-- 4. [sources] 数据源开关列表 -->
        <div class="border-t border-hairline pt-4">
          <h3 class="mb-3 font-mono text-[11px] font-semibold uppercase tracking-wider text-muted">
            [sources] 数据源列表与状态 (共 {{ Object.keys(configSources).length }} 个)
          </h3>
          <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div
              v-for="(val, key) in configSources"
              :key="key"
              class="flex items-center justify-between gap-3 rounded-xl border border-hairline bg-surface-soft px-3 py-2.5"
            >
              <div class="min-w-0 overflow-hidden">
                <span class="block truncate font-mono text-[12px] font-semibold text-ink">{{ key }}</span>
                <span class="block truncate text-[10px] text-muted-soft">{{ val }}</span>
              </div>
              <button
                @click="toggleSource(key)"
                class="tactile-button flex-shrink-0 rounded-lg border px-2.5 py-1 font-mono text-[11px] transition-colors"
                :class="isSourceEnabled(val)
                  ? 'border-accent-green/30 bg-accent-green/10 text-accent-green'
                  : 'border-hairline bg-white text-muted-soft hover:text-muted'"
              >
                {{ isSourceEnabled(val) ? '已启用' : '已停用' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '../api/client'
import GenrePrefs from '../components/GenrePrefs.vue'

const supportedChannels = [
  'pushdeer', 'feishu', 'wecom', 'dingtalk', 'bark', 'telegram', 'serverchan'
]

const testChannel = ref('feishu')
const testWebhook = ref('')
const testSecret = ref('')
const testing = ref(false)
const testResult = ref<any>(null)

const loadingConfig = ref(false)
const saving = ref(false)
const providers = ref<any[]>([])
const configSections = ref<any>({
  radar: {},
  llm: {},
  push: {},
  fetch: {},
})
const configSources = ref<Record<string, string>>({})

async function loadConfigData() {
  loadingConfig.value = true
  try {
    const [presetsRes, cfgRes] = await Promise.all([
      api.getPresets(),
      api.getConfig()
    ])
    providers.value = presetsRes.providers
    configSections.value = cfgRes.sections
    configSources.value = cfgRes.sources || {}
  } catch (e) {
    console.error('加载配置失败', e)
  } finally {
    loadingConfig.value = false
  }
}

function onProviderChange() {
  const p = providers.value.find(x => x.name === configSections.value.llm.provider)
  if (p) {
    configSections.value.llm.base_url = p.base_url
    configSections.value.llm.model = p.model
  }
}

function isSourceEnabled(rowStr: string) {
  const parts = (rowStr || '').split('|')
  return parts.length >= 2 && parts[1].trim().toLowerCase() === 'on'
}

function toggleSource(name: string) {
  const cur = configSources.value[name] || ''
  const parts = cur.split('|')
  if (parts.length >= 3) {
    const isCurrentlyOn = parts[1].trim().toLowerCase() === 'on'
    parts[1] = isCurrentlyOn ? ' off ' : ' on '
    configSources.value[name] = parts.join('|')
  }
}

async function handleTestPush() {
  testing.value = true
  testResult.value = null
  try {
    const res = await api.testPush({
      channel: testChannel.value,
      webhook: testWebhook.value || undefined,
      secret: testSecret.value || undefined,
    })
    testResult.value = res
  } catch (e: any) {
    testResult.value = { ok: false, err: e.message }
  } finally {
    testing.value = false
  }
}

async function handleSaveConfig() {
  saving.value = true
  try {
    await api.updateConfig(configSections.value, configSources.value)
    alert('配置已成功保存至 config.ini！')
  } catch (e: any) {
    alert(`保存配置失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadConfigData()
})
</script>
