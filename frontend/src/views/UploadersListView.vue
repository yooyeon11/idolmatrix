<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import { uploadersApi, type UploaderItem } from '@/api/uploaders'
import { musicVideosApi } from '@/api/musicVideos'
import { useSettingsStore } from '@/stores/settings'
import { VideoLibraryOutlined } from '@/components/icons'
import { IMG_W_CARD } from '@/utils/imageSizes'

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

function coverUrl(u: UploaderItem) {
  if (!settings.showCovers || !u.latest_video_id || brokenThumbs.value.has(u.latest_video_id)) {
    return undefined
  }
  return musicVideosApi.thumbnailUrl(u.latest_video_id, undefined, IMG_W_CARD)
}
function onCoverError(u: UploaderItem) {
  if (u.latest_video_id) brokenThumbs.value = new Set(brokenThumbs.value).add(u.latest_video_id)
}

function initialOf(u: UploaderItem) {
  return (u.name || '?').trim().charAt(0).toUpperCase()
}

onMounted(() => load(1))
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
        <input
          v-model="searchInput"
          class="search-input"
          type="text"
          placeholder="搜索博主（频道）…"
        />
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="uploader-grid">
          <div
            v-for="u in items"
            :key="u.name + (u.platform || '')"
            class="uploader-card"
            @click="goUploader(u)"
          >
            <div class="uploader-cover">
              <img
                v-if="coverUrl(u)"
                :src="coverUrl(u)!"
                :alt="u.name"
                class="uploader-img"
                loading="lazy"
                @error="onCoverError(u)"
              />
              <div v-else class="uploader-placeholder">
                <span class="uploader-initial">{{ initialOf(u) }}</span>
              </div>
              <div class="uploader-icon">
                <VideoLibraryOutlined :size="14" />
              </div>
            </div>
            <div class="uploader-name" :title="u.name">{{ u.name }}</div>
            <div class="uploader-meta">
              <span class="uploader-count">{{ u.video_count }} 个视频</span>
              <span v-if="u.platform" class="uploader-platform">{{ u.platform }}</span>
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
  max-width: 1280px;
  margin: 0 auto;
  padding: 0 32px 64px;
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
.search-input {
  width: 240px;
  padding: 9px 16px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border-subtle);
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

.uploader-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}
.uploader-card {
  cursor: pointer;
  border-radius: 16px;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  padding: 14px;
  transition: transform 0.2s, border-color 0.2s;
}
.uploader-card:hover {
  transform: translateY(-2px);
  border-color: var(--sa-accent);
}
.uploader-cover {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
}
.uploader-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.uploader-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1e2a3a, #2a1e3a);
}
.uploader-initial {
  font-size: 34px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.65);
}
.uploader-icon {
  position: absolute;
  right: 8px;
  bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
}
.uploader-name {
  margin-top: 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--sa-text-primary);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.uploader-meta {
  margin-top: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.uploader-count {
  color: var(--sa-text-secondary);
}
.uploader-platform {
  color: var(--sa-accent);
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
  .search-input {
    width: 100%;
  }
}
</style>
