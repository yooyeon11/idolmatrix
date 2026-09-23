import { defineStore } from 'pinia'
import { ref } from 'vue'
import { appSettingsApi } from '@/api/appSettings'
import type {
  AppAppearanceSettings,
  AppExternalProxySettings,
  AppMtPhotosSettings,
  AppSettings,
  AppTmdbSettings,
  HomeHeroImage,
  HomeStageType,
} from '@/types/models'
import { setAppearanceChangeHook, useThemeStore } from './theme'
import {
  aiDefaults,
  outboundDescPrompt,
  saveAi as cacheIngestAi,
  setAiSnapshot,
  stripLegacyAiFields,
  withDescPromptFallback,
  type AiSettings,
} from '@/views/settings/aiLocal'

const MTPHOTOS_KEY = 'kpml_mtphotos_settings'
const PROXY_KEY = 'kpml_external_proxy_settings'
const TMDB_KEY = 'kpml_tmdb_settings'
const STORAGE_KEY = 'kpml_show_covers'
const mtphotosDefaults: AppMtPhotosSettings = {
  enabled: false,
  base_url: '',
  api_key: '',
  disk_prefix: '',
  mount_path: '/data/mt-photos',
  api_key_set: false,
}
const externalProxyDefaults: AppExternalProxySettings = {
  enabled: false,
  url: '',
}
const tmdbDefaults: AppTmdbSettings = {
  enabled: false,
  api_key: '',
  api_key_set: false,
}
/**
 * 外观与显示默认值。2026-09-22 起这一组改为**跨设备同步**（后端 app_settings.appearance）——
 * 原先只存本机 localStorage，换设备/浏览器就变回默认。localStorage 现只作首屏缓存与
 * 未登录兜底，hydrate 后一律以服务端值为准。
 */
const appearanceDefaults: AppAppearanceSettings = {
  theme: 'dark',
  accent: 'blue',
  show_covers: true,
  home_hero_image: 'avatar',
}
const HERO_VIDEO_ID_KEY = 'kpml_home_hero_video_id'
const HERO_VIDEO_IDS_KEY = 'kpml_home_hero_video_ids'
const HOME_STAGE_TYPE_KEY = 'kpml_home_stage_type'
const HOME_HERO_IMAGE_KEY = 'kpml_home_hero_image'

function resolveShowCovers(): boolean {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === '0') return false
  return true
}

function resolveHeroVideoId(): number | null {
  const saved = localStorage.getItem(HERO_VIDEO_ID_KEY)
  if (saved == null || saved.trim() === '') return null
  const n = Number(saved)
  return Number.isFinite(n) && n > 0 ? n : null
}

function resolveHeroVideoIds(): number[] {
  try {
    const raw = localStorage.getItem(HERO_VIDEO_IDS_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    if (!Array.isArray(arr)) return []
    return arr.filter((n): n is number => Number.isFinite(n) && n > 0)
  } catch {
    return []
  }
}

function resolveHomeStageType(): HomeStageType {
  // 默认艺人/组合聚焦轮播；仅显式选择 video 时用视频轮播
  return localStorage.getItem(HOME_STAGE_TYPE_KEY) === 'video' ? 'video' : 'artist'
}

/**
 * PC 首页刊头（HomeMagazineHero）优先图。默认 `avatar` = 改动前的既有行为
 * （有头像用头像，无头像才回退横幅）。选 `banner` 时改为「有横幅就用横幅，无横幅回退头像」。
 *
 * ⚠ 与「外观与显示」卡里其它项（主题 / 强调色 / 显示封面）同一口径：**只落本机 localStorage**，
 * 不进后端 app_settings —— 它是单台设备的观感偏好，改完立即生效、无保存按钮、未登录也生效。
 * ⚠ **只作用于 PC**：移动端首页走 `HomeCinemaHero`（1:1 头像盒），不读这一项。
 */
function resolveHomeHeroImage(): HomeHeroImage {
  return localStorage.getItem(HOME_HERO_IMAGE_KEY) === 'banner' ? 'banner' : 'avatar'
}

function cacheHeroLocal(videoId: number | null) {
  localStorage.removeItem('kpml_home_hero_title')
  localStorage.removeItem('kpml_home_hero_tagline')
  localStorage.removeItem('kpml_home_hero_sub')
  if (videoId == null) localStorage.removeItem(HERO_VIDEO_ID_KEY)
  else localStorage.setItem(HERO_VIDEO_ID_KEY, String(videoId))
}

function cacheHeroHomeLocal(videoIds: number[], stageType?: HomeStageType) {
  localStorage.setItem(HERO_VIDEO_IDS_KEY, JSON.stringify(videoIds))
  localStorage.removeItem('kpml_home_display_mode')
  if (stageType) localStorage.setItem(HOME_STAGE_TYPE_KEY, stageType)
}

export const useSettingsStore = defineStore('settings', () => {
  const showCovers = ref<boolean>(resolveShowCovers())
  const heroVideoId = ref<number | null>(resolveHeroVideoId())
  const heroVideoIds = ref<number[]>(resolveHeroVideoIds())
  const homeStageType = ref<HomeStageType>(resolveHomeStageType())
  const homeHeroImage = ref<HomeHeroImage>(resolveHomeHeroImage())
  const ingestAi = ref<AiSettings>({ ...aiDefaults })
  const mtphotos = ref<AppMtPhotosSettings>({ ...mtphotosDefaults })
  const externalProxy = ref<AppExternalProxySettings>({ ...externalProxyDefaults })
  const tmdb = ref<AppTmdbSettings>({ ...tmdbDefaults })
  const hydrated = ref(false)

  function setShowCovers(v: boolean) {
    showCovers.value = v
    localStorage.setItem(STORAGE_KEY, v ? '1' : '0')
    void saveAppearance()
  }

  /** PC 首页刊头优先图（改完立即生效，无保存按钮；2026-09-22 起随外观同步到后端） */
  function setHomeHeroImage(v: HomeHeroImage) {
    homeHeroImage.value = v === 'banner' ? 'banner' : 'avatar'
    localStorage.setItem(HOME_HERO_IMAGE_KEY, homeHeroImage.value)
    void saveAppearance()
  }

  function applyHero(videoId: number | null) {
    heroVideoId.value = videoId
    cacheHeroLocal(videoId)
  }

  /** 当前外观与显示的完整快照（前端统一全量提交；后端 PATCH 也支持部分字段） */
  function snapshotAppearance(): AppAppearanceSettings {
    const themeStore = useThemeStore()
    return {
      theme: themeStore.mode,
      accent: themeStore.accent,
      show_covers: showCovers.value,
      home_hero_image: homeHeroImage.value,
    }
  }

  /** 应用后端下发的外观值，并回写本机缓存（首屏用） */
  function applyAppearance(remote?: AppAppearanceSettings) {
    const a = { ...appearanceDefaults, ...(remote || {}) }
    useThemeStore().applyRemote(a.theme, a.accent)
    showCovers.value = a.show_covers !== false
    localStorage.setItem(STORAGE_KEY, showCovers.value ? '1' : '0')
    homeHeroImage.value = a.home_hero_image === 'banner' ? 'banner' : 'avatar'
    localStorage.setItem(HOME_HERO_IMAGE_KEY, homeHeroImage.value)
  }

  /**
   * 推送外观与显示到后端（跨设备同步）。失败（未登录 401 / 网络不通）时静默 —— 本地已生效，
   * 不打断用户操作。连点用「保存中则标脏再来一轮」合并，保证最后一次值一定落库。
   */
  let appearanceSaving = false
  let appearanceDirty = false
  async function saveAppearance() {
    if (appearanceSaving) {
      appearanceDirty = true
      return
    }
    appearanceSaving = true
    try {
      do {
        appearanceDirty = false
        const remote = await appSettingsApi.patch({ appearance: snapshotAppearance() })
        applyRemote(remote)
      } while (appearanceDirty)
    } catch {
      /* 后端不可用时保持本机行为 */
    } finally {
      appearanceSaving = false
    }
  }

  function applyRemote(remote: AppSettings) {
    applyAppearance(remote.appearance)
    applyHero(remote.hero.video_id ?? null)
    const remoteIds = Array.isArray(remote.hero.video_ids)
      ? remote.hero.video_ids.filter((n) => Number.isFinite(n) && n > 0)
      : []
    heroVideoIds.value = remoteIds
    homeStageType.value = remote.hero.stage_type === 'video' ? 'video' : 'artist'
    cacheHeroHomeLocal(heroVideoIds.value, homeStageType.value)
    ingestAi.value = withDescPromptFallback({ ...aiDefaults, ...remote.ingest_ai })
    mtphotos.value = { ...mtphotosDefaults, ...(remote.mtphotos || {}) }
    localStorage.setItem(MTPHOTOS_KEY, JSON.stringify({ ...mtphotos.value, api_key: '' }))
    externalProxy.value = { ...externalProxyDefaults, ...(remote.external_proxy || {}) }
    localStorage.setItem(PROXY_KEY, JSON.stringify(externalProxy.value))
    tmdb.value = { ...tmdbDefaults, ...(remote.tmdb || {}) }
    localStorage.setItem(TMDB_KEY, JSON.stringify({ ...tmdb.value, api_key: '' }))
    // 跨站放行开关与「内容与匹配」栏均已从产品层移除（前者只剩部署层 env，
    // 后者档位固定 strict），后端不再回传 network / ingest 分区。
    setAiSnapshot({
      ingest_ai: ingestAi.value,
    })
    cacheIngestAi(ingestAi.value)
  }

  async function persistHero() {
    const remote = await appSettingsApi.patch({
      hero: {
        video_id: heroVideoId.value,
        video_ids: heroVideoIds.value,
        stage_type: homeStageType.value,
      },
    })
    applyRemote(remote)
  }

  // 保存首页展示配置：轮播池（第一条为主打视频）+ 主舞台类型；展示模式固定沉浸
  async function setHeroHome(
    videoIds: number[],
    stageType?: HomeStageType,
  ) {
    heroVideoIds.value = videoIds
    heroVideoId.value = videoIds[0] ?? null
    if (stageType) homeStageType.value = stageType
    cacheHeroLocal(heroVideoId.value)
    cacheHeroHomeLocal(videoIds, homeStageType.value)
    await persistHero()
  }

  // 切换沉浸模式主舞台类型（艺人/组合轮播 vs 视频轮播）
  async function setHomeStageType(stageType: HomeStageType) {
    homeStageType.value = stageType
    cacheHeroHomeLocal(heroVideoIds.value, stageType)
    await persistHero()
  }

  async function setHeroVideoId(v: number | null) {
    heroVideoId.value = v
    cacheHeroLocal(v)
  }

  async function saveIngestAi() {
    // 中文简介提示词与默认文本一致时存空串 = 跟随后端内置默认（后端升级默认能自动生效）
    const remote = await appSettingsApi.patch({
      ingest_ai: {
        ...ingestAi.value,
        desc_prompt: outboundDescPrompt(ingestAi.value.desc_prompt),
      },
    })
    applyRemote(remote)
  }

  async function resetIngestAi() {
    ingestAi.value = { ...aiDefaults }
    await saveIngestAi()
  }

  async function saveMtphotos() {
    const remote = await appSettingsApi.patch({ mtphotos: { ...mtphotos.value } })
    applyRemote(remote)
  }

  async function resetMtphotos() {
    mtphotos.value = { ...mtphotosDefaults }
    await saveMtphotos()
  }

  async function saveExternalProxy() {
    const remote = await appSettingsApi.patch({ external_proxy: { ...externalProxy.value } })
    applyRemote(remote)
  }

  async function resetExternalProxy() {
    externalProxy.value = { ...externalProxyDefaults }
    await saveExternalProxy()
  }

  async function saveTmdb() {
    const remote = await appSettingsApi.patch({ tmdb: { ...tmdb.value } })
    applyRemote(remote)
  }

  async function resetTmdb() {
    tmdb.value = { ...tmdbDefaults }
    await saveTmdb()
  }

  function scrubLocalApiKeys() {
    for (const key of [
      'kpml_ingest_ai_settings',
      'kpml_ai_settings',
      'kpml_home_title_ai_settings',
      MTPHOTOS_KEY,
      TMDB_KEY,
    ]) {
      const raw = localStorage.getItem(key)
      if (!raw) continue
      try {
        const obj = JSON.parse(raw) as { api_key?: string }
        if (obj && typeof obj === 'object' && obj.api_key) {
          localStorage.setItem(key, JSON.stringify({ ...obj, api_key: '' }))
        }
      } catch {
        /* ignore */
      }
    }
  }

  // 已移除的设置项：清掉老用户 localStorage 里的残留键（详情页主图固定头像）
  function dropLegacyKeys() {
    localStorage.removeItem('kpml_detail_hero_media')
    // 原「自动化能力」三开关（auto_describe/auto_tag/auto_subject）已下线
    for (const key of ['kpml_ingest_ai_settings', 'kpml_ai_settings']) {
      const raw = localStorage.getItem(key)
      if (!raw) continue
      try {
        const obj = JSON.parse(raw) as Record<string, unknown>
        if (!obj || typeof obj !== 'object') continue
        let dirty = false
        for (const k of ['auto_describe', 'auto_tag', 'auto_subject']) {
          if (k in obj) {
            delete obj[k]
            dirty = true
          }
        }
        if (dirty) localStorage.setItem(key, JSON.stringify(obj))
      } catch {
        /* ignore */
      }
    }
  }

  async function hydrate() {
    scrubLocalApiKeys()
    try {
      const remote = await appSettingsApi.get()
      applyRemote(remote)
    } catch (e) {
      const status = (e as Error & { status?: number }).status
      if (status === 401) {
        hydrated.value = true
        return
      }
      setAiSnapshot({
        ingest_ai: ingestAi.value,
      })
    } finally {
      hydrated.value = true
    }
  }

  function readLocalJson<T extends object>(key: string, fallback: T, extraKeys: string[] = []): T {
    for (const k of [key, ...extraKeys]) {
      const raw = localStorage.getItem(k)
      if (!raw) continue
      try {
        return { ...fallback, ...(JSON.parse(raw) as Partial<T>) }
      } catch {
        /* ignore */
      }
    }
    return { ...fallback }
  }

  ingestAi.value = withDescPromptFallback(
    stripLegacyAiFields(
      readLocalJson('kpml_ingest_ai_settings', aiDefaults, ['kpml_ai_settings']),
    ),
  )
  mtphotos.value = readLocalJson(MTPHOTOS_KEY, mtphotosDefaults)
  externalProxy.value = readLocalJson(PROXY_KEY, externalProxyDefaults)
  tmdb.value = readLocalJson(TMDB_KEY, tmdbDefaults)
  dropLegacyKeys()
  setAiSnapshot({
    ingest_ai: ingestAi.value,
  })
  // 主题 / 强调色可能在页头主题按钮上被改 → 通过钩子回流到这里同步后端（避免 store 互相 import）
  setAppearanceChangeHook(() => {
    void saveAppearance()
  })

  return {
    showCovers,
    setShowCovers,
    setHomeHeroImage,
    saveAppearance,
    heroVideoId,
    heroVideoIds,
    homeStageType,
    homeHeroImage,
    ingestAi,
    mtphotos,
    externalProxy,
    tmdb,
    hydrated,
    setHeroVideoId,
    setHeroHome,
    setHomeStageType,
    saveIngestAi,
    resetIngestAi,
    saveMtphotos,
    resetMtphotos,
    saveExternalProxy,
    resetExternalProxy,
    saveTmdb,
    resetTmdb,
    hydrate,
  }
})
