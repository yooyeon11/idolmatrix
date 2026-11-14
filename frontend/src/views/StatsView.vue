<script setup lang="ts">
/**
 * 统计页 —— 数据流动画卷
 *
 * 版式取自设计稿：刊头 → 流体指标带 → 年度双焦点 → 24h 波形 →
 * 母带谱系 / 365 天热力 → 分类链 → 热量总榜。
 * 所有数字来自 dashboardApi.libraryStats()（含后端 heat[]），
 * 24h 节律与 365 天入库日历复用现有 dashboardApi.recap()。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import { dashboardApi } from '@/api/dashboard'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import {
  LEGACY_VIDEO_TYPE_ALIAS,
  RESOLUTION_OPTIONS,
  VIDEO_TYPE_LABEL,
  type LibraryStats,
  type RecapDay,
  type StatsRecap,
} from '@/types/models'
import { formatDuration } from '@/utils/format'

interface HeatRow {
  kind: string
  id: number
  uid: string
  name: string
  chinese_name?: string | null
  avatar_path?: string | null
  video_count: number
  duration_seconds?: number
  plays: number
  seconds_watched: number
  favorites: number
  completion_rate: number
  content_score: number
  engagement_score: number
  quadrant: string
}

type StatsResponse = LibraryStats & { heat?: HeatRow[] }

const router = useRouter()
const loading = ref(true)
const data = ref<StatsResponse | null>(null)
const recap = ref<StatsRecap | null>(null)
const loadError = ref('')
const range = ref<'30d' | 'year' | 'all'>('all')
const brokenAvatars = ref<Set<string>>(new Set())
const hoverHour = ref<number | null>(null)
const chartEl = ref<HTMLElement | null>(null)

let requestSeq = 0

async function load() {
  const seq = ++requestSeq
  loading.value = true
  loadError.value = ''
  try {
    const [stats, recapData] = await Promise.all([
      dashboardApi.libraryStats({ range: range.value, limit: 30 }),
      dashboardApi.recap().catch(() => null),
    ])
    if (seq !== requestSeq) return
    data.value = stats as StatsResponse
    recap.value = recapData
  } catch (error) {
    if (seq !== requestSeq) return
    data.value = null
    loadError.value = (error as Error).message || '统计编排失败，请稍后重试'
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}

watch(range, () => void load())
onMounted(() => void load())

const year = new Date().getFullYear()
const rangeOptions = [
  { value: '30d' as const, label: '近 30 天' },
  { value: 'year' as const, label: '本年度' },
  { value: 'all' as const, label: '全周期' },
]
const continuumLabel = computed(
  () =>
    ({
      '30d': '近 30 天',
      year: '本年度',
      all: '全周期',
    })[range.value],
)

const overview = computed(() => data.value?.overview)
const watchBlock = computed(() => data.value?.watch)
const heatRows = computed<HeatRow[]>(() => data.value?.heat ?? [])
const topArtist = computed(() => data.value?.artists?.[0])
const topGroup = computed(() => data.value?.groups?.[0])
const libraryCount = computed(() => overview.value?.library_count ?? 0)

function heatOf(kind: 'artist' | 'group', id: number | undefined) {
  if (id == null) return undefined
  return heatRows.value.find((row) => row.kind === kind && row.id === id)
}

const topArtistHeat = computed(() => heatOf('artist', topArtist.value?.id))
const topGroupHeat = computed(() => heatOf('group', topGroup.value?.id))

const artistPoints = computed(() => Math.round(((topArtistHeat.value?.content_score ?? 0) + (topArtistHeat.value?.engagement_score ?? 0)) * 10))
const groupPoints = computed(() => Math.round(((topGroupHeat.value?.content_score ?? 0) + (topGroupHeat.value?.engagement_score ?? 0)) * 10))
const groupShare = computed(() =>
  libraryCount.value ? Math.round(((topGroup.value?.video_count ?? 0) / libraryCount.value) * 100) : 0,
)

const runtimeMetric = computed(() => {
  const total = overview.value?.total_duration_seconds ?? 0
  if (total >= 3600) return { value: (total / 3600).toFixed(1), unit: '小时' }
  if (total >= 60) return { value: String(Math.floor(total / 60)), unit: '分钟' }
  return { value: String(total), unit: '秒' }
})

const averageRuntime = computed(() => {
  const count = libraryCount.value
  if (!count) return '00m 00s'
  const seconds = Math.round((overview.value?.total_duration_seconds ?? 0) / count)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return h > 0
    ? `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`
    : `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`
})

const sizeMetric = computed(() => {
  const bytes = overview.value?.total_size_bytes ?? 0
  if (bytes >= 1024 ** 4) return { value: (bytes / 1024 ** 4).toFixed(2), unit: 'TB' }
  if (bytes >= 1024 ** 3) return { value: (bytes / 1024 ** 3).toFixed(2), unit: 'GB' }
  if (bytes >= 1024 ** 2) return { value: (bytes / 1024 ** 2).toFixed(1), unit: 'MB' }
  return { value: Math.max(0, Math.round(bytes / 1024)).toString(), unit: 'KB' }
})

const averageBitrate = computed(() => {
  const seconds = overview.value?.total_duration_seconds ?? 0
  if (!seconds) return '— Mbps'
  const bps = ((overview.value?.total_size_bytes ?? 0) * 8) / seconds
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(2)} Mbps`
  return `${Math.round(bps / 1000)} kbps`
})

const uhdCount = computed(() => {
  const map = overview.value?.resolution_breakdown ?? {}
  return (map['8k'] ?? 0) + (map['4k'] ?? 0)
})
const uhdPct = computed(() => (libraryCount.value ? (uhdCount.value / libraryCount.value) * 100 : 0))

const kpis = computed(() => [
  {
    label: '收录总量',
    value: libraryCount.value.toLocaleString('zh-CN'),
    unit: '部',
    note: `近 30 日新增 +${overview.value?.last_30d_count ?? 0} 部`,
    tone: 'positive',
  },
  {
    label: '累计时长',
    value: runtimeMetric.value.value,
    unit: runtimeMetric.value.unit,
    note: `单部平均时长 ${averageRuntime.value}`,
    tone: 'muted',
  },
  {
    label: '4K 及以上占比',
    value: uhdPct.value.toFixed(1),
    unit: '%',
    note: `${uhdCount.value} 部母带达 4K / UHD`,
    tone: 'muted',
  },
  {
    label: '入库体积',
    value: sizeMetric.value.value,
    unit: sizeMetric.value.unit,
    note: `平均码率 ${averageBitrate.value}`,
    tone: 'muted',
  },
])

const watchSummary = computed(() => {
  const block = watchBlock.value
  if (!block?.available) return '暂无观看记录'
  return `${block.play_count.toLocaleString('zh-CN')} 次播放 · ${formatDuration(block.seconds_watched)}`
})

const typeRows = computed(() => {
  const raw = overview.value?.type_breakdown ?? {}
  const merged = new Map<string, number>()
  for (const [key, count] of Object.entries(raw)) {
    if (!count) continue
    const canonical = (LEGACY_VIDEO_TYPE_ALIAS as Record<string, string>)[key] ?? key
    merged.set(canonical, (merged.get(canonical) ?? 0) + count)
  }
  const total = Array.from(merged.values()).reduce((sum, n) => sum + n, 0) || 1
  return Array.from(merged.entries())
    .map(([key, count]) => ({
      key,
      label: VIDEO_TYPE_LABEL[key] ?? key,
      count,
      pct: Math.round((count / total) * 100),
    }))
    .sort((a, b) => b.count - a.count)
})

const resolutionRows = computed(() => {
  const raw = overview.value?.resolution_breakdown ?? {}
  const order = new Map(RESOLUTION_OPTIONS.map((item, index) => [item.key, index]))
  const total = Object.entries(raw).reduce((sum, [, count]) => sum + (count > 0 ? count : 0), 0) || 1
  return Object.entries(raw)
    .filter(([, count]) => count > 0)
    .map(([key, count]) => ({
      key,
      label: RESOLUTION_OPTIONS.find((item) => item.key === key)?.label ?? key.toUpperCase(),
      count,
      pct: Math.round((count / total) * 100),
    }))
    .sort((a, b) => (order.get(a.key) ?? 99) - (order.get(b.key) ?? 99))
})

const knownResolutionCount = computed(() =>
  Object.values(overview.value?.resolution_breakdown ?? {}).reduce((sum, count) => sum + count, 0),
)
const resolutionCoverage = computed(() =>
  libraryCount.value ? Math.round((knownResolutionCount.value / libraryCount.value) * 100) : 0,
)

const categoriesTotal = computed(() => typeRows.value.reduce((sum, row) => sum + row.count, 0))

const clockValues = computed(() => {
  const values = new Array<number>(24).fill(0)
  for (const item of recap.value?.clock ?? []) {
    if (item.hour >= 0 && item.hour < 24) values[item.hour] = item.count
  }
  return values
})
const clockTotal = computed(() => clockValues.value.reduce((sum, n) => sum + n, 0))
const hasClock = computed(() => clockTotal.value > 0)
const chartMax = computed(() => Math.max(1, ...clockValues.value))
const chartMean = computed(() => clockTotal.value / 24)
const chartMeanY = computed(() => 205 - (chartMean.value / chartMax.value) * 158)

interface ChartPoint {
  x: number
  y: number
  hour: number
  count: number
}

function smoothPath(points: ChartPoint[]) {
  if (points.length < 2) return ''
  const parts = [`M ${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`]
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i]
    const p1 = points[i]
    const p2 = points[i + 1]
    const p3 = points[i + 2] ?? p2
    const cp1x = p1.x + (p2.x - p0.x) / 6
    const cp1y = p1.y + (p2.y - p0.y) / 6
    const cp2x = p2.x - (p3.x - p1.x) / 6
    const cp2y = p2.y - (p3.y - p1.y) / 6
    parts.push(
      `C ${cp1x.toFixed(2)} ${cp1y.toFixed(2)} ${cp2x.toFixed(2)} ${cp2y.toFixed(2)} ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`,
    )
  }
  return parts.join(' ')
}

const chartPoints = computed<ChartPoint[]>(() =>
  clockValues.value.map((count, hour) => ({
    x: (hour / 23) * 1200,
    y: 205 - (count / chartMax.value) * 158,
    hour,
    count,
  })),
)
const chartLinePath = computed(() => smoothPath(chartPoints.value))
const chartAreaPath = computed(() => {
  const path = chartLinePath.value
  if (!path) return ''
  return `${path} L 1200 240 L 0 240 Z`
})
const peakPoint = computed<ChartPoint>(() => {
  if (!hasClock.value) return { x: 0, y: 205, hour: 0, count: 0 }
  return chartPoints.value.reduce((best, point) => (point.count > best.count ? point : best), chartPoints.value[0])
})
const peakLabel = computed(() => `${String(peakPoint.value.hour).padStart(2, '0')}:00`)
const peakLeft = computed(() => `${(peakPoint.value.x / 1200) * 100}%`)
const peakTop = computed(() => `${(peakPoint.value.y / 240) * 100}%`)

const axisLabels = computed(() => [
  '00:00 (夜间休眠)',
  '04:00',
  '08:00',
  '12:00 (午间波段)',
  '16:00',
  '20:00',
  `${peakLabel.value} (黄金波峰)`,
  '23:59',
])

function onChartMove(event: MouseEvent) {
  const el = chartEl.value
  if (!el || !hasClock.value) return
  const rect = el.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width))
  hoverHour.value = Math.round(ratio * 23)
}

const hoverCount = computed(() => (hoverHour.value == null ? 0 : clockValues.value[hoverHour.value] ?? 0))
const hoverLeft = computed(() => `${((hoverHour.value ?? 0) / 23) * 100}%`)
const hoverLabel = computed(
  () => `${String(hoverHour.value ?? 0).padStart(2, '0')}:00 · ${hoverCount.value} 次观看`,
)

const heatDays = computed<RecapDay[]>(() => recap.value?.heatmap ?? [])
const heatTotal = computed(() => heatDays.value.reduce((sum, day) => sum + day.count, 0))
const heatMax = computed(() => Math.max(1, ...heatDays.value.map((day) => day.count)))
const heatOffset = computed(() => {
  const first = heatDays.value[0]
  if (!first) return 0
  const date = new Date(`${first.date}T00:00:00`)
  return Number.isNaN(date.getTime()) ? 0 : date.getDay()
})
const heatAverage = computed(() => (heatDays.value.length ? heatTotal.value / heatDays.value.length : 0))
const heatRangeLabel = computed(() => {
  if (!heatDays.value.length) return '近 365 天'
  const first = heatDays.value[0].date.replace(/-/g, '.')
  const last = heatDays.value[heatDays.value.length - 1].date.replace(/-/g, '.')
  return `${first} — ${last}`
})

function heatLevel(count: number) {
  if (count <= 0) return 0
  const ratio = count / heatMax.value
  if (ratio >= 0.75) return 4
  if (ratio >= 0.5) return 3
  if (ratio >= 0.25) return 2
  return 1
}

function heatTitle(day: RecapDay) {
  return `${day.date} · 入库 ${day.count} 部`
}

const rankedRows = computed(() =>
  [...heatRows.value]
    .sort(
      (a, b) =>
        b.engagement_score + b.content_score - (a.engagement_score + a.content_score),
    )
    .slice(0, 10),
)
const rankedMaxScore = computed(() =>
  Math.max(1, ...rankedRows.value.map((row) => row.content_score + row.engagement_score)),
)

function rankWidth(row: HeatRow) {
  const score = row.content_score + row.engagement_score
  return `${Math.max(5, Math.round((score / rankedMaxScore.value) * 100))}%`
}

const QUADRANT_LABEL: Record<string, string> = {
  both: '双高象限',
  engagement: '观看驱动',
  content: '内容驱动',
  new: '新入库',
}

function kindLabel(kind: string) {
  return kind === 'group' ? '组合' : '艺人'
}

function displayName(item: { name: string; chinese_name?: string | null }) {
  const chinese = (item.chinese_name || '').trim()
  if (chinese && chinese !== item.name) return `${chinese} ${item.name}`
  return item.name
}

function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

function avatarKey(kind: string, id: number) {
  return `${kind}-${id}`
}

function avatarUrl(kind: string, id: number) {
  return kind === 'group' ? groupsApi.avatarUrl(id) : artistsApi.avatarUrl(id)
}

function onAvatarError(kind: string, id: number) {
  brokenAvatars.value = new Set(brokenAvatars.value).add(avatarKey(kind, id))
}

function goEntity(kind: string, uid: string) {
  void router.push(`/${kind === 'group' ? 'groups' : 'artists'}/${uid}`)
}

function percentOne(value: number) {
  return `${(value * 100).toFixed(1)}`
}
</script>

<template>
  <div class="stats-page">
    <SaHeader />

    <main class="stats-main">
      <!-- 刊头 + 宏观指标 -->
      <section class="chapter hero-chapter">
        <div class="hero-utility">
          <span class="stream-chip">
            <i class="stream-dot" />
            <span>数据流 {{ year }}</span>
          </span>

          <div class="range-switch" role="group" aria-label="统计范围">
            <span class="range-caption">播放量</span>
            <button
              v-for="option in rangeOptions"
              :key="option.value"
              type="button"
              class="range-btn"
              :class="{ 'is-on': range === option.value }"
              :aria-pressed="range === option.value"
              @click="range = option.value"
            >
              {{ option.label }}
            </button>
            <span v-if="loading" class="sync-label">同步中</span>
          </div>
        </div>

        <div class="hero-masthead">
          <div class="masthead-main">
            <div class="eyebrow">{{ continuumLabel }} · 播放量</div>
            <div class="title-row">
              <h1 class="page-title">视听数据编年谱</h1>
              
            </div>
          </div>
          <div class="hero-meta">
            <span>更新周期: 实时连续归档</span>
            <span class="meta-dot">·</span>
            <span>基准编码: HEVC / VP9</span>
            <span class="meta-watch">{{ watchSummary }}</span>
          </div>
        </div>

        <div v-if="loading && !data" class="state-box">
          <span class="state-pulse" />
          正在编入数据…
        </div>
        <div v-else-if="loadError && !data" class="state-box state-box--error">
          <span>{{ loadError }}</span>
          <button type="button" class="text-action" @click="load">重新读取</button>
        </div>

        <div v-else-if="data" class="kpi-grid">
          <article v-for="kpi in kpis" :key="kpi.label" class="kpi">
            <div class="kpi-label">{{ kpi.label }}</div>
            <div class="kpi-value">
              <span>{{ kpi.value }}</span>
              <em>{{ kpi.unit }}</em>
            </div>
            <div class="kpi-note" :class="`kpi-note--${kpi.tone}`">
              <span v-if="kpi.tone === 'positive'" class="kpi-arrow">▲</span>
              {{ kpi.note }}
            </div>
          </article>
        </div>
      </section>

      <template v-if="data">
        <!-- 01 年度双焦点 -->
        <section class="chapter resonance-chapter">
          <div class="section-kicker">01 / 年度人物与组合</div>

          <div class="resonance-grid" :class="{ 'is-single': !topArtist || !topGroup }">
            <article v-if="topArtist" class="artist-feature">
              <button
                type="button"
                class="artist-portrait"
                :aria-label="`查看 ${displayName(topArtist)}`"
                @click="goEntity('artist', topArtist.uid)"
              >
                <img
                  v-if="topArtist.avatar_path && !brokenAvatars.has(avatarKey('artist', topArtist.id))"
                  :src="avatarUrl('artist', topArtist.id)"
                  :alt="displayName(topArtist)"
                  @error="onAvatarError('artist', topArtist.id)"
                />
                <span v-else class="portrait-fallback">{{ initialOf(topArtist.name) }}</span>
                <span class="portrait-shade" />
                <span class="portrait-caption">{{ kindLabel('artist') }} · {{ topArtist.extra || '独立艺人' }}</span>
              </button>

              <div class="artist-copy">
                <div class="artist-badge-row">
                  <span class="rank-label">年度艺人 · 第 1 名</span>
                  <span class="score-pill">{{ artistPoints.toLocaleString('zh-CN') }} 分</span>
                </div>
                <div>
                  <h2 class="artist-name">{{ displayName(topArtist) }}</h2>
                  <div class="artist-sub">
                    入库量全库排名第 1 · 历史收录 {{ topArtist.video_count }} 支
                  </div>
                </div>
                <p class="artist-desc">
                  本周期共收录 {{ topArtist.video_count }} 支作品，累计
                  {{ formatDuration(topArtist.duration_seconds) }}。观看热度
                  {{ topArtistHeat?.engagement_score?.toFixed(1) ?? '0.0' }}，内容力
                  {{ topArtistHeat?.content_score?.toFixed(1) ?? '0.0' }}，以稳定的舞台存量与观看回响进入年度焦点。
                </p>
                <div class="artist-metrics">
                  <span>内容力 <strong>{{ topArtistHeat?.content_score?.toFixed(1) ?? '0.0' }}</strong></span>
                  <i>·</i>
                  <span>观看热度 <strong>{{ topArtistHeat?.engagement_score?.toFixed(1) ?? '0.0' }}</strong></span>
                  <i>·</i>
                  <span>完播率 <strong>{{ percentOne(topArtistHeat?.completion_rate ?? 0) }}%</strong></span>
                </div>
              </div>
            </article>

            <article
              v-if="topGroup"
              class="group-panel"
              role="link"
              tabindex="0"
              :aria-label="`查看组合 ${displayName(topGroup)}`"
              @click="goEntity('group', topGroup.uid)"
              @keydown.enter.prevent="goEntity('group', topGroup.uid)"
              @keydown.space.prevent="goEntity('group', topGroup.uid)"
            >
              <div class="group-head">
                <span>年度组合 · 第 1 名</span>
                <span class="group-top">第 1 名</span>
              </div>
              <div class="group-body">
                <div class="group-eyebrow">{{ topGroup.extra || '组合' }} · 成员矩阵</div>
                <h3>{{ displayName(topGroup) }}</h3>
                <p>
                  累计归档 {{ topGroup.video_count }} 部舞台与影像，总时长
                  {{ formatDuration(topGroup.duration_seconds) }}，约占全库
                  {{ groupShare }}% 的影像体量。
                </p>
              </div>
              <div class="group-foot">
                <span>总收录量: <strong>{{ topGroup.video_count }} 部</strong></span>
                <span class="group-score">{{ groupPoints.toLocaleString('zh-CN') }} 分</span>
              </div>
            </article>
          </div>

          <div v-if="!topArtist && !topGroup" class="state-box">
            还没有可编排的艺人或组合数据。
          </div>
        </section>

        <!-- 02 24h 观看波形 -->
        <section class="chapter waveform-chapter">
          <div class="section-head">
            <div>
              <div class="section-kicker">02 / 24 小时视听活跃峰值</div>
              <h2 class="section-title">全天候观看与入库波形流</h2>
            </div>
            <div class="chart-legend">
              <span><i class="legend-line legend-line--solid" />观看峰值 {{ peakLabel }}</span>
              <span><i class="legend-line legend-line--dash" />小时均值 {{ chartMean.toFixed(1) }}</span>
            </div>
          </div>

          <div
            ref="chartEl"
            class="wave-chart"
            @mousemove="onChartMove"
            @mouseleave="hoverHour = null"
          >
            <svg
              class="wave-svg"
              viewBox="0 0 1200 240"
              preserveAspectRatio="none"
              role="img"
              aria-label="24 小时观看次数波形图"
            >
              <defs>
                <linearGradient id="stats-stream-grad" x1="0" y1="0" x2="0" y2="1">
                  <stop class="wave-stop-a" offset="0%" />
                  <stop class="wave-stop-b" offset="82%" />
                  <stop class="wave-stop-c" offset="100%" />
                </linearGradient>
              </defs>
              <line class="wave-grid-line" x1="0" y1="60" x2="1200" y2="60" stroke-dasharray="3 4" />
              <line class="wave-grid-line" x1="0" y1="130" x2="1200" y2="130" stroke-dasharray="3 4" />
              <line class="wave-baseline" x1="0" y1="205" x2="1200" y2="205" />
              <path class="wave-area" :d="chartAreaPath" fill="url(#stats-stream-grad)" />
              <line
                class="wave-mean-line"
                x1="0"
                :y1="chartMeanY"
                x2="1200"
                :y2="chartMeanY"
                stroke-dasharray="4 5"
              />
              <path class="wave-line" :d="chartLinePath" pathLength="1" vector-effect="non-scaling-stroke" />
              <circle
                v-if="hasClock"
                class="wave-peak"
                :cx="peakPoint.x"
                :cy="peakPoint.y"
                r="5"
                vector-effect="non-scaling-stroke"
              />
            </svg>

            <span v-if="hasClock" class="peak-label" :style="{ left: peakLeft, top: peakTop }">
              {{ peakLabel }} 峰值
            </span>

            <div
              v-if="hoverHour !== null && hasClock"
              class="chart-scrubber"
              :style="{ left: hoverLeft }"
            >
              <span class="scrubber-badge">{{ hoverLabel }}</span>
            </div>

            <div v-if="!hasClock" class="chart-empty">还没有足够的观看记录形成 24 小时节律</div>
          </div>

          <div class="wave-axis">
            <span v-for="(label, index) in axisLabels" :key="label" :class="{ 'is-peak': index === 6 }">
              {{ label }}
            </span>
          </div>
        </section>

        <!-- 03 母带谱系 + 365 天节律 -->
        <section class="chapter spectrum-chapter">
          <div class="spectrum-grid">
            <div class="resolution-column">
              <div class="section-kicker">03 / 母带规格分布</div>
              <h2 class="section-title">音画分辨率母带构成</h2>
              <p class="section-lede">
                4K 超清母带合计占比达到 {{ uhdPct.toFixed(1) }}%
              </p>

              <div v-if="resolutionRows.length" class="resolution-list">
                <div v-for="(row, index) in resolutionRows" :key="row.key" class="resolution-row">
                  <div class="resolution-meta">
                    <span :class="{ 'is-muted': index > 1 }">{{ row.label }} 规格母带</span>
                    <span>{{ row.count }} 部 ({{ row.pct }}%)</span>
                  </div>
                  <div class="resolution-track">
                    <span
                      class="resolution-fill"
                      :class="`tone-${Math.min(index, 3)}`"
                      :style="{ width: `${row.pct}%`, animationDelay: `${index * 90}ms` }"
                    />
                  </div>
                </div>
              </div>
              <div v-else class="state-box state-box--inline">还没有分辨率探测数据。</div>

              <div class="resolution-foot">
                <span>编码: HEVC Main 10-bit</span>
                <span class="resolution-covered">{{ resolutionCoverage }}% 已探测</span>
              </div>
            </div>

            <div class="heatmap-column">
              <div class="heatmap-head">
                <div>
                  <div class="section-kicker">04 / 365 天入库节奏</div>
                  <h2 class="section-title">365 天入库与打歌节律</h2>
                </div>
                <div class="heat-legend">
                  <span>低频</span>
                  <i class="heat-swatch heat-level-1" />
                  <i class="heat-swatch heat-level-2" />
                  <i class="heat-swatch heat-level-3" />
                  <i class="heat-swatch heat-level-4" />
                  <span>高频</span>
                </div>
              </div>

              <div class="heat-scroll">
                <div class="heat-grid" role="img" :aria-label="`${heatRangeLabel} 每日入库量热力图`">
                  <span v-for="n in heatOffset" :key="`blank-${n}`" class="heat-cell is-blank" />
                  <span
                    v-for="day in heatDays"
                    :key="day.date"
                    class="heat-cell"
                    :class="`heat-level-${heatLevel(day.count)}`"
                    :title="heatTitle(day)"
                  />
                </div>
              </div>

              <div class="heat-foot">
                <span>一季度 · 冬季回归季</span>
                <span>二季度 · 春季大学祭</span>
                <span>三季度 · 夏日打歌潮</span>
                <span>四季度 · 年末大赏</span>
                <span class="heat-average">日均收录 <strong>{{ heatAverage.toFixed(2) }} 部</strong></span>
              </div>
            </div>
          </div>

          <div class="categories">
            <div class="categories-head">
              <div>分类细分分布</div>
              <div class="categories-total">共 {{ categoriesTotal.toLocaleString('zh-CN') }} 条</div>
            </div>
            <div v-if="typeRows.length" class="category-grid">
              <div v-for="row in typeRows" :key="row.key" class="category-chip">
                <span>{{ row.label }}</span>
                <strong>
                  {{ row.count }}
                  <small>({{ row.pct }}%)</small>
                </strong>
              </div>
            </div>
            <div v-else class="state-box state-box--inline">暂无视频分类数据。</div>
          </div>
        </section>

        <!-- 05 热量总榜 -->
        <section class="chapter ledger-chapter">
          <div class="ledger-head">
            <div>
              <div class="section-kicker">05 / 热度走势明细</div>
              <h2 class="section-title">入库排名与艺人热度动量总榜</h2>
            </div>
            <p class="ledger-note">
              热度指数 = 内容力 + 观看热度；内容力按条数与总时长，观看热度按播放时长、播放、完播与收藏加权。
            </p>
          </div>

          <div v-if="rankedRows.length" class="ledger-scroll">
            <table class="ledger-table">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>艺人 / 组合</th>
                  <th>历史收录总量</th>
                  <th>播放次数</th>
                  <th>完播率</th>
                  <th class="heat-flow-head">热度动量流</th>
                  <th class="points-head">热度指数</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, index) in rankedRows"
                  :key="`${row.kind}-${row.id}`"
                  tabindex="0"
                  role="link"
                  :aria-label="`查看 ${displayName(row)}`"
                  @click="goEntity(row.kind, row.uid)"
                  @keydown.enter.prevent="goEntity(row.kind, row.uid)"
                >
                  <td class="rank-cell">#{{ String(index + 1).padStart(2, '0') }}</td>
                  <td class="entity-cell">
                    <div class="entity-name">
                      {{ displayName(row) }}
                      <span class="entity-kind">{{ kindLabel(row.kind) }}</span>
                    </div>
                    <div class="entity-sub">
                      {{ QUADRANT_LABEL[row.quadrant] ?? '新入库' }} · {{ row.video_count }} 部影像
                    </div>
                  </td>
                  <td class="base-cell">{{ row.video_count }} 部</td>
                  <td class="plays-cell">
                    <strong>{{ row.plays.toLocaleString('zh-CN') }}</strong>
                    <span>收藏 {{ row.favorites }}</span>
                  </td>
                  <td class="completion-cell">{{ percentOne(row.completion_rate) }}%</td>
                  <td class="flow-cell">
                    <div class="flow-track">
                      <span class="flow-fill" :style="{ width: rankWidth(row) }" />
                    </div>
                  </td>
                  <td class="points-cell">
                    {{ Math.round(row.content_score + row.engagement_score).toLocaleString('zh-CN') }}
                    <small>分</small>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="state-box">还没有形成热度数据。</div>

          <div class="ledger-foot">
            <span>观看累计: {{ watchSummary }} · 完播 {{ watchBlock?.completed_count ?? 0 }} 次</span>
            <span>{{ heatRangeLabel }}</span>
          </div>
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.stats-page {
  --paper: #f8fafc;
  --ink: #0f172a;
  --ink-soft: #475569;
  --muted: #64748b;
  --faint: #94a3b8;
  --line: #e2e8f0;
  --hair: rgba(226, 232, 240, 0.72);
  --chip: #f1f5f9;
  --chip-hover: #e2e8f0;
  --card: #ffffff;
  --track: #e2e8f0;
  --inverted-bg: #0f172a;
  --inverted-fg: #ffffff;
  --inverted-muted: #94a3b8;
  --inverted-line: rgba(255, 255, 255, 0.12);
  --green: #059669;
  --blue: #1d63ff;
  --spring: cubic-bezier(0.16, 1, 0.3, 1);
  min-height: 100vh;
  background: var(--paper);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC',
    'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

html[data-theme='dark'] .stats-page {
  --paper: #0b0f17;
  --ink: #f1f5f9;
  --ink-soft: #cbd5e1;
  --muted: #94a3b8;
  --faint: #64748b;
  --line: #243044;
  --hair: rgba(148, 163, 184, 0.13);
  --chip: #141c29;
  --chip-hover: #1c2738;
  --card: #111826;
  --track: #263346;
  --inverted-bg: #151d2b;
  --inverted-fg: #f8fafc;
  --inverted-muted: #94a3b8;
  --inverted-line: rgba(255, 255, 255, 0.13);
  --green: #34d399;
  --blue: #5b8def;
}

.stats-page :deep(::selection) {
  background: var(--ink);
  color: var(--paper);
}

.stats-page button {
  font: inherit;
}

.stats-page button:focus-visible,
.stats-page [tabindex]:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 3px;
}

.stats-main {
  width: 100%;
  max-width: 1920px;
  margin: 0 auto;
  padding: 36px clamp(24px, 5.55vw, 80px) 120px;
}

.chapter {
  position: relative;
  padding-bottom: clamp(48px, 5vw, 72px);
  margin-bottom: clamp(56px, 6.2vw, 90px);
  border-bottom: 1px solid var(--hair);
  animation: section-in 0.75s var(--spring) both;
}

.hero-chapter {
  animation-delay: 0.02s;
}
.resonance-chapter {
  animation-delay: 0.08s;
}
.waveform-chapter {
  animation-delay: 0.13s;
}
.spectrum-chapter {
  animation-delay: 0.18s;
}
.ledger-chapter {
  animation-delay: 0.23s;
}

.hero-utility {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: clamp(30px, 3.8vw, 52px);
}

.stream-chip {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 12px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--chip) 82%, transparent);
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: 0.08em;
  white-space: nowrap;
}

.stream-dot {
  position: relative;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.45);
  animation: stream-pulse 2.4s ease-out infinite;
}

.range-switch {
  display: flex;
  align-items: center;
  gap: 6px;
}

.range-caption {
  margin-right: 5px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: 0.18em;
}

.range-btn {
  padding: 6px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.2;
  cursor: pointer;
  transition: color 0.18s ease, background-color 0.18s ease;
}

.range-btn:hover {
  color: var(--ink);
  background: var(--chip);
}

.range-btn.is-on {
  background: var(--ink);
  color: var(--paper);
  font-weight: 700;
}

.sync-label {
  margin-left: 2px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  letter-spacing: 0.12em;
  animation: soft-blink 1.1s ease-in-out infinite;
}

.hero-masthead {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
}

.masthead-main {
  min-width: 0;
}

.eyebrow,
.section-kicker {
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.235em;
  line-height: 1.5;
  text-transform: uppercase;
}

.eyebrow {
  margin-bottom: 8px;
}

.title-row {
  display: flex;
  align-items: baseline;
  gap: 14px;
  min-width: 0;
}

.page-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Instrument Serif', 'Songti SC', serif;
  font-size: clamp(38px, 3.8vw, 58px);
  font-weight: 900;
  letter-spacing: -0.035em;
  line-height: 1.08;
}

.title-flow {
  color: var(--faint);
  font-family: 'Noto Serif SC', 'Instrument Serif', 'Songti SC', serif;
  font-size: clamp(24px, 2.5vw, 38px);
  font-style: italic;
  font-weight: 400;
  line-height: 1;
  white-space: nowrap;
}

.hero-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 9px;
  padding-bottom: 8px;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  white-space: nowrap;
}

.meta-dot {
  color: var(--faint);
}

.meta-watch {
  flex-basis: 100%;
  color: var(--faint);
  font-size: 13.5px;
  text-align: right;
  letter-spacing: 0.04em;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: clamp(24px, 4.2vw, 70px);
  padding-top: clamp(34px, 4.3vw, 58px);
}

.kpi {
  min-width: 0;
}

.kpi-label {
  margin-bottom: 5px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: 0.16em;
  line-height: 1.5;
}

.kpi-value {
  display: flex;
  align-items: baseline;
  gap: 8px;
  color: var(--ink);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: clamp(34px, 3.7vw, 56px);
  font-weight: 700;
  letter-spacing: -0.055em;
  line-height: 1.08;
  white-space: nowrap;
}

.kpi-value em {
  color: var(--muted);
  font-family: inherit;
  font-size: clamp(11px, 0.9vw, 13px);
  font-style: normal;
  font-weight: 600;
  letter-spacing: 0;
}

.kpi-note {
  margin-top: 8px;
  color: var(--faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13.5px;
  line-height: 1.6;
}

.kpi-note--positive {
  color: var(--green);
}

.kpi-arrow {
  margin-right: 2px;
  font-size: 13px;
  vertical-align: 1px;
}

.state-box {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 96px;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  text-align: center;
}

.state-box--inline {
  min-height: 60px;
  justify-content: flex-start;
  text-align: left;
}

.state-box--error {
  color: #dc2626;
}

.state-pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink);
  animation: soft-blink 1.1s ease-in-out infinite;
}

.text-action {
  padding: 3px 0;
  border: 0;
  border-bottom: 1px solid currentColor;
  background: none;
  color: inherit;
  font-family: inherit;
  font-size: inherit;
  cursor: pointer;
}

.section-kicker {
  margin-bottom: 7px;
}

.section-title {
  margin: 0;
  color: var(--ink);
  font-size: clamp(21px, 1.65vw, 25px);
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1.35;
}

.section-lede {
  margin: 5px 0 0;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.7;
}

/* ---------- 01 年度双焦点 ---------- */
.resonance-grid {
  display: grid;
  grid-template-columns: minmax(0, 7fr) minmax(0, 5fr);
  gap: clamp(36px, 5vw, 74px);
  align-items: stretch;
  margin-top: clamp(24px, 2.8vw, 38px);
}

.resonance-grid.is-single {
  grid-template-columns: minmax(0, 1fr);
}

.artist-feature {
  display: flex;
  align-items: center;
  gap: clamp(26px, 3.3vw, 46px);
  min-width: 0;
}

.artist-portrait {
  position: relative;
  flex: 0 0 auto;
  width: clamp(208px, 18vw, 270px);
  aspect-ratio: 3 / 4;
  padding: 0;
  border: 0;
  border-radius: 18px;
  overflow: hidden;
  background: linear-gradient(145deg, var(--chip), var(--track));
  box-shadow: 0 20px 45px -25px rgba(15, 23, 42, 0.5);
  cursor: pointer;
}

.artist-portrait img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.8s var(--spring);
}

.artist-portrait:hover img {
  transform: scale(1.045);
}

.portrait-fallback {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  color: var(--muted);
  font-family: 'Noto Serif SC', 'Instrument Serif', 'Songti SC', serif;
  font-size: 76px;
  font-weight: 900;
}

.portrait-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(15, 23, 42, 0.7), transparent 52%);
  pointer-events: none;
}

.portrait-caption {
  position: absolute;
  left: 14px;
  bottom: 12px;
  color: #fff;
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.artist-copy {
  display: flex;
  flex-direction: column;
  gap: 13px;
  min-width: 0;
}

.artist-badge-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
}

.rank-label {
  color: var(--ink);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.score-pill {
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--ink);
  color: var(--paper);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.artist-name {
  margin: 0;
  font-family: 'Noto Serif SC', 'Instrument Serif', 'Songti SC', serif;
  font-size: clamp(32px, 3.2vw, 49px);
  font-weight: 900;
  letter-spacing: -0.045em;
  line-height: 1.1;
}

.artist-sub {
  margin-top: 6px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  line-height: 1.6;
}

.artist-desc {
  max-width: 510px;
  margin: 0;
  color: var(--ink-soft);
  font-size: 13px;
  line-height: 1.9;
}

.artist-metrics {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  padding-top: 2px;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
}

.artist-metrics i {
  color: var(--faint);
  font-style: normal;
}

.artist-metrics strong {
  color: var(--ink);
  font-weight: 700;
}

.group-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-height: 320px;
  padding: clamp(26px, 2.7vw, 38px);
  border-radius: 24px;
  overflow: hidden;
  background:
    radial-gradient(130% 120% at 90% 0%, rgba(29, 99, 255, 0.18), transparent 44%),
    var(--inverted-bg);
  color: var(--inverted-fg);
  box-shadow: 0 18px 45px -30px rgba(15, 23, 42, 0.65);
  cursor: pointer;
  transition: transform 0.35s var(--spring), box-shadow 0.35s ease;
}

.group-panel:hover {
  transform: translateY(-3px);
  box-shadow: 0 26px 55px -30px rgba(15, 23, 42, 0.7);
}

.group-head,
.group-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  color: var(--inverted-muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  letter-spacing: 0.09em;
}

.group-top {
  padding: 3px 7px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.1);
  color: var(--inverted-fg);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.group-body {
  margin: clamp(28px, 3.8vw, 48px) 0;
}

.group-eyebrow {
  margin-bottom: 8px;
  color: var(--inverted-muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  letter-spacing: 0.11em;
}

.group-body h3 {
  margin: 0;
  color: var(--inverted-fg);
  font-family: 'Noto Serif SC', 'Instrument Serif', 'Songti SC', serif;
  font-size: clamp(32px, 3.2vw, 46px);
  font-weight: 900;
  letter-spacing: -0.04em;
  line-height: 1.1;
  overflow-wrap: anywhere;
}

.group-body p {
  max-width: 390px;
  margin: 10px 0 0;
  color: var(--inverted-muted);
  font-size: 14px;
  line-height: 1.85;
}

.group-foot {
  padding-top: 15px;
  border-top: 1px solid var(--inverted-line);
}

.group-foot strong {
  color: var(--inverted-fg);
  font-weight: 700;
}

.group-score {
  color: #6ee7b7;
  font-weight: 700;
}

/* ---------- 02 波形 ---------- */
.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 20px;
}

.chart-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 20px;
  padding-bottom: 3px;
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
}

.chart-legend span {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}

.legend-line {
  display: inline-block;
  width: 17px;
  height: 0;
  border-top: 2px solid var(--ink);
}

.legend-line--dash {
  border-top: 1.5px dashed var(--faint);
}

.wave-chart {
  position: relative;
  width: 100%;
  height: clamp(238px, 23vw, 330px);
  padding-top: 10px;
  cursor: crosshair;
  user-select: none;
}

.wave-svg {
  width: 100%;
  height: 100%;
  overflow: visible;
}

.wave-stop-a {
  stop-color: var(--ink);
  stop-opacity: 0.12;
}

.wave-stop-b {
  stop-color: var(--ink);
  stop-opacity: 0.025;
}

.wave-stop-c {
  stop-color: var(--ink);
  stop-opacity: 0;
}

.wave-grid-line {
  stroke: var(--line);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.wave-baseline {
  stroke: var(--faint);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}

.wave-area {
  pointer-events: none;
}

.wave-line {
  fill: none;
  stroke: var(--ink);
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-dasharray: 1;
  stroke-dashoffset: 1;
  animation: draw-line 1.7s var(--spring) 0.15s forwards;
}

.wave-mean-line {
  stroke: var(--faint);
  stroke-width: 1.25;
  vector-effect: non-scaling-stroke;
  opacity: 0.8;
}

.wave-peak {
  fill: var(--ink);
  stroke: var(--paper);
  stroke-width: 2;
}

.peak-label {
  position: absolute;
  transform: translate(-50%, -118%);
  color: var(--ink);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.07em;
  white-space: nowrap;
  pointer-events: none;
}

.peak-label::before {
  position: absolute;
  right: calc(100% + 5px);
  top: 50%;
  width: 5px;
  border-top: 1px solid var(--ink);
  content: '';
}

.chart-scrubber {
  position: absolute;
  top: 8px;
  bottom: 8px;
  width: 1px;
  background: var(--ink);
  opacity: 0.72;
  pointer-events: none;
}

.scrubber-badge {
  position: absolute;
  top: -2px;
  left: 50%;
  padding: 3px 7px;
  border-radius: 4px;
  background: var(--ink);
  color: var(--paper);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  white-space: nowrap;
  transform: translate(-50%, -100%);
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.16);
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
}

.wave-axis {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 7px;
  color: var(--faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13.5px;
  letter-spacing: 0.01em;
}

.wave-axis .is-peak {
  color: var(--ink);
  font-weight: 700;
}

/* ---------- 03 分辨率 + 热力 ---------- */
.spectrum-grid {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 7fr);
  gap: clamp(42px, 5.2vw, 76px);
  align-items: start;
}

.resolution-list {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding-top: 26px;
}

.resolution-meta {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 7px;
  color: var(--ink);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
}

.resolution-meta .is-muted {
  color: var(--muted);
  font-weight: 500;
}

.resolution-meta span:last-child {
  color: var(--muted);
  font-weight: 600;
  white-space: nowrap;
}

.resolution-track {
  width: 100%;
  height: 7px;
  border-radius: 999px;
  overflow: hidden;
  background: color-mix(in srgb, var(--track) 80%, transparent);
}

.resolution-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  transform-origin: left;
  animation: grow-bar 1.1s var(--spring) both;
}

.resolution-fill.tone-0 {
  background: var(--ink);
}
.resolution-fill.tone-1 {
  background: #64748b;
}
.resolution-fill.tone-2 {
  background: #94a3b8;
}
.resolution-fill.tone-3 {
  background: #cbd5e1;
}

.resolution-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 27px;
  padding-top: 14px;
  border-top: 1px solid var(--hair);
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
}

.resolution-covered {
  color: var(--ink);
  font-weight: 700;
}

.heatmap-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 18px;
}

.heat-legend {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  white-space: nowrap;
}

.heat-swatch {
  width: 9px;
  height: 9px;
  border-radius: 2px;
}

.heat-scroll {
  width: 100%;
  margin-top: 24px;
  padding: 7px 0 10px;
  overflow-x: auto;
  scrollbar-width: none;
}

.heat-scroll::-webkit-scrollbar {
  display: none;
}

.heat-grid {
  --heat-cell: 11px;
  --heat-gap: 5px;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: var(--heat-cell);
  grid-template-rows: repeat(7, var(--heat-cell));
  gap: var(--heat-gap);
  width: max-content;
}

.heat-cell {
  display: block;
  width: var(--heat-cell);
  height: var(--heat-cell);
  border-radius: 2.5px;
  background: var(--chip);
  transition: transform 0.15s ease;
}

.heat-cell:not(.is-blank):hover {
  position: relative;
  z-index: 2;
  transform: scale(1.5);
}

.heat-cell.is-blank {
  background: transparent;
}

.heat-level-1 {
  background: #e2e8f0;
}
.heat-level-2 {
  background: #94a3b8;
}
.heat-level-3 {
  background: #475569;
}
.heat-level-4 {
  background: #0f172a;
}

html[data-theme='dark'] .heat-level-1 {
  background: #263346;
}
html[data-theme='dark'] .heat-level-2 {
  background: #475569;
}
html[data-theme='dark'] .heat-level-3 {
  background: #94a3b8;
}
html[data-theme='dark'] .heat-level-4 {
  background: #f1f5f9;
}

.heat-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding-top: 10px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  white-space: nowrap;
}

.heat-average {
  color: var(--muted);
}

.heat-average strong {
  color: var(--ink);
  font-weight: 700;
}

.categories {
  margin-top: clamp(42px, 5vw, 68px);
  padding-top: clamp(28px, 3.2vw, 40px);
  border-top: 1px solid var(--hair);
}

.categories-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 15px;
  color: var(--ink);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
}

.categories-total {
  color: var(--faint);
  font-weight: 500;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.category-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 11px;
}

.category-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  padding: 11px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--chip) 82%, transparent);
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  transition: background-color 0.2s ease, transform 0.2s var(--spring);
}

.category-chip:hover {
  background: var(--chip-hover);
  transform: translateY(-1px);
}

.category-chip > span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-chip strong {
  color: var(--ink);
  font-size: 14px;
  font-weight: 700;
  white-space: nowrap;
}

.category-chip small {
  color: var(--faint);
  font-size: 13px;
  font-weight: 400;
}

/* ---------- 04 总榜 ---------- */
.ledger-chapter {
  margin-bottom: 0;
  padding-bottom: 12px;
  border-bottom: 0;
}

.ledger-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 36px;
  margin-bottom: 24px;
}

.ledger-note {
  max-width: 520px;
  margin: 0;
  color: var(--faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13.5px;
  line-height: 1.75;
  text-align: right;
}

.ledger-scroll {
  width: 100%;
  overflow-x: auto;
  scrollbar-width: thin;
}

.ledger-table {
  width: 100%;
  min-width: 760px;
  border-collapse: collapse;
  text-align: left;
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 14px;
}

.ledger-table th {
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
  color: var(--faint);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.ledger-table th:first-child,
.ledger-table td:first-child {
  padding-left: 3px;
}

.ledger-table th:last-child,
.ledger-table td:last-child {
  padding-right: 3px;
}

.ledger-table td {
  padding: 16px 12px;
  border-bottom: 1px solid var(--hair);
  vertical-align: middle;
}

.ledger-table tbody tr {
  cursor: pointer;
  transition: background-color 0.16s ease;
}

.ledger-table tbody tr:hover,
.ledger-table tbody tr:focus-visible {
  background: color-mix(in srgb, var(--chip) 54%, transparent);
}

.rank-cell {
  width: 58px;
  color: var(--faint);
  font-weight: 700;
  white-space: nowrap;
}

.entity-cell {
  min-width: 210px;
}

.entity-name {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
    'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.4;
}

.entity-kind {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--chip);
  color: var(--muted);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}

.entity-sub {
  margin-top: 4px;
  color: var(--faint);
  font-size: 13px;
  line-height: 1.5;
}

.base-cell {
  color: var(--ink-soft);
  font-weight: 700;
  white-space: nowrap;
}

.plays-cell {
  color: var(--ink);
  white-space: nowrap;
}

.plays-cell strong {
  font-size: 14px;
  font-weight: 700;
}

.plays-cell span {
  display: block;
  margin-top: 3px;
  color: var(--faint);
  font-size: 13px;
}

.completion-cell {
  color: var(--ink-soft);
  white-space: nowrap;
}

.heat-flow-head {
  width: 23%;
}

.flow-cell {
  width: 23%;
  min-width: 130px;
}

.flow-track {
  width: 100%;
  height: 5px;
  border-radius: 999px;
  overflow: hidden;
  background: var(--track);
}

.flow-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--ink);
  transform-origin: left;
  animation: grow-bar 1s var(--spring) both;
}

.points-head {
  text-align: right !important;
}

.points-cell {
  color: var(--ink);
  font-size: 13px;
  font-weight: 700;
  text-align: right;
  white-space: nowrap;
}

.points-cell small {
  color: var(--faint);
  font-size: 13px;
  font-weight: 500;
}

.ledger-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding-top: 14px;
  color: var(--faint);
  font-family: 'Puhuiti', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 13px;
}

@keyframes section-in {
  from {
    opacity: 0;
    transform: translateY(18px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes stream-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.42);
  }
  70% {
    box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
  }
}

@keyframes soft-blink {
  0%,
  100% {
    opacity: 0.35;
  }
  50% {
    opacity: 1;
  }
}

@keyframes draw-line {
  to {
    stroke-dashoffset: 0;
  }
}

@keyframes grow-bar {
  from {
    transform: scaleX(0);
  }
  to {
    transform: scaleX(1);
  }
}

@media (max-width: 1180px) {
  .resonance-grid,
  .spectrum-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .group-panel {
    min-height: 280px;
  }

  .artist-feature {
    align-items: flex-start;
  }

  .heat-grid {
    --heat-cell: 10px;
    --heat-gap: 4.5px;
  }
}

@media (max-width: 900px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 30px 24px;
  }

  .hero-masthead {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
  }

  .hero-meta {
    justify-content: flex-start;
    padding-bottom: 0;
  }

  .meta-watch {
    text-align: left;
  }

  .artist-feature {
    flex-direction: column;
    align-items: stretch;
  }

  .artist-portrait {
    width: min(72vw, 300px);
  }

  .section-head,
  .ledger-head {
    align-items: flex-start;
    flex-direction: column;
    gap: 15px;
  }

  .ledger-note {
    max-width: 640px;
    text-align: left;
  }

  .category-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .wave-axis {
    font-size: 13px;
  }

  .heat-legend {
    display: none;
  }

  .ledger-foot {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }
}

@media (max-width: 620px) {
  .stats-main {
    padding: 22px 18px 112px;
  }

  .hero-utility {
    align-items: flex-start;
    flex-direction: column;
    gap: 15px;
  }

  .range-switch {
    flex-wrap: wrap;
  }

  .range-btn {
    padding: 5px 8px;
  }

  .title-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .page-title {
    font-size: 35px;
  }

  .title-flow {
    font-size: 26px;
  }

  .kpi-grid {
    gap: 25px 18px;
  }

  .kpi-value {
    font-size: 31px;
  }

  .kpi-value em {
    font-size: 13.5px;
  }

  .kpi-label {
    font-size: 13.5px;
  }

  .kpi-note {
    font-size: 13.5px;
  }

  .artist-portrait {
    width: min(78vw, 280px);
  }

  .artist-name,
  .group-body h3 {
    font-size: 34px;
  }

  .group-panel {
    padding: 24px;
    border-radius: 20px;
  }

  .artist-desc {
    font-size: 13px;
    line-height: 1.8;
  }

  .artist-metrics {
    gap: 9px;
    font-size: 13.5px;
  }

  .wave-chart {
    height: 235px;
  }

  .wave-axis {
    font-size: 13px;
  }

  .wave-axis span:nth-child(even) {
    display: none;
  }

  .peak-label {
    font-size: 13px;
  }

  .heat-grid {
    --heat-cell: 9px;
    --heat-gap: 4px;
  }

  .heat-foot {
    overflow-x: auto;
    padding-bottom: 4px;
  }

  .categories-head {
    align-items: flex-start;
    flex-direction: column;
    gap: 4px;
  }

  .category-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .ledger-table {
    min-width: 720px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .chapter,
  .wave-line,
  .resolution-fill,
  .flow-fill,
  .stream-dot {
    animation: none !important;
  }

  .wave-line {
    stroke-dashoffset: 0;
  }
}
</style>
