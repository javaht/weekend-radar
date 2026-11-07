<template>
  <div class="mx-auto max-w-[880px] space-y-5 px-4 pb-20 pt-5 sm:px-6">
    <!-- Webhook 实时测试调试台 -->
    <section class="panel p-5">
      <div>
        <h2 class="text-[16px] font-semibold tracking-tight text-ink">Webhook 推送通道调试台</h2>
        <p class="mt-1 text-[12px] text-muted">仅用于单次连通性测试，不写入配置；下方配置项均为修改即自动保存</p>
      </div>

      <div class="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
        <div>
          <label class="field-label">推送渠道 (Channel)</label>
          <select v-model="testChannel" class="field">
            <option v-for="ch in supportedChannels" :key="ch" :value="ch">{{ ch }}</option>
          </select>
          <p class="mt-1.5 truncate text-[11px] text-muted-soft">{{ currentChannelMeta.desc }}</p>
        </div>

        <div>
          <label class="field-label">{{ currentChannelMeta.urlLabel }}</label>
          <input
            v-model="testWebhook"
            type="text"
            :placeholder="currentChannelMeta.urlPlaceholder"
            class="field"
          />
          <p class="mt-1.5 truncate text-[11px] text-muted-soft">{{ currentChannelMeta.urlHint }}</p>
        </div>

        <div>
          <label class="field-label">{{ currentChannelMeta.secretLabel }}</label>
          <input
            v-model="testSecret"
            type="text"
            :placeholder="currentChannelMeta.secretPlaceholder"
            class="field"
          />
          <p class="mt-1.5 truncate text-[11px] text-muted-soft">{{ currentChannelMeta.secretHint }}</p>
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
          <p class="mt-1 text-[12px] text-muted">修改后自动保存，热载入生效</p>
        </div>
        <span class="text-[11px] text-muted-soft">全部改动即时生效</span>
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
              <label class="field-label">默认城市 (city)</label>
              <input v-model="configSections.radar.city" type="text" placeholder="例: 北京、上海、广州" class="field" />
            </div>
            <div>
              <label class="field-label">时间窗口天数 (window_days)</label>
              <input v-model="configSections.radar.window_days" type="number" placeholder="例: 30" class="field" />
            </div>
            <div>
              <label class="field-label">仅保留周末 (weekend_only)</label>
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
              <label class="field-label">Provider 预设</label>
              <select v-model="configSections.llm.provider" @change="onProviderChange" class="field">
                <option v-for="p in providers" :key="p.name" :value="p.name">{{ p.name }}</option>
              </select>
            </div>
            <div>
              <label class="field-label">接口地址 (base_url)</label>
              <input v-model="configSections.llm.base_url" type="text" placeholder="例: http://127.0.0.1:3425 或 https://api.deepseek.com/v1" class="field" />
            </div>
            <div>
              <label class="field-label">模型名称 (model)</label>
              <div class="flex gap-2">
                <select v-if="modelOptions.length && !modelManual" v-model="configSections.llm.model" class="field min-w-0">
                  <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
                </select>
                <input v-else v-model="configSections.llm.model" type="text" class="field min-w-0" placeholder="例: group/deepseek-v4-1-flash" />
                <button
                  type="button"
                  @click="handleFetchModels"
                  :disabled="fetchingModels"
                  :title="'从网关 /models 接口自动拉取可用模型'"
                  class="tactile-button flex h-[34px] flex-shrink-0 items-center gap-1.5 rounded-xl border border-hairline bg-surface-soft px-2.5 text-[11px] font-medium text-muted transition-colors hover:border-ink/30 hover:text-ink disabled:opacity-50"
                >
                  <span v-if="fetchingModels" class="h-3 w-3 animate-spin rounded-full border-2 border-muted border-t-transparent"></span>
                  <svg v-else class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 0 0-9-9 9 9 0 0 0-6.7 3M3 12a9 9 0 0 0 9 9 9 9 0 0 0 6.7-3" />
                    <path d="M3 4v5h5M21 20v-5h-5" />
                  </svg>
                  <span>获取模型</span>
                </button>
              </div>
            </div>
          </div>
          <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span v-if="modelNote" class="text-[11px]" :class="modelFetchOk ? 'text-accent-green' : 'text-accent'">
              {{ modelNote }}
            </span>
            <button
              v-if="modelOptions.length"
              type="button"
              @click="modelManual = !modelManual"
              class="text-[11px] text-muted underline-offset-2 hover:text-ink hover:underline"
            >
              {{ modelManual ? '从列表选择' : '手动输入模型 id' }}
            </button>
          </div>
          <div class="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div class="sm:col-span-2">
              <label class="field-label">API 密钥 (key)</label>
              <div class="relative">
                <input
                  v-model="configSections.llm.key"
                  :type="showKey ? 'text' : 'password'"
                  autocomplete="off"
                  placeholder="留空则读环境变量 / key_file；留空保存不会覆盖已有密钥"
                  class="field pr-16"
                />
                <button
                  type="button"
                  @click="toggleShowKey"
                  :disabled="revealingKey"
                  class="absolute right-2 top-1/2 -translate-y-1/2 rounded-md px-2 py-1 text-[11px] text-muted hover:bg-surface-soft hover:text-ink disabled:opacity-50"
                >{{ revealingKey ? '…' : (showKey ? '隐藏' : '显示') }}</button>
              </div>
              <p class="mt-1 text-[11px] text-muted-soft">
                已设置的密钥以 ••• 显示，保持不变即可；点「显示」可查看真实值。密钥与 base_url 属私有值，保存时只写入 git 忽略的 config.local.ini。
              </p>
            </div>
            <div>
              <label class="field-label">协议 (mode)</label>
              <select v-model="configSections.llm.mode" class="field">
                <option value="auto">auto（自动探测）</option>
                <option value="openai">openai</option>
                <option value="anthropic">anthropic</option>
              </select>
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
              <label class="field-label">启用推送 (enabled)</label>
              <select v-model="configSections.push.enabled" class="field">
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            </div>
            <div>
              <label class="field-label">默认渠道 (channel)</label>
              <input v-model="configSections.push.channel" type="text" placeholder="如 feishu 或 pushdeer,bark" class="field" />
            </div>
            <div>
              <label class="field-label">新增触发门槛 (min_new_changes)</label>
              <input v-model="configSections.push.min_new_changes" type="number" placeholder="例: 3" class="field" />
            </div>
            <div>
              <label class="field-label">源失败推告警</label>
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

    <!-- 固定定位：滚动到页面任意位置都能看到保存反馈 -->
    <SaveFlash :state="saveFlash" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { api } from '../api/client'
import GenrePrefs from '../components/GenrePrefs.vue'
import SaveFlash from '../components/SaveFlash.vue'

const supportedChannels = [
  'pushdeer', 'feishu', 'wecom', 'dingtalk', 'bark', 'telegram', 'serverchan'
]

const channelPresets: Record<string, {
  name: string
  desc: string
  urlLabel: string
  urlPlaceholder: string
  urlHint: string
  secretLabel: string
  secretPlaceholder: string
  secretHint: string
}> = {
  pushdeer: {
    name: 'PushDeer',
    desc: '轻量推送，支持 iOS / Mac / Android 客户端',
    urlLabel: '接口 Endpoint（可选自建）',
    urlPlaceholder: '例: https://api2.pushdeer.com/message/push',
    urlHint: '留空读配置；官方通道默认使用官方 Endpoint 即可',
    secretLabel: 'PushKey',
    secretPlaceholder: '例: PDU12345Txxxxxxxxxxxx',
    secretHint: 'PushDeer 客户端中生成的 PushKey',
  },
  feishu: {
    name: '飞书机器人',
    desc: '飞书群自定义机器人，支持交互式 Markdown 流式卡片',
    urlLabel: 'Webhook URL',
    urlPlaceholder: '例: https://open.feishu.cn/open-apis/bot/v2/hook/xxxx',
    urlHint: '留空读配置；群设置中添加自定义机器人获取',
    secretLabel: 'Secret / 加签密钥（可选）',
    secretPlaceholder: '例: xxxxxxxxxxxxxxxxxxxxxxxx',
    secretHint: '开启安全加签设置时填入；未开启请留空',
  },
  wecom: {
    name: '企业微信',
    desc: '企微群机器人，自动按 4096 字节安全截断',
    urlLabel: 'Webhook URL',
    urlPlaceholder: '例: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx',
    urlHint: '留空读配置；填完整 Webhook 地址或只填右侧 key',
    secretLabel: '机器人 Key（与 URL 二选一）',
    secretPlaceholder: '例: 693a91f6-xxxx-xxxx-xxxx-xxxxxxxxxxxx',
    secretHint: '若未填 URL，填入 key 即可自动拼接官方地址',
  },
  dingtalk: {
    name: '钉钉机器人',
    desc: '钉钉群自定义机器人，支持签名校验与 Markdown 渲染',
    urlLabel: 'Webhook URL',
    urlPlaceholder: '例: https://oapi.dingtalk.com/robot/send?access_token=xxxx',
    urlHint: '留空读配置；群机器人设置中复制完整 Webhook',
    secretLabel: 'Secret / 加签密钥（可选）',
    secretPlaceholder: '例: SECxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
    secretHint: '开启「加签」安全选项时填入 SEC 开头的密钥',
  },
  bark: {
    name: 'Bark (iOS)',
    desc: 'iOS 极简推送工具，支持即时响铃与分组归档',
    urlLabel: '服务器 URL（可选）',
    urlPlaceholder: '例: https://api.day.app/',
    urlHint: '留空读配置；官方服务使用默认地址即可',
    secretLabel: 'Device Key',
    secretPlaceholder: '例: xxxxxxxxxxxxxxxxxxxxxxxx',
    secretHint: 'Bark App 首页显示的专属设备 Key',
  },
  telegram: {
    name: 'Telegram',
    desc: 'Telegram Bot 推送，通过 Bot API 发送消息',
    urlLabel: 'Bot Token（或完整 URL）',
    urlPlaceholder: '例: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyz',
    urlHint: '留空读配置；从 @BotFather 创建获取',
    secretLabel: 'Chat ID',
    secretPlaceholder: '例: 123456789 或 @my_channel',
    secretHint: '接收消息的用户 ID、群组 ID 或公开频道名',
  },
  serverchan: {
    name: 'Server酱',
    desc: 'Server酱 Turbo 微信推送通道',
    urlLabel: '自定义 Webhook URL（可选）',
    urlPlaceholder: '例: https://sctapi.ftqq.com/xxxx.send',
    urlHint: '留空读配置；官方通道无需填写 URL',
    secretLabel: 'SendKey',
    secretPlaceholder: '例: SCTxxxxxxxxxxxxxxxxxxxxxxxxx',
    secretHint: 'Server酱后台分配的 SendKey',
  },
}

const testChannel = ref('pushdeer')
const currentChannelMeta = computed(() => {
  return channelPresets[testChannel.value] || {
    name: testChannel.value,
    desc: '自定义消息推送通道',
    urlLabel: 'Webhook URL / Endpoint',
    urlPlaceholder: '例: https://example.com/webhook',
    urlHint: '留空则读取 config.ini',
    secretLabel: 'Secret / Key（可选）',
    secretPlaceholder: '例: xxxxxxxxxxxxxxxx',
    secretHint: '留空则读取 config.ini',
  }
})

const testWebhook = ref('')
const testSecret = ref('')
const testing = ref(false)
const testResult = ref<any>(null)

const loadingConfig = ref(false)
const showKey = ref(false)
const revealingKey = ref(false)
const keyConfigured = ref(false)
const revealedKeyValue = ref('')  // 「显示」时取到的真实值，用于区分查看与真正改密钥
const providers = ref<any[]>([])
const configSections = ref<any>({
  radar: {},
  llm: {},
  push: {},
  fetch: {},
})
const configSources = ref<Record<string, string>>({})

// 模型列表：点击「获取模型」后从网关 /models 拉取，之后 model 字段变成下拉选择
const modelOptions = ref<string[]>([])
const modelManual = ref(false)
const fetchingModels = ref(false)
const modelNote = ref('')
const modelFetchOk = ref(false)

async function handleFetchModels() {
  fetchingModels.value = true
  modelNote.value = ''
  try {
    const llm = configSections.value.llm || {}
    const res = await api.fetchModels({
      base_url: llm.base_url,
      key: llm.key,
      mode: llm.mode,
      provider: llm.provider,
    })
    modelFetchOk.value = res.ok
    if (res.ok) {
      modelOptions.value = res.models
      modelManual.value = false
      // 当前配置的模型若在列表里则保持选中，否则提示
      if (res.current && !res.models.includes(res.current)) {
        modelNote.value = `已获取 ${res.count} 个模型；当前「${res.current}」不在列表中，请重新选择`
      } else {
        modelNote.value = `已获取 ${res.count} 个模型 · ${res.note}`
      }
    } else {
      modelNote.value = res.note || '获取失败，请检查接口地址与密钥'
    }
  } catch (e: any) {
    modelFetchOk.value = false
    modelNote.value = `获取失败: ${e.message}`
  } finally {
    fetchingModels.value = false
  }
}

async function loadConfigData() {
  loadingConfig.value = true
  suspendBaselineSync = true
  try {
    const [presetsRes, cfgRes] = await Promise.all([
      api.getPresets(),
      api.getConfig()
    ])
    providers.value = presetsRes.providers
    configSections.value = cfgRes.sections
    configSources.value = cfgRes.sources || {}
    keyConfigured.value = cfgRes.sections?.llm?.key === '***'
  } catch (e) {
    console.error('加载配置失败', e)
  } finally {
    baseline = snapshot()
    loadingConfig.value = false
    suspendBaselineSync = false
  }
}

function onProviderChange() {
  const p = providers.value.find(x => x.name === configSections.value.llm.provider)
  if (p) {
    configSections.value.llm.base_url = p.base_url
    configSections.value.llm.model = p.model
  }
  // 换了网关，之前拉到的模型列表不再适用
  modelOptions.value = []
  modelManual.value = false
  modelNote.value = ''
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
    // 保持原写法：off 与原文一致写 'off'，on 保留配置里的 'on ' 对齐风格
    parts[1] = isCurrentlyOn ? 'off' : 'on '
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
      pushkey: testSecret.value || undefined,
    })
    testResult.value = res
  } catch (e: any) {
    testResult.value = { ok: false, err: e.message }
  } finally {
    testing.value = false
  }
}

// ---- 自动保存：改动后 debounce 700ms 落盘，成功后闪一下「已保存」 ----
const saveFlash = ref('')          // '' | 'ok' | 'error'
let saveTimer: number | null = null
let flashTimer: number | null = null
// 基线快照：只有相对基线真的变了才保存。加载、保存回填、切换密钥显示
// 都会改动响应式对象，用基线比较可以避免这些内部动作触发误保存。
let baseline = ''
let suspendBaselineSync = false

function snapshot() {
  return JSON.stringify([configSections.value, configSources.value])
}

function flash(kind: 'ok' | 'error') {
  saveFlash.value = kind
  if (flashTimer) clearTimeout(flashTimer)
  // 零点几秒的轻量反馈，不打断操作
  flashTimer = window.setTimeout(() => { saveFlash.value = '' }, 600)
}

async function saveConfigNow() {
  // 掩码值（***）表示「未修改」，不回传，避免把占位符写进文件。
  // 「显示」态下若密钥仍是查看时的原值，也跳过 —— 看一眼不等于改密钥。
  const sections: Record<string, any> = {}
  for (const [sec, kv] of Object.entries(configSections.value || {})) {
    sections[sec] = {}
    for (const [k, v] of Object.entries((kv as Record<string, any>) || {})) {
      if (v === '***') continue
      if (sec === 'llm' && k === 'key' && showKey.value && v === revealedKeyValue.value) continue
      sections[sec][k] = v
    }
  }
  try {
    const res = await api.updateConfig(sections, configSources.value)
    suspendBaselineSync = true
    if (res?.sections) configSections.value = res.sections
    if (res?.sources) configSources.value = res.sources
    // 回填后若仍处于「显示」态，保持真实值展示，不闪回掩码
    if (showKey.value && revealedKeyValue.value) {
      configSections.value.llm.key = revealedKeyValue.value
    }
    baseline = snapshot()
    suspendBaselineSync = false
    flash('ok')
  } catch (e: any) {
    suspendBaselineSync = false
    saveFlash.value = 'error'
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = window.setTimeout(() => { saveFlash.value = '' }, 2000)
  }
}

function scheduleSave() {
  if (suspendBaselineSync) return
  if (snapshot() === baseline) return
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = window.setTimeout(saveConfigNow, 700)
}

watch([configSections, configSources], scheduleSave, { deep: true })

async function toggleShowKey() {
  // 显示/隐藏只是查看动作，不该触发自动保存，也不该把真实值写回配置
  suspendBaselineSync = true
  if (showKey.value) {
    showKey.value = false
    if (keyConfigured.value) configSections.value.llm.key = '***'
    baseline = snapshot()
    suspendBaselineSync = false
    return
  }
  revealingKey.value = true
  try {
    const res = await api.revealSecret()
    keyConfigured.value = res.configured
    revealedKeyValue.value = res.value
    if (res.configured) configSections.value.llm.key = res.value
    showKey.value = true
    baseline = snapshot()
  } catch (e: any) {
    saveFlash.value = 'error'
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = window.setTimeout(() => { saveFlash.value = '' }, 2000)
  } finally {
    revealingKey.value = false
    suspendBaselineSync = false
  }
}

onMounted(() => {
  loadConfigData()
})

onUnmounted(() => {
  if (saveTimer) clearTimeout(saveTimer)
  if (flashTimer) clearTimeout(flashTimer)
})
</script>
