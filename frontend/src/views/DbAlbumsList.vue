<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'
import DbListToolbar from '@/components/db/DbListToolbar.vue'
import SaProgressCircle from '@/components/SaProgressCircle.vue'
import SaIssueBadge from '@/components/SaIssueBadge.vue'
import { dbViewsApi, type DbAlbumRow } from '@/api/dbViews'
import { useDbListQuery, DB_SORTS_WITH_DATE } from '@/composables/useDbListQuery'

const router = useRouter()

// 内嵌进设置页·资料库时为 true：去掉整屏外壳，跳转改为抛出目标路径，由宿主内嵌打开
const props = defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{
  (e: 'open', path: string): void
}>()
const data = ref<{ items: DbAlbumRow[]; total: number } | null>(null)
const loading = ref(false)
const loadError = ref('')

const SORTS = DB_SORTS_WITH_DATE
const { search, sort, showNoVideo, hiddenNoVideo, rows } = useDbListQuery({
  rows: () => data.value?.items ?? [],
  searchKeys: (r) => [r.name, r.chinese_name, r.korean_name, r.release_artist_name],
  nameKey: (r) => r.name,
  dateKey: (r) => r.release_date ?? null,
  videoKey: (r) => r.works.videos,
})

function goOrEmit(path: string) {
  if (props.embedded) emit('open', path)
  else void router.push(path)
}

function openWorkspace(row: DbAlbumRow) {
  goOrEmit(`/db/albums/${row.uid}`)
}

function openSubject(row: DbAlbumRow) {
  if (row.release_artist_type === 'group' && row.release_artist_uid) {
    goOrEmit(`/db/groups/${row.release_artist_uid}`)
  } else if (row.release_artist_type === 'artist' && row.release_artist_uid) {
    goOrEmit(`/db/artists/${row.release_artist_uid}`)
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await dbViewsApi.getAlbumRows()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载列表失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

function completenessColor(score: number) {
  if (score >= 80) return 'var(--dh-ok)'
  if (score >= 50) return 'var(--dh-warn)'
  return 'var(--dh-bad)'
}

function issueInfo(row: DbAlbumRow) {
  const { error, warning } = row.issues
  const count = error + warning
  if (error > 0)
    return { count, color: 'var(--dh-bad)', title: `⚠ ${count} 问题（${error} 错误 / ${warning} 待补）` }
  if (warning > 0) return { count, color: 'var(--dh-warn)', title: `⚠ ${warning} 待补` }
  return { count: 0, color: 'var(--dh-ok)', title: '✓ 健康' }
}

function worksText(row: DbAlbumRow) {
  const w = row.works
  return `${w.tracks} 曲目 · ${w.videos} 影像`
}

function worksCompact(row: DbAlbumRow) {
  const w = row.works
  return `${w.tracks}曲 · ${w.videos}影`
}

function coverUrl(row: DbAlbumRow) {
  return row.cover_path ? `/api/albums/${row.id}/cover` : ''
}

function initials(name: string) {
  return name.trim().slice(0, 2).toUpperCase()
}

function subName(row: DbAlbumRow) {
  return row.korean_name || row.chinese_name || ''
}

function releaseText(row: DbAlbumRow) {
  return row.release_date || ''
}

function subjectCompact(row: DbAlbumRow) {
  if (!row.release_artist_name) return '无主体'
  const kind = row.release_artist_type === 'artist' ? 'solo' : '组合'
  return `${kind}·${row.release_artist_name}`
}

function releaseCompact(row: DbAlbumRow) {
  const parts = [row.album_type, row.release_date].filter(Boolean)
  return parts.length ? parts.join(' ') : '缺类型/日期'
}
</script>

<template>
  <div class="mv-page">
    <DbPageShell title="资料库 · 专辑" :count="data ? `${data.total} 张专辑` : ''" :embedded="embedded">
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
        placeholder="搜索专辑名 / 中文名 / 发行主体…"
        :sorts="SORTS"
      />

      <div v-if="data" class="card list-card">
        <div class="tbl-bar">
          <span class="hint">一行看清专辑的发行主体、曲目与封面完整度 · 点击行打开专辑工作台</span>
          <span class="sort-hint">共 {{ rows.length }} 张</span>
        </div>

        <div v-for="row in rows" :key="row.uid" class="album-block">
          <div class="album-row" @click="openWorkspace(row)">
            <div class="cell cell-entity">
              <div class="avatar">
                <img v-if="row.cover_path" :src="coverUrl(row)" alt="" />
                <template v-else>{{ initials(row.name) }}</template>
              </div>
              <div class="entity-info">
                <div class="entity-name">
                  <button class="open-ws" type="button" @click.stop="openWorkspace(row)">{{ row.name }}</button>
                </div>
                <div class="entity-sub">{{ subName(row) || '缺专辑别名' }}</div>
              </div>
            </div>

            <div class="cell cell-subject">
              <template v-if="row.release_artist_name">
                <button
                  class="tag tag-gold tag-btn"
                  type="button"
                  title="打开发行主体工作台"
                  @click.stop="openSubject(row)"
                >
                  {{ row.release_artist_type === 'artist' ? 'solo' : '组合' }} · {{ row.release_artist_name }}
                </button>
              </template>
              <span v-else class="missing-text">未挂发行主体</span>
            </div>

            <div class="cell cell-release">
              <template v-if="row.album_type || releaseText(row)">
                <span v-if="row.album_type" class="tag tag-plain">{{ row.album_type }}</span>
                <span v-if="releaseText(row)" class="release-date">{{ releaseText(row) }}</span>
                <span v-else class="missing-text">缺日期</span>
              </template>
              <span v-else class="missing-text">缺类型 / 日期</span>
            </div>

            <div class="cell cell-works">{{ worksText(row) }}</div>

            <div class="cell cell-mobile-line">
              <div class="avatar m-avatar">
                <img v-if="row.cover_path" :src="coverUrl(row)" alt="" />
                <template v-else>{{ initials(row.name) }}</template>
              </div>
              <div class="m-text">
                <button class="open-ws m-name" type="button" @click.stop="openWorkspace(row)">{{ row.name }}</button>
                <span class="m-sep">·</span>
                <button
                  v-if="row.release_artist_name"
                  class="m-subject"
                  type="button"
                  @click.stop="openSubject(row)"
                >
                  {{ subjectCompact(row) }}
                </button>
                <span v-else class="m-subject muted">无主体</span>
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

        <div v-if="!rows.length" class="empty">还没有专辑数据</div>
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

.album-block + .album-block {
  border-top: 1px solid var(--sa-border-subtle);
}

.album-row {
  display: grid;
  grid-template-columns:
    minmax(180px, 1.35fr)
    minmax(150px, 1.05fr)
    minmax(130px, 0.95fr)
    minmax(110px, 0.8fr)
    auto;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
  cursor: pointer;
  transition: background 0.12s;
}
.album-row:hover {
  background: var(--sa-accent-subtle);
}

.cell-entity {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
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

.cell-subject {
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cell-release {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  white-space: nowrap;
}
.release-date,
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

.tag-btn {
  border: none;
  font-family: inherit;
  cursor: pointer;
}
.tag-btn:hover {
  filter: brightness(1.15);
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
  .album-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
  }
  .cell-entity,
  .cell-subject,
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
    font-size: 11px;
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
  .m-subject,
  .m-release {
    flex-shrink: 0;
    max-width: 22%;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--sa-text-tertiary);
    font-size: 11.5px;
    border: none;
    background: none;
    padding: 0;
    font: inherit;
    cursor: pointer;
  }
  .m-subject.muted {
    cursor: default;
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
