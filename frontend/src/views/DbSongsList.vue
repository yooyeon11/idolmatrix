<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { MusicNoteOutlined, RefreshOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'
import DbListToolbar from '@/components/db/DbListToolbar.vue'
import SaProgressCircle from '@/components/SaProgressCircle.vue'
import SaIssueBadge from '@/components/SaIssueBadge.vue'
import { dbViewsApi, type DbSongRow } from '@/api/dbViews'
import { useDbListQuery, DB_SORTS_BASE } from '@/composables/useDbListQuery'
import { formatDuration } from '@/utils/format'

const router = useRouter()

// 内嵌进设置页·资料库时为 true：去掉整屏外壳，行点击改为抛出目标路径，由宿主内嵌打开
const props = defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{
  (e: 'open', path: string): void
}>()
const data = ref<{ items: DbSongRow[]; total: number } | null>(null)
const loading = ref(false)
const loadError = ref('')

const SORTS = DB_SORTS_BASE
const { search, sort, showNoVideo, hiddenNoVideo, rows } = useDbListQuery({
  rows: () => data.value?.items ?? [],
  searchKeys: (r) => [
    r.name,
    r.chinese_name,
    r.korean_name,
    ...r.performers.map((p) => p.name),
  ],
  nameKey: (r) => r.name,
  dateKey: (r) => r.release_date ?? null,
  videoKey: (r) => r.works.videos,
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await dbViewsApi.getSongRows()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载列表失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function openSong(row: DbSongRow) {
  if (props.embedded) emit('open', `/db/songs/${row.uid}`)
  else void router.push(`/db/songs/${row.uid}`)
}

function completenessColor(score: number) {
  if (score >= 80) return 'var(--dh-ok)'
  if (score >= 50) return 'var(--dh-warn)'
  return 'var(--dh-bad)'
}

function issueInfo(row: DbSongRow) {
  const { error, warning } = row.issues
  const count = error + warning
  if (error > 0)
    return { count, color: 'var(--dh-bad)', title: `⚠ ${count} 问题（${error} 错误 / ${warning} 待补）` }
  if (warning > 0) return { count, color: 'var(--dh-warn)', title: `⚠ ${warning} 待补` }
  return { count: 0, color: 'var(--dh-ok)', title: '✓ 健康' }
}

function worksText(row: DbSongRow) {
  const w = row.works
  return `${w.albums} 专辑 · ${w.videos} 影像`
}

function worksCompact(row: DbSongRow) {
  const w = row.works
  return `${w.albums}专 · ${w.videos}影`
}

function subName(row: DbSongRow) {
  return row.korean_name || row.chinese_name || ''
}

function releaseText(row: DbSongRow) {
  const parts = [row.release_date]
  if (row.duration != null) parts.push(formatDuration(row.duration))
  return parts.filter(Boolean).join(' · ')
}

function performersCompact(row: DbSongRow) {
  if (!row.performers.length) return '缺演唱'
  return row.performers
    .map((p) => (p.role === 'FeaturedArtist' ? `${p.name}(feat)` : p.name))
    .join('/')
}

function releaseCompact(row: DbSongRow) {
  const parts: string[] = []
  if (row.release_date) parts.push(row.release_date)
  if (row.duration != null) parts.push(formatDuration(row.duration))
  return parts.length ? parts.join(' ') : '缺日期/时长'
}
</script>

<template>
  <div class="mv-page">
    <DbPageShell title="资料库 · 歌曲" :count="data ? `${data.total} 首歌曲` : ''" :embedded="embedded">
      <template #actions>
        <button class="reload-btn" :disabled="loading" @click="load">
          <RefreshOutlined :size="15" />
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </template>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>

      <DbListToolbar
        v-model:search="search"
        v-model:sort="sort"
        v-model:show-no-video="showNoVideo"
        :hidden-no-video="hiddenNoVideo"
        video-toggle
        placeholder="搜索歌名 / 中文名 / 演唱者…"
        :sorts="SORTS"
      />

      <div v-if="data" class="card list-card">
        <div class="tbl-bar">
          <span class="hint">一行看清歌曲的演唱者、专辑/影像关联与完整度 · 点击行打开歌曲工作台</span>
          <span class="sort-hint">共 {{ rows.length }} 首</span>
        </div>

        <div v-for="row in rows" :key="row.uid" class="song-block">
          <div class="song-row" @click="openSong(row)">
            <div class="cell cell-entity">
              <div class="avatar song-icon">
                <MusicNoteOutlined :size="17" />
              </div>
              <div class="entity-info">
                <div class="entity-name">
                  <button class="open-ws" type="button" @click.stop="openSong(row)">{{ row.name }}</button>
                </div>
                <div class="entity-sub">{{ subName(row) || '缺歌曲别名' }}</div>
              </div>
            </div>

            <div class="cell cell-performers">
              <template v-if="row.performers.length">
                <span
                  v-for="(p, i) in row.performers"
                  :key="`${p.subject_type}-${p.subject_id}-${i}`"
                  class="tag tag-gold"
                >
                  {{ p.name }}<template v-if="p.role === 'FeaturedArtist'">（feat）</template>
                </span>
              </template>
              <span v-else class="missing-text">缺演唱者</span>
            </div>

            <div class="cell cell-release">
              <template v-if="releaseText(row)">{{ releaseText(row) }}</template>
              <span v-else class="missing-text">缺日期 / 时长</span>
            </div>

            <div class="cell cell-works">{{ worksText(row) }}</div>

            <div class="cell cell-mobile-line">
              <div class="avatar song-icon m-avatar">
                <MusicNoteOutlined :size="14" />
              </div>
              <div class="m-text">
                <button class="open-ws m-name" type="button" @click.stop="openSong(row)">{{ row.name }}</button>
                <span class="m-sep">·</span>
                <span class="m-perf">{{ performersCompact(row) }}</span>
                <span class="m-sep">·</span>
                <span class="m-release">{{ releaseCompact(row) }}</span>
                <span class="m-sep">·</span>
                <span class="m-works">{{ worksCompact(row) }}</span>
              </div>
              <SaProgressCircle
                class="m-score"
                :size="28"
                :value="row.completeness"
                :color="completenessColor(row.completeness)"
                :label="`完整度 ${row.completeness}`"
              />
              <SaIssueBadge
                class="m-badge"
                :count="issueInfo(row).count"
                :color="issueInfo(row).color"
                :label="issueInfo(row).title"
              />
            </div>

            <div class="cell cell-meta">
              <div class="cell-score">
                <SaProgressCircle
                  :value="row.completeness"
                  :color="completenessColor(row.completeness)"
                  :label="`完整度 ${row.completeness}`"
                />
              </div>
              <div class="cell-status">
                <SaIssueBadge
                  :count="issueInfo(row).count"
                  :color="issueInfo(row).color"
                  :label="issueInfo(row).title"
                />
              </div>
            </div>
          </div>
        </div>

        <div v-if="!rows.length" class="empty">还没有歌曲数据</div>
      </div>

      <div v-else-if="loading" class="list-loading">加载中…</div>
    </DbPageShell>
  </div>
</template>

<style scoped src="./db-list-shared.css"></style>

<style scoped>
.mv-page {
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}

.song-block + .song-block {
  border-top: 1px solid var(--sa-border-subtle);
}

.song-row {
  display: grid;
  grid-template-columns:
    minmax(170px, 1.3fr)
    minmax(150px, 1.05fr)
    minmax(120px, 0.9fr)
    minmax(110px, 0.8fr)
    auto;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
  cursor: pointer;
  transition: background 0.12s;
}
.song-row:hover {
  background: var(--sa-accent-subtle);
}

.cell-entity {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
}
.song-icon {
  background: var(--sa-subtle);
  color: var(--sa-accent);
}
.entity-info {
  min-width: 0;
}
.entity-name {
  font-size: 13.5px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.entity-sub {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cell-performers {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}
.cell-release,
.cell-works {
  font-size: 12.5px;
  color: var(--sa-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.cell-meta {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-shrink: 0;
}
.cell-score {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cell-mobile-line {
  display: none;
}

.open-ws {
  padding: 0;
  border: none;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: inherit;
  cursor: pointer;
}
.open-ws:hover {
  color: var(--sa-accent);
}

@media (max-width: 900px) {
  .tbl-bar {
    padding: 10px 14px;
  }
  .tbl-bar .hint {
    display: none;
  }
  .song-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
  }
  .cell-entity,
  .cell-performers,
  .cell-release,
  .cell-works,
  .cell-meta {
    display: none;
  }
  .cell-mobile-line {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    flex: 1;
  }
  .m-avatar {
    width: 32px;
    height: 32px;
    border-radius: 9px;
  }
  .m-text {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: 12px;
    color: var(--sa-text-secondary);
    white-space: nowrap;
    overflow: hidden;
  }
  .m-name {
    font-size: 13px;
    font-weight: 700;
    color: var(--sa-text-primary);
    max-width: 26%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .m-sep {
    color: var(--sa-text-tertiary);
    flex-shrink: 0;
  }
  .m-perf,
  .m-release {
    flex-shrink: 0;
    max-width: 22%;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--sa-text-tertiary);
    font-size: 11.5px;
  }
  .m-works {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    font-variant-numeric: tabular-nums;
  }
  .m-score,
  .m-badge {
    flex-shrink: 0;
  }
  .list-card {
    border-radius: 14px;
    margin-bottom: 16px;
  }
}
</style>
