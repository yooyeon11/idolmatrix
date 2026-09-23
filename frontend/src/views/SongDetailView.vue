<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NSpin, useMessage } from 'naive-ui'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaDensitySwitch from '@/components/SaDensitySwitch.vue'
import SaMobileSettings from '@/components/SaMobileSettings.vue'
import VideoCard from '@/components/VideoCard.vue'
import { songsApi } from '@/api/songs'
import { musicVideosApi } from '@/api/musicVideos'
import type { MusicVideo, Song } from '@/types/models'
import { fetchByRouteParam, songPath } from '@/utils/routes'
import { useGridDensity } from '@/composables/useGridDensity'
import { MusicNoteOutlined } from '@/components/icons'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const song = ref<Song | null>(null)
const videos = ref<MusicVideo[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 100
const loading = ref(false)
const loadingVideos = ref(false)
const videosLoaded = ref(false)
const notFound = ref(false)

const { density } = useGridDensity()

const routeKey = computed(() => String(route.params.uid || ''))

function songName() {
  return song.value?.chinese_name || song.value?.name || '歌曲'
}
function relationText() {
  return (song.value?.relation_names || []).filter(Boolean).join(' · ')
}

async function loadSong() {
  const param = routeKey.value.trim()
  if (!param) {
    notFound.value = true
    return
  }
  loading.value = true
  notFound.value = false
  try {
    const s = await fetchByRouteParam(param, songsApi.getByUid, songsApi.get)
    song.value = s
    if (s.uid && s.uid !== param) {
      router.replace(songPath(s.uid))
    }
    pageNum.value = 1
    videos.value = []
    total.value = 0
    videosLoaded.value = false
    await loadVideos(1)
  } catch (e) {
    notFound.value = true
    message.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function loadVideos(targetPage: number) {
  if (!song.value || loadingVideos.value) return
  loadingVideos.value = true
  try {
    const r = await musicVideosApi.list({
      song_id: song.value.id,
      is_short: false,
      page: targetPage,
      page_size: pageSize,
    })
    videos.value = r.items
    total.value = r.total
    pageNum.value = targetPage
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    loadingVideos.value = false
    videosLoaded.value = true
  }
}

function onPageChange(p: number) {
  void loadVideos(p).then(() => window.scrollTo({ top: 0 }))
}

onMounted(loadSong)
watch(
  () => route.params.uid,
  (next, prev) => {
    if (!next || next === prev) return
    void loadSong()
  },
)
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <n-spin :show="loading">
        <div v-if="notFound" class="empty-state">
          <MusicNoteOutlined :size="40" />
          <p>歌曲不存在或已删除</p>
          <button class="empty-back" type="button" @click="router.push('/songs')">返回歌曲列表</button>
        </div>
        <div v-else-if="song" class="song-page">
          <div class="song-head">
            <div class="song-title-wrap">
              <h1 class="song-title">{{ songName() }}</h1>
              <span class="song-count">{{ song.video_count ?? total }} 个视频</span>
            </div>
            <p v-if="relationText()" class="song-relation">{{ relationText() }}</p>
            <div class="song-actions">
              <SaMobileSettings :breakpoint="640" title="显示设置">
                <div class="sms-group">
                  <div class="sms-label">宽松度</div>
                  <SaDensitySwitch />
                </div>
              </SaMobileSettings>
              <span class="head-extra">
                <SaDensitySwitch />
              </span>
            </div>
          </div>
          <div class="video-grid" :class="`density-${density}`">
            <VideoCard v-for="(v, i) in videos" :key="v.id" :video="v" :index="i" />
          </div>
          <div v-if="!videos.length && videosLoaded" class="empty-state">
            <MusicNoteOutlined :size="32" />
            <p>这首歌还没有关联视频</p>
          </div>
          <SaPagination
            v-if="videos.length"
            :total="total"
            :page="pageNum"
            :page-size="pageSize"
            :disabled="loadingVideos"
            :show-total="false"
            @update:page="onPageChange"
          />
        </div>
      </n-spin>
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

.song-head {
  padding: 28px 0 18px;
}
.song-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}
.song-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  color: var(--sa-text-primary);
  letter-spacing: -0.02em;
}
.song-count {
  font-size: 14px;
  color: var(--sa-text-tertiary);
}
.song-relation {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--sa-text-secondary);
}
.song-actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
/* 桌面端：head-extra 以 contents 透传给 song-actions 的 flex 布局 */
.head-extra {
  display: contents;
}

.video-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(var(--grid-min, 220px), 1fr));
  gap: 20px;
  margin-top: 16px;
  /* VideoCard 兜底宽 224px：移动端 --grid-min 降到 130/160/200px 后轨道比卡窄，
     卡片横向溢出互相压叠 —— 与 entity-detail.css 同口径，卡片一律填满网格轨道 */
  --video-card-width: 100%;
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

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 80px 0;
  color: var(--sa-text-tertiary);
}
.empty-state p {
  margin: 0;
  font-size: 14px;
}
.empty-back {
  padding: 6px 18px;
  border: 1px solid var(--sa-border);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  cursor: pointer;
}

@media (max-width: 768px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  .song-title {
    font-size: 22px;
  }
  /* 移动端：密度切换隐藏，由 ⚙ 收纳入口替代 */
  .head-extra {
    display: none;
  }
  .video-grid {
    gap: 12px;
    margin-top: 12px;
  }
  .video-grid.density-compact {
    --grid-min: 130px;
  }
  .video-grid.density-standard {
    --grid-min: 160px;
  }
  .video-grid.density-loose {
    --grid-min: 200px;
  }
}
</style>