<script setup lang="ts">
import { computed, h, onMounted, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, useDialog, useMessage, type SelectOption } from 'naive-ui'
import { authApi, type AuthUser } from '@/api/auth'
import { libraryApi } from '@/api/library'
import { photosApi } from '@/api/photos'
import { providersApi } from '@/api/providers'
import { systemApi } from '@/api/system'
import { useAppStore } from '@/stores/app'
import type {
  CacheStats,
  FFmpegStatus,
  LibraryCleanupResult,
  SystemPaths,
} from '@/types/models'
import {
  InboxOutlined,
  RefreshOutlined,
  RobotOutlined,
  FolderOutlined,
  InfoOutlined,
  SettingsOutlined,
  DeleteOutlined,
  PersonOutlined,
  DatabaseOutlined,
  DashboardOutlined,
  GroupOutlined,
  MusicNoteOutlined,
  AlbumOutlined,
  UploadOutlined,
} from '@/components/icons'
import SaHeader from '@/components/SaHeader.vue'
import SaSelect from '@/components/SaSelect.vue'
import type { SaOption } from '@/components/SaSelect.vue'
import SaOverflowTabs from '@/components/SaOverflowTabs.vue'
import DbGroupListPanel from '@/components/db/DbGroupListPanel.vue'
import DbGroupWorkspacePanel from '@/components/db/DbGroupWorkspacePanel.vue'
// 资料库其余入口（数据体检 / 艺人 / 专辑 / 歌曲 / 回收站）内嵌复用整页组件
import DataHealthView from '@/views/DataHealthView.vue'
import DbArtistsList from '@/views/DbArtistsList.vue'
import DbArtistWorkspace from '@/views/DbArtistWorkspace.vue'
import DbAlbumsList from '@/views/DbAlbumsList.vue'
import DbAlbumWorkspace from '@/views/DbAlbumWorkspace.vue'
import DbSongsList from '@/views/DbSongsList.vue'
import DbSongWorkspace from '@/views/DbSongWorkspace.vue'
import DbRecycleView from '@/views/DbRecycleView.vue'
import { useAuthStore } from '@/stores/auth'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore, type ThemeAccent } from '@/stores/theme'

import SettingsAiPanel from '@/views/settings/SettingsAiPanel.vue'
import SettingsIncomingPanel from '@/views/settings/SettingsIncomingPanel.vue'
import SettingsUploadersPanel from '@/views/settings/SettingsUploadersPanel.vue'


const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const app = useAppStore()
const settings = useSettingsStore()
const auth = useAuthStore()

function onShowCoversChange(e: Event) {
  settings.setShowCovers((e.target as HTMLInputElement).checked)
}

const themeStore = useThemeStore()

function onThemeModeChange(e: Event) {
  themeStore.setMode((e.target as HTMLInputElement).checked ? 'dark' : 'light')
}

// ===== 强调色：统一走选择器组件（SaSelect）=====
// 紫色（紫罗兰）档已移除；色点靠 render-label 画在选项和已选项里。
const ACCENT_DOTS: Record<string, string> = {
  rose: '#e11d48',
  blue: '#0485f7',
}
const accentOptions = [
  { value: 'rose', label: '玫瑰暖调' },
  { value: 'blue', label: '经典蓝' },
]
const accentModel = computed({
  get: () => themeStore.accent as string,
  set: (v: string) => onAccentChange(v as ThemeAccent),
})

/** 下拉 / 已选项渲染：色点 + 名称（naive 的下拉挂在 body 下，样式必须全局） */
function renderAccentLabel(option: SelectOption) {
  const dot = ACCENT_DOTS[String(option.value)]
  if (!dot) return String(option.label ?? '')
  return h('span', { class: 'sa-accent-opt' }, [
    h('i', { class: 'sa-accent-opt__dot', style: { background: dot } }),
    h('span', String(option.label ?? '')),
  ])
}

function onAccentChange(a: ThemeAccent) {
  themeStore.setAccent(a)
}

// ===== PC 首页刊头（杂志轮播）优先图 =====
// 只影响 PC 的 `HomeMagazineHero`；移动端 `HomeCinemaHero` 是 1:1 头像盒，不读这一项。
// 与同卡片其它项一致：改完立即生效、只落本机 localStorage（见 stores/settings.ts）。
const HERO_IMAGE_OPTIONS: SaOption[] = [
  { value: 'avatar', label: '头像图片' },
  { value: 'banner', label: '横幅海报' },
]
const heroImageModel = computed({
  get: () => settings.homeHeroImage as string,
  set: (v: string) => settings.setHomeHeroImage(v === 'banner' ? 'banner' : 'avatar'),
})

// ===== 账号 =====
async function onLogout() {
  await auth.logout()
  router.replace({ name: 'login' })
}

async function onLogoutAll() {
  await auth.logoutAll()
  router.replace({ name: 'login' })
}

// ===== 修改密码 =====
const pwdOld = ref('')
const pwdNew = ref('')
const pwdConfirm = ref('')
const pwdSaving = ref(false)

const usernameDraft = ref(auth.user?.username || '')
const usernameSaving = ref(false)
watch(
  () => auth.user?.username,
  (name) => {
    if (name) usernameDraft.value = name
  },
)

async function onChangeUsername() {
  const name = usernameDraft.value.trim()
  if (!name) {
    message.warning('请填写用户名')
    return
  }
  if (name === auth.user?.username) {
    message.info('用户名未变化')
    return
  }
  usernameSaving.value = true
  try {
    const r = await authApi.patchMe({ username: name })
    auth.setUser(r.user)
    usernameDraft.value = r.user.username
    message.success('用户名已修改')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    usernameSaving.value = false
  }
}

const isOwner = computed(() => auth.user?.role === 'owner')
const agentList = ref<AuthUser[]>([])
const agentUser = ref('')
const agentPass = ref('')
const agentPass2 = ref('')
const agentSaving = ref(false)

async function loadAgents() {
  if (!isOwner.value) {
    agentList.value = []
    return
  }
  try {
    const r = await authApi.listUsers()
    agentList.value = (r.items || []).filter((u) => u.role === 'agent')
  } catch {
    agentList.value = []
  }
}

async function onCreateAgent() {
  const name = agentUser.value.trim()
  if (!name) {
    message.warning('请填写测试账号用户名')
    return
  }
  if (!agentPass.value) {
    message.warning('请填写测试账号密码')
    return
  }
  if (agentPass.value.length < 8 || agentPass.value.length > 128) {
    message.warning('密码长度须为 8–128 位')
    return
  }
  if (agentPass.value !== agentPass2.value) {
    message.warning('两次输入的密码不一致')
    return
  }
  agentSaving.value = true
  try {
    await authApi.createUser({ username: name, password: agentPass.value })
    agentUser.value = ''
    agentPass.value = ''
    agentPass2.value = ''
    await loadAgents()
    message.success('测试账号已创建')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    agentSaving.value = false
  }
}

function onDeleteAgent(u: AuthUser) {
  dialog.warning({
    title: '删除测试账号',
    content: `确定删除测试账号「${u.username}」？该账号将立即无法登录。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await authApi.deleteUser(u.id)
        await loadAgents()
        message.success('已删除')
      } catch (e) {
        message.error((e as Error).message)
        return false
      }
    },
  })
}

async function onChangePassword() {
  if (!pwdOld.value || !pwdNew.value) {
    message.warning('请填写当前密码与新密码')
    return
  }
  if (pwdNew.value.length < 8 || pwdNew.value.length > 128) {
    message.warning('新密码长度须为 8–128 位')
    return
  }
  if (pwdNew.value !== pwdConfirm.value) {
    message.warning('两次输入的新密码不一致')
    return
  }
  pwdSaving.value = true
  try {
    await authApi.changePassword(pwdOld.value, pwdNew.value)
    pwdOld.value = ''
    pwdNew.value = ''
    pwdConfirm.value = ''
    message.success('密码已修改，其他设备已退出登录')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    pwdSaving.value = false
  }
}

const mtPingLoading = ref(false)
const mtPingHint = ref('')

async function pingMtphotos() {
  mtPingLoading.value = true
  mtPingHint.value = ''
  try {
    const r = await photosApi.mtPing({
      base_url: settings.mtphotos.base_url,
      api_key: settings.mtphotos.api_key,
    })
    mtPingHint.value = `已连接${r.version ? ` ${r.version}` : ''}，${r.album_count} 个相册`
    message.success(mtPingHint.value)
  } catch (e) {
    mtPingHint.value = (e as Error).message
    message.error(mtPingHint.value)
  } finally {
    mtPingLoading.value = false
  }
}

async function saveMtphotos() {
  try {
    await settings.saveMtphotos()
    message.success('MT Photos 设置已保存（各设备共用）')
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function resetMtphotos() {
  try {
    await settings.resetMtphotos()
    mtPingHint.value = ''
    message.success('已恢复默认')
  } catch (e) {
    message.error((e as Error).message)
  }
}

// ===== 站点获取代理 =====
// 原「放行跨站写请求」开关已移除（v3.2.18）：设置页不再暴露该能力，
// 只剩部署层环境变量 ALLOW_CROSS_ORIGIN_WRITES。卡片现在只管代理一项。
const proxySaving = ref(false)

async function saveProxySettings() {
  if (settings.externalProxy.enabled && !(settings.externalProxy.url || '').trim()) {
    message.error('开启代理前请先填写代理地址')
    return
  }
  proxySaving.value = true
  try {
    await settings.saveExternalProxy()
    message.success('代理设置已保存（各设备共用）')
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    proxySaving.value = false
  }
}

async function resetProxySettings() {
  try {
    await settings.resetExternalProxy()
    message.success('已恢复默认（关闭代理）')
  } catch (e) {
    message.error((e as Error).message)
  }
}

// ===== TMDB 艺人资料源 =====
const tmdbPingLoading = ref(false)
const tmdbPingHint = ref('')

async function pingTmdb() {
  tmdbPingLoading.value = true
  tmdbPingHint.value = ''
  try {
    await providersApi.tmdbTest(settings.tmdb.api_key)
    tmdbPingHint.value = '连接成功，API Key 有效'
    message.success(tmdbPingHint.value)
  } catch (e) {
    tmdbPingHint.value = (e as Error).message
    message.error(tmdbPingHint.value)
  } finally {
    tmdbPingLoading.value = false
  }
}

async function saveTmdb() {
  const hasKey = !!(settings.tmdb.api_key || '').trim() || !!settings.tmdb.api_key_set
  if (settings.tmdb.enabled && !hasKey) {
    message.error('启用 TMDB 前请先填写 API Key')
    return
  }
  try {
    await settings.saveTmdb()
    message.success('TMDB 设置已保存（各设备共用）')
  } catch (e) {
    message.error((e as Error).message)
  }
}

async function resetTmdb() {
  try {
    await settings.resetTmdb()
    tmdbPingHint.value = ''
    message.success('已恢复默认')
  } catch (e) {
    message.error((e as Error).message)
  }
}

// ===== 侧边栏分区 =====
const sections = [
  { key: 'base', icon: SettingsOutlined, label: '基础设置' },
  { key: 'incoming', icon: InboxOutlined, label: '待整理' },
  { key: 'ai', icon: RobotOutlined, label: 'AI 设置' },
  { key: 'db', icon: DatabaseOutlined, label: '资料库' },
  { key: 'system', icon: InfoOutlined, label: '系统信息' },
]
const activeSection = ref('incoming')

// ===== 资料库入口 =====
// 全部条目都在设置页·资料库范围内内嵌展开，不再跳整屏独立页
type DbEntryKey = 'dashboard' | 'groups' | 'artists' | 'albums' | 'songs' | 'uploaders' | 'recycle'
const dbEntries: { key: DbEntryKey; icon: Component; label: string; desc: string }[] = [
  { key: 'dashboard', icon: DashboardOutlined, label: '数据体检', desc: '全库问题扫描、健康分与修复直达' },
  { key: 'groups', icon: GroupOutlined, label: '组合', desc: '成员轨迹、公司关系与作品完整度' },
  { key: 'artists', icon: PersonOutlined, label: '艺人', desc: '艺人档案、所属组合与作品关联' },
  { key: 'albums', icon: AlbumOutlined, label: '专辑', desc: '发行主体、曲目与封面完整度' },
  { key: 'songs', icon: MusicNoteOutlined, label: '歌曲', desc: '演唱者、专辑/影像关联与孤立检查' },
  { key: 'uploaders', icon: UploadOutlined, label: '博主管理', desc: '按发布博主固定视频类型，待整理自动选中' },
  { key: 'recycle', icon: DeleteOutlined, label: '回收站', desc: '软删除记录的恢复与永久删除' },
]

/**
 * 资料库分区内的子视图（全部内嵌，不离开设置页）：
 * home = 入口卡；dashboard = 数据体检；groups/group = 组合列表 / 工作台；
 * artists/artist、albums/album、songs/song = 各自列表 / 工作台；
 * uploaders = 博主管理；recycle = 回收站
 */
type DbView =
  | 'home'
  | 'dashboard'
  | 'groups'
  | 'group'
  | 'artists'
  | 'artist'
  | 'albums'
  | 'album'
  | 'songs'
  | 'song'
  | 'uploaders'
  | 'recycle'
const dbView = ref<DbView>('home')
/** 内嵌工作台当前实体 uid */
const dbEmbedUid = ref('')

const DB_PATH_TO_VIEW: Record<string, DbView> = {
  groups: 'group',
  artists: 'artist',
  albums: 'album',
  songs: 'song',
}

function openDbGroup(uid: string) {
  dbEmbedUid.value = uid
  dbView.value = 'group'
}

/** 解析 /db/(groups|artists|albums|songs)/:uid 深链 → 切到对应内嵌工作台 */
function openDbPath(path: string) {
  const m = /^\/db\/(groups|artists|albums|songs)\/([^/?#]+)/.exec(path)
  if (!m) return
  dbEmbedUid.value = m[2]
  dbView.value = DB_PATH_TO_VIEW[m[1]] ?? 'home'
}

// ===== 系统信息板块 =====
const ffmpeg = ref<FFmpegStatus | null>(null)
const paths = ref<SystemPaths | null>(null)
const config = ref<Awaited<ReturnType<typeof systemApi.config>> | null>(null)
const sysLoading = ref(false)

async function loadSystem() {
  sysLoading.value = true
  try {
    const [f, p, c] = await Promise.all([
      systemApi.ffmpeg(),
      libraryApi.paths(),
      systemApi.config(),
    ])
    ffmpeg.value = f
    paths.value = p
    config.value = c
    app.ffmpeg = f
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    sysLoading.value = false
  }
}

// ===== 缓存管理板块 =====
const cacheStats = ref<CacheStats | null>(null)
const cacheLoading = ref(false)
const cacheClearing = ref(false)
// 统计写成**一整句**（原来「0 个缓存目录 · 0 个文件」在左、字节数被 margin-left:auto 顶到最右，
// 中间空一大片）。放 computed 里拼，也免得模板里跨行插值多出空格。
const cacheSummaryText = computed(() => {
  const c = cacheStats.value
  if (!c) return '尚未统计'
  return `${c.dirs} 个目录 · ${c.files} 个文件 · ${formatBytes(c.total_bytes)}`
})

function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** i
  return `${value >= 100 || i === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`
}

async function loadCache() {
  cacheLoading.value = true
  try {
    cacheStats.value = await systemApi.cache()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    cacheLoading.value = false
  }
}

// ===== 失效视频清理 =====
const missingResult = ref<LibraryCleanupResult | null>(null)
const missingLoading = ref(false)
const missingCleaning = ref(false)

async function loadMissing() {
  missingLoading.value = true
  try {
    missingResult.value = await libraryApi.missing()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    missingLoading.value = false
  }
}

function missingSummaryText(r: LibraryCleanupResult | null): string {
  if (!r) return '尚未扫描'
  if (r.skipped_unmounted) return '正式库可能未挂载或半挂载，已跳过以免误删'
  if (r.missing === 0) return '当前没有失效视频'
  return `发现 ${r.missing} 条文件已不存在的记录`
}

async function cleanupMissing(force = false, purgeFiles = false) {
  const preview = missingResult.value
  const count = preview?.missing ?? 0
  if (!force && preview?.skipped_unmounted) {
    dialog.warning({
      title: '正式库可能未挂载',
      content:
        '正式库目录下扫到的视频远少于库记录，或目录不可访问。若存储（如 NAS）尚未挂载就强制清理，会把整个媒体库记录标为删除。确认存储已挂载且文件确实都已删除后再继续。',
      positiveText: '仍然强制清理',
      negativeText: '取消',
      onPositiveClick: () => cleanupMissing(true, purgeFiles),
    })
    return
  }
  if (!force && count === 0) {
    message.info('当前没有失效视频')
    return
  }
  const names = (preview?.items || []).slice(0, 8).map((it) => it.name)
  const extra = (preview?.items?.length || 0) > 8 ? ' 等' : ''
  if (!purgeFiles) {
    const text = force
      ? `将强制软删除所有找不到文件的视频记录（扫描 ${preview?.scanned ?? 0} 条）。记录进回收站。默认不删除磁盘上的视频、封面、info.json。`
      : `将从库中移除 ${count} 条文件已删除的视频${names.length ? `：${names.join('、')}${extra}` : ''}。记录进回收站，不会删除任何本地视频、图片或 json。`
    dialog.warning({
      title: force ? '强制清理失效记录' : '清理失效视频记录',
      content: text,
      positiveText: '只删记录',
      negativeText: '取消',
      onPositiveClick: async () => {
        missingCleaning.value = true
        try {
          const result = await libraryApi.cleanupMissing(force, false)
          missingResult.value = result
          message.success(
            result.cleaned > 0 ? `已软删除 ${result.cleaned} 条记录（未删磁盘文件）` : '没有需要清理的记录',
          )
        } catch (e) {
          message.error((e as Error).message)
          return false
        } finally {
          missingCleaning.value = false
        }
      },
    })
    return
  }
  dialog.error({
    title: '确认删除本地文件',
    content: `将软删除 ${count} 条记录，并删除对应缩略图；仅当视频文件确认不存在时，才会删除旁边的封面和 info.json。此操作不可从回收站恢复这些文件。`,
    positiveText: '删除文件',
    negativeText: '取消',
    onPositiveClick: async () => {
      missingCleaning.value = true
      try {
        const result = await libraryApi.cleanupMissing(force, true)
        missingResult.value = result
        message.success(
          `已清理 ${result.cleaned} 条记录` +
            (result.files_purged ? `，并删除 ${result.files_purged} 组派生文件` : ''),
        )
      } catch (e) {
        message.error((e as Error).message)
        return false
      } finally {
        missingCleaning.value = false
      }
    },
  })
}

async function clearCache() {
  if (!cacheStats.value || cacheStats.value.total_bytes === 0) {
    message.info('当前没有转码缓存')
    return
  }
  const text = `确定清空全部转码缓存？当前共 ${cacheStats.value.dirs} 个缓存目录、${formatBytes(cacheStats.value.total_bytes)}，清空后再次播放需重新转码。`
  // dialog.warning 返回 DialogReactive 而非 Promise，确认逻辑必须放在
  // onPositiveClick 回调内；async 回调会让弹窗保持 loading 直到完成
  dialog.warning({
    title: '清空转码缓存',
    content: text,
    positiveText: '清空',
    negativeText: '取消',
    onPositiveClick: async () => {
      cacheClearing.value = true
      try {
        const result = await systemApi.clearCache()
        await loadCache()
        message.success(
          `已清空 ${result.dirs_removed} 个缓存目录，释放 ${formatBytes(result.bytes_freed)}`
        )
      } catch (e) {
        message.error((e as Error).message)
        return false
      } finally {
        cacheClearing.value = false
      }
    },
  })
}

watch(activeSection, (v) => {
  if (v === 'base') {
    void loadCache()
    void loadMissing()
    void loadAgents()
  }
})

onMounted(() => {
  void loadSystem()
  // 进设置页时拉一次后端设置：多客户端之间改了配置后，另一端只要点开设置就能看到最新值
  // （启动时那次 hydrate 可能早于另一端的改动；只在此处补一次，不做轮询）
  void settings.hydrate()
})
</script>
<template>
  <div class="sa-settings">
    <!-- 顶栏 -->
    <SaHeader />

    <main>
      <div class="container-sa">
        <!-- 页头 -->
        <section class="sa-hero">
          <p v-if="auth.authenticated && !auth.isOwner" class="hero-sub">
            当前是测试账号，可以浏览设置；保存 AI、密钥等改动需要主账号。
          </p>
        </section>

        <!-- 侧边栏 + 分区内容 -->
        <div class="settings-layout">
          <!-- 移动端：HeroUI Tabs「溢出」形式（滚动箭头 + 渐隐边缘），桌面端隐藏 -->
          <div class="settings-otabs">
            <SaOverflowTabs v-model="activeSection" :items="sections" aria-label="设置分区" />
          </div>

          <nav class="settings-nav" aria-label="设置分区">
            <button
              v-for="s in sections"
              :key="s.key"
              class="nav-item"
              :class="{ 'nav-item--active': activeSection === s.key }"
              @click="activeSection = s.key"
            >
              <component :is="s.icon" :size="16" />
              <span>{{ s.label }}</span>
            </button>
          </nav>

          <div class="settings-content">
            <!-- 分区一：基础设置 -->
            <!-- 左侧导航/移动端标签条已经标明当前分区，内容区不再重复标题行 -->
            <section v-if="activeSection === 'base'" class="sa-section base-panel">
              <!-- ===================== 大区：外观与内容 ===================== -->
              <div class="panel-group-title">外观</div>

              <div class="panel">
                <div class="panel-subtitle">外观与显示</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">深色主题</span>
                      <label class="sa-switch">
                        <input
                          type="checkbox"
                          :checked="themeStore.mode === 'dark'"
                          @change="onThemeModeChange"
                        />
                        <span class="track"><span class="thumb" /></span>
                      </label>
                    </div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">强调色</span>
                    </div>
                    <SaSelect
                      v-model="accentModel"
                      :options="accentOptions"
                      :clearable="false"
                      :render-label="renderAccentLabel"
                    />
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">显示封面</span>
                      <label class="sa-switch">
                        <input
                          type="checkbox"
                          :checked="settings.showCovers"
                          @change="onShowCoversChange"
                        />
                        <span class="track"><span class="thumb" /></span>
                      </label>
                    </div>
                    <div class="field-hint">关闭后首页与播放页都不显示封面</div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">首页轮播优先图</span>
                    </div>
                    <SaSelect
                      v-model="heroImageModel"
                      :options="HERO_IMAGE_OPTIONS"
                      :clearable="false"
                    />
                    <div class="field-hint">
                      仅 PC 端刊头：选「横幅海报」时有横幅的优先用横幅，没有横幅的仍回退头像；移动端不受影响
                    </div>
                  </div>
                </div>
                <div class="panel-foot">
                  <div class="panel-note">以上设置改动后自动保存</div>
                </div>
              </div>

              <!-- 「内容与匹配」整栏已移除：自动关联策略固定严格（无设置入口），详情页主图固定头像 -->

              <!-- ===================== 大区：网络与服务 ===================== -->
              <div class="panel-group-title">网络与服务</div>

              <div class="panel">
                <div class="panel-subtitle">网络与代理</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">外部站点走代理</span>
                      <label class="sa-switch">
                        <input v-model="settings.externalProxy.enabled" type="checkbox" />
                        <span class="track"><span class="thumb" /></span>
                      </label>
                    </div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">代理地址</span>
                    </div>
                    <input
                      v-model="settings.externalProxy.url"
                      class="sa-input"
                      type="text"
                      placeholder="http://192.168.1.10:7890"
                    />
                  </div>
                </div>
                <div class="panel-foot">
                  <div class="panel-actions">
                    <n-button size="small" quaternary :disabled="proxySaving" @click="resetProxySettings">
                      恢复默认
                    </n-button>
                    <n-button size="small" type="primary" :loading="proxySaving" @click="saveProxySettings">
                      保存
                    </n-button>
                  </div>
                </div>
              </div>

              <div class="panel">
                <div class="panel-subtitle">MT Photos 照片墙</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">启用</span>
                      <label class="sa-switch">
                        <input v-model="settings.mtphotos.enabled" type="checkbox" />
                        <span class="track"><span class="thumb" /></span>
                      </label>
                    </div>
                    <div class="field-hint">启用后照片墙可绑定 MT；成帖需容器能读到导出目录的 txt</div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">服务地址</span>
                    </div>
                    <input
                      v-model="settings.mtphotos.base_url"
                      class="sa-input"
                      type="text"
                      placeholder="http://192.168.1.10:8063"
                    />
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">API Key</span>
                    </div>
                    <input
                      v-model="settings.mtphotos.api_key"
                      class="sa-input"
                      type="password"
                      :placeholder="settings.mtphotos.api_key_set ? '已保存，留空则不修改' : 'sk_live_...'"
                      autocomplete="off"
                    />
                    <div class="field-hint">MT 网页右上角用户名 → API 密钥里创建；授权码后端自动续期</div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">MT 磁盘路径前缀</span>
                    </div>
                    <input
                      v-model="settings.mtphotos.disk_prefix"
                      class="sa-input"
                      type="text"
                      placeholder="如 /volume1/photo 或 Y:\photo（MT 里显示的路径开头）"
                    />
                    <div class="field-hint">填 MT 容器内路径，不是 NAS 宿主机路径（例：/photo）</div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">容器内挂载路径</span>
                    </div>
                    <input
                      v-model="settings.mtphotos.mount_path"
                      class="sa-input"
                      type="text"
                      placeholder="/data/mt-photos"
                    />
                    <div class="field-hint">与 compose 里 MT_PHOTOS_DIR 的容器路径一致</div>
                  </div>
                  <div class="field field--wide">
                    <div class="hero-text-actions">
                      <n-button size="small" quaternary :loading="mtPingLoading" @click="pingMtphotos">
                        测试连接
                      </n-button>
                      <button
                        class="sa-btn sa-btn--icon"
                        type="button"
                        title="恢复默认"
                        aria-label="恢复默认"
                        @click="resetMtphotos"
                      >
                        <RefreshOutlined :size="15" />
                      </button>
                      <n-button size="small" type="primary" @click="saveMtphotos">保存</n-button>
                    </div>
                    <div v-if="mtPingHint" class="field-hint">{{ mtPingHint }}</div>
                  </div>
                </div>
              </div>

              <div class="panel">
                <div class="panel-subtitle">TMDB 艺人资料源</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">启用</span>
                      <label class="sa-switch">
                        <input v-model="settings.tmdb.enabled" type="checkbox" />
                        <span class="track"><span class="thumb" /></span>
                      </label>
                    </div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">API Key</span>
                    </div>
                    <input
                      v-model="settings.tmdb.api_key"
                      class="sa-input"
                      type="password"
                      :placeholder="settings.tmdb.api_key_set ? '已保存，留空则不修改' : '32 位十六进制（v3 key）'"
                      autocomplete="off"
                    />
                  </div>
                  <div class="field field--wide">
                    <div class="hero-text-actions">
                      <n-button size="small" quaternary :loading="tmdbPingLoading" @click="pingTmdb">
                        测试连接
                      </n-button>
                      <button
                        class="sa-btn sa-btn--icon"
                        type="button"
                        title="恢复默认"
                        aria-label="恢复默认"
                        @click="resetTmdb"
                      >
                        <RefreshOutlined :size="15" />
                      </button>
                      <n-button size="small" type="primary" @click="saveTmdb">保存</n-button>
                    </div>
                    <div v-if="tmdbPingHint" class="field-hint">{{ tmdbPingHint }}</div>
                  </div>
                </div>
              </div>

              <!-- ===================== 大区：存储与维护 ===================== -->
              <div class="panel-group-title">存储与维护</div>

              <div class="panel">
                <div class="panel-subtitle">存储与维护</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">转码缓存</span>
                    </div>
                    <!-- PC：统计文字与刷新/清空同一行（窄屏自动堆叠） -->
                    <div class="storage-row">
                      <div class="storage-stat">
                        <span>{{ cacheSummaryText }}</span>
                      </div>
                      <div class="storage-actions">
                        <button
                          class="sa-btn sa-btn--icon"
                          :class="{ 'is-busy': cacheLoading }"
                          type="button"
                          title="刷新"
                          aria-label="刷新"
                          :disabled="cacheLoading"
                          @click="loadCache"
                        >
                          <RefreshOutlined :size="15" />
                        </button>
                        <button
                          class="sa-btn sa-btn--danger"
                          type="button"
                          :disabled="cacheClearing"
                          @click="clearCache"
                        >
                          <DeleteOutlined :size="14" />
                          {{ cacheClearing ? '清空中…' : '清空' }}
                        </button>
                      </div>
                    </div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">失效视频</span>
                    </div>
                    <!-- PC：统计文字与扫描/清空同一行（窄屏自动堆叠） -->
                    <div class="storage-row">
                      <div class="storage-stat">
                        <span>{{ missingSummaryText(missingResult) }}</span>
                      </div>
                      <div class="storage-actions">
                        <button
                          class="sa-btn sa-btn--icon"
                          :class="{ 'is-busy': missingLoading }"
                          type="button"
                          title="扫描"
                          aria-label="扫描"
                          :disabled="missingLoading"
                          @click="loadMissing"
                        >
                          <RefreshOutlined :size="15" />
                        </button>
                        <button
                          class="sa-btn sa-btn--danger"
                          type="button"
                          :disabled="missingCleaning || missingLoading || !missingResult?.missing"
                          @click="cleanupMissing(false, true)"
                        >
                          <DeleteOutlined :size="14" />
                          {{ missingCleaning ? '清空中…' : '清空' }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- ===================== 大区：账号 ===================== -->
              <div class="panel-group-title">账号</div>

              <div class="panel">
                <div class="panel-subtitle">账号安全</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">当前用户</span>
                      <span class="account-name">{{ auth.user?.username || '—' }}</span>
                    </div>
                    <div class="field-hint">账号操作已统一收拢到设置页，各端一致</div>
                  </div>
                  <div class="field field--wide">
                    <div class="field-row">
                      <span class="field-label">修改用户名</span>
                    </div>
                    <input
                      v-model="usernameDraft"
                      class="sa-input"
                      type="text"
                      autocomplete="username"
                      placeholder="3–32 位字母、数字、点、下划线或短横"
                    />
                    <div class="field-hint">登录用的用户名，须以字母或数字开头</div>
                  </div>
                  <div class="field field--wide">
                    <div class="hero-text-actions">
                      <n-button
                        size="small"
                        type="primary"
                        :disabled="usernameSaving"
                        @click="onChangeUsername"
                      >
                        {{ usernameSaving ? '保存中…' : '保存用户名' }}
                      </n-button>
                    </div>
                  </div>
                </div>

                <!-- 修改密码 -->
                <div class="panel-grid pwd-grid">
                  <div class="field">
                    <span class="field-label">当前密码</span>
                    <input
                      v-model="pwdOld"
                      class="sa-input"
                      type="password"
                      autocomplete="current-password"
                      placeholder="输入当前密码"
                    />
                  </div>
                  <div class="field">
                    <span class="field-label">新密码（8–128 位）</span>
                    <input
                      v-model="pwdNew"
                      class="sa-input"
                      type="password"
                      autocomplete="new-password"
                      placeholder="输入新密码"
                    />
                  </div>
                  <div class="field">
                    <span class="field-label">确认新密码</span>
                    <input
                      v-model="pwdConfirm"
                      class="sa-input"
                      type="password"
                      autocomplete="new-password"
                      placeholder="再次输入新密码"
                    />
                    <div class="field-hint">修改后其他设备将退出登录，当前设备保持登录</div>
                  </div>
                </div>
                <div class="pwd-actions">
                  <button
                    class="sa-btn sa-btn--primary"
                    type="button"
                    :disabled="pwdSaving"
                    @click="onChangePassword"
                  >
                    {{ pwdSaving ? '保存中…' : '修改密码' }}
                  </button>
                </div>

                <div class="panel-foot">
                  <div class="panel-note">
                    <PersonOutlined :size="13" />
                    退出后需重新登录才能访问资料库
                  </div>
                  <div class="panel-actions">
                    <button class="sa-btn" type="button" @click="onLogoutAll">退出所有设备</button>
                    <button class="sa-btn sa-btn--danger" type="button" @click="onLogout">退出登录</button>
                  </div>
                </div>
              </div>

              <div v-if="isOwner" class="panel">
                <div class="panel-subtitle">测试账号</div>
                <div class="panel-grid">
                  <div class="field field--wide">
                    <div class="field-hint">给 AI 自动任务单独登录用，与主账号隔离</div>
                  </div>
                  <div v-if="agentList.length" class="field field--wide">
                    <div v-for="u in agentList" :key="u.id" class="agent-row">
                      <span class="account-name">{{ u.username }}</span>
                      <button class="sa-btn sa-btn--danger" type="button" @click="onDeleteAgent(u)">
                        删除
                      </button>
                    </div>
                  </div>
                  <div class="field">
                    <span class="field-label">用户名</span>
                    <input
                      v-model="agentUser"
                      class="sa-input"
                      type="text"
                      autocomplete="off"
                      placeholder="测试账号用户名"
                    />
                  </div>
                  <div class="field">
                    <span class="field-label">密码（8–128 位）</span>
                    <input
                      v-model="agentPass"
                      class="sa-input"
                      type="password"
                      autocomplete="new-password"
                      placeholder="测试账号密码"
                    />
                  </div>
                  <div class="field">
                    <span class="field-label">确认密码</span>
                    <input
                      v-model="agentPass2"
                      class="sa-input"
                      type="password"
                      autocomplete="new-password"
                      placeholder="再次输入密码"
                    />
                  </div>
                </div>
                <div class="pwd-actions">
                  <button
                    class="sa-btn sa-btn--primary"
                    type="button"
                    :disabled="agentSaving"
                    @click="onCreateAgent"
                  >
                    {{ agentSaving ? '创建中…' : '添加测试账号' }}
                  </button>
                </div>
              </div>
            </section>


            <SettingsIncomingPanel v-else-if="activeSection === 'incoming'" @goto-ai="activeSection = 'ai'" />

            <SettingsAiPanel v-else-if="activeSection === 'ai'" />

            <!-- 分区：资料库 -->
            <section v-else-if="activeSection === 'db'" class="sa-section">
              <!-- 关系化数据管理：入口（全部内嵌进设置页，不再跳整屏独立页） -->
              <div v-if="dbView === 'home'" class="panel">
                <div class="panel-subtitle">关系化数据管理</div>
                <div class="db-entry-grid">
                  <button
                    v-for="e in dbEntries"
                    :key="e.key"
                    type="button"
                    class="db-entry"
                    @click="dbView = e.key"
                  >
                    <span class="db-entry-icon"><component :is="e.icon" :size="18" /></span>
                    <span class="db-entry-main">
                      <span class="db-entry-name">{{ e.label }}</span>
                      <span class="db-entry-desc">{{ e.desc }}</span>
                    </span>
                    <span class="db-entry-go">→</span>
                  </button>
                </div>
                <div class="field-hint">
                  资料库负责组合 / 艺人 / 专辑 / 歌曲的关系型元数据维护：体检问题归因到实体、完整度逐行可见、改动实时保存。
                </div>
              </div>

              <!-- 数据体检（内嵌） -->
              <div v-else-if="dbView === 'dashboard'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  数据体检
                </div>
                <DataHealthView embedded @open="openDbPath" />
              </div>

              <!-- 组合列表 / 工作台（内嵌） -->
              <div v-else-if="dbView === 'group'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'groups'">← 返回列表</button>
                  组合工作台
                </div>
                <DbGroupWorkspacePanel :uid="dbEmbedUid" />
              </div>
              <div v-else-if="dbView === 'groups'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  组合
                </div>
                <DbGroupListPanel
                  @open="openDbGroup"
                  @created="openDbGroup"
                />
              </div>

              <!-- 艺人列表 / 工作台（内嵌） -->
              <div v-else-if="dbView === 'artist'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'artists'">← 返回列表</button>
                  艺人工作台
                </div>
                <DbArtistWorkspace :uid="dbEmbedUid" embedded />
              </div>
              <div v-else-if="dbView === 'artists'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  艺人
                </div>
                <DbArtistsList embedded @open="openDbPath" />
              </div>

              <!-- 专辑列表 / 工作台（内嵌） -->
              <div v-else-if="dbView === 'album'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'albums'">← 返回列表</button>
                  专辑工作台
                </div>
                <DbAlbumWorkspace :uid="dbEmbedUid" embedded />
              </div>
              <div v-else-if="dbView === 'albums'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  专辑
                </div>
                <DbAlbumsList embedded @open="openDbPath" />
              </div>

              <!-- 歌曲列表 / 工作台（内嵌） -->
              <div v-else-if="dbView === 'song'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'songs'">← 返回列表</button>
                  歌曲工作台
                </div>
                <DbSongWorkspace :uid="dbEmbedUid" embedded />
              </div>
              <div v-else-if="dbView === 'songs'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  歌曲
                </div>
                <DbSongsList embedded @open="openDbPath" />
              </div>

              <!-- 博主管理（内嵌） -->
              <div v-else-if="dbView === 'uploaders'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  博主管理
                </div>
                <SettingsUploadersPanel />
              </div>

              <!-- 回收站（内嵌） -->
              <div v-else-if="dbView === 'recycle'" class="panel db-embed-panel">
                <div class="panel-subtitle db-embed-head">
                  <button class="db-back" type="button" @click="dbView = 'home'">← 返回</button>
                  回收站
                </div>
                <DbRecycleView embedded />
              </div>
            </section>

            <!-- 分区三：系统信息 -->
            <section v-else-if="activeSection === 'system'" class="sa-section">
              <!-- 标题行去掉，只留刷新按钮（右对齐工具行） -->
              <div class="section-head section-head--tools">
                <button
                  class="section-refresh"
                  :disabled="sysLoading"
                  title="刷新系统信息"
                  aria-label="刷新系统信息"
                  @click="loadSystem"
                >
                  <RefreshOutlined :size="14" />
                </button>
              </div>
              <div class="sys-grid">
                <div class="sys-card">
                  <div class="sys-title">FFmpeg</div>
                  <div class="sys-rows">
                    <div class="sys-row">
                      <span class="dot" :class="ffmpeg?.available ? 'dot--ok' : 'dot--err'" />
                      <span>{{ ffmpeg ? (ffmpeg.available ? '可用' : '不可用') : '检测中…' }}</span>
                    </div>
                    <div class="sys-row"><span class="k">信息</span><span class="v">{{ ffmpeg?.message || '--' }}</span></div>
                    <div class="sys-row"><span class="k">ffmpeg</span><span class="v">{{ ffmpeg?.ffmpeg_path || '--' }}</span></div>
                    <div class="sys-row"><span class="k">ffprobe</span><span class="v">{{ ffmpeg?.ffprobe_path || '--' }}</span></div>
                  </div>
                </div>
                <div class="sys-card">
                  <div class="sys-title">
                    <FolderOutlined :size="14" /> 存储路径
                  </div>
                  <div class="sys-rows">
                    <div class="sys-row"><span class="k">待整理</span><span class="v">{{ paths?.incoming || '--' }}</span></div>
                    <div class="sys-row"><span class="k">正式库</span><span class="v">{{ paths?.library || '--' }}</span></div>
                    <div class="sys-row"><span class="k">派生</span><span class="v">{{ paths?.derived || '--' }}</span></div>
                    <div class="sys-row"><span class="k">转码输出</span><span class="v">{{ paths?.transcode || '--' }}</span></div>
                  </div>
                </div>
                <div class="sys-card">
                  <div class="sys-title">后端配置</div>
                  <div class="sys-rows">
                    <div class="sys-row"><span class="k">应用名</span><span class="v">{{ config?.app_name || '--' }}</span></div>
                    <div class="sys-row"><span class="k">转码编码</span><span class="v">{{ config?.transcode_defaults.video_codec }} / {{ config?.transcode_defaults.audio_codec }}</span></div>
                    <div class="sys-row"><span class="k">转码缩放</span><span class="v">{{ config?.transcode_defaults.scale || '--' }}</span></div>
                    <div class="sys-row"><span class="k">数据库</span><span class="v">{{ config?.database_url || '--' }}</span></div>
                  </div>
                </div>
              </div>
            </section>

          </div>
        </div>
      </div>
    </main>

    <footer class="sa-footer">
      <div class="container-sa footer-inner">
        <span>idolMatrix · 设置</span>
        <button class="footer-home" @click="router.push('/')">返回首页</button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* ===== StarAtlas 风格设计令牌（全局主题提供 --sa-*） ===== */
.sa-settings {
  min-height: 100vh;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  font-size: 14px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

.container-sa {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px;
}
@media (max-width: 768px) {
  .container-sa {
    padding: 0 16px;
  }
}

/* ===== 页头 ===== */
.sa-hero {
  padding: 20px 0 12px;
}
.hero-kicker {
  font-size: 11px;
  font-weight: 500;
  color: var(--sa-accent);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 8px;
}
.hero-sub {
  font-size: 15px;
  color: var(--sa-text-secondary);
  margin: 0;
}

/* ===== 侧边栏布局 ===== */
.settings-layout {
  display: flex;
  align-items: flex-start;
  gap: 24px;
  padding-bottom: 56px;
}
/* HeroUI Tabs 风格（tabs__list：圆角浅底 + 细边框） */
.settings-nav {
  width: 200px;
  flex-shrink: 0;
  position: sticky;
  top: 76px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 14px;
  overflow-x: hidden;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: none;
  border-radius: 10px;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  text-align: left;
}
.nav-item:hover {
  background: var(--sa-subtle);
  color: var(--sa-text-primary);
}
/* 选中：单色画廊风（HeroUI 原生做法）——深底白字，随主题自动反转 */
/* 需在 :hover 之后定义，避免指针停留（触屏 hover 残留）盖掉高亮 */
.nav-item--active,
.nav-item--active:hover {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-weight: 600;
}
.settings-content {
  flex: 1;
  min-width: 0;
}
/* 移动端溢出标签条：桌面端不渲染 */
.settings-otabs {
  display: none;
}
@media (max-width: 768px) {
  .settings-layout {
    flex-direction: column;
    gap: 12px;
    padding-bottom: 32px;
  }
  .settings-content {
    width: 100%;
  }
  /* 桌面侧栏退场，换成 HeroUI「溢出」标签条 */
  .settings-nav {
    display: none;
  }
  .settings-otabs {
    display: block;
    position: sticky;
    top: 0;
    z-index: 30;
    /* 与页面内容栏同宽对齐（不再全幅出血）；背景带挡住滚到标签条后面的内容 */
    min-width: 0;
    width: 100%;
    padding: 8px 0;
    background: var(--sa-bg);
  }
}

/* ===== Section 通用 ===== */
.sa-section {
  min-height: 240px;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.section-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
/* 无标题的工具行（同 views/settings/settings-shared.css，改一处必须改两处） */
.section-head--tools {
  justify-content: flex-end;
  margin-bottom: 12px;
  padding-bottom: 0;
  border-bottom: none;
}
.section-more {
  font-size: 13px;
  color: var(--sa-text-secondary);
  transition: color 0.15s;
}
.section-more:hover {
  color: var(--sa-text-primary);
}
.section-tag {
  font-size: 11px;
  color: var(--sa-accent);
  background: var(--sa-accent-subtle);
  padding: 2px 10px;
  border-radius: 9999px;
}
.section-refresh {
  display: inline-flex;
  background: none;
  border: none;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  padding: 4px;
}
.section-refresh:hover:not(:disabled) {
  color: var(--sa-text-primary);
}
.section-refresh:disabled {
  opacity: 0.5;
}

/* ===== 待整理 ===== */
.action-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 768px) {
  .action-grid {
    grid-template-columns: 1fr 1fr;
  }
}
.action-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
}
.action-clickable {
  cursor: pointer;
  transition: all 0.15s;
}
.action-clickable:hover {
  background: var(--sa-subtle);
  border-color: var(--sa-border);
}
.action-icon {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.action-main {
  min-width: 0;
  flex: 1;
}
.action-label {
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.action-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.3;
}
.action-value--small {
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-primary);
}
.action-unit {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-tertiary);
  margin-left: 2px;
}
.action-meta {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  max-width: 160px;
  text-align: right;
}

/* ===== 待整理列表 ===== */
.incoming-list {
  margin-top: 16px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 12px 16px;
}
.incoming-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  margin-bottom: 4px;
}
.incoming-refresh {
  display: inline-flex;
  background: none;
  border: none;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  padding: 4px;
}
.incoming-refresh:hover:not(:disabled) {
  color: var(--sa-text-primary);
}
.incoming-refresh:disabled {
  opacity: 0.5;
}
.incoming-empty {
  padding: 28px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.incoming-items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.incoming-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 4px;
  border-top: 1px solid var(--sa-border-subtle);
  min-width: 0;
}
.incoming-item:first-child {
  border-top: none;
}
.incoming-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.incoming-name {
  font-size: 13px;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.incoming-meta {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.incoming-tag {
  flex-shrink: 0;
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 9999px;
}
.tag--new {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.12);
}
.tag--dup {
  color: #c9a96e;
  background: rgba(201, 169, 110, 0.12);
}
.list-fade-enter-active,
.list-fade-leave-active {
  transition: opacity 0.18s ease;
}
.list-fade-enter-from,
.list-fade-leave-to {
  opacity: 0;
}

/* ===== 待整理详情 ===== */
.incoming-item--clickable {
  cursor: pointer;
  transition: background 0.15s;
}
.incoming-item--clickable:hover {
  background: var(--sa-subtle);
}
.detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.detail-back {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 14px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.detail-back:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.detail-file {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.detail-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  align-items: start;
}
@media (max-width: 900px) {
  .detail-layout {
    grid-template-columns: 1fr;
  }
}
.detail-left,
.detail-right {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.detail-hint {
  padding: 20px;
  border: 1px dashed var(--sa-border);
  border-radius: 12px;
  color: var(--sa-text-tertiary);
  font-size: 13px;
  text-align: center;
}
.info-card,
.form-card {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 16px;
}
.info-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
  word-break: break-all;
}
.info-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.info-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
  min-width: 0;
}
.info-row .k {
  color: var(--sa-text-tertiary);
  flex-shrink: 0;
  width: 60px;
}
.info-row .v {
  color: var(--sa-text-secondary);
  word-break: break-all;
  min-width: 0;
}
.info-row a.v {
  color: var(--sa-accent);
  text-decoration: none;
}
.info-row a.v:hover {
  text-decoration: underline;
}
.info-desc {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--sa-border-subtle);
  font-size: 12px;
  color: var(--sa-text-secondary);
  line-height: 1.7;
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.form-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 16px;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
}
.form-label {
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.sa-textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 10px 12px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  color: var(--sa-text-primary);
  font-size: 13px;
  font-family: inherit;
  line-height: 1.6;
  outline: none;
  resize: vertical;
  min-height: 84px;
  transition: border-color 0.15s;
}
.sa-textarea:focus {
  border-color: var(--sa-accent-border);
}
.sa-textarea::placeholder {
  color: var(--sa-text-tertiary);
}
.sa-textarea--readonly {
  color: var(--sa-text-tertiary);
  cursor: not-allowed;
  background: var(--sa-elevated);
}
.form-check {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
}
.form-check input[type='checkbox'] {
  width: 16px;
  height: 16px;
  accent-color: var(--sa-accent);
  cursor: pointer;
}
.form-check label {
  font-size: 13px;
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 4px;
}
.dest-path-preview {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  background: var(--sa-elevated);
  border: 1px dashed var(--sa-border-subtle);
  border-radius: 8px;
}
.dest-path-label {
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.dest-path-code {
  font-size: 12px;
  color: var(--sa-accent);
  font-family: inherit;
  word-break: break-all;
  line-height: 1.5;
}
.dest-path-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dest-path-row .sa-input {
  flex: 1;
}
.dest-dir-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow-y: auto;
}
.dest-dir-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  color: var(--sa-text-primary);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.dest-dir-item:hover {
  border-color: var(--sa-accent-border);
  color: var(--sa-accent);
}
.dest-dir-empty {
  padding: 16px;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.sa-btn--ai {
  background: var(--sa-accent-subtle);
  border-color: var(--sa-accent-border);
  color: var(--sa-accent);
}
.sa-btn--ai:hover:not(:disabled) {
  background: var(--sa-accent-subtle);
  color: var(--sa-accent-hover);
}
.sa-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.form-field :deep(.n-select) {
  width: 100%;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.fuzzy-hint {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--sa-text-secondary);
  line-height: 1.6;
}
.fuzzy-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 260px;
  overflow-y: auto;
}
.fuzzy-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-subtle);
  color: var(--sa-text-primary);
  text-align: left;
  cursor: pointer;
  transition: all 0.15s;
}
.fuzzy-item:hover {
  border-color: var(--sa-accent);
  background: var(--sa-accent-subtle);
}
.fuzzy-item-name {
  font-weight: 600;
  font-size: 13px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fuzzy-item-meta {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.album-ai-group {
  margin-bottom: 14px;
}
.album-ai-group:last-child {
  margin-bottom: 0;
}
.album-ai-group-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 700;
  color: var(--sa-text-primary);
}
.album-ai-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-subtle);
}
.album-ai-ident {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-accent);
}
.album-ai-name {
  font-weight: 600;
  font-size: 13px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.album-ai-link {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* ===== Panel ===== */
.panel {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 20px;
}
.panel + .panel {
  margin-top: 16px;
}
.panel-subtitle {
  margin-bottom: 14px;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-accent);
  letter-spacing: 0.02em;
}

/* ===== 资料库入口卡片 ===== */
.db-entry-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
  margin-bottom: 12px;
}
@media (min-width: 720px) {
  .db-entry-grid {
    grid-template-columns: 1fr 1fr;
  }
}
.db-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-elevated, transparent);
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.db-entry:hover {
  border-color: var(--sa-accent);
  background: var(--sa-accent-subtle, var(--sa-subtle));
}
.db-entry-icon {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.db-entry-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.db-entry-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-primary);
}
.db-entry-desc {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.db-entry-go {
  flex-shrink: 0;
  font-size: 14px;
  color: var(--sa-text-tertiary);
  transition: color 0.15s ease, transform 0.15s ease;
}
.db-entry:hover .db-entry-go {
  color: var(--sa-accent);
  transform: translateX(2px);
}
/* 内嵌组合列表后，入口卡里组合那一项是 <button>，与 <router-link> 同外观 */
button.db-entry {
  width: 100%;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
}
/* ===== 内嵌组合列表面板（迁移自 /db/list）===== */
.db-embed-panel {
  padding-bottom: 4px;
}
.db-embed-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.db-back {
  padding: 4px 10px;
  border: 1px solid var(--sa-border);
  border-radius: 8px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.db-back:hover {
  color: var(--sa-accent);
  border-color: var(--sa-accent-border);
}
.panel-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 18px;
}
@media (min-width: 768px) {
  .panel-grid {
    grid-template-columns: 1fr 1fr;
  }
}
.field {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.field--wide {
  grid-column: 1 / -1;
}
.field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.field-label {
  font-size: 13px;
  color: var(--sa-text-primary);
}
.field-hint {
  font-size: 12px;
  color: var(--sa-text-secondary);
  /* 提示里会出现长路径/长 token（如转码缓存绝对路径），不断行会把整页撑出视口 → 移动端被整页缩小 */
  overflow-wrap: anywhere;
}
.field :deep(.n-select) {
  width: 100%;
}
.hero-text-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}


.sa-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  color: var(--sa-text-primary);
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}
.sa-input:focus {
  border-color: var(--sa-accent-border);
}
.sa-input::placeholder {
  color: var(--sa-text-tertiary);
}
/* ⚠ `.sa-select` 是 naive-ui `n-select` 的根节点：框画在子元素 `.n-base-selection` 上
   （见 styles/db-controls.css）。这里若给它加 background / border / height / padding，
   根节点就会多画一层壳把 naive 的框套住 = 组件套娃，且多行标签会溢出固定高度的根节点。
   （与 views/settings/settings-shared.css 同名规则保持一致 —— 两处是重复定义，改一处必须改两处。） */
.sa-select {
  width: 100%;
}

/* 开关 */
.sa-switch {
  display: inline-flex;
  cursor: pointer;
}
.sa-switch input {
  display: none;
}
.sa-switch .track {
  width: 40px;
  height: 22px;
  border-radius: 9999px;
  background: var(--sa-hover);
  border: 1px solid var(--sa-border);
  position: relative;
  transition: background 0.2s, border-color 0.2s;
  flex-shrink: 0;
}
.sa-switch .thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--sa-text-tertiary);
  transition: all 0.2s;
}
.sa-switch input:checked + .track {
  background: var(--sa-accent-subtle);
  border-color: var(--sa-accent-border);
}
.sa-switch input:checked + .track .thumb {
  left: 20px;
  background: var(--sa-accent);
}

.switch-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
}
.switch-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--sa-text-secondary);
  cursor: pointer;
}

.panel-foot {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--sa-border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.panel-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.panel-actions {
  display: flex;
  gap: 10px;
  /* 面板底部只剩按钮时（如网络与代理）也要贴右 */
  margin-left: auto;
}
.sa-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 18px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border);
  /* ⚠⚠ 底色/字色必须显式写死：不写就落到浏览器默认的 ButtonFace(#f0f0f0)+buttontext(纯黑)，
     于是「刷新 / 扫描 / 恢复默认」这几个 .sa-btn--icon 在暗色主题下渲染成**实心白圆 + 黑图标**。
     几何尺寸其实一样（都是 36px），但实心块 vs 旁边 1px 描边胶囊在肉眼看来更大
     —— 用户报的「刷新按钮上下比较高」根因在此。口径与 CollectionDetailView.vue /
     VideoCollectionDetailView.vue 里同名类的写法保持一致。 */
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.sa-btn--ghost {
  background: transparent;
  color: var(--sa-text-secondary);
}
.sa-btn--ghost:hover {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}
.sa-btn--primary {
  background: var(--sa-accent);
  border-color: var(--sa-accent);
  color: #0e0e12;
  font-weight: 600;
}
.sa-btn--primary:hover {
  background: var(--sa-accent-hover);
  border-color: var(--sa-accent-hover);
}
.sa-btn--danger {
  background: transparent;
  border-color: var(--sa-danger, #e5484d);
  color: var(--sa-danger, #e5484d);
}
.sa-btn--danger:hover:not(:disabled) {
  background: var(--sa-danger, #e5484d);
  border-color: var(--sa-danger, #e5484d);
  color: #fff;
}
.sa-btn--danger:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
/* 纯图标按钮（恢复默认 / 刷新）：文字去掉后收成 36px 方形，数值与 settings-shared.css 的同名类一致。
   加载态时图标自转（.is-busy），仍然是个一眼能看懂的「正在刷新」。 */
.sa-btn--icon {
  width: 36px;
  padding: 0;
  gap: 0;
  justify-content: center;
}
.sa-btn--icon.is-busy svg {
  animation: sa-btn-spin 0.9s linear infinite;
}
@keyframes sa-btn-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .sa-btn--icon.is-busy svg {
    animation: none;
  }
}
/* 存储与维护卡：统计条 + 操作排**同一行**（PC）——统计在左、按钮贴右（与统计条右缘对齐）；
   窄屏由下面的 @media 改回上下堆叠。 */
.storage-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
/* 数据行（存储与维护卡）：只放统计文字，白底 + `--sa-border` 描边（与 .sa-input 同口径）。
   ⚠ 高度必须显式 36px：此前只写 `padding: 8px 14px`，实际高 = 8+8+20.8+2 = **38.8px**，
   与同一排右侧的刷新/清空按钮（.sa-btn 36px）差 2.8px —— 用户报的「有些组件上下高度不一致」就是它。
   窄屏保持堆叠，`.storage-stat` 改 `height:auto + min-height` 防长文案换行被裁。 */
.storage-stat {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 36px;
  padding: 0 14px;
  border-radius: 12px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.04),
    0 1px 2px rgba(0, 0, 0, 0.06),
    0 0 1px rgba(0, 0, 0, 0.06);
  font-size: 13px;
  color: var(--sa-text-secondary);
}
html[data-theme='dark'] .storage-stat {
  /* 深色下不带投影（与 .sa-input 一致） */
  box-shadow: none;
}
/* 操作排（转码缓存 / 失效视频）：刷新 + 清空同一排，靠右；与统计条同行时不占上边距 */
.storage-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
/* 只读字段值（如已固定的「自动关联策略：严格」） */
.field-static {
  margin-left: auto;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}

/* ===== 数据库 ===== */
.db-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  padding: 4px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  overflow-x: auto;
}
.db-tab {
  flex-shrink: 0;
  height: 34px;
  padding: 0 18px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.db-tab:hover {
  background: var(--sa-subtle);
  color: var(--sa-text-primary);
}
.db-tab--active {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-weight: 500;
}
.db-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.db-count {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.db-search {
  display: flex;
  align-items: center;
  gap: 8px;
}
.db-search .sa-input {
  width: 220px;
  height: 34px;
  padding: 0 12px;
  font-size: 13px;
}
.db-loading {
  padding: 40px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.db-table-wrap {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  overflow-x: auto;
}
.db-table {
  width: 100%;
  min-width: 560px;
  border-collapse: collapse;
  font-size: 13px;
}
.db-table th {
  text-align: left;
  padding: 10px 16px;
  font-size: 12px;
  font-weight: 500;
  color: var(--sa-text-tertiary);
  background: var(--sa-subtle);
  border-bottom: 1px solid var(--sa-border-subtle);
  white-space: nowrap;
}
.db-table td {
  padding: 10px 16px;
  border-bottom: 1px solid var(--sa-border-subtle);
  vertical-align: middle;
}
.db-table tbody tr:last-child td {
  border-bottom: none;
}
.db-table tbody tr:hover {
  background: var(--sa-subtle);
}
.db-name {
  font-weight: 500;
  color: var(--sa-text-primary);
}
.db-sub {
  color: var(--sa-text-secondary);
}
.db-type {
  color: var(--sa-accent);
  white-space: nowrap;
}
.db-empty {
  text-align: center;
  color: var(--sa-text-tertiary);
  padding: 32px 0;
}
.db-members-link {
  border: none;
  background: transparent;
  padding: 0;
  color: var(--sa-accent);
  font-size: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.db-members-link:hover {
  opacity: 0.8;
}
.db-name-link {
  text-decoration: none;
}
.db-name-link:hover {
  text-decoration: underline;
}
.db-company-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.db-company-section + .db-company-section {
  margin-top: 18px;
}
.db-company-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
.db-member-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 60vh;
  overflow-y: auto;
}
.db-member-row {
  padding: 10px 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-subtle);
}
.db-member-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-text-primary);
}
.db-member-former {
  font-size: 11px;
  font-weight: 400;
  color: var(--sa-text-tertiary);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 999px;
  padding: 1px 8px;
}
.db-member-meta {
  margin-top: 2px;
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.db-member-positions {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.db-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-top: 16px;
}
.db-page-num {
  font-size: 13px;
  color: var(--sa-text-secondary);
  min-width: 24px;
  text-align: center;
}
.db-op-col {
  width: 120px;
  white-space: nowrap;
}
.db-op-col .db-edit-btn + .db-edit-btn {
  margin-left: 6px;
}
.db-edit-btn {
  height: 26px;
  padding: 0 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 6px;
  background: var(--sa-subtle);
  color: var(--sa-accent);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.db-edit-btn:hover:not(:disabled) {
  background: var(--sa-accent-subtle);
  border-color: var(--sa-accent);
}
.db-edit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.db-edit-btn--danger {
  color: var(--sa-danger, #e5484d);
}
.db-edit-btn--danger:hover:not(:disabled) {
  background: rgba(229, 72, 77, 0.08);
  border-color: var(--sa-danger, #e5484d);
}
.db-edit-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 20px;
  row-gap: 4px;
}
.db-edit-multi {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.db-edit-multi :deep(.n-select) {
  width: 100%;
}
.db-edit-multi .n-button {
  align-self: flex-start;
}
.db-edit-grid :deep(.n-form-item) {
  min-width: 0;
}
.db-edit-grid :deep(.n-form-item--full),
.db-edit-item--full {
  grid-column: 1 / -1;
}
.db-edit-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.db-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  object-fit: cover;
  vertical-align: middle;
  margin-right: 8px;
  border: 1px solid var(--sa-border-subtle);
}
.db-edit-avatar {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  background: var(--sa-subtle);
  border: 1px dashed var(--sa-border-subtle);
  border-radius: 10px;
}
.db-edit-avatar-img {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  object-fit: cover;
  border: 2px solid var(--sa-elevated);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
  flex-shrink: 0;
}
.db-edit-avatar-empty {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--sa-elevated);
  border: 1px dashed var(--sa-border);
  color: var(--sa-text-tertiary);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.db-edit-cover-img,
.db-edit-cover-empty {
  border-radius: 8px;
}
.db-cover-fetch {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.db-cover-search {
  display: flex;
  align-items: center;
  gap: 8px;
}
.db-cover-search .n-input {
  flex: 1;
}
.db-cover-sources {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.db-cover-source {
  padding: 6px 14px;
  font-size: 13px;
  color: var(--sa-text-secondary);
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.db-cover-source.active {
  color: var(--sa-text-primary);
  border-color: var(--sa-text-primary);
  background: var(--sa-elevated);
}
.db-cover-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
  gap: 10px;
  max-height: 420px;
  overflow-y: auto;
}
.db-cover-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s;
}
.db-cover-item:hover {
  border-color: var(--sa-accent-border);
}
.db-cover-item:disabled {
  cursor: default;
  opacity: 0.6;
}
.db-cover-thumb {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  border-radius: 6px;
  background: var(--sa-elevated);
}
.db-cover-name {
  font-size: 12px;
  color: var(--sa-text-primary);
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.db-cover-artist {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.db-cover-empty {
  padding: 24px 0;
  text-align: center;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}
.db-edit-avatar-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.db-edit-avatar-actions {
  display: flex;
  gap: 8px;
}
.db-fetch-search {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.db-fetch-search :deep(.n-input) {
  flex: 1;
}
.db-fetch-results {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 14px;
  max-height: 220px;
  overflow-y: auto;
}
.db-fetch-candidate {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s;
}
.db-fetch-candidate:hover {
  background: var(--sa-subtle);
}
.db-fetch-candidate.active {
  border-color: var(--sa-text-primary);
  background: var(--sa-subtle);
}
.db-fetch-cand-thumb {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  object-fit: cover;
  flex-shrink: 0;
}
.db-fetch-cand-thumb--empty {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  font-size: 16px;
}
.db-fetch-cand-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.db-fetch-cand-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
}
.db-fetch-kind {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
}
.db-fetch-cand-excerpt {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.db-fetch-preview {
  margin-top: 4px;
  border-top: 1px solid var(--sa-border-subtle);
  padding-top: 14px;
}
.db-fetch-preview-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.db-fetch-preview-img {
  width: 64px;
  height: 64px;
  border-radius: 10px;
  object-fit: cover;
  border: 1px solid var(--sa-border-subtle);
}
.db-fetch-preview-name {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.db-fetch-preview-name strong {
  font-size: 15px;
  color: var(--sa-text-primary);
}
.db-fetch-preview-name span {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.db-fetch-bio {
  margin-bottom: 10px;
}
.db-fetch-bio-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
  white-space: pre-line;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.db-fetch-bio-text.expanded {
  -webkit-line-clamp: unset;
  display: block;
  overflow: visible;
}
.db-fetch-expand {
  margin-top: 4px;
  border: none;
  background: transparent;
  padding: 0;
  color: var(--sa-accent);
  font-size: 12px;
  cursor: pointer;
}
.db-fetch-expand:hover {
  opacity: 0.8;
}
.db-fetch-warn {
  font-size: 12px;
  color: var(--sa-warning, #d97706);
}
.db-fetch-opts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 6px;
}
.db-fetch-opt {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.mb-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.mb-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
}
.mb-empty {
  color: var(--sa-text-secondary);
  font-size: 13px;
  padding: 4px 0;
}
.mb-add {
  display: flex;
}

/* ===== 系统信息 ===== */
.sys-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
@media (min-width: 768px) {
  .sys-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
.sys-card {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  padding: 16px;
}
.sys-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 12px;
  color: var(--sa-text-primary);
}
.sys-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sys-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
  min-width: 0;
}
.sys-row .k {
  color: var(--sa-text-tertiary);
  flex-shrink: 0;
  width: 52px;
}
.sys-row .v {
  color: var(--sa-text-secondary);
  word-break: break-all;
  min-width: 0;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 4px;
  flex-shrink: 0;
}
.dot--ok {
  background: #4ade80;
}
.dot--err {
  background: #f87171;
}

/* ===== Footer ===== */
.sa-footer {
  border-top: 1px solid var(--sa-border-subtle);
  padding: 24px 0;
}
.footer-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--sa-text-tertiary);
  font-size: 12px;
}
.footer-home {
  background: none;
  border: none;
  color: var(--sa-text-secondary);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.footer-home:hover {
  color: var(--sa-text-primary);
}

@media (max-width: 768px) {
  .sa-hero {
    padding: 14px 0 10px;
  }
  .hero-sub {
    font-size: 13px;
    line-height: 1.5;
  }
  .action-card {
    flex-wrap: wrap;
    padding: 14px 16px;
    gap: 10px;
  }
  .action-meta {
    max-width: none;
    width: 100%;
    text-align: left;
    padding-left: 54px;
  }
  .action-value {
    font-size: 20px;
  }
  .detail-head {
    flex-wrap: wrap;
    gap: 8px;
  }
  .detail-file {
    flex-basis: 100%;
    white-space: normal;
    word-break: break-all;
  }
  .form-actions {
    flex-wrap: wrap;
  }
  .form-actions .sa-btn {
    flex: 1;
    justify-content: center;
  }
  .dest-path-row {
    flex-wrap: wrap;
  }
  .dest-path-row .sa-input {
    flex-basis: 100%;
  }
  .panel {
    padding: 16px;
  }
  .panel-foot {
    flex-direction: column;
    align-items: stretch;
  }
  /* 操作排一律贴右（与 PC 同口径）。
     ⚠ 此前 `.panel-actions` 只在窄屏被撑成 100% 宽、`.hero-text-actions` 还被改成
     `justify-content: flex-start`，导致「恢复默认 / 保存 / 测试连接 / 保存用户名」在移动端
     全跑到左边，而同页 `.pwd-actions`（修改密码）/ `.storage-actions`（刷新·清空）却贴右 —— 自相矛盾。
     现在三处统一 flex-end；`.sa-btn` 不再 `flex:1` 拉满，避免「撑满=既不像左也不像右」。 */
  .panel-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .hero-text-actions {
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .db-toolbar {
    flex-direction: column;
    align-items: stretch;
  }
  .db-search {
    width: 100%;
  }
  .db-search .sa-input {
    flex: 1;
    width: auto;
    min-width: 0;
  }
  .db-tabs {
    min-width: 0;
    margin-left: -16px;
    margin-right: -16px;
    padding-left: 16px;
    padding-right: 16px;
    border-radius: 0;
    border-left: none;
    border-right: none;
    scrollbar-width: none;
  }
  .db-tabs::-webkit-scrollbar {
    display: none;
  }
  .hide-sm {
    display: none;
  }
  .db-table th,
  .db-table td {
    padding: 10px 12px;
  }
  .db-op-col {
    width: auto;
  }
  .db-edit-btn {
    padding: 0 8px;
  }
  .db-edit-grid {
    grid-template-columns: 1fr;
  }
  .db-edit-footer {
    flex-wrap: wrap;
  }
  .db-edit-footer .n-button {
    flex: 1;
  }
  .mb-row {
    flex-direction: column;
    align-items: stretch;
  }
  .mb-row > * {
    width: 100% !important;
    max-width: 100%;
  }
  .footer-inner {
    flex-direction: column;
    align-items: flex-start;
  }
  /* 窄屏：统计条与操作排改回上下堆叠（PC 上同行） */
  .storage-row {
    flex-direction: column;
    align-items: stretch;
  }
  .storage-stat {
    /* 堆叠态：不写死 36px（长文案可能换行），用 min-height 兜住「和按钮同高」 */
    flex-wrap: wrap;
    height: auto;
    min-height: 36px;
    padding: 6px 12px;
  }
  .storage-actions {
    margin-top: 8px;
  }
}

/* 账号面板：各端统一在设置页展示 */
.account-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-primary);
}
/* 修改密码表单 */
.pwd-grid {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--sa-border-subtle);
}
.pwd-actions {
  display: flex;
  justify-content: flex-end;
  margin: 12px 0 16px;
}
.agent-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 0;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.agent-row:last-child {
  border-bottom: none;
}
</style>
