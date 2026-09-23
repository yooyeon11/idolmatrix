<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import { photosApi } from '@/api/photos'
import { videoCollectionsApi } from '@/api/videoCollections'
import { musicVideosApi } from '@/api/musicVideos'
import type { PhotoCollection, VideoCollection } from '@/types/models'
import { collectionPath, videoCollectionPath } from '@/utils/routes'
import { IMG_W_CARD } from '@/utils/imageSizes'
import { BookmarkOutlined } from '@/components/icons'

const router = useRouter()
const message = useMessage()
const tab = ref<'photos' | 'videos'>('photos')
const photoItems = ref<PhotoCollection[]>([])
const videoItems = ref<VideoCollection[]>([])
const loading = ref(false)
const creating = ref(false)
const newName = ref('')

const itemsCount = computed(() =>
  tab.value === 'photos' ? photoItems.value.length : videoItems.value.length,
)

async function load() {
  loading.value = true
  try {
    const [photos, videos] = await Promise.all([
      photosApi.collections(),
      videoCollectionsApi.list(),
    ])
    photoItems.value = photos
    videoItems.value = videos
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function createCollection() {
  const name = newName.value.trim()
  if (!name) {
    message.warning('请填写收藏夹名称')
    return
  }
  creating.value = true
  try {
    if (tab.value === 'videos') {
      const col = await videoCollectionsApi.create({ name })
      newName.value = ''
      router.push(videoCollectionPath(col.uid))
    } else {
      const col = await photosApi.createCollection({ name })
      newName.value = ''
      router.push(collectionPath(col.uid))
    }
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    creating.value = false
  }
}

function coverSrc(col: PhotoCollection) {
  return col.cover_photo_id ? photosApi.thumbUrl(col.cover_photo_id) : ''
}

function videoCoverSrc(col: VideoCollection) {
  return col.cover_video_id
    ? musicVideosApi.thumbnailUrl(col.cover_video_id, undefined, IMG_W_CARD)
    : ''
}

watch(tab, () => {
  newName.value = ''
})

onMounted(load)
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">收藏</h1>
          <span class="page-count">{{ itemsCount }}</span>
        </div>
        <form class="create-row" @submit.prevent="createCollection">
          <input
            v-model="newName"
            class="search-input"
            type="text"
            :placeholder="tab === 'videos' ? '新建视频收藏夹…' : '新建图片收藏夹…'"
          />
          <button class="create-btn" type="submit" :disabled="creating">
            {{ creating ? '创建中…' : '创建' }}
          </button>
        </form>
      </div>

      <div class="kind-tabs">
        <button
          class="kind-tab"
          :class="{ 'kind-tab--on': tab === 'photos' }"
          type="button"
          @click="tab = 'photos'"
        >
          图片收藏
        </button>
        <button
          class="kind-tab"
          :class="{ 'kind-tab--on': tab === 'videos' }"
          type="button"
          @click="tab = 'videos'"
        >
          视频收藏
        </button>
      </div>

      <div v-if="loading && !photoItems.length && !videoItems.length" class="list-loading">加载中…</div>
      <template v-else-if="tab === 'photos' && photoItems.length">
        <div class="card-grid">
          <button
            v-for="col in photoItems"
            :key="'p-' + col.id"
            class="card"
            type="button"
            @click="router.push(collectionPath(col.uid))"
          >
            <div class="card-cover">
              <img v-if="coverSrc(col)" class="card-cover-img" :src="coverSrc(col)" alt="" />
              <BookmarkOutlined v-else :size="28" />
            </div>
            <div class="card-info">
              <div class="card-name">{{ col.name }}</div>
              <div class="card-meta">{{ col.photo_count }} 张</div>
            </div>
          </button>
        </div>
      </template>
      <template v-else-if="tab === 'videos' && videoItems.length">
        <div class="card-grid">
          <button
            v-for="col in videoItems"
            :key="'v-' + col.id"
            class="card"
            type="button"
            @click="router.push(videoCollectionPath(col.uid))"
          >
            <div class="card-cover card-cover--video">
              <img v-if="videoCoverSrc(col)" class="card-cover-img" :src="videoCoverSrc(col)" alt="" />
              <BookmarkOutlined v-else :size="28" />
            </div>
            <div class="card-info">
              <div class="card-name">{{ col.name }}</div>
              <div class="card-meta">{{ col.video_count }} 个</div>
            </div>
          </button>
        </div>
      </template>
      <div v-else class="empty-state">
        <BookmarkOutlined :size="40" />
        <p v-if="tab === 'videos'">还没有视频收藏夹。播放页标题旁可以把视频加进来。</p>
        <p v-else>还没有图片收藏夹。在图库大图左上角可以把照片加进来。</p>
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
.kind-tabs {
  display: flex;
  gap: 8px;
  margin: -8px 0 20px;
}
.kind-tab {
  padding: 6px 14px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.kind-tab--on {
  background: var(--sa-accent);
  border-color: transparent;
  color: #fff;
}
.create-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.search-input {
  width: 220px;
  padding: 9px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 14px;
  outline: none;
}
.search-input:focus {
  border-color: var(--sa-accent);
}
.create-btn {
  padding: 9px 16px;
  border: 0;
  border-radius: 9999px;
  background: var(--sa-accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}
.create-btn:disabled {
  opacity: 0.6;
}
.list-loading,
.empty-state {
  padding: 80px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.empty-state p {
  margin: 0;
  max-width: 36ch;
  line-height: 1.6;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}
.card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
  padding: 14px;
  text-align: left;
  cursor: pointer;
}
.card:hover {
  border-color: var(--sa-accent);
}
.card-cover {
  width: 100%;
  aspect-ratio: 1;
  border-radius: 12px;
  overflow: hidden;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  display: flex;
  align-items: center;
  justify-content: center;
}
/* 视频收藏夹的封面是 16:9 视频缩略图 → 用正方形框会被裁掉两侧，改成同比例显示 */
.card-cover--video {
  aspect-ratio: 16 / 9;
}
.card-cover-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.card-info {
  margin-top: 12px;
}
.card-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-meta {
  margin-top: 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}

@media (max-width: 768px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  /* 标题行与「新建」行各占一行：挤在同一行时输入框被压到 160px、placeholder 被截断 */
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
    padding: 20px 0 16px;
  }
  .page-title {
    font-size: 22px;
  }
  .create-row {
    width: 100%;
  }
  .search-input {
    flex: 1 1 auto;
    width: auto;
    min-width: 0;
  }
  .create-btn {
    flex: none;
  }
  .kind-tabs {
    margin: -4px 0 16px;
  }
  /* 单列全宽巨卡（封面 1:1 → 358px 大方块）改为 2 列小卡，与艺人/组合列表同口径 */
  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .card {
    padding: 10px;
    border-radius: 14px;
  }
  .card-cover {
    border-radius: 9px;
  }
  .card-info {
    margin-top: 8px;
  }
  .card-name {
    font-size: 13px;
  }
  .card-meta {
    margin-top: 2px;
    font-size: 11px;
  }
}
</style>
