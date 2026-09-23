import { computed, onMounted, onUnmounted, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import type { MusicVideo, PhotoSection } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { MV_TYPES, LIVE_TYPES, isShortVideo, videoHasType } from '@/utils/videoTypes'

interface EntityLike {
  id: number
  uid?: string | null
}

// 艺人/组合详情页共用的标签栏
export const ENTITY_TABS = [
  { key: 'overview', label: '概览' },
  { key: 'all', label: '全部作品' },
  { key: 'mv', label: '音乐视频' },
  { key: 'live', label: '现场 · 直拍' },
  { key: 'behind', label: '幕后 · 花絮' },
  { key: 'shorts', label: '短视频' },
  { key: 'official', label: '官方' },
  { key: 'fan', label: '粉丝' },
  { key: 'wall', label: '照片墙' },
]

const PHOTO_TABS = ['official', 'fan', 'wall'] as const

// ===== 视频区工具栏（歌曲筛选 / 搜索 / 排序） =====
// 排序键与照片墙 SORT_KEYS 同构；持久化键独立（视频区与照片墙记忆互不干扰）
export type VideoSortKey = 'date_desc' | 'date_asc' | 'name_asc' | 'name_desc'

export const VIDEO_SORT_OPTIONS: { key: VideoSortKey; label: string }[] = [
  { key: 'date_desc', label: '最新优先' },
  { key: 'date_asc', label: '最早优先' },
  { key: 'name_asc', label: '名称 A→Z' },
  { key: 'name_desc', label: '名称 Z→A' },
]

const VIDEO_SORT_STORAGE_KEY = 'kpml_video_sort'

function resolveVideoSort(): VideoSortKey {
  const saved = localStorage.getItem(VIDEO_SORT_STORAGE_KEY)
  return VIDEO_SORT_OPTIONS.some((o) => o.key === saved)
    ? (saved as VideoSortKey)
    : 'date_desc'
}

export interface VideoSongOption {
  label: string
  value: number
}

export interface EntityDetailOptions<T extends EntityLike> {
  fetchEntity: (param: string) => Promise<T>
  entityPath: (uid: string) => string
  fetchVideos: (entity: T) => Promise<{ items: MusicVideo[] }>
  // 非关键附加数据（成员/公司等）：失败时由调用方自行兜底为空数组，不应让页面变成"不存在"
  fetchExtras?: (entity: T, isCurrent: () => boolean) => Promise<void>
}

// 详情页共用逻辑：
// - loadSeq 防竞态：快速切换路由时丢弃过期响应
// - 实体请求失败 → notFound；作品列表失败 → videosError（页内横幅 + 重试，不误报"不存在"）
// - 长视频/短视频/分类筛选/时间线等集合计算
export function useEntityDetail<T extends EntityLike>(
  options: EntityDetailOptions<T>,
  activeTab: Ref<string>,
) {
  const route = useRoute()
  const router = useRouter()
  const message = useMessage()

  const entity = ref<T | null>(null)
  const loading = ref(false)
  const notFound = ref(false)
  const videos = ref<MusicVideo[]>([])
  const videosError = ref('')

  // 作品列表独立序列号：重试只刷新列表，不牵动实体，也不被后续导航的旧响应覆盖
  let loadSeq = 0
  let videosSeq = 0

  async function loadVideos(e: T) {
    const seq = ++videosSeq
    videosError.value = ''
    try {
      const vp = await options.fetchVideos(e)
      if (seq !== videosSeq) return
      videos.value = vp.items
    } catch (err) {
      if (seq !== videosSeq) return
      videosError.value = (err as Error).message || '作品列表加载失败'
    }
  }

  // 重试入口：实体已成功加载，仅重新拉取作品列表
  function retryVideos() {
    if (entity.value) loadVideos(entity.value)
  }

  async function load() {
    const seq = ++loadSeq
    const param = String(route.params.uid || '').trim()
    if (!param) {
      if (seq !== loadSeq) return
      notFound.value = true
      entity.value = null
      return
    }
    loading.value = true
    notFound.value = false
    // 新实体开始加载：清掉上一实体的作品数据，避免加载失败时串页显示旧数据
    videos.value = []
    videosError.value = ''
    // 同步重置视频区筛选状态（歌曲/搜索是按实体圈定的，换实体必须清）
    videoSongId.value = null
    videoSearchText.value = ''
    videoSearch.value = ''
    try {
      const e = await options.fetchEntity(param)
      if (seq !== loadSeq) return
      entity.value = e
      if (e.uid && e.uid !== param) {
        router.replace(options.entityPath(e.uid))
      }
      const isCurrent = () => seq === loadSeq
      const extras = options.fetchExtras?.(e, isCurrent) ?? Promise.resolve()
      await Promise.all([extras, loadVideos(e)])
    } catch (err) {
      if (seq !== loadSeq) return
      notFound.value = true
      message.error((err as Error).message)
    } finally {
      if (seq === loadSeq) loading.value = false
    }
  }

  onMounted(load)

  // 路由参数变化（同组件复用）时按新 id 重新加载；指向当前实体则跳过
  watch(() => route.params.uid, (newId, oldId) => {
    if (!newId || newId === oldId) return
    const cur = entity.value
    if (cur && (cur.uid === String(newId) || String(cur.id) === String(newId))) return
    load()
  })

  const longVideos = computed(() => videos.value.filter((v) => !isShortVideo(v)))
  const shortVideos = computed(() => videos.value.filter(isShortVideo))

  const sortedVideos = computed(() => {
    const list = [...longVideos.value]
    list.sort((a, b) => {
      const da = a.performance_date || a.release_date || a.published_date || ''
      const db = b.performance_date || b.release_date || b.published_date || ''
      return da < db ? 1 : da > db ? -1 : 0
    })
    return list
  })

  const latestVideos = computed(() => sortedVideos.value.slice(0, 6))

  const activeVideos = computed(() => {
    if (activeTab.value === 'shorts') return shortVideos.value
    const map: Record<string, string[]> = {
      mv: MV_TYPES,
      live: LIVE_TYPES,
      behind: ['SpecialVideo', 'Other'],
    }
    const allowed = map[activeTab.value]
    const pool = longVideos.value
    if (!allowed) return pool
    return pool.filter((v) => videoHasType(v, allowed))
  })

  // 条目自带唯一 id：模板 key 直接用它，避免"日期+标题"拼接重复
  const timeline = computed(() => {
    const items: { id: number; uid: string; date: string; title: string; type: string }[] = []
    for (const v of sortedVideos.value) {
      const d = v.performance_date || v.release_date || v.published_date
      if (!d) continue
      items.push({
        id: v.id,
        uid: v.uid,
        date: d,
        title: v.name,
        type: VIDEO_TYPE_LABEL[v.video_type] || v.video_type,
      })
    }
    return items.slice(0, 8)
  })

  const isPhotoTab = computed(() =>
    PHOTO_TABS.includes(activeTab.value as (typeof PHOTO_TABS)[number]),
  )
  const photoSection = computed(() => activeTab.value as PhotoSection)

  // ===== 视频区工具栏状态（在 activeVideos 分类过滤之后叠加） =====
  const videoSongId = ref<number | null>(null)
  const videoSearchText = ref('')
  const videoSearch = ref('')
  const videoSortKey = ref<VideoSortKey>(resolveVideoSort())
  let videoSearchTimer: ReturnType<typeof setTimeout> | undefined

  watch(videoSearchText, (v) => {
    clearTimeout(videoSearchTimer)
    // 与照片墙同款 350ms 防抖：打字不触发重排，停顿才应用
    videoSearchTimer = setTimeout(() => {
      videoSearch.value = v.trim()
    }, 350)
  })
  watch(videoSortKey, (v) => {
    localStorage.setItem(VIDEO_SORT_STORAGE_KEY, v)
  })

  // 歌曲筛选候选：当前实体全部视频出现过的歌曲（song_id / song_ids / songs / tracks 去重）
  const videoSongOptions = computed<VideoSongOption[]>(() => {
    const byId = new Map<number, string>()
    for (const v of videos.value) {
      const push = (id: number | null | undefined, name?: string | null) => {
        if (id == null || byId.has(id)) return
        byId.set(id, name || v.song_name || `歌曲 #${id}`)
      }
      push(v.song_id, v.song_name)
      for (const id of v.song_ids || []) push(id)
      for (const s of v.songs || []) push(s.id, s.name)
      for (const t of v.tracks || []) push(t.song_id)
    }
    return [...byId.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label, 'zh-Hans-CN'))
  })

  function videoMatchesSong(v: MusicVideo, songId: number): boolean {
    return (
      v.song_id === songId ||
      (v.song_ids || []).includes(songId) ||
      (v.songs || []).some((s) => s.id === songId) ||
      (v.tracks || []).some((t) => t.song_id === songId)
    )
  }

  const hasVideoFilters = computed(
    () => videoSongId.value != null || !!videoSearch.value,
  )

  function resetVideoFilters() {
    videoSongId.value = null
    videoSearchText.value = ''
    videoSearch.value = ''
  }

  // 最终展示列表：分类 tab → 歌曲筛选 → 搜索 → 排序
  const displayedVideos = computed(() => {
    let list = activeVideos.value
    const songId = videoSongId.value
    if (songId != null) list = list.filter((v) => videoMatchesSong(v, songId))
    const q = videoSearch.value.toLowerCase()
    if (q) {
      list = list.filter((v) =>
        [v.name, v.original_title, v.song_name, v.subject_artist_name].some(
          (t) => (t || '').toLowerCase().includes(q),
        ),
      )
    }
    const sorted = [...list]
    const dateOf = (v: MusicVideo) =>
      v.performance_date || v.release_date || v.published_date || ''
    sorted.sort((a, b) => {
      switch (videoSortKey.value) {
        case 'date_asc':
          return dateOf(a).localeCompare(dateOf(b))
        case 'name_asc':
          return a.name.localeCompare(b.name, 'zh-Hans-CN')
        case 'name_desc':
          return b.name.localeCompare(a.name, 'zh-Hans-CN')
        default:
          return dateOf(a) < dateOf(b) ? 1 : dateOf(a) > dateOf(b) ? -1 : 0
      }
    })
    return sorted
  })

  // ===== 手机端吸顶标签栏：下滑隐藏、上滑复现 =====
  // 标签栏吸顶后与顶栏一起占掉 ~92px 固定高度，内容从其下方穿过被长期遮挡；
  // 下滑阅读时把标签栏滑出视口（藏到不透明顶栏后面），上滑再滑回来，桌面端不受影响
  const tabsEl = ref<HTMLElement | null>(null)
  const tabsHidden = ref(false)
  let lastScrollY = 0
  let narrowMq: MediaQueryList | null = null

  function onTabsScroll() {
    if (!narrowMq?.matches) {
      tabsHidden.value = false
      return
    }
    const y = window.scrollY
    const dy = y - lastScrollY
    lastScrollY = y
    // 标签栏尚未吸顶（仍在文档原位）时不隐藏
    if (!tabsEl.value || tabsEl.value.getBoundingClientRect().top > 49) {
      tabsHidden.value = false
      return
    }
    if (dy > 2 && y > 120) tabsHidden.value = true
    else if (dy < -2) tabsHidden.value = false
  }

  onMounted(() => {
    narrowMq = window.matchMedia('(max-width: 768px)')
    lastScrollY = window.scrollY
    window.addEventListener('scroll', onTabsScroll, { passive: true })
  })
  onUnmounted(() => {
    window.removeEventListener('scroll', onTabsScroll)
  })

  return {
    entity,
    loading,
    notFound,
    videos,
    videosError,
    retryVideos,
    longVideos,
    shortVideos,
    sortedVideos,
    latestVideos,
    activeVideos,
    timeline,
    isPhotoTab,
    photoSection,
    videoSongId,
    videoSearchText,
    videoSortKey,
    videoSongOptions,
    displayedVideos,
    hasVideoFilters,
    resetVideoFilters,
    tabsEl,
    tabsHidden,
  }
}
