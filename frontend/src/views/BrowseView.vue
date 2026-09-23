<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import { musicVideosApi } from '@/api/musicVideos'
import { useSettingsStore } from '@/stores/settings'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'
import type { MusicVideo } from '@/types/models'
import { RESOLUTION_LABEL, RESOLUTION_OPTIONS, VIDEO_TYPE_LABEL, VIDEO_TYPE_OPTIONS } from '@/types/models'
import {
  AppOutlined,
  CloseOutlined,
  DownOutlined,
  FolderOutlined,
  MovieOutlined,
  ResolutionOutlined,
  SearchOutlined,
  SortOutlined,
  VideoLibraryOutlined,
} from '@/components/icons'
import { videoPath } from '@/utils/routes'
import { formatVideoSongs } from '@/utils/format'
import { IMG_W_CARD } from '@/utils/imageSizes'

const router = useRouter()
const settings = useSettingsStore()
const message = useMessage()

const loading = ref(false)
const allVideos = ref<MusicVideo[]>([])
const brokenThumbs = ref<Set<number>>(new Set())
const total = ref(0)
const pageNum = ref(1)
const loadError = ref('')

const viewMode = ref<'videos' | 'folders'>('videos')
const activeType = ref('')
const activeResolution = ref('')
const sortOrder = ref('created_desc')
const searchQuery = ref('')
const currentFolder = ref('')

// 分辨率档位计数：菜单只列「当前条件下有条数」的档位，空档位不展示。
// 口径与统计页、列表筛选同一套（按短边归档），菜单条数 = 选中后的实际条数。
const resolutionFacets = ref<Record<string, number>>({})
const resolutionFacetsReady = ref(false)
const { density } = useGridDensity()
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl)

const folders = ref<{ name: string; count: number; cover_id?: number | null }[]>([])
const folderVideos = ref<MusicVideo[]>([])
const folderVideoTotal = ref(0)
const folderPageNum = ref(1)

function listFilter() {
  return {
    video_types: activeType.value || undefined,
    resolution: activeResolution.value || undefined,
    sort: sortOrder.value === 'created_desc' ? undefined : sortOrder.value,
    q: searchQuery.value.trim() || undefined,
    is_short: false,
  }
}

// 加载序列号：丢弃过期的异步回调（快速切换类型/排序/文件夹时旧请求不应覆盖新数据）
let loadSeq = 0

async function loadVideos(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await musicVideosApi.list({
      ingestion_status: 'library',
      page: targetPage,
      page_size: pageSize.value,
      ...listFilter(),
    })
    if (seq !== loadSeq) return
    total.value = res.total
    allVideos.value = res.items
    pageNum.value = targetPage
  } catch (e) {
    if (seq !== loadSeq) return
    loadError.value = (e as Error).message
    message.error(loadError.value)
    if (targetPage === 1) allVideos.value = []
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadFolders(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  loadError.value = ''
  try {
    const res = await musicVideosApi.folderBrowse({
      prefix: currentFolder.value,
      page: targetPage,
      page_size: pageSize.value,
      ...listFilter(),
    })
    if (seq !== loadSeq) return
    folders.value = res.folders
    folderVideoTotal.value = res.video_total
    folderVideos.value = res.videos
    folderPageNum.value = targetPage
  } catch (e) {
    if (seq !== loadSeq) return
    loadError.value = (e as Error).message
    message.error(loadError.value)
    if (targetPage === 1) {
      folders.value = []
      folderVideos.value = []
      folderVideoTotal.value = 0
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function load(targetPage: number) {
  if (viewMode.value === 'folders') await loadFolders(targetPage)
  else await loadVideos(targetPage)
}

function onPageChange(p: number) {
  void loadVideos(p).then(() => window.scrollTo({ top: 0 }))
}

function onFolderPageChange(p: number) {
  void loadFolders(p).then(() => window.scrollTo({ top: 0 }))
}

// 移动端抽屉内的排序单选列表（与桌面端 sort-select 选项保持一致）
const SORT_OPTIONS = [
  { value: 'created_desc', label: '最近上传' },
  { value: 'performance_date_desc', label: '表演日期（新→旧）' },
  { value: 'performance_date_asc', label: '表演日期（旧→新）' },
  { value: 'published_date_desc', label: '上传日期（新→旧）' },
  { value: 'published_date_asc', label: '上传日期（旧→新）' },
]

// ===== 移动端紧凑工具栏 =====
// ≤640px：搜索框缩短，类型 / 分辨率 / 排序 / 视图 / 宽松度全部收成图标按钮并排在搜索一栏，
// 各自的选项走下拉菜单（原来的 ⚙ 底部抽屉已下线）；当前生效的筛选靠下方激活条显示。
const narrowMq = window.matchMedia('(max-width: 640px)')
const isNarrow = ref(narrowMq.matches)
function onNarrowChange(e: MediaQueryListEvent) {
  isNarrow.value = e.matches
}

const sortDropdownOptions = computed(() =>
  SORT_OPTIONS.map((o) => ({ label: o.label, key: o.value })),
)
function onSortSelect(key: string | number) {
  sortOrder.value = String(key)
}
function toggleViewMode() {
  viewMode.value = viewMode.value === 'videos' ? 'folders' : 'videos'
}

// 图标按钮的 title / aria：图标本身不显示文字，选中值要在提示里说清楚
const typeIconLabel = computed(() =>
  activeType.value ? `视频类型：${videoTypeName(activeType.value)}` : '视频类型：全部',
)
const resolutionIconLabel = computed(() =>
  activeResolution.value ? `分辨率：${resolutionName(activeResolution.value)}` : '分辨率：全部',
)
const sortIconLabel = computed(
  () => `排序：${SORT_OPTIONS.find((o) => o.value === sortOrder.value)?.label ?? '最近上传'}`,
)
const viewIconLabel = computed(() =>
  viewMode.value === 'videos' ? '切换到文件夹视图' : '切换到视频视图',
)

// 「全部」哨兵：n-dropdown 的 key 不适合直接用空串（移动端图标菜单里也要能一键还原）
const RES_ALL_KEY = '__all'
const TYPE_ALL_KEY = '__all_type'

const typeDropdownOptions = computed(() => [
  { label: '全部', key: TYPE_ALL_KEY },
  ...VIDEO_TYPE_OPTIONS.map((o) => ({ label: o.label, key: o.value })),
])

function onTypeSelect(key: string | number) {
  const value = String(key)
  activeType.value = value === TYPE_ALL_KEY ? '' : value
}

/**
 * 分辨率菜单项：只列当前筛选条件下有条数的档位（口径与统计页一致，空档位不展示）。
 * 已选中但已变为 0 条的档位继续保留 —— 否则筛选生效后菜单里找不到它，无法撤销。
 */
const resolutionOptions = computed(() => {
  let list = resolutionFacetsReady.value
    ? RESOLUTION_OPTIONS.filter((o) => (resolutionFacets.value[o.key] || 0) > 0)
    : [...RESOLUTION_OPTIONS]
  if (activeResolution.value && !list.some((o) => o.key === activeResolution.value)) {
    const hit = RESOLUTION_OPTIONS.find((o) => o.key === activeResolution.value)
    if (hit) list = [hit, ...list]
  }
  return list
})

const resolutionDropdownOptions = computed(() => [
  { label: '全部', key: RES_ALL_KEY },
  ...resolutionOptions.value.map((o) => ({
    label: resolutionFacetsReady.value
      ? `${o.label} · ${resolutionFacets.value[o.key] || 0}`
      : o.label,
    key: o.key,
  })),
])

function onResolutionSelect(key: string | number) {
  const value = String(key)
  activeResolution.value = value === RES_ALL_KEY ? '' : value
}

function resolutionName(key: string) {
  return RESOLUTION_LABEL[key] || key
}

async function loadResolutionFacets() {
  try {
    const res = await musicVideosApi.resolutionFacets({
      q: searchQuery.value.trim() || undefined,
      video_types: activeType.value || undefined,
      is_short: false,
      ingestion_status: 'library',
    })
    resolutionFacets.value = Object.fromEntries(res.items.map((i) => [i.key, i.count]))
    resolutionFacetsReady.value = true
  } catch {
    // 静默降级：菜单回退成展示全部档位，筛选本身照常可用
    resolutionFacetsReady.value = false
  }
}

const gridVideos = computed(() => allVideos.value)

const subFolders = computed(() => folders.value)

// 文件夹视图徽标口径：当前目录直属视频 + 各子文件夹命中视频，与视频视图的 total 同一口径。
// 后端 video_total 只算当前目录直属视频（子文件夹内命中不算），根目录搜索时会误显示 0
const folderBadgeTotal = computed(() => {
  const subTotal = subFolders.value.reduce((s, f) => s + (f.count || 0), 0)
  return folderVideoTotal.value + subTotal
})

function folderCoverUrl(f: { name: string; cover_id?: number | null }) {
  if (!f.cover_id || !settings.showCovers || brokenThumbs.value.has(f.cover_id)) return undefined
  return musicVideosApi.thumbnailUrl(f.cover_id, undefined, IMG_W_CARD)
}
function onFolderCoverError(f: { cover_id?: number | null }) {
  if (f.cover_id) brokenThumbs.value = new Set(brokenThumbs.value).add(f.cover_id)
}

const crumbs = computed(() => (currentFolder.value ? currentFolder.value.split('/') : []))

function enterFolder(name: string) {
  currentFolder.value = currentFolder.value ? `${currentFolder.value}/${name}` : name
}

function crumbJump(i: number) {
  currentFolder.value = crumbs.value.slice(0, i).join('/')
}

function goVideo(mv: { uid: string }) {
  router.push(videoPath(mv.uid))
}

function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_CARD)
}
function onThumbError(mv: MusicVideo) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(mv.id)
}

function videoTypeName(t: string) {
  return VIDEO_TYPE_LABEL[t] || t
}

// 类型/排序/视图切换：立即回到第 1 页
watch([activeType, sortOrder, viewMode], () => void load(1))
// 分辨率切换：立即回到第 1 页
watch(activeResolution, () => void load(1))
// 搜索：防抖 350ms，与其他列表页一致
let searchDebounce: ReturnType<typeof setTimeout> | undefined
watch(searchQuery, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => {
    void load(1)
    void loadResolutionFacets()
  }, 350)
})
// 类型变化后，可选的分辨率档位与其条数随之变化
watch(activeType, () => void loadResolutionFacets())
onMounted(() => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  void loadResolutionFacets()
})
onBeforeUnmount(() => narrowMq.removeEventListener('change', onNarrowChange))
// 一键清空：走既有 watch 触发防抖刷新，避免与 watcher 重复请求
function clearSearch() {
  searchQuery.value = ''
}
// 文件夹切换
watch(currentFolder, () => {
  if (viewMode.value === 'folders') void loadFolders(1)
})
// page_size 变化（首次测量完成 / 视口跨列数边界 / 切换密度档位）：回到第 1 页重新加载
watch(pageSize, (v) => {
  if (v > 0) void load(1)
})
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">浏览</h1>
          <span class="page-count">{{ viewMode === 'folders' ? folderBadgeTotal : total }}</span>
        </div>

        <div class="head-tools">
          <div class="search-box">
            <SearchOutlined :size="15" />
            <input
              v-model="searchQuery"
              class="search-input"
              type="text"
              :placeholder="isNarrow ? '搜索…' : '搜索视频、歌曲、艺人、组合、来源 ID…'"
            />
            <button
              v-if="searchQuery"
              class="search-clear"
              type="button"
              aria-label="清空搜索"
              @click="clearSearch"
            >
              <CloseOutlined :size="12" />
            </button>
          </div>
          <!-- 移动端紧凑工具栏：与搜索同一行，全部图标化（无文字），各自下拉选择 -->
          <div class="head-icons">
            <n-dropdown
              :options="typeDropdownOptions"
              trigger="click"
              placement="bottom-end"
              @select="onTypeSelect"
            >
              <button
                class="head-icon"
                :class="{ 'head-icon--on': activeType !== '' }"
                type="button"
                :title="typeIconLabel"
                :aria-label="typeIconLabel"
              >
                <VideoLibraryOutlined :size="16" />
              </button>
            </n-dropdown>
            <n-dropdown
              :options="resolutionDropdownOptions"
              trigger="click"
              placement="bottom-end"
              @select="onResolutionSelect"
            >
              <button
                class="head-icon"
                :class="{ 'head-icon--on': activeResolution !== '' }"
                type="button"
                :title="resolutionIconLabel"
                :aria-label="resolutionIconLabel"
              >
                <ResolutionOutlined :size="16" />
              </button>
            </n-dropdown>
            <n-dropdown
              :options="sortDropdownOptions"
              trigger="click"
              placement="bottom-end"
              @select="onSortSelect"
            >
              <button
                class="head-icon"
                :class="{ 'head-icon--on': sortOrder !== 'created_desc' }"
                type="button"
                :title="sortIconLabel"
                :aria-label="sortIconLabel"
              >
                <SortOutlined :size="16" />
              </button>
            </n-dropdown>
            <button
              class="head-icon"
              :class="{ 'head-icon--on': viewMode === 'folders' }"
              type="button"
              :title="viewIconLabel"
              :aria-label="viewIconLabel"
              @click="toggleViewMode"
            >
              <!-- 图标显示「将要切到的视图」：视频视图时显示文件夹图标 -->
              <FolderOutlined v-if="viewMode === 'videos'" :size="16" />
              <AppOutlined v-else :size="16" />
            </button>
            <SaDensitySwitch icon-only />
          </div>
          <!-- display: contents：桌面端不参与布局，移动端整体隐藏 -->
          <div class="head-extra">
            <SaDensitySwitch />
            <div class="view-switch" role="tablist">
              <button
                class="view-btn"
                :class="{ 'view-btn--active': viewMode === 'videos' }"
                role="tab"
                @click="viewMode = 'videos'"
              >
                <AppOutlined :size="15" />
                视频
              </button>
              <button
                class="view-btn"
                :class="{ 'view-btn--active': viewMode === 'folders' }"
                role="tab"
                @click="viewMode = 'folders'"
              >
                <FolderOutlined :size="15" />
                文件夹
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="type-chips">
        <button
          class="chip"
          :class="{ 'chip--active': activeType === '' }"
          @click="activeType = ''"
        >
          全部
        </button>
        <n-dropdown
          :options="typeDropdownOptions"
          trigger="click"
          @select="onTypeSelect"
        >
          <button
            class="chip type-chip"
            :class="{ 'chip--active': activeType !== '' }"
          >
            {{ activeType ? videoTypeName(activeType) : '类型' }}
            <DownOutlined :size="12" />
          </button>
        </n-dropdown>
        <n-dropdown
          :options="resolutionDropdownOptions"
          trigger="click"
          @select="onResolutionSelect"
        >
          <button
            class="chip type-chip"
            :class="{ 'chip--active': activeResolution !== '' }"
          >
            {{ activeResolution ? resolutionName(activeResolution) : '分辨率' }}
            <DownOutlined :size="12" />
          </button>
        </n-dropdown>
        <select v-model="sortOrder" class="sort-select">
          <option value="created_desc">最近上传</option>
          <option value="performance_date_desc">表演日期（新→旧）</option>
          <option value="performance_date_asc">表演日期（旧→新）</option>
          <option value="published_date_desc">上传日期（新→旧）</option>
          <option value="published_date_asc">上传日期（旧→新）</option>
        </select>
      </div>

      <!-- 移动端激活筛选条：类型/分辨率收进抽屉后，已生效的筛选在此保持可见，点 ✕ 撤销 -->
      <div v-if="activeType || activeResolution" class="active-filters">
        <button
          v-if="activeType"
          class="chip chip--active filter-chip"
          @click="activeType = ''"
        >
          {{ videoTypeName(activeType) }}
          <CloseOutlined :size="12" />
        </button>
        <button
          v-if="activeResolution"
          class="chip chip--active filter-chip"
          @click="activeResolution = ''"
        >
          {{ resolutionName(activeResolution) }}
          <CloseOutlined :size="12" />
        </button>
      </div>

      <div
        v-if="loading && !allVideos.length && !folders.length && !folderVideos.length"
        class="list-loading"
      >
        加载中…
        <!-- 空网格的 auto-fill 轨道数与有数据时一致：加载态也渲染网格供列数测量，避免首屏死锁 -->
        <div :ref="bindGrid" class="card-grid" :class="`density-${density}`" aria-hidden="true"></div>
      </div>

      <template v-else-if="viewMode === 'videos'">
        <div :ref="bindGrid" class="card-grid" :class="`density-${density}`">
            <a
              v-for="mv in gridVideos"
              :key="mv.id"
              class="poster-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="poster-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="poster-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="poster-img poster-placeholder">
                  <MovieOutlined :size="26" />
                </div>
              </div>
              <div class="poster-title" :title="mv.name">{{ mv.name }}</div>
              <div class="poster-meta">
                <span>{{ videoTypeName(mv.video_type) }}</span>
                <span v-if="formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name)">
                  {{ formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name) }}
                </span>
              </div>
            </a>
          </div>
          <SaPagination
            v-if="gridVideos.length"
            :total="total"
            :page="pageNum"
            :page-size="pageSize"
            :disabled="loading"
            :show-total="false"
            @update:page="onPageChange"
          />
        <div v-else class="empty-state">
          <MovieOutlined :size="40" />
          <p>{{ loadError || '没有找到视频' }}</p>
        </div>
      </template>

      <template v-else>
        <nav v-if="crumbs.length" class="crumbs">
          <button class="crumb" @click="crumbJump(0)">根目录</button>
          <template v-for="(c, i) in crumbs" :key="i">
            <span class="crumb-sep">/</span>
            <button class="crumb" @click="crumbJump(i + 1)">{{ c }}</button>
          </template>
        </nav>

        <template v-if="subFolders.length || folderVideos.length">
          <div v-if="subFolders.length" class="folder-grid">
            <button
              v-for="f in subFolders"
              :key="f.name"
              class="folder-card"
              @click="enterFolder(f.name)"
            >
              <div class="folder-cover">
                <img
                  v-if="folderCoverUrl(f)"
                  :src="folderCoverUrl(f)!"
                  :alt="f.name"
                  class="folder-img"
                  loading="lazy"
                  @error="onFolderCoverError(f)"
                />
                <div v-else class="folder-icon">
                  <FolderOutlined :size="36" />
                </div>
              </div>
              <div class="folder-name" :title="f.name">{{ f.name }}</div>
              <div class="folder-count">{{ f.count }} 个视频</div>
            </button>
          </div>

          <div :ref="bindGrid" class="card-grid" :class="`density-${density}`">
            <a
              v-for="mv in folderVideos"
              :key="mv.id"
              class="poster-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="poster-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="poster-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="poster-img poster-placeholder">
                  <MovieOutlined :size="26" />
                </div>
              </div>
              <div class="poster-title" :title="mv.name">{{ mv.name }}</div>
              <div class="poster-meta">
                <span>{{ videoTypeName(mv.video_type) }}</span>
                <span v-if="formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name)">
                  {{ formatVideoSongs(mv.songs, mv.song_name, mv.song_chinese_name) }}
                </span>
              </div>
            </a>
          </div>
          <SaPagination
            v-if="folderVideos.length"
            :total="folderVideoTotal"
            :page="folderPageNum"
            :page-size="pageSize"
            :disabled="loading"
            :show-total="false"
            @update:page="onFolderPageChange"
          />
        </template>
        <div v-else class="empty-state">
          <FolderOutlined :size="40" />
          <p>{{ loadError || '该文件夹没有视频' }}</p>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
}
.mv-container {
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 24px;
}
.page-title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
  letter-spacing: -0.02em;
}
/* 独立徽标样式：与标题拉开层次，避免读成“浏览147” */
.page-count {
  font-size: 13px;
  color: var(--sa-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  padding: 2px 10px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  line-height: 1.6;
}
.head-tools {
  display: flex;
  align-items: center;
  gap: 12px;
}
.search-box {
  position: relative;
  display: flex;
  align-items: center;
}
.search-box svg {
  position: absolute;
  left: 14px;
  color: var(--sa-text-tertiary);
  pointer-events: none;
}
.search-input {
  width: 220px;
  padding: 9px 34px 9px 38px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}
.search-input:focus {
  border-color: var(--sa-accent);
}
.search-input::placeholder {
  color: var(--sa-text-tertiary);
}
.search-clear {
  position: absolute;
  right: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: none;
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.search-clear:hover {
  color: var(--sa-text-primary);
  background: var(--sa-hover);
}

.view-switch {
  display: flex;
  align-items: center;
  padding: 3px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
}
.view-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.view-btn:hover {
  color: var(--sa-text-primary);
}
.view-btn--active {
  color: var(--sa-bg);
  background: var(--sa-text-primary);
}

.type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 24px;
}
.chip {
  padding: 6px 14px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.chip:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-border);
}
.chip--active {
  color: var(--sa-bg);
  border-color: var(--sa-text-primary);
  background: var(--sa-text-primary);
}
.type-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.sort-select {
  margin-left: auto;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 13px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.2s;
}
.sort-select:focus {
  border-color: var(--sa-accent);
}

/* 桌面端：head-extra 以 contents 透传给 head-tools 的 flex 布局，激活筛选条不显示 */
.head-extra {
  display: contents;
}
/* 移动端图标筛选栏：桌面端整体不渲染，≤640px 与搜索框同一行展示 */
.head-icons {
  display: none;
}
.head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.head-icon:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-accent);
}
/* 已生效的筛选：图标按钮点亮，与下方激活条呼应 */
.head-icon--on {
  color: var(--sa-accent);
  border-color: var(--sa-accent);
  background: color-mix(in srgb, var(--sa-accent) 12%, transparent);
}
.active-filters {
  display: none;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 220px), 1fr));
  gap: 20px;
}
.density-compact {
  --grid-min: 180px;
}
.density-standard {
  --grid-min: 220px;
}
.density-loose {
  --grid-min: 280px;
}
.poster-card {
  display: block;
  min-width: 0;
}
.poster-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 10px;
  overflow: hidden;
  background: var(--sa-subtle);
  margin-bottom: 10px;
}
.poster-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: filter 0.2s;
}
.poster-card:hover .poster-img {
  filter: brightness(1.05);
}
.poster-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.poster-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--sa-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.poster-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
}
.poster-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
  margin-bottom: 28px;
}
.folder-card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  background: var(--sa-elevated);
  padding: 12px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s;
  min-width: 0;
}
.folder-card:hover {
  border-color: var(--sa-accent);
  transform: translateY(-2px);
}
.folder-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  overflow: hidden;
  background: linear-gradient(135deg, var(--sa-accent-subtle), var(--sa-subtle));
  display: flex;
  align-items: center;
  justify-content: center;
}
.folder-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0.85;
}
.folder-icon {
  color: var(--sa-accent);
  display: flex;
}
.folder-name {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.folder-count {
  margin-top: 3px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

.crumbs {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 18px;
  font-size: 13px;
}
.crumb {
  border: none;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  padding: 3px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.2s;
}
.crumb:hover {
  color: var(--sa-accent);
}
.crumb-sep {
  color: var(--sa-text-tertiary);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
}
.empty-state p {
  margin: 0;
  font-size: 14px;
}

@media (max-width: 640px) {
  /* 与其他页一致：移动端左右留白收窄到 16px（桌面为 32px） */
  .mv-container {
    padding: 0 16px 48px;
  }
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
    padding: 20px 0 16px;
  }
  /* 移动端工具栏：搜索框缩短 + 类型/分辨率/排序/视图/宽松度五个图标同排（无文字） */
  .head-tools {
    flex-wrap: nowrap;
    gap: 8px;
  }
  .head-extra {
    display: none;
  }
  .head-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
  }
  .search-box {
    flex: 1 1 auto;
    min-width: 0;
  }
  .search-box svg {
    left: 11px;
  }
  .search-input {
    width: 100%;
    padding: 8px 28px 8px 30px;
    font-size: 13px;
  }
  .search-clear {
    right: 7px;
  }
  .type-chips {
    display: none;
  }
  .active-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding-bottom: 16px;
  }
  /* 手机端列数：紧凑 2 列；标准/宽松固定 1 列大卡片（auto-fill 在 500–640px 会变 2 列，显式锁定） */
  .card-grid.density-compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .card-grid.density-standard,
  .card-grid.density-loose {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
