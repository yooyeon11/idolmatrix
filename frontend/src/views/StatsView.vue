<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import StatsChapter from '@/components/StatsChapter.vue'
import StatsClock from '@/components/StatsClock.vue'
import StatsHeatmap from '@/components/StatsHeatmap.vue'
import StatsSummaryCard from '@/components/StatsSummaryCard.vue'
import { dashboardApi } from '@/api/dashboard'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import { useThemeStore } from '@/stores/theme'
import type { LibraryStats, StatsRankItem, StatsRecap } from '@/types/models'
import {
  LEGACY_VIDEO_TYPE_ALIAS,
  RESOLUTION_OPTIONS,
  VIDEO_TYPE_LABEL,
  VIDEO_TYPE_OPTIONS,
} from '@/types/models'
import { formatFileSize } from '@/utils/format'
import { artistPath, groupPath, videoPath } from '@/utils/routes'

// 统计页 —— 「个人年度刊物」式推翻重建：
// 封面 → 八章正文（一屏一事）→ 终章封底卡。
// 数据来自 /stats 与 /stats/recap；口径默认「当年」（range=year），封面可切「全部」。
const router = useRouter()
const message = useMessage()

const loading = ref(false)
const data = ref<LibraryStats | null>(null)
const recap = ref<StatsRecap | null>(null)

// 口径：year=真·当年（本地时区 1 月 1 日起），all=开馆以来
const range = ref<'year' | 'all'>('year')
const rangeLabel = computed(() => (range.value === 'year' ? '年度' : '馆藏'))

// 排行榜双维度：收藏榜 / 观看榜
const rankKind = ref<'library' | 'watch'>('library')
const RANK_TYPE_KEY = 'kpml_stats_rank_type'
function readRankType(): 'groups' | 'artists' | 'songs' | 'videos' {
  const raw = localStorage.getItem(RANK_TYPE_KEY)
  return raw === 'artists' || raw === 'groups' || raw === 'songs' || raw === 'videos' ? raw : 'artists'
}
const rankType = ref<'groups' | 'artists' | 'songs' | 'videos'>(readRankType())
watch(rankType, (v) => localStorage.setItem(RANK_TYPE_KEY, v))

// 头像加载失败回退首字母
const brokenAvatars = ref<Set<string>>(new Set())

async function load() {
  loading.value = true
  try {
    const [stats, recapData] = await Promise.all([
      dashboardApi.libraryStats({ range: range.value, include_shorts: false, limit: 20 }),
      dashboardApi.recap(),
    ])
    data.value = stats
    recap.value = recapData
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function setRange(r: 'year' | 'all') {
  if (range.value === r) return
  range.value = r
  void load()
}

onMounted(() => void load())

const overview = computed(() => data.value?.overview)
const yearLabel = computed(() => String(new Date().getFullYear()))

// ===== 01 年度总览 =====
const durationBig = computed(() => {
  const s = overview.value?.total_duration_seconds || 0
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h >= 1 ? { num: h.toLocaleString(), unit: '小时' } : { num: String(m), unit: '分钟' }
})
// ===== 02 年度组合 =====
const topGroups = computed(() => data.value?.groups || [])
const topGroup = computed(() => topGroups.value[0])
const runnerUps = computed(() => topGroups.value.slice(1, 5))

// ===== 01 文案（时长类比用年度主角冠名，故放在 02 之后） =====
// 参照物：一场演唱会 ≈ 2.5 小时；一部音乐电影 ≈ 110 分钟；一轮巡演 ≈ 30 场
const CONCERT_HOURS = 2.5
const MUSIC_FILM_HOURS = 110 / 60
const TOUR_HOURS = CONCERT_HOURS * 30

function humanCount(n: number) {
  if (n >= 100) return Math.round(n).toLocaleString()
  if (n >= 10) return String(Math.round(n))
  return (Math.round(n * 10) / 10).toFixed(1)
}

const overviewChapterTitle = computed(() =>
  range.value === 'year' ? '三个数字，就是这一年' : '三个数字，就是这些年',
)
const countNote = computed(() => (range.value === 'year' ? '这一年保存的影像' : '这些年保存的影像'))
const sizeNote = '硬盘里的全部家当'

// 把总时长换成人有体感的节点：几场演唱会 / 几部音乐电影 / 几次巡演
const durationAnalogy = computed(() => {
  const seconds = overview.value?.total_duration_seconds || 0
  if (seconds <= 0) return '连起来能放这么久'
  const hours = seconds / 3600
  const headliner = topGroup.value ? displayName(topGroup.value) : ''
  const shows = hours / CONCERT_HOURS
  if (shows < 1) return '还不够一场演唱会'
  const showsLabel = headliner ? `场 ${headliner} 演唱会` : '场演唱会'
  const parts = [`≈ ${humanCount(shows)} ${showsLabel}`]
  if (hours / TOUR_HOURS >= 1) parts.push(`≈ ${Math.max(1, Math.round(hours / TOUR_HOURS))} 次巡演`)
  else if (hours / MUSIC_FILM_HOURS >= 2) parts.push(`≈ ${humanCount(hours / MUSIC_FILM_HOURS)} 部音乐电影`)
  return parts.join(' · ')
})

// ===== 03 年度艺人（口径=参演 ∪ 直拍对象） =====
const topArtists = computed(() => data.value?.artists || [])
const topArtist = computed(() => topArtists.value[0])
const artistRunnerUps = computed(() => topArtists.value.slice(1, 5))

function goArtist(uid: string) {
  router.push(artistPath(uid))
}

// ===== 04 镜头焦点（直拍对象，纯 subject_artist_id 口径） =====
const topSubjects = computed(() => data.value?.subject_artists || [])
const topSubject = computed(() => topSubjects.value[0])
const subjectMax = computed(() => Math.max(1, ...topSubjects.value.map((r) => r.video_count)))
function subjectBar(count: number) {
  return `${Math.max(6, (count / subjectMax.value) * 100)}%`
}

// ===== 章节文案随口径 =====
const groupChapterTitle = computed(() => {
  if (!topGroup.value) return ''
  return range.value === 'year'
    ? `${yearLabel.value} 是 ${displayName(topGroup.value)} 的一年`
    : `开馆以来，${displayName(topGroup.value)} 是绝对主角`
})
const artistChapterTitle = computed(() => {
  if (!topArtist.value) return ''
  return range.value === 'year'
    ? `${yearLabel.value} 的镜头常客是 ${displayName(topArtist.value)}`
    : `开馆以来，镜头常客是 ${displayName(topArtist.value)}`
})
const subjectChapterTitle = computed(() => {
  if (!topSubject.value) return ''
  return range.value === 'year'
    ? `${yearLabel.value} 你把镜头对准 ${displayName(topSubject.value)} 最多`
    : `开馆以来，${displayName(topSubject.value)} 站在镜头正中`
})

// ===== 03 收藏节奏 =====
const heatTotal = computed(() =>
  (recap.value?.heatmap || []).reduce((n, d) => n + d.count, 0),
)

// ===== 04 类型构成：Top4 + 其他 =====
// 图表系列色从主题 CSS 变量读取，跟随基础设置的强调色切换
const themeStore = useThemeStore()
const TYPE_COLORS = computed<string[]>(() => {
  void themeStore.accent
  const cs = getComputedStyle(document.documentElement)
  const read = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback
  return [
    read('--sa-series-1', '#0485f7'),
    read('--sa-series-2', '#48a0ff'),
    read('--sa-series-3', '#78bbff'),
    read('--sa-series-4', '#a8d5ff'),
  ]
})
const OTHER_COLOR = '#d3d6de'
interface TypeRow {
  key: string
  label: string
  count: number
  pct: number
  color: string
}

/** 两色线性插值（用于按主题强调色生成色阶）。 */
function mixHex(from: string, to: string, t: number): string {
  const parse = (hex: string) => {
    const s = hex.trim().replace('#', '')
    const full = s.length === 3 ? s.split('').map((c) => c + c).join('') : s
    const n = Number.parseInt(full.slice(0, 6), 16)
    return Number.isNaN(n)
      ? [0, 0, 0]
      : [(n >> 16) & 255, (n >> 8) & 255, n & 255]
  }
  const a = parse(from)
  const b = parse(to)
  const mix = a.map((v, i) => Math.round(v + (b[i] - v) * t))
  return `#${mix.map((v) => v.toString(16).padStart(2, '0')).join('')}`
}

/** 取 n 个色阶：n ≤ 锚点数直接用主题系列色，更多则在锚点之间插值补足。 */
function rampColors(anchors: string[], n: number): string[] {
  if (n <= 0) return []
  if (n === 1) return [anchors[0]]
  if (n <= anchors.length) return anchors.slice(0, n)
  const segs = anchors.length - 1
  return Array.from({ length: n }, (_, i) => {
    const t = (i / (n - 1)) * segs
    const idx = Math.min(Math.floor(t), segs - 1)
    return mixHex(anchors[idx], anchors[idx + 1], t - idx)
  })
}

const typeRows = computed<TypeRow[]>(() => {
  const raw = overview.value?.type_breakdown || {}
  // 归入现行分类：历史细分类型（PersonalFancam 等）并进对应类别，
  // 其余不在现行分类表里的值统一落「其他视频」，避免出现英文原始键或空分类。
  const counts = new Map<string, number>()
  for (const [key, count] of Object.entries(raw)) {
    if (!count || count <= 0) continue
    const canonical = LEGACY_VIDEO_TYPE_ALIAS[key] || (VIDEO_TYPE_LABEL[key] ? key : 'Other')
    counts.set(canonical, (counts.get(canonical) || 0) + count)
  }
  // 只列现行分类里真有视频的类别（无视频的不出现在构成里）
  const items = VIDEO_TYPE_OPTIONS.filter((o) => (counts.get(o.value) || 0) > 0)
    .map((o) => ({ key: o.value as string, label: o.label, count: counts.get(o.value) || 0 }))
    .sort((a, b) => b.count - a.count)
  const total = items.reduce((n, t) => n + t.count, 0) || 1
  const colors = rampColors(TYPE_COLORS.value, items.length)
  return items.map((t, i) => ({
    ...t,
    pct: Math.round((t.count / total) * 100),
    color: colors[i] || OTHER_COLOR,
  }))
})
const typeTotal = computed(() => typeRows.value.reduce((n, t) => n + t.count, 0))

/** 把「有序排列、各带颜色」的行拼成 conic-gradient —— 类型构成与分辨率构成共用同一个环形图。 */
function conicGradient(rows: { count: number; color: string }[]): Record<string, string> {
  const total = rows.reduce((n, r) => n + r.count, 0)
  if (!total) return {}
  let acc = 0
  const parts = rows.map((r) => {
    const start = (acc / total) * 100
    acc += r.count
    return `${r.color} ${start}% ${(acc / total) * 100}%`
  })
  return { background: `conic-gradient(${parts.join(', ')})` }
}

const roseStyle = computed(() => conicGradient(typeRows.value))

// ===== 04b 分辨率构成 =====
interface ResolutionRow {
  key: string
  label: string
  count: number
  pct: number
  color: string
}

/** 分辨率档位的色阶：以主题强调色向白插值，档位越高越深；未探测固定中性灰。 */
const RES_TINT: Record<string, number> = {
  '8k': 0,
  '4k': 0.18,
  '2k': 0.36,
  '1080p': 0.52,
  '720p': 0.66,
  '480p': 0.78,
  '360p': 0.85,
  '240p': 0.9,
  sd: 0.94,
}

const resolutionRows = computed<ResolutionRow[]>(() => {
  const raw = overview.value?.resolution_breakdown || {}
  const items = RESOLUTION_OPTIONS.map((o) => ({ ...o, count: raw[o.key] || 0 })).filter(
    (r) => r.count > 0,
  )
  const total = items.reduce((n, r) => n + r.count, 0)
  if (!total) return []
  const base = TYPE_COLORS.value[0]
  return items.map((r) => {
    const tint = RES_TINT[r.key]
    return {
      ...r,
      pct: Math.round((r.count / total) * 100),
      color: tint == null ? OTHER_COLOR : mixHex(base, '#ffffff', tint),
    }
  })
})

const resolutionTotal = computed(() => resolutionRows.value.reduce((n, r) => n + r.count, 0))
const resRoseStyle = computed(() => conicGradient(resolutionRows.value))

const resolutionTop = computed(() =>
  resolutionRows.value.reduce<ResolutionRow | null>(
    (best, r) => (!best || r.count > best.count ? r : best),
    null,
  ),
)

const resolutionNote = computed(() => {
  const top = resolutionTop.value
  if (!top) return ''
  const parts = [`以 ${top.label} 为主，占 ${top.pct}%`]
  const unprobed = overview.value?.resolution_breakdown?.unknown || 0
  if (unprobed) parts.push(`${unprobed} 条未探测`)
  return parts.join(' · ')
})

const resolutionAria = computed(() =>
  resolutionRows.value.map((r) => `${r.label} ${r.count} 条`).join('，'),
)

// ===== 05 观看习惯 =====
const watchBlock = computed(() => data.value?.watch)
function padHour(n: number) {
  return String(n).padStart(2, '0')
}
const watchTitle = computed(() => {
  const p = recap.value?.clock_peak_hour
  return p == null ? '还没有观看记录' : `你最常在 ${padHour(p)}:00 打开舞台`
})

// ===== 06 排行榜 =====
const RANK_TYPE_LABEL: Record<string, string> = {
  groups: '组合',
  artists: '艺人',
  songs: '歌曲',
  videos: '视频',
}
const rankTabs = computed(() =>
  rankKind.value === 'library'
    ? (['groups', 'artists', 'songs'] as const)
    : (['groups', 'artists', 'songs', 'videos'] as const),
)

function setRankKind(kind: 'library' | 'watch') {
  rankKind.value = kind
  // 收藏榜没有视频维度，切回组合
  if (kind === 'library' && rankType.value === 'videos') rankType.value = 'groups'
}

const rankRows = computed<StatsRankItem[]>(() => {
  const d = data.value
  if (!d) return []
  if (rankKind.value === 'watch') {
    const w = d.watch
    if (rankType.value === 'artists') return w.artists || []
    if (rankType.value === 'songs') return w.songs || []
    if (rankType.value === 'videos') return w.videos || w.items || []
    return w.groups || []
  }
  if (rankType.value === 'artists') return d.artists
  if (rankType.value === 'songs') return d.songs
  return d.groups
})

const top10 = computed(() => rankRows.value.slice(0, 10))
const rankMax = computed(() => Math.max(1, ...top10.value.map((r) => r.video_count)))
const rankTitle = computed(() =>
  rankKind.value === 'library' ? '你收得最多的' : '你看得最多的',
)

function barWidth(count: number) {
  return `${Math.max(6, (count / rankMax.value) * 100)}%`
}

// ===== 终章封底 =====
const summaryStats = computed(() => [
  { label: '条影像', value: (overview.value?.library_count ?? 0).toLocaleString() },
  { label: durationBig.value.unit, value: durationBig.value.num },
  { label: '占用空间', value: formatFileSize(overview.value?.total_size_bytes) },
])

const summaryAvatar = computed(() =>
  topGroup.value?.avatar_path ? groupsApi.avatarUrl(topGroup.value.id) : '',
)
// 封底双主角：年度艺人
const summarySecondAvatar = computed(() =>
  topArtist.value?.avatar_path ? artistsApi.avatarUrl(topArtist.value.id) : '',
)

// ===== 通用 =====
function displayName(row: StatsRankItem | null | undefined) {
  if (!row) return ''
  return row.chinese_name && row.chinese_name !== row.name ? row.chinese_name : row.name
}

function displaySub(row: StatsRankItem) {
  if (row.chinese_name && row.chinese_name !== row.name) return row.name
  return row.extra || ''
}

function formatTotalDuration(seconds?: number) {
  const s = Math.max(0, Math.floor(seconds || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h >= 1) return m ? `${h} 小时 ${m} 分钟` : `${h} 小时`
  return `${m} 分钟`
}

function initialOf(row: StatsRankItem) {
  return (displayName(row) || '?').trim().charAt(0).toUpperCase()
}

function rowAvatar(row: StatsRankItem) {
  if (!row.avatar_path) return ''
  const key = `${rankType.value}-${row.id}`
  if (brokenAvatars.value.has(key)) return ''
  if (rankType.value === 'artists') return artistsApi.avatarUrl(row.id)
  if (rankType.value === 'groups') return groupsApi.avatarUrl(row.id)
  return ''
}

function onAvatarError(row: StatsRankItem, kind?: string) {
  // kind：排行榜里随当前 tab 变化；章节里固定传 'groups'/'artists'/'subject'
  brokenAvatars.value = new Set(brokenAvatars.value).add(`${kind || rankType.value}-${row.id}`)
}

function goRankRow(row: StatsRankItem) {
  if (rankType.value === 'songs') return
  if (rankKind.value === 'watch' && rankType.value === 'videos') {
    router.push(videoPath(row.uid))
    return
  }
  if (rankType.value === 'artists') router.push(artistPath(row.uid))
  else if (rankType.value === 'groups') router.push(groupPath(row.uid))
}

function goGroup(uid: string) {
  router.push(groupPath(uid))
}
</script>

<template>
  <div class="stats-page">
    <SaHeader />

    <!-- 封面 -->
    <section class="cover">
      <div class="container-sa cover-inner">
        <div class="cover-kicker">{{ rangeLabel }}影像报告</div>
        <h1 class="cover-year">{{ range === 'year' ? yearLabel : 'ALL' }}</h1>
        <p v-if="overview" class="cover-preview">
          {{ overview.library_count }} 条影像
          · {{ durationBig.num }} {{ durationBig.unit }}
          · {{ formatFileSize(overview.total_size_bytes) }}
        </p>
        <div class="range-toggle" role="tablist" aria-label="统计口径">
          <button
            class="range-btn"
            :class="{ 'range-btn--on': range === 'year' }"
            type="button"
            @click="setRange('year')"
          >
            {{ yearLabel }} 年度
          </button>
          <button
            class="range-btn"
            :class="{ 'range-btn--on': range === 'all' }"
            type="button"
            @click="setRange('all')"
          >
            开馆以来
          </button>
        </div>
      </div>
    </section>

    <main class="container-sa report">
      <div v-if="loading && !data" class="empty">加载中…</div>
      <div v-else-if="!data" class="empty">统计加载失败，请稍后重试</div>
      <template v-else>
        <!-- 01 年度总览 -->
        <StatsChapter no="01" kicker="年度总览" :title="overviewChapterTitle">
          <div class="bignums">
            <div class="bignum">
              <div class="bignum-value">
                <b>{{ overview?.library_count ?? 0 }}</b><span>条</span>
              </div>
              <p class="bignum-note">{{ countNote }}</p>
            </div>
            <div class="bignum">
              <div class="bignum-value">
                <b>{{ durationBig.num }}</b><span>{{ durationBig.unit }}</span>
              </div>
              <p class="bignum-note">{{ durationAnalogy }}</p>
            </div>
            <div class="bignum">
              <div class="bignum-value">
                <b>{{ formatFileSize(overview?.total_size_bytes) }}</b>
              </div>
              <p class="bignum-note">{{ sizeNote }}</p>
            </div>
          </div>
        </StatsChapter>

        <!-- 02 组合 -->
        <StatsChapter
          v-if="topGroup"
          no="02"
          :kicker="`${rangeLabel}组合`"
          :title="groupChapterTitle"
        >
          <div class="yeartop">
            <button class="yeartop-main" type="button" @click="goGroup(topGroup.uid)">
              <span class="yeartop-avatar">
                <img
                  v-if="topGroup.avatar_path && !brokenAvatars.has(`groups-${topGroup.id}`)"
                  :src="groupsApi.avatarUrl(topGroup.id)"
                  :alt="displayName(topGroup)"
                  @error="onAvatarError(topGroup, 'groups')"
                />
                <span v-else>{{ initialOf(topGroup) }}</span>
              </span>
              <span class="yeartop-info">
                <b class="yeartop-name">{{ displayName(topGroup) }}</b>
                <span v-if="displaySub(topGroup)" class="yeartop-sub">{{ displaySub(topGroup) }}</span>
                <span class="yeartop-meta">
                  {{ topGroup.video_count }} 条影像 · {{ formatTotalDuration(topGroup.duration_seconds) }}
                </span>
              </span>
            </button>
            <ul v-if="runnerUps.length" class="yeartop-rest">
              <li v-for="(g, i) in runnerUps" :key="g.id">
                <button type="button" @click="goGroup(g.uid)">
                  <span class="yr-n">{{ i + 2 }}</span>
                  <span class="yr-avatar">
                    <img
                      v-if="g.avatar_path && !brokenAvatars.has(`groups-${g.id}`)"
                      :src="groupsApi.avatarUrl(g.id)"
                      :alt="displayName(g)"
                      @error="onAvatarError(g, 'groups')"
                    />
                    <span v-else>{{ initialOf(g) }}</span>
                  </span>
                  <span class="yr-name">{{ displayName(g) }}</span>
                  <span class="yr-count">{{ g.video_count }} 条</span>
                </button>
              </li>
            </ul>
          </div>
        </StatsChapter>

        <!-- 03 艺人（参演 ∪ 直拍对象口径） -->
        <StatsChapter
          v-if="topArtist"
          no="03"
          :kicker="`${rangeLabel}艺人`"
          :title="artistChapterTitle"
        >
          <div class="yeartop">
            <button class="yeartop-main" type="button" @click="goArtist(topArtist.uid)">
              <span class="yeartop-avatar">
                <img
                  v-if="topArtist.avatar_path && !brokenAvatars.has(`artists-${topArtist.id}`)"
                  :src="artistsApi.avatarUrl(topArtist.id)"
                  :alt="displayName(topArtist)"
                  @error="onAvatarError(topArtist, 'artists')"
                />
                <span v-else>{{ initialOf(topArtist) }}</span>
              </span>
              <span class="yeartop-info">
                <b class="yeartop-name">{{ displayName(topArtist) }}</b>
                <span v-if="displaySub(topArtist)" class="yeartop-sub">{{ displaySub(topArtist) }}</span>
                <span class="yeartop-meta">
                  {{ topArtist.video_count }} 条影像 · {{ formatTotalDuration(topArtist.duration_seconds) }}
                </span>
              </span>
            </button>
            <ul v-if="artistRunnerUps.length" class="yeartop-rest">
              <li v-for="(a, i) in artistRunnerUps" :key="a.id">
                <button type="button" @click="goArtist(a.uid)">
                  <span class="yr-n">{{ i + 2 }}</span>
                  <span class="yr-avatar">
                    <img
                      v-if="a.avatar_path && !brokenAvatars.has(`artists-${a.id}`)"
                      :src="artistsApi.avatarUrl(a.id)"
                      :alt="displayName(a)"
                      @error="onAvatarError(a, 'artists')"
                    />
                    <span v-else>{{ initialOf(a) }}</span>
                  </span>
                  <span class="yr-name">{{ displayName(a) }}</span>
                  <span class="yr-count">{{ a.video_count }} 条</span>
                </button>
              </li>
            </ul>
          </div>
        </StatsChapter>

        <!-- 04 镜头焦点（直拍对象口径） -->
        <StatsChapter
          v-if="topSubjects.length"
          no="04"
          kicker="镜头焦点"
          :title="subjectChapterTitle"
        >
          <ol class="rank-list">
            <li v-for="(row, i) in topSubjects.slice(0, 5)" :key="`subject-${row.id}`">
              <button
                class="rank-row"
                :class="{ 'rank-row--top': i === 0 }"
                type="button"
                @click="goArtist(row.uid)"
              >
                <span class="rank-n">{{ i + 1 }}</span>
                <span class="rank-avatar">
                  <img
                    v-if="row.avatar_path && !brokenAvatars.has(`subject-${row.id}`)"
                    :src="artistsApi.avatarUrl(row.id)"
                    :alt="displayName(row)"
                    @error="onAvatarError(row, 'subject')"
                  />
                  <span v-else>{{ initialOf(row) }}</span>
                </span>
                <span class="rank-body">
                  <span class="rank-name">{{ displayName(row) }}</span>
                  <span class="rank-bar">
                    <span class="rank-fill" :style="{ width: subjectBar(row.video_count) }" />
                  </span>
                </span>
                <span class="rank-meta">
                  <b>{{ row.video_count }}</b>
                  <small>条</small>
                </span>
              </button>
            </li>
          </ol>
          <p class="focus-note">口径：仅统计「直拍对象」，不含参演关联；点按查看艺人页</p>
        </StatsChapter>

        <!-- 05 收藏节奏 -->
        <StatsChapter
          no="03"
          kicker="收藏节奏"
          :title="`过去 365 天，你收了 ${heatTotal} 条影像`"
        >
          <StatsHeatmap :days="recap?.heatmap || []" />
        </StatsChapter>

        <!-- 06 收藏构成 -->
        <StatsChapter
          v-if="typeRows.length"
          no="06"
          kicker="收藏构成"
          :title="`你收的最多的是${typeRows[0].label}，占 ${typeRows[0].pct}%`"
        >
          <div class="mix">
            <div class="rose" :style="roseStyle">
              <div class="rose-core">
                <b>{{ typeTotal }}</b>
                <span>条舞台</span>
              </div>
            </div>
            <ul class="mix-legend">
              <li v-for="t in typeRows" :key="t.key">
                <i :style="{ background: t.color }" />
                <span>{{ t.label }}</span>
                <b>{{ t.count }}</b>
                <em>{{ t.pct }}%</em>
              </li>
            </ul>
          </div>

          <div v-if="resolutionRows.length" class="resblock">
            <div class="resblock-head">
              <span>分辨率</span>
              <em>{{ resolutionNote }}</em>
            </div>
            <div class="mix">
              <div
                class="rose"
                :style="resRoseStyle"
                role="img"
                :aria-label="`分辨率构成：${resolutionAria}`"
              >
                <div class="rose-core">
                  <b>{{ resolutionTotal }}</b>
                  <span>条影像</span>
                </div>
              </div>
              <ul class="mix-legend">
                <li v-for="r in resolutionRows" :key="r.key">
                  <i :style="{ background: r.color }" />
                  <span>{{ r.label }}</span>
                  <b>{{ r.count }}</b>
                  <em>{{ r.pct }}%</em>
                </li>
              </ul>
            </div>
          </div>
        </StatsChapter>

        <!-- 07 观看习惯 -->
        <StatsChapter no="07" kicker="观看时间" :title="watchTitle">
          <StatsClock :hours="recap?.clock || []" :peak-hour="recap?.clock_peak_hour ?? null" />
          <p v-if="(watchBlock?.play_count || 0) > 0" class="watch-kpis">
            看了 <b>{{ watchBlock?.play_count ?? 0 }}</b> 次
            · 共 {{ formatTotalDuration(watchBlock?.seconds_watched) }}
            · 看完 <b>{{ watchBlock?.completed_count ?? 0 }}</b> 部
          </p>
        </StatsChapter>

        <!-- 08 排行榜 -->
        <StatsChapter no="08" kicker="排行榜" :title="rankTitle">
          <div class="rank-tabs">
            <div class="seg" role="tablist">
              <button
                class="seg-btn"
                :class="{ 'seg-btn--on': rankKind === 'library' }"
                @click="setRankKind('library')"
              >
                收藏榜
              </button>
              <button
                class="seg-btn"
                :class="{ 'seg-btn--on': rankKind === 'watch' }"
                @click="setRankKind('watch')"
              >
                观看榜
              </button>
            </div>
            <div class="seg" role="tablist">
              <button
                v-for="t in rankTabs"
                :key="t"
                class="seg-btn"
                :class="{ 'seg-btn--on': rankType === t }"
                @click="rankType = t"
              >
                {{ RANK_TYPE_LABEL[t] }}
              </button>
            </div>
          </div>

          <ol v-if="top10.length" class="rank-list">
            <li v-for="(row, i) in top10" :key="`${rankType}-${row.id}`">
              <button
                class="rank-row"
                :class="{ 'rank-row--top': i === 0 }"
                type="button"
                :disabled="rankType === 'songs'"
                @click="goRankRow(row)"
              >
                <span class="rank-n">{{ i + 1 }}</span>
                <span class="rank-avatar">
                  <img
                    v-if="rowAvatar(row)"
                    :src="rowAvatar(row)"
                    :alt="displayName(row)"
                    @error="onAvatarError(row)"
                  />
                  <span v-else>{{ initialOf(row) }}</span>
                </span>
                <span class="rank-body">
                  <span class="rank-name">{{ displayName(row) }}</span>
                  <span class="rank-bar">
                    <span class="rank-fill" :style="{ width: barWidth(row.video_count) }" />
                  </span>
                </span>
                <span class="rank-meta">
                  <b>{{ row.video_count }}</b>
                  <small>{{ rankKind === 'watch' ? '次' : '条' }}</small>
                </span>
              </button>
            </li>
          </ol>
          <p v-else class="empty">这个维度还没有可展示的内容</p>
        </StatsChapter>

        <!-- 终端 · 封底 -->
        <section class="finale">
          <StatsSummaryCard
            :year="range === 'year' ? yearLabel : 'ALL'"
            :top-name="topGroup ? displayName(topGroup) : '—'"
            :top-label="`${rangeLabel}组合`"
            :avatar-url="summaryAvatar"
            :second-name="topArtist ? displayName(topArtist) : undefined"
            :second-avatar-url="summarySecondAvatar"
            :stats="summaryStats"
          />
        </section>
      </template>
    </main>
  </div>
</template>

<style scoped>
.container-sa {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px;
}
.stats-page {
  min-height: 100dvh;
  background: var(--sa-bg);
}

/* ===== 封面 ===== */
.cover {
  background:
    radial-gradient(900px 340px at 12% -10%, color-mix(in srgb, var(--sa-accent) 9%, transparent), transparent 62%),
    radial-gradient(700px 280px at 95% 0%, color-mix(in srgb, var(--sa-accent) 6%, transparent), transparent 55%);
  border-bottom: 1px solid var(--sa-border-subtle);
}
.cover-inner {
  padding-top: 56px;
  padding-bottom: 48px;
}
.cover-kicker {
  font-size: 12px;
  letter-spacing: 0.32em;
  text-transform: uppercase;
  color: var(--sa-accent);
  font-weight: 700;
}
.cover-year {
  margin: 10px 0 0;
  font-size: clamp(4rem, 10vw, 7.2rem);
  font-weight: 800;
  line-height: 0.95;
  letter-spacing: -0.05em;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-primary);
}
.cover-preview {
  margin: 18px 0 0;
  font-size: 14px;
  color: var(--sa-text-secondary);
  font-variant-numeric: tabular-nums;
}
/* 口径切换：当年 / 开馆以来 */
.range-toggle {
  margin-top: 22px;
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
}
.range-btn {
  padding: 6px 16px;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.range-btn--on {
  color: var(--sa-accent);
  background: color-mix(in srgb, var(--sa-accent) 12%, transparent);
  font-weight: 600;
}
/* 镜头焦点章口径说明 */
.focus-note {
  margin: 14px 0 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

/* ===== 正文：章节流 ===== */
.report {
  display: flex;
  flex-direction: column;
  gap: 100px;
  padding-top: 64px;
  padding-bottom: 96px;
}

/* ===== 01 三个大数字：无卡片框，靠字号与留白分层 ===== */
.bignums {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 24px 40px;
}
.bignum {
  min-width: 0;
}
.bignum-value {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.bignum-value b {
  font-size: clamp(2.4rem, 5vw, 4rem);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.04em;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-primary);
  overflow-wrap: anywhere;
}
.bignum-value span {
  font-size: 1rem;
  color: var(--sa-text-secondary);
  font-weight: 650;
}
.bignum-note {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}

/* ===== 02 年度组合：Top1 大图 + 2-5 名纵列 ===== */
.yeartop {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}
.yeartop-main {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 26px 28px;
  border-radius: 20px;
  border: 1px solid var(--sa-border-subtle);
  background:
    radial-gradient(320px 160px at 88% 0%, color-mix(in srgb, var(--sa-accent) 8%, transparent), transparent 70%),
    var(--sa-elevated);
  cursor: pointer;
  text-align: left;
  min-width: 0;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.yeartop-main:hover {
  border-color: var(--sa-accent-border);
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.07);
}
.yeartop-avatar {
  width: 112px;
  height: 112px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--sa-subtle);
  display: grid;
  place-items: center;
  font-size: 2.4rem;
  font-weight: 800;
  color: var(--sa-accent);
  border: 3px solid var(--sa-accent-border);
  flex: none;
}
.yeartop-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.yeartop-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.yeartop-name {
  font-size: clamp(1.4rem, 3vw, 1.9rem);
  font-weight: 800;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}
.yeartop-sub {
  font-size: 13px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yeartop-meta {
  margin-top: 6px;
  font-size: 13px;
  color: var(--sa-text-secondary);
  font-variant-numeric: tabular-nums;
}
.yeartop-rest {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.yeartop-rest button {
  width: 100%;
  display: grid;
  grid-template-columns: 20px 40px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 10px 14px;
  border-radius: 14px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
  cursor: pointer;
  text-align: left;
  transition: border-color 0.2s ease;
}
.yeartop-rest button:hover {
  border-color: var(--sa-accent-border);
}
.yr-n {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.yr-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--sa-subtle);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 13px;
  color: var(--sa-accent);
}
.yr-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.yr-name {
  font-size: 14px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yr-count {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* ===== 04 类型构成 ===== */
.mix {
  display: flex;
  align-items: center;
  gap: 48px;
  margin-top: 8px;
}
.rose {
  flex: none;
  width: 180px;
  height: 180px;
  border-radius: 50%;
  position: relative;
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
}
.rose-core {
  position: absolute;
  inset: 20%;
  border-radius: 50%;
  background: var(--sa-elevated);
  display: grid;
  place-content: center;
  text-align: center;
}
.rose-core b {
  font-size: 1.6rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}
.rose-core span {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.mix-legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
  min-width: 0;
  flex: 1;
  max-width: 420px;
}
.mix-legend li {
  display: grid;
  grid-template-columns: 10px minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  font-size: 13px;
}
.mix-legend i {
  width: 10px;
  height: 10px;
  border-radius: 99px;
}
.mix-legend span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mix-legend b {
  font-variant-numeric: tabular-nums;
}
.mix-legend em {
  color: var(--sa-text-tertiary);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

/* 分辨率构成（同章节第二个统计块） */
.resblock {
  margin-top: 30px;
  padding-top: 24px;
  border-top: 1px solid var(--sa-border-subtle);
}
.resblock-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px 14px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.resblock-head > span {
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.01em;
}
.resblock-head em {
  font-style: normal;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
}
/* 分辨率用的也是 .mix（环形图 + 图例），与类型构成同一套排版；
   这里只清掉 .mix 自带的顶部间距（标题行已给了 margin-bottom）。 */
.resblock .mix {
  margin-top: 0;
}

/* ===== 05 观看习惯 ===== */
.watch-kpis {
  margin: 18px 0 0;
  font-size: 13px;
  color: var(--sa-text-secondary);
  font-variant-numeric: tabular-nums;
}
.watch-kpis b {
  color: var(--sa-text-primary);
}

/* ===== 06 排行榜 ===== */
.rank-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 12px;
  margin-bottom: 18px;
}
.seg {
  display: inline-flex;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  overflow: hidden;
  background: var(--sa-elevated);
}
.seg-btn {
  border: 0;
  background: transparent;
  color: var(--sa-text-secondary);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: color 0.15s ease, background 0.15s ease;
}
.seg-btn--on {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
}
.rank-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: 1fr;
  gap: 2px;
}
.rank-row {
  width: 100%;
  display: grid;
  grid-template-columns: 26px 44px minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  padding: 8px 8px;
  border-radius: 12px;
  cursor: pointer;
}
.rank-row:hover:not(:disabled) {
  background: var(--sa-elevated);
}
.rank-row:disabled {
  cursor: default;
}
.rank-row--top {
  padding: 16px 12px;
  margin-bottom: 6px;
  border: 1px solid var(--sa-accent-border);
  background: var(--sa-elevated);
}
.rank-n {
  font-size: 13px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.rank-row--top .rank-n {
  color: var(--sa-accent);
  font-weight: 800;
  font-size: 1.2rem;
}
.rank-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  overflow: hidden;
  background: var(--sa-subtle);
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 14px;
  color: var(--sa-accent);
}
.rank-row--top .rank-avatar {
  width: 56px;
  height: 56px;
  border: 2px solid var(--sa-accent-border);
  font-size: 1.2rem;
}
.rank-avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.rank-body {
  min-width: 0;
}
.rank-name {
  display: block;
  font-weight: 650;
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-row--top .rank-name {
  font-size: 1.05rem;
  font-weight: 800;
  letter-spacing: -0.01em;
}
.rank-bar {
  display: block;
  margin-top: 7px;
  height: 4px;
  border-radius: 99px;
  background: var(--sa-subtle);
  overflow: hidden;
}
.rank-fill {
  display: block;
  height: 100%;
  border-radius: 99px;
  background: var(--sa-accent);
}
.rank-meta {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.rank-meta b {
  font-size: 14px;
}
.rank-row--top .rank-meta b {
  font-size: 1.2rem;
  font-weight: 800;
}
.rank-meta small {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}

/* ===== 终章 ===== */
.finale {
  padding-top: 8px;
}

/* ===== 通用 ===== */
.empty {
  color: var(--sa-text-tertiary);
  font-size: 13px;
}

/* ===== 移动端 ===== */
@media (max-width: 768px) {
  .container-sa {
    padding: 0 16px;
  }
  .cover-inner {
    padding-top: 36px;
    padding-bottom: 30px;
  }
  .report {
    gap: 56px;
    padding-top: 40px;
    padding-bottom: 64px;
  }
  .bignums {
    grid-template-columns: 1fr;
    gap: 28px;
  }
  .yeartop {
    grid-template-columns: 1fr;
  }
  .yeartop-main {
    flex-direction: column;
    text-align: center;
    gap: 14px;
    padding: 22px 18px;
  }
  .yeartop-info {
    align-items: center;
  }
  .yeartop-sub {
    white-space: normal;
  }
  .mix {
    flex-direction: column;
    align-items: center;
    gap: 28px;
  }
  .mix-legend {
    width: 100%;
    max-width: none;
  }
  .resblock {
    margin-top: 24px;
    padding-top: 20px;
  }
  /* 分辨率环形图：窄屏跟类型图一样「环在上、图例在下」，环略小一档省高度 */
  .resblock .mix {
    gap: 20px;
  }
  .resblock .rose {
    width: 150px;
    height: 150px;
  }
  .resblock .mix-legend li {
    font-size: 12px;
  }
  .rank-tabs {
    gap: 8px;
  }
  .rank-row {
    grid-template-columns: 22px 40px minmax(0, 1fr) auto;
    gap: 10px;
  }
  .rank-avatar {
    width: 40px;
    height: 40px;
  }
  .rank-row--top .rank-avatar {
    width: 48px;
    height: 48px;
  }
  .records-grid {
    grid-template-columns: 1fr;
  }
}
</style>
