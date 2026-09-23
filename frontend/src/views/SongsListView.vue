<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import { songsApi } from '@/api/songs'
import type { Song } from '@/types/models'
import { formatDuration } from '@/utils/format'
import { songPath } from '@/utils/routes'
import { MusicNoteOutlined } from '@/components/icons'

const router = useRouter()

const items = ref<Song[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 30
const loading = ref(false)
const keyword = ref('')
const searchInput = ref('')

// 「仅显示有关联视频的歌曲」开关：默认开启，与艺人页行为一致
const ONLY_WITH_VIDEOS_KEY = 'songs:only_with_videos'
const onlyWithVideos = ref(
  (() => {
    const saved = localStorage.getItem(ONLY_WITH_VIDEOS_KEY)
    return saved === null ? true : saved === '1'
  })(),
)
function toggleOnlyWithVideos() {
  onlyWithVideos.value = !onlyWithVideos.value
  localStorage.setItem(ONLY_WITH_VIDEOS_KEY, onlyWithVideos.value ? '1' : '0')
  load(1)
}

const palettes: [string, string][] = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function coverStyle(id: number) {
  const [c1, c2] = palettes[id % palettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}

function displayName(s: Song) {
  return s.chinese_name || s.name || `#${s.id}`
}
function displaySub(s: Song) {
  const names = (s.relation_names || []).filter(Boolean)
  if (names.length) return names.join(' · ')
  const parts: string[] = []
  if (s.song_type) parts.push(s.song_type)
  const d = formatDuration(s.duration)
  if (d !== '--') parts.push(d)
  return parts.join(' · ')
}
function videoLabel(s: Song) {
  return `${s.video_count ?? 0} 个视频`
}

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await songsApi.list({
      q: keyword.value || undefined,
      page: targetPage,
      page_size: pageSize,
      only_with_videos: onlyWithVideos.value || undefined,
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

function goSong(s: { uid: string }) {
  router.push(songPath(s.uid))
}

onMounted(() => load(1))
</script>

<template>
  <div class="mv-page">
    <SaHeader />
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">歌曲</h1>
          <span class="page-count">{{ total }}</span>
        </div>
        <div class="head-actions">
          <button
            class="filter-toggle"
            :class="{ active: onlyWithVideos }"
            type="button"
            title="隐藏未关联任何视频的歌曲（如批量导入尚未剪校的曲目）"
            @click="toggleOnlyWithVideos"
          >
            {{ onlyWithVideos ? '仅有关联视频' : '显示全部歌曲' }}
          </button>
          <input
            v-model="searchInput"
            class="search-input"
            type="text"
            placeholder="搜索歌曲名称…"
          />
        </div>
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="card-grid">
          <button
            v-for="s in items"
            :key="s.id"
            class="card"
            @click="goSong(s)"
          >
            <div class="card-cover" :style="coverStyle(s.id)">
              <span class="cover-icon"><MusicNoteOutlined :size="30" /></span>
              <span
                v-if="s.video_count"
                class="cover-badge"
                :class="{ 'cover-badge--zero': !s.video_count }"
              >
                {{ videoLabel(s) }}
              </span>
            </div>
            <div class="card-info">
              <div class="card-name">{{ displayName(s) }}</div>
              <div class="card-meta">{{ displaySub(s) || '歌曲' }}</div>
            </div>
          </button>
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
        <MusicNoteOutlined :size="40" />
        <p>没有找到歌曲</p>
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
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.filter-toggle {
  padding: 8px 14px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-tertiary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.filter-toggle:hover {
  border-color: var(--sa-border);
  color: var(--sa-text-secondary);
}
.filter-toggle.active {
  border-color: var(--sa-text-primary);
  color: var(--sa-text-primary);
  background: var(--sa-elevated);
}
.search-input {
  width: 240px;
  padding: 9px 16px;
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

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}

.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 20px;
}
.card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-width: 0; /* grid item：长歌名 nowrap 不撑破轨道 */
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
  padding: 14px;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.2s, transform 0.2s;
}
.card:hover {
  border-color: var(--sa-accent);
  transform: translateY(-2px);
}
.card-cover {
  position: relative;
  width: 100%;
  aspect-ratio: 1;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.85);
  overflow: hidden;
}
.cover-icon {
  display: inline-flex;
}
.cover-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 8px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 11px;
  font-weight: 500;
  backdrop-filter: blur(4px);
}
.cover-badge--zero {
  opacity: 0.75;
}
.card-info {
  margin-top: 12px;
  min-width: 0;
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
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

@media (max-width: 768px) {
  .mv-container {
    padding: 0 16px 48px;
  }
  .page-head {
    flex-direction: column;
    align-items: stretch;
    gap: 12px;
  }
  .search-input {
    width: 100%;
  }
  .card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .card {
    padding: 10px;
    min-width: 0;
  }
  .cover-icon {
    display: none;
  }
  .cover-badge {
    right: 6px;
    bottom: 6px;
    font-size: 10px;
  }
  .card-name {
    font-size: 14px;
  }
  .card-meta {
    font-size: 11px;
  }
}
</style>