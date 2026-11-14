import { defineStore } from 'pinia'
import { ref } from 'vue'
import { appSettingsApi } from '@/api/appSettings'
import type {
  AppAppearanceSettings,
  AppExternalProxySettings,
  AppMtPhotosSettings,
  AppSettings,
  AppTmdbSettings,
  AppYoutubeSettings,
} from '@/types/models'
import { setAppearanceChangeHook, useThemeStore } from './theme'

const MTPHOTOS_KEY = 'kpml_mtphotos_settings'
const PROXY_KEY = 'kpml_external_proxy_settings'
const TMDB_KEY = 'kpml_tmdb_settings'
const YOUTUBE_KEY = 'kpml_youtube_settings'
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
const youtubeDefaults: AppYoutubeSettings = {
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
  group_hero_dim: 1,
}

/** 组合详情页海报压暗档位：1=轻度（默认）/2=中等/3=通透；越界一律回落到 1 */
const HERO_DIM_KEY = 'kpml_group_hero_dim'

function normalizeHeroDim(v: unknown): number {
  const n = Number(v)
  return n === 2 || n === 3 ? n : 1
}

function resolveHeroDim(): number {
  return normalizeHeroDim(localStorage.getItem(HERO_DIM_KEY))
}

function resolveShowCovers(): boolean {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === '0') return false
  return true
}

export const useSettingsStore = defineStore('settings', () => {
  const showCovers = ref<boolean>(resolveShowCovers())
  const groupHeroDim = ref<number>(resolveHeroDim())
  const mtphotos = ref<AppMtPhotosSettings>({ ...mtphotosDefaults })
  const externalProxy = ref<AppExternalProxySettings>({ ...externalProxyDefaults })
  const tmdb = ref<AppTmdbSettings>({ ...tmdbDefaults })
  const youtube = ref<AppYoutubeSettings>({ ...youtubeDefaults })
  const hydrated = ref(false)

  function setShowCovers(v: boolean) {
    showCovers.value = v
    localStorage.setItem(STORAGE_KEY, v ? '1' : '0')
    void saveAppearance()
  }

  /** 组合详情页海报压暗档位（1/2/3）：立即生效并落库 */
  function setGroupHeroDim(v: number) {
    groupHeroDim.value = normalizeHeroDim(v)
    localStorage.setItem(HERO_DIM_KEY, String(groupHeroDim.value))
    void saveAppearance()
  }

  /** 当前外观与显示的完整快照（前端统一全量提交；后端 PATCH 也支持部分字段） */
  function snapshotAppearance(): AppAppearanceSettings {
    const themeStore = useThemeStore()
    return {
      theme: themeStore.mode,
      accent: themeStore.accent,
      show_covers: showCovers.value,
      group_hero_dim: groupHeroDim.value,
    }
  }

  /** 应用后端下发的外观值，并回写本机缓存（首屏用） */
  function applyAppearance(remote?: AppAppearanceSettings) {
    const a = { ...appearanceDefaults, ...(remote || {}) }
    useThemeStore().applyRemote(a.theme, a.accent)
    showCovers.value = a.show_covers !== false
    localStorage.setItem(STORAGE_KEY, showCovers.value ? '1' : '0')
    groupHeroDim.value = normalizeHeroDim(a.group_hero_dim)
    localStorage.setItem(HERO_DIM_KEY, String(groupHeroDim.value))
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
    mtphotos.value = { ...mtphotosDefaults, ...(remote.mtphotos || {}) }
    localStorage.setItem(MTPHOTOS_KEY, JSON.stringify({ ...mtphotos.value, api_key: '' }))
    externalProxy.value = { ...externalProxyDefaults, ...(remote.external_proxy || {}) }
    localStorage.setItem(PROXY_KEY, JSON.stringify(externalProxy.value))
    tmdb.value = { ...tmdbDefaults, ...(remote.tmdb || {}) }
    localStorage.setItem(TMDB_KEY, JSON.stringify({ ...tmdb.value, api_key: '' }))
    youtube.value = { ...youtubeDefaults, ...(remote.youtube || {}) }
    localStorage.setItem(YOUTUBE_KEY, JSON.stringify({ ...youtube.value, api_key: '' }))
    // 跨站放行开关与「内容与匹配」栏均已从产品层移除（前者只剩部署层 env，
    // 后者档位固定 strict），后端不再回传 network / ingest 分区。
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

  async function saveYoutube() {
    const remote = await appSettingsApi.patch({ youtube: { ...youtube.value } })
    applyRemote(remote)
  }

  async function resetYoutube() {
    youtube.value = { ...youtubeDefaults }
    await saveYoutube()
  }

  function scrubLocalApiKeys() {
    for (const key of [
      'kpml_ingest_ai_settings',
      'kpml_ai_settings',
      'kpml_home_title_ai_settings',
      MTPHOTOS_KEY,
      TMDB_KEY,
      YOUTUBE_KEY,
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

  // 已移除的设置项：清掉老用户 localStorage 里的残留键（详情页主图固定头像、旧首页 hero 配置）
  function dropLegacyKeys() {
    localStorage.removeItem('kpml_detail_hero_media')
    // 旧首页刊头/轮播配置（2026-10-16 首页 H 方案改版后全部下线）
    for (const key of [
      'kpml_home_hero_video_id',
      'kpml_home_hero_video_ids',
      'kpml_home_stage_type',
      'kpml_home_hero_image',
      'kpml_home_hero_title',
      'kpml_home_hero_tagline',
      'kpml_home_hero_sub',
      'kpml_home_display_mode',
    ]) {
      localStorage.removeItem(key)
    }
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

  mtphotos.value = readLocalJson(MTPHOTOS_KEY, mtphotosDefaults)
  externalProxy.value = readLocalJson(PROXY_KEY, externalProxyDefaults)
  tmdb.value = readLocalJson(TMDB_KEY, tmdbDefaults)
  youtube.value = readLocalJson(YOUTUBE_KEY, youtubeDefaults)
  dropLegacyKeys()
  // 主题 / 强调色可能在页头主题按钮上被改 → 通过钩子回流到这里同步后端（避免 store 互相 import）
  setAppearanceChangeHook(() => {
    void saveAppearance()
  })

  return {
    showCovers,
    setShowCovers,
    groupHeroDim,
    setGroupHeroDim,
    saveAppearance,
    mtphotos,
    externalProxy,
    tmdb,
    youtube,
    hydrated,
    saveMtphotos,
    resetMtphotos,
    saveExternalProxy,
    resetExternalProxy,
    saveTmdb,
    resetTmdb,
    saveYoutube,
    resetYoutube,
    hydrate,
  }
})
