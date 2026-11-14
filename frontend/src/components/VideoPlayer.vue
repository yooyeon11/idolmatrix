<script setup lang="ts">
/**
 * 视频播放器 —— 原生 <video> + hls.js（无第三方 UI 框架）
 *
 * 对外契约（props / emits / expose）与历史实现保持一致，调用方 VideoPlayView 零改动。
 *
 * 为什么自建而不是继续用 UI 播放器库：
 * - 4K/8K 片源全部是 AV1/MKV，能否直连只取决于 <video> 的解码能力与容器；
 *   播放器库只是壳，却把 MSE 参数（buffer / ABR / capLevelToPlayerSize）封装住，
 *   出问题时无法调参，也无法「8K 强制绕过 MSE」。
 * - 本文件把三件事握在手里：① 直连走裸 <video>（含 8K）；② HLS 只在转码时用 hls.js；
 *   ③ 进度条 = 绝对时间轴（窗内原地 seek / 窗外重建会话），语义完全可控。
 *
 * 关键行为：
 * - pending：不加载任何源，只展示 poster；点击 → play-request，由父组件建会话。
 * - 换源：同一 <video> 换 src，HLS 需重建 hls 实例；换源期间抑制瞬时 error。
 * - 转码窗口：进度条总长 = 片源完整时长，当前时间 = 流内时间 + startOffset。
 *   拖动时若目标在已转出范围内 → 原地 seek；否则 emit seeked 让父组件重建会话。
 * - 字幕：外挂 VTT，转码窗口按 offset 平移时间轴。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Hls from 'hls.js'
import { androidBridge, nativePipSupported, playNativeFullscreen, playNativePip } from '@/native/android'

interface Source {
  src: string
  type?: string
  label?: string
  /**
   * 无 MSE 时的兜底地址（服务端渐进转码的 fMP4/H.264）。
   * 手机自带浏览器常常没有可用的 MediaSource，HLS 这条路必然失败，
   * 此时改用 <video src> 直连这条流。
   */
  fallbackSrc?: string
  /** 兼容版：整片转好的普通 MP4（有真实时长、支持 Range），给会被劫持的内核用 */
  compatSrc?: string
}

/** 外挂字幕轨（后端统一转 VTT 出流） */
export interface SubtitleTrack {
  url: string
  label: string
  language?: string
  default?: boolean
}

type PlayerErrorKind = 'network' | 'decode' | 'capability' | 'unknown'

const emit = defineEmits<{
  (
    e: 'state',
    payload: {
      buffering: boolean
      error: string | null
      errorKind?: PlayerErrorKind
      errorCode?: number
    },
  ): void
  (e: 'timeupdate', currentTime: number): void
  /** 用户拖动进度条完成后触发：父组件按目标时间重建转码会话 */
  (e: 'seeked', targetTime: number): void
  (e: 'quality-select', quality: string): void
  /** 待播预览态点击播放，请求父组件创建会话 */
  (e: 'play-request'): void
  /** 已交给全屏原生播放器：父组件应停掉窗口态会话（避免服务端同时跑两路转码） */
  (e: 'native-handoff', position: number): void
  (e: 'play'): void
  (e: 'pause'): void
  (e: 'ended'): void
}>()

const props = withDefaults(
  defineProps<{
    sources: Source[]
    poster?: string
    activeLabel?: string
    qualities?: string[]
    activeQuality?: string
    playModeLabel?: string
    playModeReason?: string
    playMode?: string
    buffering?: boolean
    pending?: boolean
    restoreTime?: boolean
    duration?: number
    startOffset?: number
    nativeSeek?: boolean
    autoPlay?: boolean
    seekInto?: number
    availableUntil?: number
    subtitles?: SubtitleTrack[]
    /**
     * 平板客户端上下文：点了全屏就把播放交给原生 ExoPlayer。
     * 没有 window.IdolAndroid（普通浏览器）时这两个字段完全不起作用。
     */
    nativeVideoId?: number
    nativeTitle?: string
    nativeSubtitle?: string
  }>(),
  {
    sources: () => [],
    qualities: () => [],
    activeQuality: 'original',
    playModeLabel: '',
    playModeReason: '',
    playMode: '',
    buffering: false,
    pending: false,
    restoreTime: false,
    startOffset: 0,
    nativeSeek: true,
    autoPlay: false,
    seekInto: 0,
    availableUntil: 0,
    subtitles: () => [],
    nativeVideoId: 0,
    nativeTitle: '',
    nativeSubtitle: '',
  },
)

const rootRef = ref<HTMLDivElement | null>(null)
const videoRef = ref<HTMLVideoElement | null>(null)
let hls: Hls | null = null
/** 平板客户端桥（浏览器里为 null，所有原生分支自动跳过） */
const nativeApp = androidBridge()
/**
 * 是否跑在平板客户端壳里（UA 里带 idolMatrixPad/，见 MainActivity）。
 *
 * 用途只有一个：**区分「浏览器不支持全屏」和「App 里的桥没接上」**。
 * 后者的用户提示完全不同（要更新 App/网页），混在一起就会变成「点了没反应」。
 */
const appShell = typeof navigator !== 'undefined' && /idolMatrixPad\//.test(navigator.userAgent || '')

// ===== 状态 =====
const currentTime = ref(0) // 流内时间（秒）
const mediaDuration = ref(0)
const playing = ref(false)
const bufferedEnd = ref(0)
// 待播态（封面被唤出）一进来就亮菜单栏 —— 用户刚刚悬停/单击过，等于已经打了招呼
const controlsVisible = ref(props.pending)
const menu = ref<'' | 'quality' | 'subtitle' | 'speed'>('')
const muted = ref(false)
const volume = ref(1)
const rate = ref(1)
const isFullscreen = ref(false)
const pipActive = ref(false)
const activeSubtitleKey = ref('off')
const errorText = ref('')
/** 一次性提示（全屏 / 画中画失败时说人话，不再静默吞掉） */
const hintText = ref('')
let hintTimer: number | null = null

function showHint(text: string) {
  hintText.value = text
  if (hintTimer !== null) window.clearTimeout(hintTimer)
  hintTimer = window.setTimeout(() => {
    hintText.value = ''
    hintTimer = null
  }, 2600)
}

/**
 * 画中画能力：属性 `disablePictureInPicture` 会让 requestPictureInPicture()
 * 直接抛 InvalidStateError，所以先按「有没有这个方法 + 是否被禁用」判断。
 * iPhone 上的 Safari 根本没有这个 API，按钮直接不渲染。
 */
const canPip = computed(() => {
  if (typeof document === 'undefined') return false
  const d = document as Document & { pictureInPictureEnabled?: boolean }
  if (d.pictureInPictureEnabled === false) return false
  return typeof (HTMLVideoElement.prototype as unknown as Record<string, unknown>)
    .requestPictureInPicture === 'function'
})

/**
 * 平板客户端：WebView 没有 PiP API，自己的 `canPip` 恒为 false，
 * 画中画只能交给原生（把整个播放页缩成系统小窗）。
 * 旧版 App 没接这条桥 → false，按钮不渲染，与之前一致。
 */
const canNativePip = computed(() => nativePipSupported())

/* 各内核的全屏 API 名字不一样：Chrome/新版 Safari 用标准名，
   旧 Safari / 国产内核只有 webkit 前缀，老 Edge 只有 ms 前缀 */
type FsDocument = Document & {
  webkitFullscreenElement?: Element | null
  webkitExitFullscreen?: () => Promise<void> | void
  msFullscreenElement?: Element | null
  msExitFullscreen?: () => Promise<void> | void
}
type FsElement = HTMLElement & {
  webkitRequestFullscreen?: () => Promise<void> | void
  webkitRequestFullScreen?: () => Promise<void> | void
  mozRequestFullScreen?: () => Promise<void> | void
  msRequestFullscreen?: () => Promise<void> | void
}
type FsVideo = HTMLVideoElement & {
  webkitEnterFullscreen?: () => void
  webkitExitFullscreen?: () => void
  webkitDisplayingFullscreen?: boolean
}

function currentFullscreenEl(): Element | null {
  const d = document as FsDocument
  return d.fullscreenElement || d.webkitFullscreenElement || d.msFullscreenElement || null
}

/** 换源进行中：抑制瞬时 error（旧源失效 / 空源），真错误 8s 后仍会显示 */
const switching = ref(false)
let switchingTimer: number | null = null

function markSwitching() {
  switching.value = true
  if (switchingTimer) window.clearTimeout(switchingTimer)
  switchingTimer = window.setTimeout(() => {
    switching.value = false
    switchingTimer = null
  }, 8000)
}
function clearSwitching() {
  if (switchingTimer) {
    window.clearTimeout(switchingTimer)
    switchingTimer = null
  }
  switching.value = false
}

// ===== 换源状态机 =====
let srcGen = 0
/** 用户已点过播放 / 拖过进度：换源后必须继续播 */
let wantPlay = false
/** 换源 / 窗口内 seek 引发的 pause 不算用户暂停 */
let ignorePause = false
let hushGen = 0
/** 换源后待执行的定位回调（按 gen 比对，过期丢弃） */
let pendingCanplay: (() => void) | null = null
let lastSwitchAt = 0
/** 换源前的时间（直连恢复位置用） */
let prevTime = 0

function hushPause(ms = 800) {
  const gen = ++hushGen
  ignorePause = true
  window.setTimeout(() => {
    if (gen === hushGen) ignorePause = false
  }, ms)
}

function currentSource(): Source | undefined {
  if (props.activeLabel) {
    const found = props.sources.find((s) => s.label === props.activeLabel)
    if (found) return found
  }
  return props.sources[0]
}

// ===== 时间轴 =====
const totalDuration = computed<number>(() => {
  const fallback = props.duration ?? 0
  if (!props.nativeSeek && fallback > 0) return fallback
  return mediaDuration.value || fallback || 0
})
const displayTime = computed(() => (props.nativeSeek ? currentTime.value : (props.startOffset || 0) + currentTime.value))
const progressPct = computed(() => {
  const d = totalDuration.value
  if (!d) return 0
  return Math.min(100, Math.max(0, (displayTime.value / d) * 100))
})
const bufferedPct = computed(() => {
  const d = totalDuration.value
  if (!d) return 0
  const abs = (props.nativeSeek ? 0 : props.startOffset || 0) + bufferedEnd.value
  return Math.min(100, Math.max(0, (abs / d) * 100))
})

function fmt(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return '0:00'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${m}:${String(ss).padStart(2, '0')}`
}

const timeText = computed(() => `${fmt(displayTime.value)} / ${fmt(totalDuration.value)}`)

// ===== 原地 seek 判定 =====
const SEEK_AHEAD_TOLERANCE = 3

function bufferedEndOf(media: HTMLVideoElement): number {
  try {
    const b = media.buffered
    if (!b || !b.length) return 0
    const cur = Number(media.currentTime) || 0
    for (let i = 0; i < b.length; i += 1) {
      if (b.start(i) - 1 <= cur && cur <= b.end(i) + 1) return b.end(i)
    }
    return b.end(b.length - 1)
  } catch {
    return 0
  }
}

/**
 * 目标时间能否原地 seek（true=流内跳，false=交给父组件重建会话）。
 * 转码进行中 HLS 是 EVENT 清单，MSE duration 恒为 Infinity —— 不能只看 duration。
 */
function canSeekInPlace(time: number): boolean {
  const offset = props.startOffset || 0
  if (!(time >= offset - 0.5)) return false
  const media = videoRef.value
  const mediaDur = Number(media?.duration ?? mediaDuration.value) || 0
  if (Number.isFinite(mediaDur) && mediaDur > 1) return time <= offset + mediaDur - 0.5
  const avail = Number(props.availableUntil) || 0
  if (avail > 0) return time <= avail + SEEK_AHEAD_TOLERANCE
  if (!media) return false
  return time <= offset + bufferedEndOf(media) + SEEK_AHEAD_TOLERANCE
}

/** 进度条交互：绝对时间轴上的目标 → 原地跳或重建会话 */
function seekToAbsolute(target: number) {
  const media = videoRef.value
  if (!media) return
  wantPlay = true
  const clamped = Math.min(Math.max(0, target), totalDuration.value || target)
  if (props.nativeSeek) {
    media.currentTime = clamped
    return
  }
  const offset = props.startOffset || 0
  if (canSeekInPlace(clamped)) {
    hushPause(1200)
    media.currentTime = Math.max(0, clamped - offset)
    return
  }
  hushPause(1200)
  emit('seeked', clamped)
}

/** 对外：按绝对时间定位（父组件的 playerRef.seekTo） */
function seekTo(time: number) {
  seekToAbsolute(time)
}

// ===== 播放控制 =====
function tryPlay() {
  const media = videoRef.value
  if (!media) return
  const ret = media.play()
  if (ret && typeof ret.catch === 'function') ret.catch(() => {})
}

function togglePlay() {
  const media = videoRef.value
  if (!media) return
  if (!props.sources.length) {
    emit('play-request')
    return
  }
  if (media.paused) {
    wantPlay = true
    tryPlay()
  } else {
    wantPlay = false
    media.pause()
  }
}

// ===== 字幕 =====
function applySubtitleMode() {
  const media = videoRef.value
  if (!media?.textTracks) return
  const tracks = props.subtitles || []
  for (let i = 0; i < media.textTracks.length; i++) {
    media.textTracks[i].mode = tracks[i] && activeSubtitleKey.value === String(i) ? 'showing' : 'disabled'
  }
}

function attachSubtitleTracks() {
  const media = videoRef.value
  if (!media) return
  for (const tr of Array.from(media.querySelectorAll('track'))) tr.remove()
  const offset = props.nativeSeek ? 0 : props.startOffset || 0
  for (const t of props.subtitles || []) {
    const tr = document.createElement('track')
    tr.kind = 'subtitles'
    tr.label = t.label
    tr.srclang = t.language || ''
    // 转码窗口的流内时间 = 片源绝对时间 - window_start，字幕时间轴同步平移
    tr.src = offset > 0 ? `${t.url}${t.url.includes('?') ? '&' : '?'}offset=${offset.toFixed(3)}` : t.url
    media.appendChild(tr)
  }
  applySubtitleMode()
}

function pickSubtitle(key: string) {
  activeSubtitleKey.value = key
  applySubtitleMode()
  menu.value = ''
}

// ===== HLS =====
function destroyHls() {
  if (hls) {
    try {
      hls.destroy()
    } catch {
      /* 忽略 */
    }
    hls = null
  }
}

let activeFallback: string | null = null

/**
 * 切到渐进 MP4 兜底。HLS 已 fatal 或 MSE 被拒时，重试同一份清单没有意义；
 * 统一在这里销毁 hls、清掉换源抑制态并续播，避免各错误分支行为不一致。
 */
function switchToFallback(fallback: string, gen: number): boolean {
  const media = videoRef.value
  if (!media || gen !== srcGen) return false
  activeFallback = null
  destroyHls()
  clearSwitching()
  errorText.value = ''
  media.src = fallback
  media.load()
  if (props.autoPlay || wantPlay) tryPlay()
  return true
}

function attachSource(src: Source, gen: number) {
  const media = videoRef.value
  if (!media) return
  destroyHls()
  activeFallback = src.fallbackSrc || null
  const isHls = /\.m3u8($|\?)/i.test(src.src)

  /**
   * 部分国产内核（vivo / OPPO / 小米 / 华为 / UC·夸克）会把 <video> **劫持**成系统原生播放器。
   * 那个播放器：① 不认 MSE 的 blob 源（hls.js 必用）→ 直接弹自己的「播放失败，请刷新」；
   * ② 也不认 4K MKV / AV1。这是厂商的系统级策略，前端改不动它的实现。
   * 唯一可靠的办法是绕开 MSE，直接喂它最认的东西：H.264/AAC 的渐进 MP4。
   */
  if (isHls && prefersProgressive()) {
    // 优先用「普通 MP4 文件」：fMP4 边转边发没有总时长，系统播放器会判失败
    const target = src.compatSrc || src.fallbackSrc
    if (target) {
      media.src = target
      media.load()
      return
    }
  }

  if (isHls && !Hls.isSupported() && !nativeHlsSupported()) {
    // 这条分支就是「vivo / 华为等定制内核」的真实处境：UA 写着 Chrome，
    // 但 MediaSource 不可用 → hls.js 不支持，manifest 一次都不会被请求。
    // 有 MP4 兜底就直接用它（不经过 MSE）：优先普通 MP4（有总时长，系统播放器认），
    // 其次才是边转边发的 fMP4。否则才报错。
    const target = src.compatSrc || src.fallbackSrc
    if (target) {
      media.src = target
      media.load()
      return
    }
    clearSwitching()
    showError(
      '',
      '当前浏览器不支持 HLS 播放（缺少 MediaSource），换 Chrome / Edge / 夸克 打开即可；如果这是手机自带浏览器，请改用 Chrome。',
      { kind: 'capability' },
    )
    return
  }
  if (isHls && Hls.isSupported()) {
    const instance = new Hls({
      // 4K 转码流：缓冲别太激进（8K 永远走直连，不在此路径）
      maxBufferLength: 30,
      maxMaxBufferLength: 60,
      capLevelToPlayerSize: true,
      enableWorker: true,
      lowLatencyMode: false,
    })
    hls = instance
    // 重试要有限度：无限 startLoad/recoverMediaError 的表现就是「永远转圈」
    let netRetry = 0
    let mediaRetry = 0
    instance.on(Hls.Events.ERROR, (_e, data) => {
      // 已换源/已销毁的实例仍可能派发尾事件，不能让它覆盖新源的状态
      if (hls !== instance || gen !== srcGen || !data.fatal) return
      // 编码不被 MSE 接受：重试没有意义（recoverMediaError 只会死循环）
      if (data.details === Hls.ErrorDetails.BUFFER_INCOMPATIBLE_CODECS_ERROR) {
        if (src.fallbackSrc && switchToFallback(src.fallbackSrc, gen)) return
        clearSwitching()
        showError('', '当前浏览器无法解码这个视频流（MSE 拒绝该编码），请改用 Chrome / Edge 打开。', {
          kind: 'decode',
        })
        return
      }
      if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        if (++netRetry > 3) {
          if (activeFallback && switchToFallback(activeFallback, gen)) return
          clearSwitching()
          showError(`转码流·切片加载失败（${data.details || 'NETWORK_ERROR'}）`, '', {
            kind: 'network',
          })
          return
        }
        instance.startLoad()
        return
      }
      if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        if (++mediaRetry > 2) {
          if (activeFallback && switchToFallback(activeFallback, gen)) return
          clearSwitching()
          showError(`转码流·解码失败（${data.details || 'MEDIA_ERROR'}）`, '', {
            kind: 'decode',
          })
          return
        }
        instance.recoverMediaError()
        return
      }
      if (activeFallback && switchToFallback(activeFallback, gen)) return
      clearSwitching()
      showError(`HLS 播放失败（${data.details || data.type}）`, '', { kind: 'unknown' })
    })
    instance.loadSource(src.src)
    instance.attachMedia(media)
  } else {
    media.src = src.src
    // 只有原生渐进源需要显式 load()；hls.js 已把 src 换成 MediaSource 的 blob，
    // 再调 load() 会把挂载重置掉（表现为起播白屏或直接报错）
    media.load()
  }
  if (gen !== srcGen) return
  // 播放意图只由父组件/用户操作决定。以前这里用 `!pending` 强制置 true，
  // 导致「暂停后切画质/切视频」必然自动出声。
  if (props.autoPlay) wantPlay = true
}

// ===== 错误 =====
/**
 * 会被「劫持到系统播放器」的国产内核。这些浏览器把 <video> 换成脱离 DOM 的原生窗口，
 * 既不认 blob(MSE) 源、也不认 4K MKV/AV1 —— 表现就是它自己弹「播放失败，请刷新」。
 * 对它们直接绕过 MSE，用渐进 MP4（H.264/AAC），这是原生播放器唯一稳吃的东西。
 */
const PROGRESSIVE_UA_RE =
  /VivoBrowser|OppoBrowser|HeyTapBrowser|MiuiBrowser|XiaoMi|HuaweiBrowser|HonorBrowser|UCBrowser|Quark/i
let forceProgressive: boolean | null = null
function prefersProgressive(): boolean {
  if (forceProgressive === null) {
    try {
      forceProgressive = PROGRESSIVE_UA_RE.test(navigator.userAgent || '')
    } catch {
      forceProgressive = false
    }
  }
  return forceProgressive
}

/** 浏览器自带 HLS 能力（Safari / 部分安卓原生内核）；Chrome 系为 false */
function nativeHlsSupported(): boolean {
  try {
    return !!document.createElement('video').canPlayType('application/vnd.apple.mpegurl')
  } catch {
    return false
  }
}

const MEDIA_ERR_TEXT: Record<number, string> = {
  1: '加载被中止',
  2: '网络错误',
  3: '解码失败',
  4: '格式不支持',
}

/**
 * extra 会跟着通用文案一起显示 —— 安卓系统浏览器的失败原因差别很大
 * （无 MSE / 解码器不认 AV1 / 容器不认 MKV），只给一句「播放出错」
 * 等于什么都没说，现场也没法定位。
 * override：能力缺失类失败（换画质也救不了）直接给可执行建议，别再说「请切换画质」。
 */
function showError(
  extra = '',
  override = '',
  meta: { kind?: PlayerErrorKind; code?: number } = {},
) {
  if (props.pending) return
  const base = '播放出错，请尝试切换画质或稍后重试'
  errorText.value = override || (extra ? `${base}（${extra}）` : base)
  emit('state', {
    buffering: false,
    error: errorText.value,
    errorKind: meta.kind || 'unknown',
    errorCode: meta.code || 0,
  })
}

// ===== 换源主流程 =====
function applySource() {
  const media = videoRef.value
  if (!media) return
  const src = currentSource()
  const gen = ++srcGen
  errorText.value = ''

  if (!src) {
    destroyHls()
    media.removeAttribute('src')
    media.load()
    emit('state', { buffering: false, error: null })
    return
  }

  emit('state', { buffering: true, error: null })
  markSwitching()
  hushPause(1200)

  // 父组件的 autoPlay 是本次换源的唯一续播意图；wantPlay 只负责同一源内
  // 「用户刚点了播放、但媒体还没 ready」的短暂窗口。
  if (!props.autoPlay) wantPlay = false
  const shouldPlay = wantPlay || props.autoPlay
  prevTime = Number(media.currentTime) || 0

  // Direct Play 换会话时必须用父组件的绝对目标时间。旧 currentTime 只属于
  // 旧源：从转码窗口切直连时它是「窗口内秒数」，直接拿来恢复会差一个 start_offset。
  const directTarget =
    props.nativeSeek && props.seekInto > 0.35 ? props.seekInto : 0
  const restoreTo =
    directTarget || (props.nativeSeek && props.restoreTime ? prevTime : 0)
  const seekTarget = !props.nativeSeek && props.seekInto > 0.35 ? props.seekInto : 0

  pendingCanplay = () => {
    if (gen !== srcGen) return
    if (restoreTo > 0) {
      const d = Number(media.duration) || restoreTo
      media.currentTime = Math.min(restoreTo, d)
    } else if (seekTarget > 0) {
      media.currentTime = seekTarget
    }
    clearSwitching()
    if (shouldPlay) tryPlay()
  }

  lastSwitchAt = Date.now()
  try {
    attachSource(src, gen)
  } catch (e) {
    // 内核不认某段 JS（或 hls.js 初始化抛错）时，异常会一路冒出去，
    // 表现成「什么都没发生」。把它变成一句可读的结论，别让用户对着转圈。
    clearSwitching()
    showError(`播放器异常：${String((e as Error)?.message || e).slice(0, 90)}`, '', {
      kind: 'unknown',
    })
    return
  }
  if (props.autoPlay) wantPlay = true
}

// ===== 媒体事件 =====
function onLoadedMetadata() {
  const media = videoRef.value
  if (!media) return
  mediaDuration.value = Number.isFinite(media.duration) ? media.duration : 0
  attachSubtitleTracks()
  const cb = pendingCanplay
  pendingCanplay = null
  cb?.()
}

function onTimeUpdate() {
  const media = videoRef.value
  if (!media) return
  currentTime.value = media.currentTime
  bufferedEnd.value = bufferedEndOf(media)
  emit('timeupdate', displayTime.value)
}

function clearPlaybackError() {
  if (!errorText.value) return
  errorText.value = ''
  emit('state', { buffering: false, error: null })
}

function onPlay() {
  playing.value = true
  clearPlaybackError()
  emit('state', { buffering: false, error: null })
  emit('play')
}

function onPause() {
  playing.value = false
  if (ignorePause) return
  emit('pause')
}

function onWaiting() {
  emit('state', { buffering: true, error: null })
}

function onPlaying() {
  clearPlaybackError()
  emit('state', { buffering: false, error: null })
}

function onEnded() {
  playing.value = false
  emit('ended')
}

function onMediaError() {
  if (props.pending) return
  const media = videoRef.value
  const code = media?.error?.code || 0
  // MSE 源（blob:）在被劫持的系统播放器里必然失败 → 立刻换渐进 MP4，只换一次
  if (activeFallback && (media?.currentSrc || '').startsWith('blob:')) {
    if (switchToFallback(activeFallback, srcGen)) return
  }
  // 3=DECODE / 4=SRC_NOT_SUPPORTED 是确定性失败：换源瞬间的残留错误不会是这个形态，
  // 所以**不受 switching 抑制**，否则安卓上「转圈 8 秒后什么都没有」就是这么来的
  const definitive = code === 3 || code === 4
  if (!definitive) {
    if (switching.value) return
    if (Date.now() - lastSwitchAt < 1500) return
  }
  clearSwitching()
  const kind = (media?.currentSrc || '').startsWith('blob:') ? '转码流' : '原画直连'
  const reason = MEDIA_ERR_TEXT[code] || '媒体错误'
  showError(`${kind}·${reason}`, '', {
    // 只有 3=DECODE / 4=SRC_NOT_SUPPORTED 才是确定性的解码/格式失败。
    // 1/2（中止、网络）绝不能写进「原画失败记忆」。
    kind: code === 3 || code === 4 ? 'decode' : code === 2 ? 'network' : 'unknown',
    code,
  })
}

// ===== 进度条交互 =====
const barRef = ref<HTMLDivElement | null>(null)
let dragging = false

function ratioFromEvent(e: PointerEvent): number {
  const el = barRef.value
  if (!el) return 0
  const rect = el.getBoundingClientRect()
  if (!rect.width) return 0
  return Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
}

function onBarPointerDown(e: PointerEvent) {
  if (!totalDuration.value) return
  dragging = true
  ;(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId)
  seekToAbsolute(ratioFromEvent(e) * totalDuration.value)
}
function onBarPointerMove(e: PointerEvent) {
  if (!dragging) return
  currentTime.value = props.nativeSeek
    ? ratioFromEvent(e) * totalDuration.value
    : ratioFromEvent(e) * totalDuration.value - (props.startOffset || 0)
}
function onBarPointerUp(e: PointerEvent) {
  if (!dragging) return
  dragging = false
  ;(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId)
  if (!totalDuration.value) return
  seekToAbsolute(ratioFromEvent(e) * totalDuration.value)
}

// ===== 音量 / 倍速 / 全屏 / 画中画 =====
function toggleMute() {
  const media = videoRef.value
  if (!media) return
  media.muted = !media.muted
  muted.value = media.muted
}
function onVolumeInput(e: Event) {
  const media = videoRef.value
  if (!media) return
  const v = Number((e.target as HTMLInputElement).value)
  media.volume = v
  volume.value = v
  media.muted = v === 0
  muted.value = media.muted
}
function pickRate(r: number) {
  const media = videoRef.value
  rate.value = r
  if (media) media.playbackRate = r
  menu.value = ''
}
/**
 * 全屏：优先「网页元素全屏」（控制栏 / 进度条都能用），
 * 元素全屏不可被支持时退回 <video> 原生全屏（iOS Safari、部分国产内核只有这条路）。
 *
 * 注意顺序：能力探测必须在 await 之前完成 —— iOS 的原生全屏要求同步调用，
 * 一旦先 await 一个被拒绝的 Promise，用户手势就过期了。
 */
async function toggleFullscreen() {
  // 平板客户端：全屏交给原生 ExoPlayer（系统硬解 / 多音轨 / 字幕轨都在那边）
  // 每次点击重查桥 —— 页面被 bfcache 恢复时模块级常量可能是 null
  if (nativeApp || androidBridge()) {
    openNativeFullscreen()
    return
  }
  // 壳在、桥不在：别去试浏览器全屏（WebView 大多没有这套 API），直接把原因说出来
  if (appShell) {
    openNativeFailure('App 与网页版本不匹配：网页没拿到原生桥接，请更新 App')
    return
  }
  const el = rootRef.value as FsElement | null
  const media = videoRef.value as FsVideo | null
  // iOS 原生播放器全屏不注册到 document.fullscreenElement，只能问 video 自己
  if (media?.webkitDisplayingFullscreen) {
    try {
      media.webkitExitFullscreen?.()
    } catch {
      showHint('退出全屏失败，请点原生播放器上的「完成」')
    }
    return
  }
  const entering = !currentFullscreenEl()

  if (!entering) {
    const d = document as FsDocument
    try {
      if (d.exitFullscreen) await d.exitFullscreen()
      else if (d.webkitExitFullscreen) await d.webkitExitFullscreen()
      else if (d.msExitFullscreen) await d.msExitFullscreen()
    } catch {
      showHint('退出全屏失败，请按 Esc')
    }
    return
  }

  const request =
    el &&
    (el.requestFullscreen ||
      el.webkitRequestFullscreen ||
      el.webkitRequestFullScreen ||
      el.mozRequestFullScreen ||
      el.msRequestFullscreen)

  if (el && request) {
    try {
      await request.call(el)
      // 有些国产内核（X5 / UC）的 requestFullscreen 存在但只是个空壳：
      // Promise 正常返回、页面根本没全屏。稍等一下复核，没生效就退回视频原生全屏。
      window.setTimeout(() => {
        if (currentFullscreenEl()) return
        const v = videoRef.value as FsVideo | null
        if (!v || v.webkitDisplayingFullscreen) return
        if (typeof v.webkitEnterFullscreen === 'function') {
          try {
            v.webkitEnterFullscreen()
            return
          } catch {
            /* 落到提示 */
          }
        }
        showHint('全屏没生效，可换 Chrome / Edge 或用系统播放器打开')
      }, 700)
      return
    } catch {
      // 常见于内嵌 iframe（缺 allow="fullscreen"）：往下走视频原生全屏
    }
  }

  if (media && typeof media.webkitEnterFullscreen === 'function') {
    try {
      media.webkitEnterFullscreen()
      return
    } catch {
      /* 继续报错 */
    }
  }
  if (document.fullscreenEnabled === false) {
    showHint('当前页面环境禁用了全屏（多见于内嵌窗口），请在新窗口打开')
    return
  }
  showHint('这个浏览器不支持全屏，可换 Chrome / Edge 或用系统播放器打开')
}

/**
 * 平板客户端按了全屏但打不开时，必须让用户看见原因。
 *
 * 之前这里的三条失败路径（没有桥 / 没有 videoId / 原生返回 false）全是 `return`，
 * 在 WebView 里表现就是「按了没反应」—— 业主反馈的正是这句。
 * 现在统一走 `fail()`：页面出药丸提示，同时让原生弹一条 Toast（小窗/全屏切换时
 * 页面提示可能被盖住），并打到 logcat。
 */
function openNativeFailure(reason: string) {
  showHint(reason)
  try {
    androidBridge()?.toast(reason)
  } catch {
    /* 原生没有 toast 就只留页面提示 */
  }
}

/**
 * 把当前播放交给全屏原生播放器。
 *
 * 起点用 displayTime（片源绝对秒）：转码窗口下网页时间是「窗口内秒」，
 * 原生自己建会话需要绝对秒，否则会从窗口起点重新开始。
 */
function openNativeFullscreen() {
  const media = videoRef.value
  const fallback = () => {
    const el = rootRef.value as FsElement | null
    if (!el) return false
    const request = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen
    if (request) {
      try {
        void request.call(el)
        return true
      } catch {
        return false
      }
    }
    const v = media as FsVideo | null
    if (v && typeof v.webkitEnterFullscreen === 'function') {
      v.webkitEnterFullscreen()
      return true
    }
    return false
  }
  // 桥可能在后来的页面生命周期里才可用（bfcache 恢复 / 极早期渲染），每次点击重查
  const bridge = nativeApp || androidBridge()
  if (!bridge) {
    // WebView 壳里却没有桥：只可能是 App 与网页版本不匹配（或页面在外部浏览器里）
    if (appShell) {
      openNativeFailure('App 与网页版本不匹配：网页没拿到原生桥接，请更新 App')
      return
    }
    if (!fallback()) showHint('这个浏览器不支持全屏，可换 Chrome / Edge 或用系统播放器打开')
    return
  }
  if (!props.nativeVideoId) {
    openNativeFailure('网页没拿到视频 ID，刷新页面后再点全屏')
    return
  }
  const startAt = Math.max(0, displayTime.value || 0)
  // 先停网页播放器：否则两边同时出声
  if (media && !media.paused) media.pause()
  const ok = playNativeFullscreen({
    videoId: props.nativeVideoId,
    quality: props.activeQuality || 'original',
    startSeconds: startAt,
    title: props.nativeTitle || '',
    subtitleText: props.nativeSubtitle || '',
    poster: props.poster || '',
  })
  if (!ok) {
    if (media) void media.play().catch(() => {})
    if (!fallback()) {
      openNativeFailure('原生播放器没接住（App 版本过旧或登录态失效），可先刷新页面')
    }
    return
  }
  wantPlay = false
  emit('native-handoff', startAt)
}

/**
 * 平板客户端点「画中画」：与全屏同一条交接流程，只是让原生起来后立刻缩成小窗。
 *
 * 网页自己的 `requestPictureInPicture` 在 WebView 里不存在，所以这条是平板上
 * 唯一的画中画通路；交接后窗口态会话必须停掉（`native-handoff`），否则服务端
 * 会同时跑两路转码。
 */
function openNativePip() {
  const media = videoRef.value
  if (!props.nativeVideoId) {
    showHint('这台设备不支持画中画')
    return
  }
  const startAt = Math.max(0, displayTime.value || 0)
  if (media && !media.paused) media.pause()
  const ok = playNativePip({
    videoId: props.nativeVideoId,
    quality: props.activeQuality || 'original',
    startSeconds: startAt,
    title: props.nativeTitle || '',
    subtitleText: props.nativeSubtitle || '',
    poster: props.poster || '',
  })
  if (!ok) {
    if (media) void media.play().catch(() => {})
    showHint('画中画没打开，可以先全屏再点小窗')
    return
  }
  wantPlay = false
  emit('native-handoff', startAt)
}

function onFullscreenChange() {
  const was = isFullscreen.value
  isFullscreen.value = !!currentFullscreenEl()
  // 进出全屏都把菜单栏亮出来：否则一进全屏只剩画面、看不到退出按钮，像「点了没反应」
  if (isFullscreen.value !== was) showControls()
}

/** iOS 原生播放器全屏（webkitEnterFullscreen）不会派发 fullscreenchange，用自家事件补状态 */
function onWebkitBeginFullscreen() {
  isFullscreen.value = true
}
function onWebkitEndFullscreen() {
  isFullscreen.value = false
}

/**
 * 画中画：<video> 上的 disablepictureinpicture 已移除 —— 它会让
 * requestPictureInPicture() 直接抛 InvalidStateError（按钮点了没反应就是这个）。
 */
async function togglePip() {
  // 平板客户端：WebView 没有 PiP API，交给原生把整个播放页缩成系统小窗
  if (!canPip.value && canNativePip.value) {
    openNativePip()
    return
  }
  const media = videoRef.value
  if (!media) return
  if (!canPip.value) {
    showHint('这个浏览器不支持画中画')
    return
  }
  try {
    if (document.pictureInPictureElement) {
      await document.exitPictureInPicture()
      return
    }
    if (media.readyState < 1) {
      showHint('画面还没准备好，稍后再试')
      return
    }
    await media.requestPictureInPicture()
  } catch (e) {
    const name = (e as { name?: string } | null)?.name || ''
    if (name === 'NotAllowedError') showHint('浏览器拒绝了画中画（可能限制在内嵌页面里）')
    else if (name === 'NotSupportedError') showHint('这个视频不支持画中画')
    else showHint('无法开启画中画')
  }
}

function onEnterPip() {
  pipActive.value = true
}
function onLeavePip() {
  pipActive.value = false
}

// ===== 手势：单击/悬停唤出菜单栏，双击画面起播（播放中双切换播放暂停）=====
let lastTap = 0
let hideTimer: number | null = null

/** 唤出控制栏；播放中 2.6 秒无操作自动收起（待播态常驻，不做自动收起） */
function showControls(autoHide = true) {
  controlsVisible.value = true
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
  if (autoHide && playing.value) {
    hideTimer = window.setTimeout(() => {
      controlsVisible.value = false
      hideTimer = null
    }, 2600)
  }
}

function onStageMove() {
  showControls()
}

function onStageLeave() {
  if (hideTimer !== null) {
    window.clearTimeout(hideTimer)
    hideTimer = null
  }
  // 待播态：滑出就收起菜单栏（封面状态本来就不该有菜单）
  if (playing.value || props.pending) controlsVisible.value = false
}

/** 双击（或触屏双击）画面：待播态起播，播放中切播放/暂停 */
function triggerPlay() {
  if (props.pending) {
    emit('play-request')
    return
  }
  togglePlay()
}

/**
 * 单击画面只负责「唤出菜单栏」，不直接播放 —— 起播要么点菜单栏的播放按钮，
 * 要么双击画面。触屏没有 dblclick 事件（部分内核不派发），所以在这里自己做
 * 340ms 内的双击判定。
 */
function onStageClick() {
  // 画质 / 字幕 / 倍速菜单开着时，点画面先收起菜单（否则菜单会挂在控制栏里跟着一起隐现）
  if (menu.value) {
    menu.value = ''
    return
  }
  const coarse =
    typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches
  if (coarse) {
    const now = Date.now()
    if (now - lastTap < 340) {
      lastTap = 0
      triggerPlay()
      return
    }
    lastTap = now
  } else if (controlsVisible.value && playing.value) {
    // 鼠标：播放中再点一次收起（否则没有收起入口）
    controlsVisible.value = false
    return
  }
  showControls()
}

function onStageDblClick(e: MouseEvent) {
  e.preventDefault()
  triggerPlay()
}

// ===== 键盘 =====
function onKeydown(e: KeyboardEvent) {
  const media = videoRef.value
  if (!media) return
  const target = e.target as HTMLElement | null
  if (target && /INPUT|TEXTAREA/.test(target.tagName)) return
  if (e.key === ' ' || e.key === 'k') {
    e.preventDefault()
    togglePlay()
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    // 必须走统一的绝对时间轴判定：转码窗口内直接改 currentTime 会绕过
    // 「目标是否已转出」的检查，向右跳进未转区域时只能干等。
    seekToAbsolute(displayTime.value + 5)
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    seekToAbsolute(displayTime.value - 5)
  } else if (e.key === 'm') {
    toggleMute()
  } else if (e.key === 'f') {
    void toggleFullscreen()
  }
}

// ===== 生命周期 =====
onMounted(() => {
  document.addEventListener('fullscreenchange', onFullscreenChange)
  // 旧 WebKit / 老 Edge 只派发带前缀的事件，不补监听会导致图标状态不跟着变
  document.addEventListener('webkitfullscreenchange', onFullscreenChange)
  document.addEventListener('msfullscreenchange', onFullscreenChange)
  const media = videoRef.value
  if (media) {
    media.addEventListener('enterpictureinpicture', onEnterPip)
    media.addEventListener('leavepictureinpicture', onLeavePip)
    media.addEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen)
    media.addEventListener('webkitendfullscreen', onWebkitEndFullscreen)
  }
  void nextTick(() => applySource())
})

onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  document.removeEventListener('webkitfullscreenchange', onFullscreenChange)
  document.removeEventListener('msfullscreenchange', onFullscreenChange)
  if (switchingTimer) window.clearTimeout(switchingTimer)
  if (hideTimer !== null) window.clearTimeout(hideTimer)
  if (hintTimer !== null) window.clearTimeout(hintTimer)
  destroyHls()
  const media = videoRef.value
  if (media) {
    media.removeEventListener('enterpictureinpicture', onEnterPip)
    media.removeEventListener('leavepictureinpicture', onLeavePip)
    media.removeEventListener('webkitbeginfullscreen', onWebkitBeginFullscreen)
    media.removeEventListener('webkitendfullscreen', onWebkitEndFullscreen)
    try {
      media.pause()
      media.removeAttribute('src')
      media.load()
    } catch {
      /* 忽略 */
    }
  }
})

/**
 * 换源触发条件用「字符串指纹」而不是数组引用：
 * 父组件的 sources 是 computed，重算会换新数组；deep 监听会因此反复重建源。
 * 指纹 = 实际选中的 src + 时间轴口径，只有它变了才真的换源。
 */
function sourceKey(): string {
  const s = currentSource()
  return [s?.src || '', props.nativeSeek ? 'n' : 't', props.startOffset || 0].join('#')
}

watch(
  () => sourceKey(),
  (key, prev) => {
    if (key === prev) return
    if (props.pending && !props.sources.length) return
    applySource()
  },
)

// 从「待播」切到「播放中」时，sources 可能先于 pending 到位：补一次装载
watch(
  () => props.pending,
  (v) => {
    if (v) return
    const media = videoRef.value
    if (media && !media.getAttribute('src') && !hls && props.sources.length) applySource()
    // 起播后菜单栏不该一直挂着：亮一下，2.6 秒后自动收起
    showControls()
  },
)

watch(
  () => [props.subtitles, props.startOffset],
  () => attachSubtitleTracks(),
  { deep: true },
)

// 父组件换会话后重新给 seekInto（精确定位目标帧）
watch(
  () => props.seekInto,
  (v) => {
    if (v > 0.35 && !props.nativeSeek) {
      const media = videoRef.value
      if (media && media.readyState >= 1) media.currentTime = v
    }
  },
)

defineExpose({ seekTo })

const QUALITY_TEXT: Record<string, string> = {
  original: '原画',
  '2160p': '2160P',
  '1440p': '1440P',
  '1080p': '1080P',
  '720p': '720P',
  '480p': '480P',
  '360p': '360P',
}
function qualityText(key: string) {
  return QUALITY_TEXT[key] || key.toUpperCase()
}
const qualityTip = computed(() => [props.playModeLabel, props.playModeReason].filter(Boolean).join(' · '))
const qualityGroups = computed(() => {
  const list = props.qualities || []
  return { original: list.includes('original'), rest: list.filter((k) => k !== 'original') }
})
</script>

<template>
  <div
    ref="rootRef"
    class="video-player"
    :class="{ 'is-pending': props.pending, 'is-switching': switching, 'controls-on': controlsVisible }"
    tabindex="0"
    @keydown="onKeydown"
    @pointermove="onStageMove"
    @pointerleave="onStageLeave"
  >
    <video
      ref="videoRef"
      class="vp-media"
      playsinline
      webkit-playsinline
      x5-playsinline
      x5-video-player-type="h5-page"
      controlslist="nodownload"
      preload="metadata"
      :poster="props.poster"
      @loadedmetadata="onLoadedMetadata"
      @timeupdate="onTimeUpdate"
      @play="onPlay"
      @pause="onPause"
      @ended="onEnded"
      @waiting="onWaiting"
      @playing="onPlaying"
      @error="onMediaError"
      @click="onStageClick"
      @dblclick.stop="onStageDblClick"
    />

    <!-- 待播态只留封面：播放入口在菜单栏的播放按钮上，双击画面也能起播 -->

    <!-- 错误层 -->
    <div v-if="errorText && !props.pending" class="vp-error">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7.5v5.5M12 16.4v.1" stroke-linecap="round" />
      </svg>
      <span>{{ errorText }}</span>
      <!-- 平板客户端：网页内核解不了时由**用户自己决定**是否交给原生，
           不自动跳、也不自动转码（业主口径：解码方式看设备，干预越少越好） -->
      <button
        v-if="nativeApp && props.nativeVideoId"
        type="button"
        style="margin-left:12px;flex-shrink:0;padding:4px 12px;border-radius:999px;border:1px solid rgba(255,255,255,.35);background:rgba(255,255,255,.14);color:#fff;font-size:12px;cursor:pointer"
        @click="openNativeFullscreen"
      >用原生播放器打开</button>
    </div>

    <!-- 一次性提示：全屏 / 画中画失败时给一句人话，不再点了没反应 -->
    <div v-if="hintText" class="vp-hint">{{ hintText }}</div>

    <!-- 控制栏 -->
    <div class="vp-controls" :class="{ on: controlsVisible || !playing }">
      <!-- 待播态没有时长/进度，只留按钮：封面 → 单击/悬停 → 这条菜单栏出现 -->
      <div
        v-if="!props.pending"
        ref="barRef"
        class="vp-bar"
        @pointerdown="onBarPointerDown"
        @pointermove="onBarPointerMove"
        @pointerup="onBarPointerUp"
        @pointercancel="onBarPointerUp"
      >
        <span class="vp-bar-buf" :style="{ width: bufferedPct + '%' }" />
        <span class="vp-bar-fill" :style="{ width: progressPct + '%' }" />
        <span class="vp-bar-knob" :style="{ left: progressPct + '%' }" />
      </div>

      <div class="vp-actions">
        <button class="vp-btn" type="button" :aria-label="playing ? '暂停' : '播放'" @click="togglePlay">
          <svg v-if="playing" viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
            <path d="M7 5h4v14H7zM13 5h4v14h-4z" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
        </button>

        <span v-if="!props.pending" class="vp-time">{{ timeText }}</span>

        <div class="vp-spacer" />

        <button class="vp-btn" type="button" aria-label="静音" @click="toggleMute">
          <svg v-if="!muted && volume > 0" viewBox="0 0 24 24" width="19" height="19" fill="currentColor" aria-hidden="true">
            <path d="M4 9v6h4l5 4V5L8 9H4zm12.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="19" height="19" fill="currentColor" aria-hidden="true">
            <path d="M4 9v6h4l5 4V5L8 9H4zm14.7 3-1.9-1.9 1.1-1.1 1.9 1.9 1.9-1.9 1.1 1.1-1.9 1.9 1.9 1.9-1.1 1.1-1.9-1.9-1.9 1.9-1.1-1.1z" />
          </svg>
        </button>
        <input
          class="vp-vol"
          type="range"
          min="0"
          max="1"
          step="0.05"
          :value="muted ? 0 : volume"
          aria-label="音量"
          @input="onVolumeInput"
        />

        <!-- 字幕 -->
        <div v-if="(props.subtitles || []).length" class="vp-menu-wrap">
          <button class="vp-btn vp-btn--text" type="button" @click="menu = menu === 'subtitle' ? '' : 'subtitle'">字幕</button>
          <div v-if="menu === 'subtitle'" class="vp-menu">
            <button class="vp-menu-item" :class="{ on: activeSubtitleKey === 'off' }" @click="pickSubtitle('off')">关闭</button>
            <button
              v-for="(t, i) in props.subtitles"
              :key="t.url + i"
              class="vp-menu-item"
              :class="{ on: activeSubtitleKey === String(i) }"
              @click="pickSubtitle(String(i))"
            >
              {{ t.label }}
            </button>
          </div>
        </div>

        <!-- 画质 -->
        <div v-if="(props.qualities || []).length" class="vp-menu-wrap">
          <button
            class="vp-btn vp-btn--text"
            type="button"
            :title="qualityTip"
            @click="menu = menu === 'quality' ? '' : 'quality'"
          >
            {{ qualityText(props.activeQuality) }}
          </button>
          <div v-if="menu === 'quality'" class="vp-menu">
            <button
              v-if="qualityGroups.original"
              class="vp-menu-item"
              :class="{ on: props.activeQuality === 'original' }"
              @click="menu = ''; emit('quality-select', 'original')"
            >
              原画
            </button>
            <template v-if="qualityGroups.rest.length">
              <div class="vp-menu-title">转码</div>
              <button
                v-for="q in qualityGroups.rest"
                :key="q"
                class="vp-menu-item"
                :class="{ on: props.activeQuality === q }"
                @click="menu = ''; emit('quality-select', q)"
              >
                {{ qualityText(q) }}
              </button>
            </template>
          </div>
        </div>

        <!-- 倍速 -->
        <div class="vp-menu-wrap">
          <button class="vp-btn vp-btn--text" type="button" @click="menu = menu === 'speed' ? '' : 'speed'">
            {{ rate === 1 ? '倍速' : rate + 'x' }}
          </button>
          <div v-if="menu === 'speed'" class="vp-menu">
            <button v-for="r in [0.5, 1, 1.25, 1.5, 2]" :key="r" class="vp-menu-item" :class="{ on: rate === r }" @click="pickRate(r)">
              {{ r }}x
            </button>
          </div>
        </div>

        <button
          v-if="canPip || canNativePip"
          class="vp-btn"
          :class="{ 'is-on': pipActive }"
          type="button"
          :aria-label="pipActive ? '退出画中画' : '画中画'"
          @click="togglePip"
        >
          <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <rect x="12" y="11" width="7" height="6" rx="1" fill="currentColor" stroke="none" />
          </svg>
        </button>

        <button class="vp-btn" type="button" :aria-label="isFullscreen ? '退出全屏' : '全屏'" @click="toggleFullscreen">
          <svg v-if="!isFullscreen" viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
            <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" stroke-linecap="round" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
            <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.video-player {
  position: relative;
  width: 100%;
  height: 100%;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  outline: none;
}
.vp-media {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #000;
}

/* 全屏：部分内核不会自动把元素拉满（父级有 transform / 固定宽高时尤其明显），
   这里显式铺满视口，并去掉圆角，避免四周留一圈页面底色。 */
.video-player:fullscreen,
.video-player:-webkit-full-screen,
.video-player:-moz-full-screen,
.video-player:-ms-fullscreen {
  position: fixed;
  inset: 0;
  width: 100vw;
  height: 100vh;
  height: 100dvh;
  max-width: none;
  max-height: none;
  border-radius: 0;
  background: #000;
}
.video-player:fullscreen .vp-media,
.video-player:-webkit-full-screen .vp-media {
  width: 100%;
  height: 100%;
}
/* 全屏时控制栏压到 iPhone 底部安全区之上 */
.video-player:fullscreen .vp-controls,
.video-player:-webkit-full-screen .vp-controls {
  padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
}

/* 一次性提示（全屏 / 画中画失败） */
.vp-hint {
  position: absolute;
  left: 50%;
  top: 14px;
  transform: translateX(-50%);
  z-index: 5;
  max-width: min(88%, 420px);
  padding: 7px 13px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.72);
  color: #fff;
  font-size: 12.5px;
  line-height: 1.4;
  text-align: center;
  pointer-events: none;
  backdrop-filter: blur(8px);
}

/* 待播入口 */
/* 错误 */
.vp-error {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: min(86%, 420px);
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.72);
  color: #fecaca;
  font-size: 12.5px;
  line-height: 1.5;
}

/* 控制栏 */
.vp-controls {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 26px 12px 10px;
  /* 压在 4K 高饱和画面上也要能读：底部实心一点，往上才淡出 */
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.82),
    rgba(0, 0, 0, 0.45) 52%,
    rgba(0, 0, 0, 0) 100%
  );
  opacity: 0;
  transform: translateY(6px);
  transition: opacity 0.2s ease, transform 0.2s ease;
  pointer-events: none;
}
.vp-controls.on {
  opacity: 1;
  transform: none;
  pointer-events: auto;
}
.vp-bar {
  position: relative;
  height: 4px;
  margin: 0 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.28);
  cursor: pointer;
  touch-action: none;
}
.vp-bar-buf {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.45);
}
.vp-bar-fill {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  border-radius: 999px;
  background: #0485f7;
}
.vp-bar-knob {
  position: absolute;
  top: 50%;
  width: 10px;
  height: 10px;
  margin-left: -5px;
  transform: translateY(-50%);
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.25);
  pointer-events: none;
}
.vp-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #fff;
}
.vp-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  height: 30px;
  padding: 0 4px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #fff;
  cursor: pointer;
  opacity: 0.9;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.55);
}
.vp-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  opacity: 1;
}
.vp-btn--text {
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
/* 画中画正在小窗里放着：按钮亮起来，提示当前状态 */
.vp-btn.is-on {
  background: rgba(255, 255, 255, 0.18);
  opacity: 1;
}
.vp-time {
  margin-left: 2px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: rgba(255, 255, 255, 0.86);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.55);
}
.vp-spacer {
  flex: 1;
}
.vp-vol {
  width: 58px;
  height: 30px;
  -webkit-appearance: none;
  appearance: none;
  background: transparent;
  cursor: pointer;
  /* 原生滑块在亮画面上是一根扎眼的蓝条，改成与进度条同款细轨 + 白点 */
  opacity: 0.72;
  transition: opacity 0.2s ease;
}
.vp-vol:hover {
  opacity: 1;
}
.vp-vol::-webkit-slider-runnable-track {
  height: 3px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.34);
}
.vp-vol::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 9px;
  height: 9px;
  margin-top: -3px;
  border: 0;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.28);
}
.vp-vol::-moz-range-track {
  height: 3px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.34);
}
.vp-vol::-moz-range-thumb {
  width: 9px;
  height: 9px;
  border: 0;
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.28);
}
.vp-menu-wrap {
  position: relative;
}
.vp-menu {
  position: absolute;
  right: 0;
  bottom: 36px;
  min-width: 96px;
  max-height: 240px;
  overflow-y: auto;
  padding: 5px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: rgba(18, 20, 26, 0.92);
  backdrop-filter: blur(14px);
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.45);
}
.vp-menu-title {
  padding: 4px 8px;
  font-size: 10px;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.45);
}
.vp-menu-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(255, 255, 255, 0.9);
  font: inherit;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.vp-menu-item:hover {
  background: rgba(255, 255, 255, 0.12);
}
.vp-menu-item.on {
  color: #7cb6ff;
  font-weight: 600;
}
.vp-menu-item.on::before {
  content: '✓ ';
  font-size: 11px;
}

@media (max-width: 768px) {
  .vp-vol {
    display: none;
  }
  .vp-time {
    font-size: 10.5px;
  }
  .vp-controls {
    padding-bottom: 8px;
  }
}
</style>
