<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaFilterBar from '@/components/SaFilterBar.vue'
import SaFilterSearch from '@/components/SaFilterSearch.vue'
import SaPagination from '@/components/SaPagination.vue'
import { uploadersApi, type UploaderItem } from '@/api/uploaders'
import { musicVideosApi } from '@/api/musicVideos'
import { useSettingsStore } from '@/stores/settings'
import { VideoLibraryOutlined } from '@/components/icons'
import { IMG_W_THUMB } from '@/utils/imageSizes'

const router = useRouter()
const settings = useSettingsStore()

const items = ref<UploaderItem[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 24
const loading = ref(false)
const keyword = ref('')
const searchInput = ref('')
const brokenThumbs = ref<Set<number>>(new Set())

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await uploadersApi.list({
      q: keyword.value || undefined,
      page: targetPage,
      page_size: pageSize,
    })
    items.value = res.items
    total.value = res.total
    pageNum.value = targetPage
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

let debounceTimer: ReturnType<typeof setTimeout> | undefined
watch(searchInput, (v) => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    keyword.value = v.trim()
    load(1)
  }, 350)
})

function goUploader(u: UploaderItem) {
  router.push({ path: `/uploaders/${encodeURIComponent(u.name)}` })
}

// ===== 悬停就地展开的 4×4 海报墙（方案 A）=====
// 16 张缩略图只在首次悬停该卡时挂载，避免一进页面就发几百个图片请求。
// ⚠ 悬停有意延迟 400ms 才展开（hover intent）：
//    浏览时鼠标常从卡片上滑过，立即展开会不断"弹开/收起"，既闪又容易误点。
//    移出立刻取消计时；键盘 focus 视为明确意图，仍然即时展开。
const HOVER_DELAY = 400
const mounted = ref<Set<string>>(new Set())
const active = ref<Set<string>>(new Set())
const hoverTimers = new Map<string, ReturnType<typeof setTimeout>>()
const tilePalettes: [string, string][] = [
  ['#2b2350', '#5b2a63'],
  ['#123a4d', '#1f6b73'],
  ['#4a1f2c', '#8a3450'],
  ['#2f2a16', '#6d5a22'],
  ['#1d2b4d', '#2f4f8a'],
  ['#3a2144', '#7a3a8a'],
  ['#103a33', '#1f6b5c'],
  ['#46251a', '#8a4a2c'],
]

function keyOf(u: UploaderItem) {
  return `${u.name}::${u.platform || ''}`
}
/** 真正展开：挂载宫格 + 打开抽屉 */
function openWall(k: string) {
  if (!mounted.value.has(k)) mounted.value = new Set(mounted.value).add(k)
  if (!active.value.has(k)) active.value = new Set(active.value).add(k)
}
/** 鼠标进入：起一个 400ms 计时器，期间移出会被取消 */
function reveal(u: UploaderItem) {
  const k = keyOf(u)
  if (active.value.has(k) || hoverTimers.has(k)) return
  hoverTimers.set(
    k,
    setTimeout(() => {
      hoverTimers.delete(k)
      openWall(k)
    }, HOVER_DELAY),
  )
}
/** 键盘 focus：明确意图，不延迟 */
function revealNow(u: UploaderItem) {
  cancelReveal(u)
  openWall(keyOf(u))
}
function cancelReveal(u: UploaderItem) {
  const k = keyOf(u)
  const t = hoverTimers.get(k)
  if (t) {
    clearTimeout(t)
    hoverTimers.delete(k)
  }
}
/** 移出即收起（宫格继续挂载，避免反复请求缩略图） */
function conceal(u: UploaderItem) {
  cancelReveal(u)
  const k = keyOf(u)
  if (active.value.has(k)) {
    const next = new Set(active.value)
    next.delete(k)
    active.value = next
  }
}
/** 海报墙用的视频 id：优先最近 16 条，老接口没有该字段时退回最新一条 */
function mosaic(u: UploaderItem): number[] {
  const ids = u.recent_video_ids?.length
    ? u.recent_video_ids
    : u.latest_video_id
      ? [u.latest_video_id]
      : []
  return ids.slice(0, 16)
}
function tileStyle(id: number) {
  const [c1, c2] = tilePalettes[id % tilePalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function tileUrl(id: number) {
  if (!settings.showCovers || brokenThumbs.value.has(id)) return ''
  return musicVideosApi.thumbnailUrl(id, undefined, IMG_W_THUMB)
}
function onTileError(id: number) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(id)
}
function countLabel(u: UploaderItem) {
  return u.channel_video_total != null
    ? `${u.video_count}/${u.channel_video_total}`
    : String(u.video_count)
}

onMounted(() => load(1))
onBeforeUnmount(() => {
  hoverTimers.forEach((t) => clearTimeout(t))
  hoverTimers.clear()
})
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">博主</h1>
          <span class="page-count">{{ total }}</span>
        </div>
        <SaFilterBar>
          <SaFilterSearch v-model="searchInput" placeholder="搜索博主（频道）…" aria-label="检索博主" width="200px" />
        </SaFilterBar>
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="uploader-grid">
          <div
            v-for="u in items"
            :key="keyOf(u)"
            class="uploader-card"
            tabindex="0"
            @click="goUploader(u)"
            @mouseenter="reveal(u)"
            @mouseleave="conceal(u)"
            @focusin="revealNow(u)"
            @focusout="conceal(u)"
          >
            <div class="up-head">
              <div class="up-id">
                <h3 class="up-name" :title="u.name">{{ u.name }}</h3>
                <div class="up-sub">
                  <span v-if="u.platform" class="up-platform">{{ u.platform }}</span>
                  <span v-if="u.missing_count" class="up-missing">缺 {{ u.missing_count }}</span>
                </div>
              </div>
              <div class="up-count">
                <b>{{ countLabel(u) }}</b>
                <span>部</span>
              </div>
            </div>

            <div
              v-if="mosaic(u).length"
              class="poster-wall"
              :class="{ 'poster-wall--open': active.has(keyOf(u)) }"
              aria-hidden="true"
            >
              <div v-if="mounted.has(keyOf(u))" class="poster-grid">
                <div v-for="id in mosaic(u)" :key="id" class="poster-tile" :style="tileStyle(id)">
                  <img
                    v-if="tileUrl(id)"
                    :src="tileUrl(id)"
                    alt=""
                    loading="lazy"
                    @error="onTileError(id)"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
        <SaPagination
          v-if="items.length"
          :total="total"
          :page="pageNum"
          :page-size="pageSize"
          :disabled="loading"
          :show-total="false"
          @update:page="onPageChange"
        />
      </template>

      <div v-else class="empty-state">
        <VideoLibraryOutlined :size="40" />
        <p>暂无博主数据</p>
      </div>
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
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 28px;
  flex-wrap: wrap;
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
/* 独立徽标样式：与标题拉开层次，避免读成“标题+数字”粘在一起 */
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

.uploader-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
  align-items: start;
}
/* 方案 A：卡片就地向下展开 4×4 海报墙，不产生浮层、不遮挡邻居 */
.uploader-card {
  cursor: pointer;
  border-radius: 16px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  padding: 16px 18px;
  transition:
    background-color 0.3s ease,
    border-color 0.3s ease,
    transform 0.3s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    box-shadow 0.3s ease;
}
.uploader-card:hover,
.uploader-card:focus-visible {
  background: var(--sa-hover);
  border-color: var(--sa-accent-border);
  transform: translateY(-4px);
  box-shadow: 0 20px 40px -20px rgba(0, 0, 0, 0.85);
  outline: none;
}
.up-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}
.up-id {
  min-width: 0;
}
.up-name {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.up-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.up-platform {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 缺失检测徽标：频道全集与库内数量差（未启用检测的博主不显示） */
.up-missing {
  flex: none;
  padding: 0 6px;
  border-radius: 999px;
  border: 1px solid var(--sa-accent);
  color: var(--sa-accent);
  font-size: 10.5px;
  line-height: 1.6;
}
.up-count {
  flex: none;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.up-count b {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-secondary);
  font-variant-numeric: tabular-nums;
}
.up-count span {
  margin-left: 2px;
  font-size: 10px;
  color: var(--sa-text-tertiary);
}

/* 展开态：高度/透明度过渡，卡片自身变高把下方内容推开（不是浮层） */
.poster-wall {
  max-height: 0;
  opacity: 0;
  overflow: hidden;
  transition:
    max-height 0.4s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    opacity 0.3s ease,
    margin-top 0.3s ease;
}
.poster-wall--open {
  max-height: 320px;
  opacity: 1;
  margin-top: 14px;
}
.poster-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  padding-top: 12px;
  border-top: 1px solid var(--sa-border-subtle);
}
.poster-tile {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 4px;
  overflow: hidden;
  border: 1px solid var(--sa-border-subtle);
}
.poster-tile img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.2s ease;
}
.poster-tile:hover img {
  transform: scale(1.08);
}

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}
.empty-state {
  padding: 80px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: var(--sa-text-tertiary);
}
.empty-state p {
  margin: 0;
  font-size: 14px;
}

@media (max-width: 640px) {
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .uploader-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .uploader-card {
    padding: 14px 16px;
  }
}
</style>
