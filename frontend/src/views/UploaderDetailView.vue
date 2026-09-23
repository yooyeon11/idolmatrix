<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import SaMobileSettings from '@/components/SaMobileSettings.vue'
import { musicVideosApi } from '@/api/musicVideos'
import { useSettingsStore } from '@/stores/settings'
import type { MusicVideo } from '@/types/models'
import { VIDEO_TYPE_LABEL } from '@/types/models'
import { MovieOutlined, SearchOutlined } from '@/components/icons'
import { videoPath } from '@/utils/routes'
import { formatVideoSongs } from '@/utils/format'
import { IMG_W_CARD } from '@/utils/imageSizes'
import { useGridDensity, useGridPageSize } from '@/composables/useGridDensity'

const route = useRoute()
const router = useRouter()
const settings = useSettingsStore()
const message = useMessage()

const uploaderName = computed(() =>
  decodeURIComponent((route.params.name as string) || '')
)
const loading = ref(false)
const videos = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const searchQuery = ref('')
const brokenThumbs = ref<Set<number>>(new Set())

const { density } = useGridDensity()
// 网格列数实测 → page_size = 列数 × 行数：整页恰好铺满网格，最后一行不再缺位
const gridEl = ref<HTMLElement | null>(null)
function bindGrid(el: unknown) {
  gridEl.value = (el as HTMLElement | null) ?? null
}
const { pageSize } = useGridPageSize(gridEl)

// 加载序列号：丢弃过期的异步回调（快速切换路由时旧请求不应覆盖新数据）
let loadSeq = 0

async function load(targetPage: number) {
  const seq = ++loadSeq
  loading.value = true
  try {
    const res = await musicVideosApi.list({
      ingestion_status: 'library',
      uploader: uploaderName.value,
      page: targetPage,
      page_size: pageSize.value,
      q: searchQuery.value.trim() || undefined,
    })
    if (seq !== loadSeq) return
    videos.value = res.items
    total.value = res.total
    pageNum.value = targetPage
  } catch (e) {
    if (seq !== loadSeq) return
    message.error((e as Error).message)
    if (targetPage === 1) {
      videos.value = []
      total.value = 0
    }
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

watch(uploaderName, () => load(1))

// page_size 变化（首次测量完成 / 切换密度档位 / 视口跨列数边界）：回到第 1 页重新加载
watch(pageSize, (v) => {
  if (v > 0) void load(1)
})

// 搜索：防抖 350ms，改为服务端过滤（与其他列表页一致）
let searchDebounce: ReturnType<typeof setTimeout> | undefined
watch(searchQuery, () => {
  clearTimeout(searchDebounce)
  searchDebounce = setTimeout(() => load(1), 350)
})

function videoTypeName(t: string) {
  return VIDEO_TYPE_LABEL[t] || t
}

function thumbUrl(mv: MusicVideo) {
  if (!settings.showCovers || brokenThumbs.value.has(mv.id)) return undefined
  return musicVideosApi.thumbnailUrl(mv.id, mv.file_hash || mv.file_size, IMG_W_CARD)
}
function onThumbError(mv: MusicVideo) {
  brokenThumbs.value = new Set(brokenThumbs.value).add(mv.id)
}

function goVideo(mv: { uid: string }) {
  router.push(videoPath(mv.uid))
}
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="detail-head">
        <div class="detail-title-wrap">
          <h1 class="detail-title">{{ uploaderName }}</h1>
          <span class="detail-count">{{ total }} 个视频</span>
        </div>
        <div class="head-tools">
          <!-- 移动端收纳入口：密度档位进底部抽屉，桌面端按断点自动隐藏 -->
          <SaMobileSettings :breakpoint="640" title="显示设置">
            <div class="sms-group">
              <div class="sms-label">宽松度</div>
              <SaDensitySwitch />
            </div>
          </SaMobileSettings>
          <!-- display: contents：桌面端不参与布局，移动端整体隐藏 -->
          <span class="head-extra">
            <SaDensitySwitch />
          </span>
          <div class="search-box">
            <SearchOutlined :size="15" />
            <input
              v-model="searchQuery"
              class="search-input"
              type="text"
              placeholder="搜索该频道视频…"
            />
          </div>
        </div>
      </div>

      <div v-if="loading && !videos.length" class="list-loading">
        加载中…
        <!-- 空网格的 auto-fill 轨道数与有数据时一致：加载态也渲染网格供列数测量，避免首屏死锁 -->
        <div :ref="bindGrid" class="card-grid" :class="`density-${density}`" aria-hidden="true"></div>
      </div>

      <template v-else>
        <div :ref="bindGrid" class="card-grid" :class="`density-${density}`">
          <a
            v-for="mv in videos"
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
              <span v-if="mv.duration" class="card-duration">
                {{ Math.floor(mv.duration / 60) }}:{{ String(mv.duration % 60).padStart(2, '0') }}
              </span>
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
          v-if="videos.length"
          :total="total"
          :page="pageNum"
          :page-size="pageSize"
          :disabled="loading"
          :show-total="false"
          @update:page="onPageChange"
        />
        <div v-if="!videos.length" class="empty-state">
          <MovieOutlined :size="40" />
          <p>该博主暂无视频</p>
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
.detail-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 36px 0 28px;
  flex-wrap: wrap;
}
.detail-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.detail-title {
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
  margin: 0;
  letter-spacing: -0.02em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail-count {
  font-size: 14px;
  color: var(--sa-text-tertiary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  flex-shrink: 0;
}
.head-tools {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
/* 桌面端：head-extra 以 contents 透传给 head-tools 的 flex 布局 */
.head-extra {
  display: contents;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 16px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
  color: var(--sa-text-tertiary);
}
.search-box:focus-within {
  border-color: var(--sa-accent);
}
.search-input {
  border: none;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 14px;
  outline: none;
  width: 200px;
}
.search-input::placeholder {
  color: var(--sa-text-tertiary);
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 220px), 1fr));
  gap: 24px;
}
.density-compact { --grid-min: 180px; }
.density-standard { --grid-min: 220px; }
.density-loose { --grid-min: 280px; }
.poster-card {
  cursor: pointer;
  text-decoration: none;
  display: block;
}
.poster-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
}
.poster-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}
.poster-card:hover .poster-cover img {
  transform: scale(1.05);
}
.poster-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.75);
}
.card-duration {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #fff;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  line-height: 1.6;
}
.poster-title {
  margin-top: 10px;
  font-size: 14px;
  font-weight: 500;
  color: var(--sa-text-primary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.poster-meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
  overflow: hidden;
}
.poster-meta span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
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
  .detail-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  /* 移动端工具栏只留：搜索框 + ⚙ 收纳入口 */
  .head-tools {
    width: 100%;
    flex-wrap: nowrap;
  }
  .head-extra {
    display: none;
  }
  .search-box {
    flex: 1;
    width: auto;
  }
  .card-grid.density-compact {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .search-input {
    flex: 1;
    width: auto;
  }
}
</style>
