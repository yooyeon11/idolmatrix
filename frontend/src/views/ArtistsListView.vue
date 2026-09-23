<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import { artistsApi } from '@/api/artists'
import type { Artist } from '@/types/models'
import { formatDate } from '@/utils/format'
import { artistPath } from '@/utils/routes'
import { PersonOutlined } from '@/components/icons'

const router = useRouter()

const items = ref<Artist[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 24
const loading = ref(false)
const keyword = ref('')
const searchInput = ref('')

// 「仅显示有关联视频的艺人」开关：默认开启（隐藏 AI 分析组合等产生的无视频艺人）
const ONLY_WITH_VIDEOS_KEY = 'artists:only_with_videos'
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

const avatarPalettes = [
  ['#2a2140', '#15151b'],
  ['#22242a', '#15151b'],
  ['#2a2220', '#15151b'],
  ['#1e2a2e', '#15151b'],
  ['#241e2e', '#15151b'],
]
function avatarStyle(id: number) {
  const [c1, c2] = avatarPalettes[id % avatarPalettes.length]
  return { background: `linear-gradient(135deg, ${c1}, ${c2})` }
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

function displayName(a: Artist) {
  return a.chinese_name || a.stage_name || a.name || `#${a.id}`
}
function displaySub(a: Artist) {
  const parts: string[] = []
  if (a.stage_name && a.stage_name !== displayName(a)) parts.push(a.stage_name)
  if (a.debut_date) {
    const d = formatDate(a.debut_date)
    if (d !== '--') parts.push(`${d} 出道`)
  }
  return parts.join(' · ')
}
function avatarSrc(a: Artist) {
  // w=256 + 去掉 ?t= 时间戳：理由同 GroupsListView.avatarSrc（原图直出 + 缓存击穿）
  return a.avatar_path ? `${artistsApi.avatarUrl(a.id)}?w=256` : ''
}

async function load(targetPage: number) {
  if (loading.value) return
  loading.value = true
  try {
    const res = await artistsApi.list({
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

function goArtist(a: { uid: string }) {
  router.push(artistPath(a.uid))
}

onMounted(() => load(1))
</script>

<template>
  <div class="mv-page">
    <SaHeader>
    </SaHeader>
    <div class="mv-container">
      <div class="page-head">
        <div class="page-title-wrap">
          <h1 class="page-title">艺人</h1>
          <span class="page-count">{{ total }}</span>
        </div>
        <div class="head-actions">
          <button
            class="filter-toggle"
            :class="{ active: onlyWithVideos }"
            type="button"
            title="隐藏未关联任何视频的艺人（如 AI 分析组合时自动生成的成员）"
            @click="toggleOnlyWithVideos"
          >
            {{ onlyWithVideos ? '仅有关联视频' : '显示全部艺人' }}
          </button>
          <input
            v-model="searchInput"
            class="search-input"
            type="text"
            placeholder="搜索艺人名称…"
          />
        </div>
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="card-grid">
          <button
            v-for="a in items"
            :key="a.id"
            class="card"
            @click="goArtist(a)"
          >
            <div class="card-avatar" :style="avatarStyle(a.id)">
              <img
                v-if="avatarSrc(a)"
                class="card-avatar-img"
                :src="avatarSrc(a)"
                alt=""
              />
              <template v-else>{{ initialOf(displayName(a)) }}</template>
            </div>
            <div class="card-info">
              <div class="card-name">{{ displayName(a) }}</div>
              <div class="card-meta">{{ displaySub(a) || '艺人' }}</div>
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
        <PersonOutlined :size="40" />
        <p>没有找到艺人</p>
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
.card-avatar {
  width: 100%;
  aspect-ratio: 1;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 40px;
  font-weight: 700;
  color: #fff;
  overflow: hidden;
}
.card-avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
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

@media (max-width: 640px) {
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
  .card-avatar {
    font-size: 28px;
  }
  .card-name {
    font-size: 14px;
  }
  .card-meta {
    font-size: 11px;
  }
}
</style>
