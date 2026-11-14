<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
import { CloseOutlined, FolderOutlined, MovieOutlined, SearchOutlined } from '@/components/icons'
import { videoPath } from '@/utils/routes'
import { resolutionLabel } from '@/utils/resolution'
import { formatDuration } from '@/utils/format'
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
const activeTypes = ref<string[]>([])
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
    video_types: activeTypes.value.join(',') || undefined,
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

// 排序选项（桌面下拉 / 移动端共用一份）
const SORT_OPTIONS = [
  { value: 'created_desc', label: '最近上传' },
  { value: 'performance_date', label: '表演日期' },
  { value: 'published_date', label: '上传日期' },
  { value: 'size_desc', label: '体积（大→小）' },
  { value: 'size_asc', label: '体积（小→大）' },
]

/** 「表演日期 / 上传日期」= 单选项，再点一次在正序/倒序之间切换。 */
const DATE_SORTS = ['performance_date', 'published_date'] as const
function toggleDateSort(base: string, current: string): string {
  const isAsc = current === `${base}_asc`
  return isAsc ? `${base}_desc` : `${base}_asc`
}


// ===== 移动端紧凑工具栏 =====
const narrowMq = window.matchMedia('(max-width: 640px)')
const isNarrow = ref(narrowMq.matches)
function onNarrowChange(e: MediaQueryListEvent) {
  isNarrow.value = e.matches
}

// ===== 顶栏三个自绘下拉（复刻样板：实色菜单 + popIn 动效）=====
// 只做「点开 / 关闭」与「点外部收起」，选项内容仍用我们自己的：
// 类型 = VIDEO_TYPE_OPTIONS，分辨率 = RESOLUTION_OPTIONS（按条数裁剪），排序 = SORT_OPTIONS。
type MenuKey = 'type' | 'res' | 'sort'
const openMenu = ref<MenuKey | ''>('')

function toggleMenu(k: MenuKey) {
  openMenu.value = openMenu.value === k ? '' : k
}
function closeMenus() {
  openMenu.value = ''
}
function onDocumentClick() {
  closeMenus()
}

const sortLabel = computed(() => {
  const raw = sortOrder.value
  const bare = raw.replace(/_(asc|desc)$/, '')
  const opt = SORT_OPTIONS.find((o) => o.value === raw) ?? SORT_OPTIONS.find((o) => o.value === bare)
  const arrow = raw.endsWith('_asc') ? ' ↑' : raw.endsWith('_desc') ? ' ↓' : ''
  return (opt?.label ?? '最近上传') + arrow
})
const typeSummary = computed(() => {
  const n = activeTypes.value.length
  if (n === 0) return '全部类型'
  if (n === 1) return videoTypeName(activeTypes.value[0])
  return `已选 ${n} 类`
})
const typeHint = computed(() =>
  activeTypes.value.length ? `已选 ${activeTypes.value.length} 类` : '未选择 = 全部',
)
/** 空数组 = 不过滤（全部），所以「全部勾选」也归一成空数组 */
function typeChecked(v: string) {
  return activeTypes.value.length === 0 || activeTypes.value.includes(v)
}
function toggleType(v: string, on: boolean) {
  const all = VIDEO_TYPE_OPTIONS.map((o) => o.value)
  const set = new Set(activeTypes.value.length ? activeTypes.value : all)
  if (on) set.add(v)
  else set.delete(v)
  activeTypes.value = set.size >= all.length ? [] : [...set]
}
function selectAllTypes() {
  activeTypes.value = []
}
function clearTypes() {
  activeTypes.value = []
}
const resolutionLabelText = computed(() =>
  activeResolution.value ? resolutionName(activeResolution.value) : '全部画质',
)
function pickResolution(key: string) {
  activeResolution.value = key
  closeMenus()
}
function pickSort(key: string) {
  const bare = key.replace(/_(asc|desc)$/, '')
  if (key !== sortOrder.value && DATE_SORTS.includes(bare as (typeof DATE_SORTS)[number])
      && sortOrder.value.replace(/_(asc|desc)$/, '') === bare) {
    sortOrder.value = toggleDateSort(bare, sortOrder.value)
  } else if (DATE_SORTS.includes(key as (typeof DATE_SORTS)[number])) {
    sortOrder.value = `${key}_desc`
  } else {
    sortOrder.value = key
  }
  closeMenus()
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

function resolutionName(key: string) {
  return RESOLUTION_LABEL[key] || key
}

async function loadResolutionFacets() {
  try {
    const res = await musicVideosApi.resolutionFacets({
      q: searchQuery.value.trim() || undefined,
      video_types: activeTypes.value.join(',') || undefined,
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
watch([activeTypes, sortOrder, viewMode], () => void load(1))
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
watch(activeTypes, () => void loadResolutionFacets())
onMounted(() => {
  isNarrow.value = narrowMq.matches
  narrowMq.addEventListener('change', onNarrowChange)
  document.addEventListener('click', onDocumentClick)
  void loadResolutionFacets()
})
onBeforeUnmount(() => {
  narrowMq.removeEventListener('change', onNarrowChange)
  document.removeEventListener('click', onDocumentClick)
})
// 一键清空：走既有 watch 触发防抖刷新，避免与 watcher 重复请求
function clearSearch() {
  searchQuery.value = ''
}

// 搜索收起/展开：默认只是一个放大镜，点击后才展开输入框
const searchOpen = ref(false)
const searchInputEl = ref<HTMLInputElement | null>(null)

async function openSearch() {
  searchOpen.value = true
  await nextTick()
  searchInputEl.value?.focus()
}

function onSearchBlur() {
  // 有内容时保持展开，避免输入途中误收起
  if (!searchQuery.value) searchOpen.value = false
}

function closeSearch() {
  searchInputEl.value?.blur()
  if (!searchQuery.value) searchOpen.value = false
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
      <div class="browse-head">
        <div class="head-left">
          <div class="title-wrap">
            <h1 class="browse-title">浏览</h1>
            <span class="count-badge">
              {{ viewMode === 'folders' ? folderBadgeTotal : total }}
            </span>
          </div>

        </div>

        <div class="head-right">
          <div class="control-bar">
            <div class="search-pill" :class="{ 'is-open': searchOpen }" :title="searchOpen ? '' : '搜索'">
              <button
                v-if="!searchOpen"
                class="search-trigger"
                type="button"
                aria-label="展开搜索"
                @click.stop="openSearch"
              >
                <SearchOutlined :size="15" />
              </button>
              <template v-else>
                <SearchOutlined :size="14" class="search-glyph" />
                <input
                  ref="searchInputEl"
                  v-model="searchQuery"
                  type="text"
                  :placeholder="isNarrow ? '搜索…' : '过滤当前结果…'"
                  @blur="onSearchBlur"
                  @keydown.esc="closeSearch"
                />
                <button
                  v-if="searchQuery"
                  class="search-clear"
                  type="button"
                  aria-label="清空搜索"
                  @click="clearSearch"
                >
                  <CloseOutlined :size="11" />
                </button>
              </template>
            </div>

            <!-- (1) 视频类型：多选 -->
            <div class="pop-group" :class="{ open: openMenu === 'type' }">
              <button
                class="pop-btn"
                :class="{ 'is-active': activeTypes.length > 0 }"
                type="button"
                @click.stop="toggleMenu('type')"
              >
                <span>视频类型: <strong>{{ typeSummary }}</strong></span>
                <svg class="caret" viewBox="0 0 10 6" width="10" height="6" fill="none"><path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </button>

              <div class="pop-menu pop-menu--type" @click.stop>
                <div class="pop-head">
                  <span class="pop-cap">SELECT MULTIPLE</span>
                  <div class="pop-actions">
                    <button type="button" class="link" @click="selectAllTypes">全选</button>
                    <span class="pop-sep">|</span>
                    <button type="button" class="link muted" @click="clearTypes">清空</button>
                  </div>
                </div>

                <div class="type-grid">
                  <label v-for="o in VIDEO_TYPE_OPTIONS" :key="o.value" class="type-row">
                    <input
                      type="checkbox"
                      :checked="typeChecked(o.value)"
                      @change="toggleType(o.value, ($event.target as HTMLInputElement).checked)"
                    />
                    <span>{{ o.label }}</span>
                  </label>
                </div>

                <div class="pop-foot">
                  <span class="pop-hint">{{ typeHint }}</span>
                  <button class="pop-done" type="button" @click="closeMenus">完成筛选</button>
                </div>
              </div>
            </div>

            <span class="control-sep" />

            <!-- (2) 分辨率：单选 -->
            <div class="pop-group" :class="{ open: openMenu === 'res' }">
              <button
                class="pop-btn"
                :class="{ 'is-active': activeResolution !== '' }"
                type="button"
                @click.stop="toggleMenu('res')"
              >
                <span>分辨率: <strong>{{ resolutionLabelText }}</strong></span>
                <svg class="caret" viewBox="0 0 10 6" width="10" height="6" fill="none"><path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </button>

              <div class="pop-menu pop-menu--res" @click.stop>
                <button
                  class="opt-row"
                  :class="{ on: activeResolution === '' }"
                  type="button"
                  @click="pickResolution('')"
                >
                  <span>全部画质</span>
                  <span v-if="activeResolution === ''" class="tick">✓</span>
                </button>
                <button
                  v-for="o in resolutionOptions"
                  :key="o.key"
                  class="opt-row"
                  :class="{ on: activeResolution === o.key }"
                  type="button"
                  @click="pickResolution(o.key)"
                >
                  <span>
                    {{ o.label }}
                    <template v-if="resolutionFacetsReady">· {{ resolutionFacets[o.key] || 0 }}</template>
                  </span>
                  <span v-if="activeResolution === o.key" class="tick">✓</span>
                </button>
              </div>
            </div>

            <span class="control-sep" />

            <!-- (3) 排序：单选 -->
            <div class="pop-group pop-group--right" :class="{ open: openMenu === 'sort' }">
              <button
                class="pop-btn"
                :class="{ 'is-active': sortOrder !== 'created_desc' }"
                type="button"
                @click.stop="toggleMenu('sort')"
              >
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
          </div>

          <SaDensitySwitch icon-only />

          <div class="browse-view-switch" role="tablist">
            <button
              class="browse-view-btn"
              :class="{ 'browse-view-btn--active': viewMode === 'videos' }"
              role="tab"
              @click="viewMode = 'videos'"
            >
              视频
            </button>
            <button
              class="browse-view-btn"
              :class="{ 'browse-view-btn--active': viewMode === 'folders' }"
              role="tab"
              @click="viewMode = 'folders'"
            >
              文件夹
            </button>
          </div>
        </div>
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
              class="gallery-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="gc-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="gc-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="gc-img gc-placeholder">
                  <MovieOutlined :size="26" />
                </div>
              </div>
              <h3 class="gc-title" :title="mv.name">{{ mv.name }}</h3>
              <div class="gc-foot">
                <span class="gc-type">{{ videoTypeName(mv.video_type) }}</span>
                <div class="gc-nums">
                  <span class="gc-dur">{{ formatDuration(mv.duration) }}</span>
                  <span class="gc-dot">●</span>
                  <span class="gc-res">{{ resolutionLabel(mv.width, mv.height) }}</span>
                </div>
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
              class="gallery-card"
              :href="videoPath(mv.uid)"
              @click.prevent="goVideo(mv)"
            >
              <div class="gc-cover">
                <img
                  v-if="thumbUrl(mv)"
                  :src="thumbUrl(mv)!"
                  :alt="mv.name"
                  class="gc-img"
                  loading="lazy"
                  @error="onThumbError(mv)"
                />
                <div v-else class="gc-img gc-placeholder">
                  <MovieOutlined :size="26" />
                </div>
              </div>
              <h3 class="gc-title" :title="mv.name">{{ mv.name }}</h3>
              <div class="gc-foot">
                <span class="gc-type">{{ videoTypeName(mv.video_type) }}</span>
                <div class="gc-nums">
                  <span class="gc-dur">{{ formatDuration(mv.duration) }}</span>
                  <span class="gc-dot">●</span>
                  <span class="gc-res">{{ resolutionLabel(mv.width, mv.height) }}</span>
                </div>
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
  max-width: var(--sa-content-max, 1280px);
  margin: 0 auto;
  padding: 0 var(--sa-gutter, 36px) 64px;
}

.page-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  padding: 30px 0 16px;
  border-bottom: 1px solid var(--sa-border-subtle);
  margin-bottom: 22px;
}
.page-title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', var(--sa-font-sans, sans-serif);
  font-size: 26px;
  font-weight: 700;
  color: var(--sa-text-primary);
  letter-spacing: -0.01em;
}
/* 独立徽标样式：与标题拉开层次，避免读成“浏览147” */
.page-count {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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
  align-items: center;
  gap: 8px;
  padding-bottom: 18px;
}
/* 筛选控件统一框型 = 类型下拉（.sa-select）：36px 高 / 12px 圆角 / elevated 底 /
   1px --sa-border 边框 / 13px 字 / text-primary 字色 —— 同排不再有 8px 小框与原生 select */
.type-chips .chip {
  height: 36px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--sa-border);
  border-radius: 12px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
}
.type-select-wrap {
  width: 150px;
}
.sort-select-wrap {
  width: 170px;
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
/* 激活态反白（移动端激活筛选条/分辨率 chip 用基础版；浏览筛选行用下面的加权版） */
.chip--active,
.chip--active:hover {
  color: var(--sa-bg);
  border-color: var(--sa-text-primary);
  background: var(--sa-text-primary);
}
/* 激活态 chip 悬停保持反白配色(hover 规则优先级更高,需显式覆盖回来)。
   ⚠ 反白配色必须写成 .type-chips .chip--active（0,2,0）才能压过上面的 .type-chips .chip
   （同为 0,2,0 但写在后面）—— 曾经只写 .chip--active（0,1,0），background 被统一框型
   的 elevated 盖掉、只剩 color 反白 → 浅色主题白底白字，整颗看起来是空白的白色胶囊。 */
.type-chips .chip--active,
.type-chips .chip--active:hover {
  color: var(--sa-bg);
  border-color: var(--sa-text-primary);
  background: var(--sa-text-primary);
}
.type-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
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
  /* 卡片去掉了外圈与内距，间距补 4px 免得封面挤在一起 */
  gap: 24px;
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
  .active-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding-bottom: 16px;
  }
  /* 筛选行：全部/类型/分辨率/排序 一行放下，两个下拉均分剩余宽度 */
  .type-chips {
    flex-wrap: nowrap;
    gap: 6px;
    padding-bottom: 16px;
  }
  .type-chips .chip {
    padding: 0 10px;
  }
  .type-select-wrap,
  .sort-select-wrap {
    width: auto;
    min-width: 0;
  }
  .type-select-wrap {
    flex: 1 1 0;
  }
  .sort-select-wrap {
    flex: 1.2 1 0;
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
/* ============================================================
   浏览页：对照 gemini-code 样板的复刻层
   实色控制条 + popIn 下拉 + 无遮挡 16:9 卡片；颜色全部走 --sa-* 令牌，
   浅色主题下与样板 1:1，深色主题自动换算。
   ============================================================ */
.mv-container {
  /* 与全局顶栏同一条基线：品牌烙印 left:36，右侧操作区 right:44（SaHeader.vue）。
     不做居中限宽 —— 顶栏是通栏的，页面一旦居中就和品牌/设置对不上了。 */
  max-width: none;
  padding: 0 44px 72px 36px;
}
.browse-head {
  position: relative;
  z-index: 30;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 26px 0 16px;
  margin-bottom: 20px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.head-left {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}
.title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.browse-title {
  margin: 0;
  font-family: 'Noto Serif SC', 'Songti SC', serif;
  font-size: 30px;
  font-weight: 900;
  letter-spacing: -0.02em;
  color: var(--sa-text-primary);
}
.count-badge {
  font-family: inherit;
  font-size: 11px;
  font-weight: 500;
  padding: 2px 9px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  color: var(--sa-text-secondary);
  white-space: nowrap;
}
.search-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0;
  width: 34px;
  justify-content: center;
  border-radius: 9999px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.2s ease;
}
.search-pill.is-open {
  width: 240px;
  padding: 0 12px;
  justify-content: flex-start;
}
.search-pill.is-open:focus-within {
  width: 280px;
  border-color: var(--sa-text-tertiary);
}
.search-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0;
  border: none;
  background: transparent;
  color: var(--sa-text-tertiary);
  cursor: pointer;
}
.search-trigger:hover {
  color: var(--sa-text-primary);
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
.search-clear:hover {
  color: var(--sa-text-primary);
}
.head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.control-bar {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 9999px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.control-sep {
  flex: none;
  width: 1px;
  height: 16px;
  background: var(--sa-border-subtle);
}
.pop-group {
  position: relative;
}
.pop-btn {
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
.pop-btn:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.pop-btn strong {
  color: var(--sa-text-primary);
  font-weight: 600;
}
.pop-btn.is-active strong {
  color: var(--sa-accent);
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
  padding: 10px;
  border-radius: 16px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border);
  box-shadow:
    0 12px 32px -4px rgba(0, 0, 0, 0.35),
    0 4px 12px -2px rgba(0, 0, 0, 0.18);
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
.pop-menu--type {
  width: 320px;
}
.pop-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4px 8px;
  margin-bottom: 8px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.pop-cap {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--sa-text-tertiary);
}
.pop-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pop-actions .link {
  padding: 0;
  border: none;
  background: none;
  color: var(--sa-accent);
  font-size: 11px;
  font-family: inherit;
  cursor: pointer;
}
.pop-actions .link.muted {
  color: var(--sa-text-tertiary);
}
.pop-actions .link:hover {
  text-decoration: underline;
}
.pop-sep {
  color: var(--sa-text-tertiary);
  opacity: 0.5;
}
.type-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  max-height: 260px;
  overflow-y: auto;
  padding-right: 2px;
}
.type-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 12px;
  color: var(--sa-text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease;
}
.type-row:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.type-row input {
  flex: none;
  width: 14px;
  height: 14px;
  accent-color: var(--sa-accent);
  cursor: pointer;
}
.type-row span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pop-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 4px 0;
  margin-top: 8px;
  border-top: 1px solid var(--sa-border-subtle);
}
.pop-hint {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.pop-done {
  padding: 5px 12px;
  border: none;
  border-radius: 8px;
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-size: 11px;
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.pop-done:hover {
  opacity: 0.85;
}
.pop-menu--res,
.pop-menu--sort {
  width: 210px;
  padding: 6px;
}
.opt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 7px 10px;
  border: none;
  border-radius: 10px;
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
.browse-view-switch {
  display: inline-flex;
  padding: 3px;
  gap: 2px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
}
.browse-view-btn {
  padding: 5px 12px;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background-color 0.18s ease, color 0.18s ease;
}
.browse-view-btn:hover {
  color: var(--sa-text-primary);
}
.browse-view-btn--active {
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
}
.card-grid {
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 200px), 1fr));
  gap: 16px;
}
.density-compact {
  --grid-min: 170px;
}
.density-standard {
  --grid-min: 200px;
}
.density-loose {
  --grid-min: 260px;
}
.gallery-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  /* 不再给每个视频套一圈：本体透明无边框无内距，封面直接落在页面底色上 */
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
}
.gc-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 10px;
  overflow: hidden;
  background: var(--sa-subtle);
  margin-bottom: 10px;
}
.gc-img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s ease;
}
.gallery-card:hover .gc-img {
  transform: scale(1.025);
}
.gc-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-tertiary);
}
.gc-title {
  margin: 0;
  font-size: 12.5px;
  font-weight: 600;
  line-height: 1.45;
  color: var(--sa-text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  transition: color 0.18s ease;
}
.gallery-card:hover .gc-title {
  color: var(--sa-accent);
}
.gc-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
}
.gc-type {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  font-size: 10.5px;
  color: var(--sa-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gc-nums {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
.gc-res {
  font-size: 10px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
.gc-dot {
  font-size: 7px;
  opacity: 0.55;
}
@media (max-width: 1100px) {
  /* ⚠ 不能给 .control-bar 加 overflow:auto —— 下拉面板是它的绝对定位子元素，
     一裁就看不见。窄屏让它换行，面板照常溢出显示。 */
  .search-pill {
    width: 200px;
  }
  .control-bar {
    flex-wrap: wrap;
  }
}
@media (max-width: 760px) {
  .mv-container {
    padding: 0 16px 56px;
  }
  .browse-head {
    flex-direction: column;
    align-items: stretch;
    gap: 14px;
    padding: 18px 0 14px;
  }
  .browse-title {
    font-size: 24px;
  }
  .head-left {
    gap: 10px;
  }
  /* 移动端筛选区：搜索 + 类型 + 分辨率 + 排序 + 密度 + 视图全部压成一行，
     横向滑动查看超出部分；控件自身绝不换行。滚动条隐藏，页面本身不产生横向溢出。 */
  .search-pill,
  .search-pill:focus-within {
    width: 34px;
    height: 34px;
    flex: 0 0 34px;
    padding: 0;
    justify-content: center;
  }
  .search-pill.is-open,
  .search-pill.is-open:focus-within {
    width: 168px;
    flex-basis: 168px;
    padding: 0 10px;
    justify-content: flex-start;
  }
  .head-right {
    flex-wrap: nowrap;
    align-items: center;
    gap: 6px;
    width: 100%;
    min-width: 0;
    height: 36px;
    padding: 0;
    overflow-x: auto;
    overflow-y: hidden;
    scrollbar-width: none;
    -ms-overflow-style: none;
    overscroll-behavior-x: contain;
    -webkit-overflow-scrolling: touch;
  }
  .head-right::-webkit-scrollbar {
    display: none;
  }
  .head-right > * {
    flex: 0 0 auto;
  }
  .control-bar {
    order: 0;
    width: auto;
    max-width: none;
    height: 36px;
    padding: 0 3px;
    flex: 0 0 auto;
    flex-wrap: nowrap;
    justify-content: flex-start;
    border-radius: 9999px;
  }
  .control-bar .pop-group {
    flex: 0 0 auto;
  }
  .pop-btn {
    height: 34px;
    padding: 0 10px;
    font-size: 11.5px;
    white-space: nowrap;
  }
  .control-sep {
    flex: 0 0 1px;
  }
  .head-right :deep(.sa-density) {
    width: 36px;
    height: 36px;
    flex: 0 0 36px;
  }
  .browse-view-switch {
    height: 36px;
    padding: 0;
    flex: 0 0 auto;
  }
  .browse-view-btn {
    display: inline-flex;
    align-items: center;
    height: 34px;
    padding: 0 10px;
    white-space: nowrap;
  }
  /* 下拉菜单改为视口底部的浮层：横向滚动容器会裁剪绝对定位子元素，
     fixed 浮层既能完整显示，也不会反过来撑宽页面。 */
  .pop-menu,
  .pop-group--right .pop-menu {
    position: fixed;
    top: auto;
    right: 16px;
    bottom: calc(var(--sa-bottom-bar-height, 64px) + 12px);
    left: 16px;
    width: auto;
    max-width: none;
    max-height: calc(100svh - var(--sa-bottom-bar-height, 64px) - 84px);
    overflow-y: auto;
  }
  /* 手机端固定两列（与样板 grid-cols-2 一致），auto-fill 在 390 宽会退化成 1 列 */
  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
}
</style>
