<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import SaGlassSelect from '@/components/SaGlassSelect.vue'
import SaFilterBar from '@/components/SaFilterBar.vue'
import SaFilterSearch from '@/components/SaFilterSearch.vue'
import type { GlassOption } from '@/components/SaGlassSelect.vue'
import SaHeader from '@/components/SaHeader.vue'
import SaPagination from '@/components/SaPagination.vue'
import { songsApi } from '@/api/songs'
import type { Song } from '@/types/models'
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

// ===== 排序(选择存 localStorage) =====
const SONGS_SORT_KEY = 'songs:sort'
const songsSort = ref<'default' | 'videos_desc' | 'videos_asc' | 'name' | '-name'>(
  (localStorage.getItem(SONGS_SORT_KEY) as 'default' | 'videos_desc' | 'videos_asc' | 'name' | '-name') || 'default',
)

/** 排序选项（与全站 SaGlassSelect 统一） */
const sortOptions: GlassOption[] = [
  { label: '默认排序', value: 'default' },
  { label: '视频数（多→少）', value: 'videos_desc' },
  { label: '视频数（少→多）', value: 'videos_asc' },
  { label: '名称 A→Z', value: 'name' },
  { label: '名称 Z→A', value: '-name' },
]

function onSortPick(v: string | number | null) {
  songsSort.value = String(v ?? 'default') as typeof songsSort.value
  localStorage.setItem(SONGS_SORT_KEY, songsSort.value)
  void load(1)
}

function displayName(s: Song) {
  return s.chinese_name || s.name || `#${s.id}`
}
/** 胶囊徽标：优先歌手名（关系表），没有就退回歌曲类型 */
function artistOf(s: Song) {
  const name = (s.relation_names || []).filter(Boolean)[0]
  return name || s.song_type || ''
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
      sort: songsSort.value === 'default' ? undefined : songsSort.value,
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
        <SaFilterBar class="head-actions">
          <button
            class="filter-toggle"
            :class="{ 'is-on': onlyWithVideos }"
            type="button"
            title="隐藏未关联任何视频的歌曲（如批量导入尚未剪校的曲目）"
            @click="toggleOnlyWithVideos"
          >
            {{ onlyWithVideos ? '仅有关联视频' : '显示全部歌曲' }}
          </button>
          <span class="sa-fb-sep" />
          <SaGlassSelect
            :model-value="songsSort"
            :options="sortOptions"
            prefix="排序"
            align="right"
            width="12rem"
            aria-label="排序方式"
            @update:model-value="onSortPick"
          />
          <span class="sa-fb-sep" />
          <SaFilterSearch v-model="searchInput" placeholder="搜索歌曲名称…" aria-label="检索歌曲" width="176px" />
        </SaFilterBar>
      </div>

      <div v-if="loading && !items.length" class="list-loading">加载中…</div>

      <template v-else-if="items.length">
        <div class="capsule-grid">
          <button
            v-for="s in items"
            :key="s.id"
            class="capsule"
            @click="goSong(s)"
          >
            <div class="capsule-main">
              <span v-if="artistOf(s)" class="capsule-badge">{{ artistOf(s) }}</span>
              <h3 class="capsule-title">{{ displayName(s) }}</h3>
            </div>
            <div class="capsule-count">
              <b>{{ s.video_count ?? 0 }}</b>
              <span>VIDEOS</span>
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
  max-width: var(--sa-content-max, 1280px);
  margin: 0 auto;
  padding: 0 var(--sa-gutter, 36px) 64px;
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
/* 开关式选项：与下拉触发器同一规格（26px / 无边框 / 悬停浅底），
   只是语义上表示「开/关」——开启时加粗并保留浅底，不做反相胶囊。 */
.filter-toggle {
  padding: 6px 13px;
  border: none;
  border-radius: 999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.18s ease, color 0.18s ease;
}
.filter-toggle:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.filter-toggle.is-on {
  color: var(--sa-accent);
  font-weight: 600;
}

.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}

.capsule-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}
/* 方案 3：胶囊徽标矩阵 —— 左侧「歌手徽标 + 歌名」，右侧「数量 / VIDEOS」 */
.capsule {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-width: 0;
  padding: 15px 20px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  background: var(--sa-elevated);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 0.28s ease,
    border-color 0.28s ease,
    transform 0.28s var(--spring, cubic-bezier(0.16, 1, 0.3, 1)),
    box-shadow 0.28s ease;
}
.capsule:hover {
  background: var(--sa-hover);
  border-color: var(--sa-accent-border);
  transform: translateY(-3px);
  box-shadow: 0 18px 36px -18px rgba(0, 0, 0, 0.8);
}
.capsule-main {
  flex: 1;
  min-width: 0;
}
.capsule-badge {
  display: inline-block;
  max-width: 100%;
  margin-bottom: 6px;
  padding: 1px 9px;
  border: 1px solid var(--sa-accent-border);
  border-radius: 9999px;
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
  font-size: 10px;
  line-height: 1.7;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.capsule-title {
  margin: 0;
  /* 与博主页列表同一套字体：普惠体（继承 body），不再用 Playfair/思源宋体 */
  font-family: inherit;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 0.2s ease;
}
.capsule:hover .capsule-title {
  color: var(--sa-accent);
}
.capsule-count {
  flex: none;
  text-align: right;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.capsule-count b {
  display: block;
  font-size: 14px;
  font-weight: 700;
  color: var(--sa-text-primary);
  font-variant-numeric: tabular-nums;
}
.capsule-count span {
  display: block;
  margin-top: 2px;
  font-size: 9.5px;
  letter-spacing: 0.08em;
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

@media (max-width: 1080px) {
  .capsule-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
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
  .capsule-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }
  .capsule {
    padding: 13px 16px;
  }
  .capsule-title {
    font-size: 15px;
  }
}
</style>