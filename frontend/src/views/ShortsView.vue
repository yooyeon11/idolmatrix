<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Hls from 'hls.js'
import { playbackApi } from '@/api/playback'
import { dashboardApi } from '@/api/dashboard'
import type { PlaybackSession } from '@/types/models'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import ShortsCard from '@/components/shorts/ShortsCard.vue'
import { musicVideosApi } from '@/api/musicVideos'
import { artistPath } from '@/utils/routes'
import type { MusicVideo } from '@/types/models'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'
import { CloseOutlined, MovieOutlined, SearchOutlined } from '@/components/icons'
import { formatDate, formatDuration } from '@/utils/format'

const router = useRouter()
const message = useMessage()
const loading = ref(false)
const items = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const searchQuery = ref('')
const loadError = ref('')
const sortOrder = ref('created_desc')
// 与浏览页排序选项保持一致
const SORT_OPTIONS = [
  { value: 'created_desc', label: '最近上传' },
  { value: 'performance_date', label: '表演日期' },
  { value: 'published_date', label: '上传日期' },
]

/** 「表演日期 / 上传日期」= 单选项，再点一次在正序/倒序之间切换。 */
const DATE_SORTS = ['performance_date', 'published_date'] as const
function toggleDateSort(base: string, current: string): string {
  const isAsc = current === `${base}_asc`
  return isAsc ? `${base}_desc` : `${base}_asc`
}


// ===== 双模态：默认全宽网格 / 点开卡片后左侧展开沉浸画框 =====
const cinemaOpen = ref(false)
const activeIndex = ref(0)
const activeItem = computed<MusicVideo | null>(() => items.value[activeIndex.value] ?? null)

// 卡片 DOM 引用：用于「打开播放器后把被点的那张卡留在原屏幕位置」
const cardEls = ref<HTMLElement[]>([])
function setCardEl(el: unknown, i: number) {
  const node = (el as { $el?: HTMLElement } | null)?.$el ?? (el as HTMLElement | null)
  if (node) cardEls.value[i] = node
}
/** 布局变化后把被点的卡片留在原来的屏幕位置（同帧完成，用户看不到跳变） */
async function keepAnchor(index: number) {
  const node = cardEls.value[index]
  if (!node) return
  const before = node.getBoundingClientRect().top
  await nextTick()
  const after = node.getBoundingClientRect().top
  const delta = after - before
  if (Math.abs(delta) > 1) window.scrollBy({ top: delta, behavior: 'auto' })
}

async function openShort(index: number) {
  activeIndex.value = index
  if (!cinemaOpen.value) {
    // 先量，再切布局，nextTick 里一次校正——不等待过渡结束（旧实现会先动画再跳）
    const node = cardEls.value[index]
    const before = node ? node.getBoundingClientRect().top : null
    cinemaOpen.value = true
    await nextTick()
    if (before != null && node) {
      const delta = node.getBoundingClientRect().top - before
      if (Math.abs(delta) > 1) window.scrollBy({ top: delta, behavior: 'auto' })
    }
    return
  }
  await keepAnchor(index)
}

function exitCinema() {
  cinemaOpen.value = false
  teardownPlayer()
}

function stepShort(delta: number) {
  const n = items.value.length
  if (!n) return
  activeIndex.value = (activeIndex.value + delta + n) % n
}

function onKeydown(e: KeyboardEvent) {
  if (!cinemaOpen.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    exitCinema()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    stepShort(-1)
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    stepShort(1)
  }
}


// ===== 顶部画框展示用的派生字段 =====
const activePoster = computed(() =>
  activeItem.value
    ? musicVideosApi.thumbnailUrl(
        activeItem.value.id,
        activeItem.value.file_hash || activeItem.value.file_size,
        900,
      )
    : '',
)
const activeResLabel = computed(() => {
  const h = activeItem.value?.height ?? 0
  const w = activeItem.value?.width ?? 0
  const shortSide = Math.min(h, w)
  if (!shortSide) return ''
  return shortSide >= 2160 ? '4K' : shortSide >= 1440 ? '2K' : shortSide >= 1080 ? '1080P' : '720P'
})
// 画框 / 卡片共用的小格式化
function artistName(v: MusicVideo | null) {
  return v?.subject_artist_name || v?.subject_artist_chinese_name || ''
}
function dateLabel(v: MusicVideo | null) {
  if (!v) return ''
  const d = formatDate(v.performance_date || v.release_date)
  return d === '--' ? '' : d
}
function durationLabel(v: MusicVideo | null) {
  return v ? formatDuration(v.duration) : ''
}

// ===== 移动端紧凑工具栏（与浏览页同一套交互）=====
const narrowMq = window.matchMedia('(max-width: 640px)')
const isNarrow = ref(narrowMq.matches)
function onNarrowChange(e: MediaQueryListEvent) {
  isNarrow.value = e.matches
}

// ===== 顶部控制岛：两个自绘下拉（复刻样板）=====
type MenuKey = 'sort' | 'density'
const openMenu = ref<MenuKey | ''>('')
function toggleMenu(k: MenuKey) {
  openMenu.value = openMenu.value === k ? '' : k
}
function closeMenus() {
  openMenu.value = ''
}
function onDocumentClick(e: MouseEvent) {
  closeMenus()
  if (!cinemaOpen.value) return
  const t = e.target as HTMLElement | null
  if (!t) return
  // 播放器内部 / 点另一张卡（切换）都不算"点空白"
  if (t.closest('.cinema-viewport')) return
  if (t.closest('.shorts-card')) return
  exitCinema()
}
const sortLabel = computed(() => {
  const raw = sortOrder.value
  const bare = raw.replace(/_(asc|desc)$/, '')
  const opt = SORT_OPTIONS.find((o) => o.value === raw) ?? SORT_OPTIONS.find((o) => o.value === bare)
  const arrow = raw.endsWith('_asc') ? ' ↑' : raw.endsWith('_desc') ? ' ↓' : ''
  return (opt?.label ?? '最近上传') + arrow
})
function pickSort(v: string) {
  const bare = v.replace(/_(asc|desc)$/, '')
  if (DATE_SORTS.includes(bare as (typeof DATE_SORTS)[number])
      && sortOrder.value.replace(/_(asc|desc)$/, '') === bare) {
    sortOrder.value = toggleDateSort(bare, sortOrder.value)
  } else if (DATE_SORTS.includes(v as (typeof DATE_SORTS)[number])) {
    sortOrder.value = `${v}_desc`
  } else {
    sortOrder.value = v
  }
  closeMenus()
}

const { density, setDensity } = useGridDensity()
// 密度档位沿用全局三档，文案按样板口径写
const DENSITY_OPTIONS = [
  { value: 'loose', label: '舒适 5列' },
  { value: 'standard', label: '默认 6列' },
  { value: 'compact', label: '紧凑 7列' },
] as const
const densityLabel = computed(
  () => DENSITY_OPTIONS.find((o) => o.value === density.value)?.label ?? '默认 6列',
)
function pickDensity(v: 'compact' | 'standard' | 'loose') {
  setDensity(v)
  closeMenus()
}
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl)
let loadSeq = 0
let searchDebounce: ReturnType<typeof setTimeout> | undefined
// 上次真正加载用的 page_size：沉浸态会改变列数（5→3），不能让它触发重载
let lastLoadedPageSize = 0

async function load(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await musicVideosApi.list({
      ingestion_status: 'library',
      is_short: true,
      page: targetPage,
      page_size: pageSize.value,
      q: searchQuery.value.trim() || undefined,
      sort: sortOrder.value === 'created_desc' ? undefined : sortOrder.value,
    })
    if (seq !== loadSeq) return
    total.value = res.total
    items.value = res.items
    cardEls.value = []
    pageNum.value = targetPage
    lastLoadedPageSize = pageSize.value
    // 数据换了，选中的下标可能越界
    if (activeIndex.value >= items.value.length) activeIndex.value = 0
    if (!items.value.length) cinemaOpen.value = false
  } catch (e) {
    if (seq !== loadSeq) return
    loadError.value = (e as Error).message
    message.error(loadError.value)
    if (targetPage === 1) items.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p: number) {
  cinemaOpen.value = false
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

// page_size 变化（首次测量完成 / 视口跨列数边界 / 切换密度档位）：回到第 1 页重新加载
// 沉浸态下右侧会收缩为 2~3 列，page_size 随之变化 —— 那是布局副作用，不该重载数据
watch(pageSize, (v) => {
  if (v > 0 && !cinemaOpen.value) void load(1)
})
// 退出沉浸：列数回到全宽档后，若与当前已加载的页容量不同，再按新容量重载一次
watch(cinemaOpen, (open) => {
  if (!open && pageSize.value > 0 && pageSize.value !== lastLoadedPageSize) void load(1)
})
watch(searchQuery, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => void load(1), 350)
})
watch(sortOrder, () => void load(1))

// ===== 分页条（样板同款：总数 + 页码 + 跳页）=====
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / Math.max(1, pageSize.value))))
const jumpPage = ref('')
const pageNumbers = computed<(number | string)[]>(() => {
  const n = totalPages.value
  const cur = pageNum.value
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const out: (number | string)[] = [1]
  const start = Math.max(2, cur - 1)
  const end = Math.min(n - 1, cur + 1)
  if (start > 2) out.push('…')
  for (let i = start; i <= end; i++) out.push(i)
  if (end < n - 1) out.push('…')
  out.push(n)
  return out
})
function doJump() {
  const n = Number(jumpPage.value)
  if (!Number.isFinite(n)) return
  const target = Math.min(Math.max(1, Math.trunc(n)), totalPages.value)
  jumpPage.value = ''
  onPageChange(target)
}
function goArtist(uid: string) {
  void router.push(artistPath(uid))
}
// ===== 左侧画框内直接播放（竖版 9:16 / 横版 16:9 自动切换）=====
const videoEl = ref<HTMLVideoElement | null>(null)
const playerLoading = ref(false)
const playerError = ref('')
let hls: Hls | null = null
let session: PlaybackSession | null = null
let playbackGen = 0

// 画框内播放同样计入观看：只在真实 @play 之后上报，暂停/关闭/完播补一条。
let watchTimer: number | null = null
let watchVideoId = 0
let watchPosition = 0
let watchDuration = 0

function sendWatch(playing: boolean, ended = false) {
  if (!watchVideoId) return
  dashboardApi
    .watchPing({
      music_video_id: watchVideoId,
      position: watchPosition,
      duration: watchDuration || undefined,
      playing,
      ended,
    })
    .catch(() => {})
}

function stopWatchTimer() {
  if (watchTimer !== null) {
    window.clearInterval(watchTimer)
    watchTimer = null
  }
}

function onCinemaPlay() {
  playerError.value = ''
  const v = activeItem.value
  if (!v) return
  if (watchVideoId && watchVideoId !== v.id) sendWatch(false)
  watchVideoId = v.id
  const el = videoEl.value
  watchDuration = Number(el?.duration) || v.duration || 0
  watchPosition = Number(el?.currentTime) || 0
  sendWatch(true)
  stopWatchTimer()
  watchTimer = window.setInterval(() => {
    if (!videoEl.value || videoEl.value.paused) return
    sendWatch(true)
  }, 15_000)
}

function onCinemaPause() {
  if (!watchVideoId) return
  sendWatch(false)
  stopWatchTimer()
}

function onCinemaTime() {
  const el = videoEl.value
  if (!el || !watchVideoId) return
  watchPosition = Number(el.currentTime) || 0
  if (Number(el.duration) > 0) watchDuration = Number(el.duration)
}

function onCinemaEnded() {
  sendWatch(false, true)
  stopWatchTimer()
}

/** 与服务端/主播放器同口径的最小能力上报。不能只发 {ua,dpr}：后端读不到，
 * 会退回「只支持 H.264/MP4、1080 上限」，HEVC/AV1 全被误判转码。 */
function clientCaps(): Record<string, unknown> {
  const ua = navigator.userAgent || ''
  const probe = document.createElement('video')
  const canProbably = (mime: string) => (probe.canPlayType?.(mime) || '') === 'probably'
  const canMaybe = (mime: string) => {
    const r = probe.canPlayType?.(mime) || ''
    return r === 'probably' || r === 'maybe'
  }
  const mseOk = (mime: string) => {
    try {
      return typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(mime)
    } catch {
      return false
    }
  }
  const videoCodecs: string[] = []
  if (mseOk('video/mp4; codecs="avc1.42E01E"')) videoCodecs.push('h264')
  if (mseOk('video/mp4; codecs="av01.0.08M.08"')) videoCodecs.push('av1')
  if (mseOk('video/mp4; codecs="vp09.00.51.08"')) videoCodecs.push('vp9')
  const audioCodecs: string[] = []
  if (mseOk('audio/mp4; codecs="mp4a.40.2"')) audioCodecs.push('aac')
  if (mseOk('audio/mp4; codecs="opus"')) audioCodecs.push('opus')
  if (mseOk('audio/mp4; codecs="mp3"')) audioCodecs.push('mp3')
  const progressiveVideo: string[] = []
  if (canProbably('video/mp4; codecs="avc1.42E01E"')) progressiveVideo.push('h264')
  if (canMaybe('video/mp4; codecs="av01.0.05M.08"')) progressiveVideo.push('av1')
  if (canMaybe('video/mp4; codecs="vp09.00.51.08"')) progressiveVideo.push('vp9')
  const progressiveAudio: string[] = []
  if (canMaybe('audio/mp4; codecs="mp4a.40.2"')) progressiveAudio.push('aac')
  if (canMaybe('audio/mp4; codecs="opus"')) progressiveAudio.push('opus')
  if (canMaybe('audio/mpeg')) progressiveAudio.push('mp3')
  return {
    supported_video_codecs: videoCodecs,
    supported_audio_codecs: audioCodecs,
    progressive_video_codecs: progressiveVideo,
    progressive_audio_codecs: progressiveAudio,
    supported_containers: ['mp4', 'm4v', 'mov'],
    supports_hls: Hls.isSupported() || nativeHlsSupported(),
    supports_fmp4: mseOk('video/mp4; codecs="avc1.42E01E"'),
    user_agent: ua,
  }
}

function nativeHlsSupported(): boolean {
  try {
    return !!document.createElement('video').canPlayType('application/vnd.apple.mpegurl')
  } catch {
    return false
  }
}

function prefersProgressive(): boolean {
  return /VivoBrowser|OppoBrowser|HeyTapBrowser|MiuiBrowser|XiaoMi|HuaweiBrowser|HonorBrowser|UCBrowser|Quark/i.test(
    navigator.userAgent || '',
  )
}

/** compat 只服务 transcode 会话；签名 query 必须保留。 */
function compatUrl(s: PlaybackSession): string {
  if (s.play_mode !== 'transcode' || !s.manifest_url) return ''
  const m = s.manifest_url.match(/\/sessions\/(\d+)\//)
  if (!m) return ''
  const q = s.manifest_url.includes('?')
    ? s.manifest_url.slice(s.manifest_url.indexOf('?'))
    : ''
  return `/api/playback/sessions/${m[1]}/compat.mp4${q}`
}

/** 横版判定：优先用实际像素尺寸，缺失时退回缩略图宽高比 */
const isLandscape = computed(() => {
  const v = activeItem.value
  if (!v) return false
  const w = Number(v.width || 0)
  const h = Number(v.height || 0)
  if (w && h) return w > h
  return false
})

function teardownPlayer() {
  // 让所有在途请求过期：关闭画框/快速切下一条时，迟到响应不得写回全局 session。
  playbackGen += 1
  if (watchVideoId) {
    sendWatch(false)
    stopWatchTimer()
    watchVideoId = 0
    watchPosition = 0
    watchDuration = 0
  }
  if (hls) {
    try {
      hls.destroy()
    } catch {
      /* 忽略 */
    }
    hls = null
  }
  const el = videoEl.value
  if (el) {
    try {
      el.pause()
      el.removeAttribute('src')
      el.load()
    } catch {
      /* 忽略 */
    }
  }
  if (session) {
    void playbackApi.stop(session.session_id, { force: true }).catch(() => {})
    session = null
  }
}

let compatUsed = false

function attachSession(el: HTMLVideoElement, s: PlaybackSession, gen: number) {
  const compat = compatUrl(s)
  if (s.manifest_url) {
    if (compat && (prefersProgressive() || (!Hls.isSupported() && !nativeHlsSupported()))) {
      compatUsed = true
      el.src = compat
      el.load()
      return
    }
    if (Hls.isSupported()) {
      const instance = new Hls({ enableWorker: true })
      hls = instance
      instance.on(Hls.Events.ERROR, (_evt, data) => {
        if (!data.fatal || gen !== playbackGen || hls !== instance) return
        // 清单/MSE 失败：同一会话还有普通 MP4 时直接换 compat，不再白等一次 720p
        if (compat && !compatUsed) {
          compatUsed = true
          try {
            instance.destroy()
          } catch {
            /* 忽略 */
          }
          hls = null
          el.src = compat
          el.load()
          void el.play().catch(() => {})
          return
        }
        playerError.value = `播放失败（${data.details || data.type}）`
      })
      instance.loadSource(s.manifest_url)
      instance.attachMedia(el)
    } else if (nativeHlsSupported()) {
      el.src = s.manifest_url
      el.load()
    } else if (compat) {
      compatUsed = true
      el.src = compat
      el.load()
    } else {
      playerError.value = '当前浏览器不支持 HLS 播放'
    }
  } else if (s.stream_url) {
    el.src = s.stream_url
    el.load()
  }
}

async function startPlayback() {
  const v = activeItem.value
  if (!cinemaOpen.value || !v) return
  teardownPlayer()
  const gen = playbackGen
  compatUsed = false
  playerError.value = ''
  playerLoading.value = true
  try {
    const s = await playbackApi.create({
      music_video_id: v.id,
      requested_quality: 'original',
      client: clientCaps(),
    })
    if (gen !== playbackGen || !cinemaOpen.value || activeItem.value?.id !== v.id) {
      void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
      return
    }
    const el = videoEl.value
    if (!el) {
      void playbackApi.stop(s.session_id, { force: true }).catch(() => {})
      return
    }
    session = s
    attachSession(el, s, gen)
    try {
      await el.play()
    } catch {
      /* 自动播放被拦截：保留画面，等用户点播放 */
    }
  } catch (e) {
    if (gen === playbackGen && cinemaOpen.value && activeItem.value?.id === v.id) {
      playerError.value = (e as Error).message || '播放失败'
    }
  } finally {
    if (gen === playbackGen) playerLoading.value = false
  }
}

function onVideoError() {
  if (!videoEl.value || !session) return
  const compat = compatUrl(session)
  if (compat && !compatUsed) {
    compatUsed = true
    const el = videoEl.value
    el.src = compat
    el.load()
    void el.play().catch(() => {})
    return
  }
  playerError.value = '播放失败，可尝试在详情页切换画质'
}

function togglePlay() {
  const el = videoEl.value
  if (!el) return
  if (el.paused) void el.play().catch(() => {})
  else el.pause()
}

let playbackTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => [cinemaOpen.value, activeIndex.value] as const,
  ([open]) => {
    if (playbackTimer) clearTimeout(playbackTimer)
    if (!open) {
      teardownPlayer()
      return
    }
    // 等布局稳定（一次重排 + 位移动画）后再建会话，避免和动画抢主线程
    playbackTimer = setTimeout(() => void startPlayback(), 420)
  },
)

function randomPlay() {
  if (!items.value.length) return
  openShort(Math.floor(Math.random() * items.value.length))
}

onMounted(() => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('click', onDocumentClick)
})
onBeforeUnmount(() => {
  narrowMq.removeEventListener('change', onNarrowChange)
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('click', onDocumentClick)
  if (playbackTimer) clearTimeout(playbackTimer)
  teardownPlayer()
})
</script>

<template>
  <div class="shorts-page">
    <SaHeader />

    <!-- 左侧：物理固定的 9:16 播放器（不占文档流，右侧内容靠 padding 压缩让位） -->
    <aside v-if="cinemaOpen" class="cinema-viewport">
      <div class="cinema-screen" :class="{ 'cinema-screen--landscape': isLandscape }">
        <video
          ref="videoEl"
          class="cinema-video"
          :poster="activePoster"
          playsinline
          preload="metadata"
          @click="togglePlay"
          @error="onVideoError"
          @play="onCinemaPlay"
          @pause="onCinemaPause"
          @timeupdate="onCinemaTime"
          @ended="onCinemaEnded"
        />
        <div v-if="playerLoading" class="cinema-loading">加载中…</div>
        <div v-if="playerError" class="cinema-error">{{ playerError }}</div>
        <div class="cinema-shade" />

        <div class="cinema-info" :class="{ 'cinema-info--compact': isLandscape }">
          <div class="cinema-who">
            <span class="who-artist">{{ artistName(activeItem) || '—' }}</span>
            <span class="who-dot">·</span>
            <span class="who-group">{{ activeItem?.song_name || activeItem?.event_name || '短视频' }}</span>
          </div>
          <p class="cinema-title">{{ activeItem?.name || '' }}</p>
          <div class="cinema-meta">
            <span>{{ dateLabel(activeItem) }}</span>
            <span class="meta-dot">·</span>
            <span>{{ durationLabel(activeItem) }}</span>
            <span class="meta-dot">·</span>
            <span class="meta-res">{{ activeResLabel }}</span>
          </div>
        </div>

        <button class="cinema-close" type="button" title="收起播放器 (Esc)" @click="exitCinema">
          ✕
        </button>

      </div>

      <div class="cinema-rail">
        <button class="rail-btn" type="button" title="上一条 (↑)" @click="stepShort(-1)">↑</button>
        <span class="rail-index">{{ activeIndex + 1 }}/{{ items.length || 0 }}</span>
        <button class="rail-btn" type="button" title="下一条 (↓)" @click="stepShort(1)">↓</button>
      </div>
    </aside>

    <!-- 右侧主舞台：分屏时整体右移压缩，网格平滑降列 -->
    <main
      class="page-canvas"
      :class="{ 'page-canvas--compressed': cinemaOpen, 'page-canvas--landscape': cinemaOpen && isLandscape }"
    >
      <div class="shorts-head">
        <div class="head-left">
          <h1 class="shorts-title">短视频</h1>
          <span class="count-badge">{{ total }}</span>
        </div>

        <div class="head-right">
          <div class="control-dock">
            <button class="dock-primary" type="button" @click="randomPlay">
              <span>随机播放</span>
            </button>

            <span class="dock-sep" />

            <div class="pop-group" :class="{ open: openMenu === 'sort' }">
              <button class="dock-btn" type="button" @click.stop="toggleMenu('sort')">
                <span>排序: <strong>{{ sortLabel }}</strong></span>
                <svg class="caret" viewBox="0 0 10 6" width="10" height="6" fill="none"><path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </button>
              <div class="pop-menu pop-menu--sort" @click.stop>
                <button
                  v-for="o in SORT_OPTIONS"
                  :key="o.value"
                  class="opt-row"
                  :class="{ on: sortOrder === o.value || sortOrder.replace(/_(asc|desc)$/, '') === o.value }"
                  type="button"
                  @click="pickSort(o.value)"
                >
                  <span>{{ o.label }}</span>
                  <span v-if="sortOrder === o.value" class="tick">✓</span>
                </button>
              </div>
            </div>

            <span class="dock-sep" />

            <div class="pop-group pop-group--right" :class="{ open: openMenu === 'density' }">
              <button class="dock-btn" type="button" @click.stop="toggleMenu('density')">
                <span>密度: <strong>{{ densityLabel }}</strong></span>
                <svg class="caret" viewBox="0 0 10 6" width="10" height="6" fill="none"><path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </button>
              <div class="pop-menu pop-menu--density" @click.stop>
                <button
                  v-for="o in DENSITY_OPTIONS"
                  :key="o.value"
                  class="opt-row"
                  :class="{ on: density === o.value }"
                  type="button"
                  @click="pickDensity(o.value)"
                >
                  <span>{{ o.label }}</span>
                  <span v-if="density === o.value" class="tick">✓</span>
                </button>
              </div>
            </div>
          </div>

          <div class="search-pill">
            <SearchOutlined :size="14" class="search-glyph" />
            <input
              v-model="searchQuery"
              type="text"
              :placeholder="isNarrow ? '搜索…' : '搜索短视频、艺人…'"
            />
            <button
              v-if="searchQuery"
              class="search-clear"
              type="button"
              aria-label="清空搜索"
              @click="searchQuery = ''"
            >
              <CloseOutlined :size="11" />
            </button>
          </div>

        </div>
      </div>

      <!-- 网格常驻挂载：page_size 依赖它的列数实测，空态时也不能卸载 -->
      <div :ref="bindGrid" class="shorts-grid" :class="`density-${density}`">
        <ShortsCard
          v-for="(mv, i) in items"
          :key="mv.id"
          :ref="(el) => setCardEl(el, i)"
          :video="mv"
          :active="cinemaOpen && i === activeIndex"
          @open="openShort(i)"
          @next="stepShort(1)"
          @artist="goArtist"
        />
      </div>

      <div v-if="loading && !items.length" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        <MovieOutlined :size="40" />
        <p>{{ loadError || '暂无短视频' }}</p>
        <button v-if="loadError" class="empty-retry" type="button" @click="load(1)">重试</button>
      </div>

      <div v-if="items.length" class="pager">
        <div class="pager-total">
          共 {{ total }} 条短视频 · 第 {{ pageNum }}/{{ totalPages }} 页
        </div>

        <div class="pager-btns">
          <button class="pg-arrow" type="button" :disabled="pageNum <= 1" @click="onPageChange(pageNum - 1)">‹</button>
          <template v-for="(p, i) in pageNumbers" :key="`${p}-${i}`">
            <span v-if="p === '…'" class="pg-gap">…</span>
            <button
              v-else
              class="pg-num"
              :class="{ on: p === pageNum }"
              type="button"
              @click="onPageChange(Number(p))"
            >
              {{ p }}
            </button>
          </template>
          <button class="pg-arrow" type="button" :disabled="pageNum >= totalPages" @click="onPageChange(pageNum + 1)">›</button>
        </div>

        <div class="pager-jump">
          <span>跳至</span>
          <input v-model="jumpPage" type="number" min="1" />
          <span>页</span>
          <button type="button" @click="doJump">确定</button>
        </div>
      </div>

    </main>
  </div>
</template>

<style scoped>
/* ============================================================
   短视频页：复刻 gemini-code 样板
   - 左侧播放器 fixed 挂在视口（不占文档流），右侧靠 padding 压缩让位
   - 9:16 卡片、实色控制岛、popIn 下拉；颜色走 --sa-* 令牌，浅/深主题自适应
   ============================================================ */
.shorts-page {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
}

/* ===== 左侧固定播放器 ===== */
.cinema-viewport {
  position: fixed;
  top: 84px;
  left: 28px;
  z-index: 40;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  animation: cinemaIn 0.42s cubic-bezier(0.16, 1, 0.3, 1) both;
  will-change: transform, opacity;
}
@keyframes cinemaIn {
  from {
    opacity: 0;
    transform: translateX(-16px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
.cinema-screen {
  position: relative;
  width: 365px;
  height: 648px;
  border-radius: 20px;
  overflow: hidden;
  background: #0a0a0e;
  border: 1px solid var(--sa-border);
  box-shadow:
    0 25px 60px -12px rgba(0, 0, 0, 0.45),
    0 0 0 1px rgba(255, 255, 255, 0.06) inset;
}
.cinema-screen--landscape {
  width: 640px;
  height: 360px;
}
.cinema-video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
  cursor: pointer;
}
.cinema-loading,
.cinema-error {
  position: absolute;
  left: 50%;
  bottom: 18px;
  transform: translateX(-50%);
  max-width: 90%;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 11.5px;
  background: rgba(0, 0, 0, 0.62);
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cinema-error {
  color: #ffb4b4;
}
.cinema-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  filter: brightness(0.95);
}
.cinema-shade {
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.85), transparent 55%);
  pointer-events: none;
}
.cinema-info {
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 20px;
  color: #fff;
  z-index: 2;
}

/* 横版：底部只留一行（标题），其余信息收进同一行的小字里 */
.cinema-info--compact {
  bottom: 12px;
  display: flex;
  align-items: baseline;
  gap: 10px;
  white-space: nowrap;
  overflow: hidden;
}
.cinema-info--compact .cinema-who,
.cinema-info--compact .cinema-meta {
  display: none;
}
.cinema-info--compact .cinema-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cinema-who {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.who-artist {
  font-weight: 700;
}
.who-dot {
  color: rgba(255, 255, 255, 0.45);
}
.who-group {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.72);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cinema-title {
  margin: 6px 0 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: rgba(255, 255, 255, 0.9);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cinema-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px;
  color: rgba(255, 255, 255, 0.55);
  font-variant-numeric: tabular-nums;
}
.cinema-meta .meta-res {
  color: #fff;
  font-weight: 600;
}
.cinema-open {
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  backdrop-filter: blur(10px);
  transition: transform 0.2s ease, background-color 0.2s ease;
}
.cinema-open:hover {
  background: rgba(0, 0, 0, 0.75);
  transform: scale(1.05);
}
.cinema-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding-top: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.rail-btn {
  width: 32px;
  height: 32px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background-color 0.18s ease, transform 0.12s ease;
}
.rail-btn:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.rail-btn:active {
  transform: scale(0.94);
}
.rail-index {
  font-size: 10px;
  color: var(--sa-text-tertiary);
  padding: 2px 0;
}

/* ===== 右侧主舞台 ===== */
.page-canvas {
  padding: 20px 44px 72px 36px;
  /* 注意：不要再 animate padding —— 每帧都会让 auto-fill 网格重算列数，主线程直接打满。
     宽度切换改成「一次重排 + 同帧滚动校正」，动画交给播放器的 transform。 */
}
.page-canvas--compressed {
  padding-left: 460px;
}
.page-canvas--landscape {
  padding-left: 740px;
}

.shorts-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0 16px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.head-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.shorts-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', serif;
  font-size: 30px;
  font-weight: 900;
  letter-spacing: -0.02em;
  color: var(--sa-text-primary);
}
.count-badge {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  padding: 2px 9px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  color: var(--sa-text-secondary);
  white-space: nowrap;
}
.head-right {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

/* 实色控制岛 */
.control-dock {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 9999px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.06);
}
.dock-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  border-radius: 9999px;
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: opacity 0.2s ease, transform 0.12s ease;
}
.dock-primary:hover {
  opacity: 0.88;
}
.dock-primary:active {
  transform: scale(0.97);
}
.dock-bolt {
  font-size: 11px;
}
.dock-sep {
  width: 1px;
  height: 16px;
  background: var(--sa-border-subtle);
  margin: 0 2px;
}

.pop-group {
  position: relative;
}
.dock-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 13px;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.18s ease, color 0.18s ease;
}
.dock-btn:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.dock-btn strong {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.caret {
  flex: none;
  color: var(--sa-text-tertiary);
  transition: transform 0.2s ease;
}
.pop-group.open .caret {
  transform: rotate(180deg);
}

.pop-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  z-index: 100;
  display: none;
  padding: 6px;
  border-radius: 14px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  box-shadow:
    0 12px 30px -4px rgba(0, 0, 0, 0.32),
    0 4px 12px -2px rgba(0, 0, 0, 0.16);
  min-width: 160px;
}
.pop-group.open .pop-menu {
  display: block;
  animation: popIn 0.18s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
.pop-group--right .pop-menu {
  left: auto;
  right: 0;
}
@keyframes popIn {
  from {
    opacity: 0;
    transform: translateY(4px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
.opt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.opt-row:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.opt-row.on {
  color: var(--sa-text-primary);
}
.tick {
  color: var(--sa-accent);
  font-weight: 700;
  font-size: 11px;
}

/* 搜索胶囊 */
.search-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 12px;
  width: 230px;
  border-radius: 9999px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.2s ease;
}
.search-pill:focus-within {
  width: 280px;
  border-color: var(--sa-text-tertiary);
}
.search-glyph {
  flex: none;
  color: var(--sa-text-tertiary);
}
.search-pill input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 12.5px;
  font-family: inherit;
}
.search-pill input::placeholder {
  color: var(--sa-text-tertiary);
}
.search-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  cursor: pointer;
}
.exit-btn {
  padding: 7px 14px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.18s ease, border-color 0.18s ease;
}
.exit-btn:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-text-tertiary);
}

/* ===== 9:16 网格 ===== */
.shorts-grid > * {
  content-visibility: auto;
  contain-intrinsic-size: auto 260px;
}
.shorts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--short-min, 180px), 1fr));
  gap: 18px;
}
.density-compact {
  --short-min: 160px;
}
.density-standard {
  --short-min: 180px;
}
.density-loose {
  --short-min: 220px;
}

/* ===== 分页 ===== */
.pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 40px;
  padding-top: 18px;
  border-top: 1px solid var(--sa-border-subtle);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.pager-btns {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pg-arrow,
.pg-num {
  min-width: 28px;
  height: 28px;
  padding: 0 6px;
  border-radius: 7px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.pg-arrow:hover:not(:disabled),
.pg-num:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.pg-arrow:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.pg-num.on {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  border-color: var(--sa-text-primary);
  font-weight: 700;
}
.pg-gap {
  padding: 0 2px;
  color: var(--sa-text-tertiary);
}
.pager-jump {
  display: flex;
  align-items: center;
  gap: 6px;
}
.pager-jump input {
  width: 52px;
  height: 28px;
  padding: 0 6px;
  border-radius: 7px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 12px;
  font-family: inherit;
  text-align: center;
  outline: none;
}
.pager-jump input:focus {
  border-color: var(--sa-text-tertiary);
}
.pager-jump button {
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: 7px;
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.pager-jump button:hover {
  opacity: 0.88;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.empty-retry {
  padding: 5px 14px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 12px;
  cursor: pointer;
}

@media (max-width: 1180px) {
  /* 窄屏把播放器收回文档流，避免 fixed 挡住主内容 */
  .cinema-viewport {
    position: static;
    justify-content: center;
    padding: 18px 16px 0;
    animation: none;
  }
  .cinema-screen {
    width: min(320px, 82vw);
    height: auto;
    aspect-ratio: 9 / 16;
  }
  .page-canvas,
  .page-canvas--compressed {
    padding-left: 16px;
    padding-right: 16px;
  }
}

@media (max-width: 760px) {
  .shorts-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .shorts-title {
    font-size: 24px;
  }
  .head-right {
    gap: 8px;
  }
  .control-dock {
    flex-wrap: wrap;
    border-radius: 18px;
    width: 100%;
    justify-content: space-between;
  }
  .search-pill,
  .search-pill:focus-within {
    width: 100%;
  }
  .shorts-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .pager {
    justify-content: center;
  }
}
/* 播放器上的收起按钮（顶栏那颗已去掉）：点空白处同样会收起 */
.cinema-close {
  position: absolute;
  top: 14px;
  left: 14px;
  z-index: 3;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.22);
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  backdrop-filter: blur(10px);
  transition: background-color 0.2s ease, transform 0.2s ease;
}
.cinema-close:hover {
  background: rgba(0, 0, 0, 0.78);
  transform: scale(1.05);
}
</style>
