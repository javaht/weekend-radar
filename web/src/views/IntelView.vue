<template>
  <div class="mx-auto max-w-[880px] px-4 pb-20 pt-5 sm:px-6">
    <!-- 面包屑 -->
    <nav class="flex items-center gap-1.5 text-[12px] text-muted">
      <span class="cursor-default transition-colors hover:text-ink">首页</span>
      <ChevronRight class="text-muted-soft" :size="12" />
      <span class="cursor-default transition-colors hover:text-ink">城市</span>
      <ChevronRight class="text-muted-soft" :size="12" />
      <span class="font-medium text-ink">{{ data?.city || city }}</span>
    </nav>

    <!-- 城市大标题 -->
    <div class="mt-4 flex items-start gap-3">
      <div class="mt-1 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full border border-hairline bg-surface-soft">
        <MapPin class="text-ink" :size="16" />
      </div>
      <div class="min-w-0">
        <h1 class="text-[26px] font-semibold leading-tight tracking-tight text-ink">
          {{ data?.city || city }}
        </h1>
        <p class="mt-1 text-[13px] leading-relaxed text-muted">
          {{ summaryLine }}
        </p>
        <p v-if="data?.data_city && data.city !== data.data_city" class="mt-1 text-[12px] text-accent-amber">
          当前库内为「{{ data.data_city }}」采集的数据；切换到 {{ data.city }} 后需以该城市执行一次任务才会更新清单。
        </p>
      </div>
    </div>

    <!-- 筛选区：分类胶囊 + 开关标签 + 搜索 -->
    <div class="mt-6 space-y-3">
      <div class="flex flex-wrap items-center gap-2">
        <button
          v-for="cat in categories"
          :key="cat.id"
          @click="selectedCategory = cat.id"
          class="tactile-button rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors"
          :class="selectedCategory === cat.id
            ? 'bg-ink text-white'
            : 'border border-hairline bg-white text-muted hover:border-ink/25 hover:text-ink'"
        >
          {{ cat.label }}
        </button>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <!-- 价格档位 -->
        <div class="flex items-center gap-1.5">
          <span class="text-[11px] text-muted-soft">票价</span>
          <button
            v-for="t in priceTiers"
            :key="t.id"
            @click="selectedPriceTier = selectedPriceTier === t.id ? null : t.id"
            class="tactile-button rounded-full border px-2.5 py-1 text-[12px] transition-colors"
            :class="selectedPriceTier === t.id
              ? 'border-ink bg-ink font-medium text-white'
              : 'border-hairline bg-white text-muted hover:border-ink/25 hover:text-ink'"
          >
            {{ t.label }}
          </button>
        </div>

        <span class="mx-1 h-px w-px text-hairline">|</span>

        <button
          @click="onlyEndingSoon = !onlyEndingSoon"
          class="tactile-button inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition-colors"
          :class="onlyEndingSoon
            ? 'border-accent/40 bg-accent-soft font-medium text-accent'
            : 'border-hairline bg-white text-muted hover:border-ink/25 hover:text-ink'"
        >
          <span class="h-1.5 w-1.5 rounded-full" :class="onlyEndingSoon ? 'bg-accent' : 'bg-hairline'"></span>
          即将结束
        </button>

        <button
          @click="onlyGoods = !onlyGoods"
          class="tactile-button inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12px] transition-colors"
          :class="onlyGoods
            ? 'border-accent-amber/40 bg-accent-amber/10 font-medium text-accent-amber'
            : 'border-hairline bg-white text-muted hover:border-ink/25 hover:text-ink'"
        >
          <span class="h-1.5 w-1.5 rounded-full" :class="onlyGoods ? 'bg-accent-amber' : 'bg-hairline'"></span>
          有周边物料
        </button>

        <!-- 搜索 + 刷新 -->
        <div class="ml-auto flex w-full items-center gap-2 sm:w-auto">
          <div class="relative flex-1 sm:w-56">
            <Search class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" :size="13" />
            <input
              v-model="searchKeyword"
              type="text"
              placeholder="搜索活动名 / 场馆 / 看点..."
              class="field py-1.5 pl-8"
            />
          </div>
          <button
            @click="loadData"
            :disabled="loading"
            title="刷新情报"
            class="tactile-button inline-flex flex-shrink-0 items-center justify-center rounded-full border border-hairline bg-white p-2 text-muted transition-colors hover:border-ink/25 hover:text-ink disabled:opacity-50"
          >
            <RefreshCw :class="{ 'animate-spin': loading }" :size="14" />
          </button>
        </div>
      </div>
    </div>

    <!-- 子视图切换：周末 / 持续展出 / 博览会 / 临近截止 -->
    <div class="mt-6 flex items-center gap-1 overflow-x-auto border-b border-hairline pb-2">
      <button
        v-for="sub in subTabs"
        :key="sub.id"
        @click="activeSubTab = sub.id"
        class="tactile-button inline-flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-[12px] transition-colors"
        :class="activeSubTab === sub.id
          ? 'bg-ink font-semibold text-white'
          : 'text-muted hover:text-ink'"
      >
        <span>{{ sub.tabLabel || sub.label }}</span>
        <span
          class="rounded-full px-1.5 text-[10px] font-medium leading-4"
          :class="activeSubTab === sub.id ? 'bg-white/20 text-white' : 'bg-surface-subtle text-muted'"
        >
          {{ sub.count }}
        </span>
      </button>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="py-24 text-center">
      <div class="inline-block h-5 w-5 animate-spin rounded-full border-2 border-ink border-t-transparent"></div>
      <p class="mt-3 text-[12px] text-muted">正在加载活动情报...</p>
    </div>

    <!-- 空状态 -->
    <div v-else-if="filteredEvents.length === 0" class="py-24 text-center">
      <p class="text-[13px] text-muted">未找到符合当前筛选条件的活动</p>
      <p class="mt-1 text-[12px] text-muted-soft">尝试切换城市或清除筛选条件</p>
    </div>

    <!-- 日期分组列表 -->
    <div v-else class="mt-2">
      <section v-for="group in displayGroups" :key="group.key" class="pt-6">
        <!-- 分组头 -->
        <div class="mb-1 flex items-baseline gap-2">
          <h2 class="text-[15px] font-semibold tracking-tight text-ink">{{ group.title }}</h2>
          <span v-if="group.subtitle" class="text-[13px] text-muted">{{ group.subtitle }}</span>
          <span class="ml-auto text-[11px] text-muted-soft">{{ group.items.length }} 场</span>
        </div>

        <!-- 行列表 -->
        <ul class="divide-y divide-hairline">
          <li v-for="ev in group.items" :key="ev.name + ev.start_date" class="py-5">
            <div class="flex gap-4">
              <!-- 缩略图占位块 -->
              <div
                class="flex h-[88px] w-[88px] flex-shrink-0 items-center justify-center rounded-xl sm:h-[110px] sm:w-[110px]"
                :class="thumbClass(ev.genre)"
              >
                <span class="text-[18px] font-semibold tracking-tight opacity-60">
                  {{ categoryInitial(ev.genre) }}
                </span>
              </div>

              <!-- 右侧内容 -->
              <div class="min-w-0 flex-1">
                <!-- 标签 chips -->
                <div class="flex flex-wrap items-center gap-1.5">
                  <span class="rounded-md border border-hairline bg-surface-soft px-1.5 py-0.5 text-[10px] font-medium text-muted">
                    {{ getCategoryName(ev.genre) }}
                  </span>
                  <span v-if="ev.price_tier === 'free'" class="rounded-md border border-accent-green/30 bg-accent-green/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-green">
                    免费
                  </span>
                  <span v-if="hasTag(ev, 'ending_soon')" class="rounded-md border border-accent/30 bg-accent-soft px-1.5 py-0.5 text-[10px] font-medium text-accent">
                    即将结束
                  </span>
                  <span v-if="hasTag(ev, 'new')" class="rounded-md border border-accent-blue/30 bg-accent-blue/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-blue">
                    新发现
                  </span>
                  <span v-if="ev.this_weekend && isWeekendGroup === false" class="rounded-md border border-hairline bg-surface-soft px-1.5 py-0.5 text-[10px] font-medium text-muted">
                    本周末可去
                  </span>
                  <span v-if="ev.has_goods === true" class="rounded-md border border-accent-amber/30 bg-accent-amber/10 px-1.5 py-0.5 text-[10px] font-medium text-accent-amber">
                    周边
                  </span>
                  <span v-if="ev.source" class="text-[10px] lowercase text-muted-soft">
                    {{ ev.source }}
                  </span>
                </div>

                <!-- 标题 -->
                <a
                  v-if="ev.url"
                  :href="ev.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="mt-1.5 block text-[17px] font-semibold leading-snug tracking-tight text-ink hover:underline"
                >
                  {{ ev.name }}
                </a>
                <h3 v-else class="mt-1.5 text-[17px] font-semibold leading-snug tracking-tight text-ink">
                  {{ ev.name }}
                </h3>

                <!-- 一句话描述 -->
                <p class="mt-1 truncate text-[13px] leading-relaxed text-muted">
                  {{ ev.note || ev.venue || '暂无更多说明' }}
                </p>

                <!-- 元信息：时间 -->
                <div class="mt-2 flex items-center gap-1.5 text-[13px] text-muted">
                  <Clock class="flex-shrink-0 text-muted-soft" :size="13" />
                  <span>{{ dateRange(ev) }}</span>
                </div>

                <!-- 元信息：地点 + 价格 -->
                <div class="mt-1 flex items-center gap-1.5 text-[13px]">
                  <MapPin class="flex-shrink-0 text-muted-soft" :size="13" />
                  <span class="truncate text-muted">{{ ev.venue || '未指定场馆 / 线上' }}</span>
                  <span class="ml-auto flex-shrink-0 pl-3 text-[13px] font-semibold" :class="priceClass(ev)">
                    {{ priceLabel(ev) }}
                  </span>
                </div>
              </div>
            </div>
          </li>
        </ul>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onActivated, watch } from 'vue'
import { ChevronRight, Clock, MapPin, RefreshCw, Search } from 'lucide-vue-next'
import { api, type GroupedEventsResponse, type EventItem } from '../api/client'

const props = defineProps<{
  city: string
}>()

const loading = ref(false)
const data = ref<GroupedEventsResponse | null>(null)

const selectedCategory = ref('ALL')
const selectedPriceTier = ref<string | null>(null)
const onlyEndingSoon = ref(false)
const priceTiers = ref<Array<{id:string;label:string}>>([])
const onlyGoods = ref(false)
const searchKeyword = ref('')
const activeSubTab = ref('all')

// 品类目录来自后端 /api/presets，与「配置渠道」页的推送品类偏好是同一套 key，
// 保证两处说的是同一件事。
const genreLabels = ref<Record<string, string>>({})
const genreOrder = ref<string[]>([])

const categories = computed(() => {
  const pills = [{ id: 'ALL', label: '全部' }]
  for (const id of genreOrder.value) {
    pills.push({ id, label: genreLabels.value[id] || id })
  }
  return pills
})

function labelOf(genre: string) {
  return genreLabels.value[genre] || '其他'
}

async function loadGenres() {
  try {
    const res = await api.getPresets()
    if (res.genres && res.genres.length) {
      const map: Record<string, string> = {}
      const order: string[] = []
      for (const g of res.genres) {
        map[g.id] = g.label
        order.push(g.id)
      }
      genreLabels.value = map
      genreOrder.value = order
    }
  } catch (e) {
    console.error('加载品类目录失败', e)
  }
}

async function loadPriceTiers() {
  try {
    const res = await api.getPriceTiers()
    // 「价格待核实」不做成筛选按钮: 它占 80%, 点它等于「几乎全部」, 没有筛选意义
    priceTiers.value = (res.tiers || []).filter(t => t.id !== 'unknown')
  } catch (e) {
    console.error('加载价格档位失败', e)
  }
}

async function loadData() {
  loading.value = true
  try {
    data.value = await api.getGroupedEvents(props.city)
    // 默认停在「全部」，不自动跳到某个周末 —— 打开页面先看到完整清单。
    if (!subTabs.value.some(t => t.id === activeSubTab.value)) {
      activeSubTab.value = 'all'
    }
  } catch (e) {
    console.error('加载活动数据失败', e)
  } finally {
    loading.value = false
  }
}

watch(() => props.city, () => {
  loadData()
})

// 目录类是静态数据，只拉一次；首屏 onMounted 与 onActivated 都会触发，
// 用标志位去重，避免 KeepAlive 下重复请求。
let catalogLoaded = false
let dataLoaded = false

function loadCatalogOnce() {
  if (catalogLoaded) return
  catalogLoaded = true
  loadGenres()
  loadPriceTiers()
}

function loadDataOnce() {
  if (dataLoaded) return
  dataLoaded = true
  loadData()
}

onMounted(() => {
  loadCatalogOnce()
  loadDataOnce()
})

onActivated(() => {
  loadCatalogOnce()
  loadDataOnce()
})

// 扁平统计
const allEvents = computed(() => {
  if (!data.value) return []
  const list: EventItem[] = []
  data.value.weekends.forEach(w => list.push(...w.items))
  list.push(...data.value.longrun)
  list.push(...data.value.expo)
  list.push(...data.value.unplaced)
  // 去重
  const seen = new Set<string>()
  return list.filter(e => {
    const k = `${e.name}-${e.start_date}`
    if (seen.has(k)) return false
    seen.add(k)
    return true
  })
})

const freeCount = computed(() => allEvents.value.filter(e => e.price_tier === 'free').length)

const subTabs = computed(() => {
  if (!data.value) return []
  // 「全部」永远是第一个且默认选中: 用户打开页面应该先看到完整清单,
  // 而不是被默认钉在第一个周末上。
  const tabs = [{ id: 'all', label: '全部', count: allEvents.value.length,
                  items: allEvents.value, kind: 'all' }]
  data.value.weekends.forEach((w, idx) => {
    tabs.push({ id: `w${idx}`, label: w.label, tabLabel: formatTabLabel(w.label), count: w.items.length, items: w.items, kind: 'weekend' })
  })
  if (data.value.longrun.length > 0) {
    tabs.push({ id: 'longrun', label: '持续展出', count: data.value.longrun.length, items: data.value.longrun, kind: 'longrun' })
  }
  if (data.value.expo.length > 0) {
    tabs.push({ id: 'expo', label: '博览会速览', count: data.value.expo.length, items: data.value.expo, kind: 'expo' })
  }
  if (data.value.urgent.length > 0) {
    tabs.push({ id: 'urgent', label: '临近截止', count: data.value.urgent.length, items: data.value.urgent, kind: 'urgent' })
  }
  return tabs
})

// 根据当前 subTab 和条件过滤
const currentTabEvents = computed(() => {
  const current = subTabs.value.find(s => s.id === activeSubTab.value)
  return current ? current.items : []
})

function matchesFilters(ev: EventItem) {
  if (selectedCategory.value !== 'ALL' && ev.genre !== selectedCategory.value) {
    return false
  }
  if (selectedPriceTier.value && ev.price_tier !== selectedPriceTier.value) {
    return false
  }
  if (onlyEndingSoon.value && !(ev.derived_tags || []).includes('ending_soon')) {
    return false
  }
  if (onlyGoods.value && ev.has_goods !== true) {
    return false
  }
  if (searchKeyword.value.trim()) {
    const q = searchKeyword.value.trim().toLowerCase()
    const text = `${ev.name} ${ev.venue} ${ev.note || ''}`.toLowerCase()
    if (!text.includes(q)) return false
  }
  return true
}

const filteredEvents = computed(() => currentTabEvents.value.filter(matchesFilters))

// 顶部一句话概览
const summaryLine = computed(() => {
  if (!data.value) return `正在汇总 ${props.city} 的周末活动情报`
  const parts = [
    `${data.value.total_active} 场活动`,
    `未来 ${data.value.window_days} 天`,
    `${freeCount.value} 场免费`,
  ]
  return `${parts.join(' · ')}　基准日 ${data.value.today}`
})

// 分组：周末标签拆成「日期 / 星期」，其余视图给语义化标题
const displayGroups = computed(() => {
  const current = subTabs.value.find(s => s.id === activeSubTab.value)
  if (!current) return []

  // 「全部」视图: 按来源子视图再分组, 保留每个周末的日期标题,
  // 避免把 5 个周末混成一个长列表。
  if (current.kind === 'all') {
    const groups: Array<{ key: string; title: string; subtitle: string; items: EventItem[] }> = []
    for (const w of data.value!.weekends) {
      const items = w.items.filter(matchesFilters)
      if (!items.length) continue
      const parsed = splitWeekendLabel(w.label)
      groups.push({ key: 'all-' + w.label, title: parsed.dates, subtitle: parsed.weekdays, items })
    }
    const longrun = data.value!.longrun.filter(matchesFilters)
    if (longrun.length) {
      groups.push({ key: 'all-longrun', title: '持续展出', subtitle: '长档期展览 · 随时可去', items: longrun })
    }
    const expo = data.value!.expo.filter(matchesFilters)
    if (expo.length) {
      groups.push({ key: 'all-expo', title: '博览会', subtitle: '行业展会速览', items: expo })
    }
    const unplaced = data.value!.unplaced.filter(matchesFilters)
    if (unplaced.length) {
      groups.push({ key: 'all-unplaced', title: '其他', subtitle: '日期未归入周末', items: unplaced })
    }
    return groups
  }

  if (current.kind === 'weekend') {
    const parsed = splitWeekendLabel(current.label)
    return [{
      key: current.id,
      title: parsed.dates,
      subtitle: parsed.weekdays,
      items: filteredEvents.value,
    }]
  }
  if (current.kind === 'longrun') {
    return [{
      key: current.id,
      title: '持续展出',
      subtitle: '长档期展览 · 随时可去',
      items: filteredEvents.value,
    }]
  }
  if (current.kind === 'expo') {
    return [{
      key: current.id,
      title: '博览会',
      subtitle: '行业展会速览',
      items: filteredEvents.value,
    }]
  }
  return [{
    key: current.id,
    title: '临近截止',
    subtitle: '最近 3 天内截止，抓紧',
    items: filteredEvents.value,
  }]
})

// 「本周末可去」角标只在非周末分组里显示: 周末分组本身就是本周末, 标了是冗余。
// 「全部」视图混合了周末和长档期, 所以也要标。
const isWeekendGroup = computed(() => {
  const current = subTabs.value.find(s => s.id === activeSubTab.value)
  return current ? current.kind === 'weekend' : false
})

function weekdayCn(raw: string) {
  if (!raw) return ''
  if (raw.startsWith('周')) return raw.replace('周', '星期')
  return raw.length === 1 ? `星期${raw}` : raw
}

function formatTabLabel(label: string) {
  // "11/7（六） · 11/8（日）" → "11/7 周六"
  const first = (label || '').split(' · ')[0] || ''
  const md = (first.match(/(\d+)\/(\d+)/) || [])[0] || first
  const wd = (first.match(/（(.*?)）/) || [])[1] || ''
  return wd ? `${md} ${weekdayCn(wd).replace('星期', '周')}` : md
}

function splitWeekendLabel(label: string) {
  // 后端格式："11/7（六） · 11/8（日）" → "11月7日" + "星期六"
  const parts = (label || '').split(' · ').filter(Boolean)
  if (parts.length === 0) return { dates: '本周末', weekdays: '' }

  const dates = parts.map(p => {
    const m = p.match(/(\d+)\/(\d+)/)
    return m ? `${Number(m[1])}月${Number(m[2])}日` : p.replace(/（.*?）/g, '')
  })
  const weekdays = parts
    .map(p => weekdayCn((p.match(/（(.*?)）/) || [])[1] || ''))
    .filter(Boolean)

  // 首日即今天时，用「今天」做分组头
  const today = data.value?.today || ''
  const todayMD = today.length >= 10 ? `${Number(today.slice(5, 7))}月${Number(today.slice(8, 10))}日` : ''
  const isToday = todayMD && dates[0] === todayMD

  return {
    dates: isToday ? '今天' : dates.join(' · '),
    weekdays: weekdays.join(' · '),
  }
}

function hasTag(ev: EventItem, tag: string) {
  return (ev.derived_tags || []).includes(tag)
}

function getCategoryName(cat: string) {
  return labelOf(cat)
}

function categoryInitial(genre: string) {
  const label = labelOf(genre)
  return label ? label.charAt(0) : '·'
}

function thumbClass(genre: string) {
  // 每个品类一组低饱和底色，替代缺失的缩略图
  const palette: Record<string, string> = {
    anime: 'bg-gradient-to-br from-[#fdf0f4] to-[#f4dae3]',
    tech: 'bg-gradient-to-br from-[#eef1f6] to-[#dbe2ec]',
    expo: 'bg-gradient-to-br from-[#f6f1e6] to-[#eae0c9]',
    museum: 'bg-gradient-to-br from-[#f3ede7] to-[#e6d9cc]',
    concert: 'bg-gradient-to-br from-[#efeaf7] to-[#ddd2ee]',
    dance: 'bg-gradient-to-br from-[#eaf5f1] to-[#d3e8e0]',
    theater: 'bg-gradient-to-br from-[#f7ecec] to-[#efd8d8]',
    opera: 'bg-gradient-to-br from-[#f5f0e8] to-[#e8dcc8]',
    comedy: 'bg-gradient-to-br from-[#fdf3e8] to-[#f5e2cc]',
    market: 'bg-gradient-to-br from-[#f2f5ea] to-[#dfe8d2]',
    festival: 'bg-gradient-to-br from-[#fdf1e6] to-[#f6e0c8]',
    sports: 'bg-gradient-to-br from-[#ecf3f8] to-[#d6e6f2]',
  }
  return palette[genre] || 'bg-surface-subtle'
}

function dateRange(ev: EventItem) {
  const start = formatDate(ev.start_date)
  const end = formatDate(ev.end_date)
  if (!start) return '时间待定'
  if (end && end !== start) return `${start} → ${end}`
  return start
}

function formatDate(iso: string) {
  if (!iso) return ''
  return iso.length >= 10 ? iso.substring(5) : iso
}

function priceLabel(ev: EventItem) {
  if (ev.price_tier === 'free') return '免费'
  if (!ev.price_text) return '待核实'
  return ev.price_text
}

function priceClass(ev: EventItem) {
  if (ev.price_tier === 'free') return 'text-accent-green'
  if (!ev.price_text) return 'font-normal text-muted-soft'
  return 'text-accent'
}
</script>
