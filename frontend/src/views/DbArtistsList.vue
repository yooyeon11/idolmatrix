<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { RefreshOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'
import DbListToolbar from '@/components/db/DbListToolbar.vue'
import SaProgressCircle from '@/components/SaProgressCircle.vue'
import SaIssueBadge from '@/components/SaIssueBadge.vue'
import { dbViewsApi, type DbArtistRow } from '@/api/dbViews'
import { artistsApi } from '@/api/artists'
import { useDbListQuery, DB_SORTS_BASE } from '@/composables/useDbListQuery'

const router = useRouter()

// 内嵌进设置页·资料库时为 true：去掉整屏外壳，行点击 / 新建改为抛出目标路径，由宿主内嵌打开
const props = defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{
  (e: 'open', path: string): void
}>()
const data = ref<{ items: DbArtistRow[]; total: number } | null>(null)
const loading = ref(false)
const loadError = ref('')

const creating = ref(false)
const createOpen = ref(false)
const createName = ref('')
const createError = ref('')

async function createArtist() {
  const name = createName.value.trim()
  if (!name) {
    createError.value = '请输入艺人名称'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const a = await artistsApi.create({ name })
    createOpen.value = false
    createName.value = ''
    if (props.embedded) emit('open', `/db/artists/${a.uid}`)
    else await router.push(`/db/artists/${a.uid}`)
  } catch (e) {
    createError.value = e instanceof Error ? e.message : '创建失败'
  } finally {
    creating.value = false
  }
}

const SORTS = DB_SORTS_BASE
const { search, sort, showNoVideo, hiddenNoVideo, rows } = useDbListQuery({
  rows: () => data.value?.items ?? [],
  searchKeys: (r) => [r.name, r.stage_name, r.chinese_name, r.korean_name, r.english_name],
  nameKey: (r) => r.stage_name || r.name,
  videoKey: (r) => r.works.videos,
})

function openWorkspace(row: DbArtistRow) {
  if (props.embedded) emit('open', `/db/artists/${row.uid}`)
  else void router.push(`/db/artists/${row.uid}`)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await dbViewsApi.getArtistRows()
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

function issueInfo(row: DbArtistRow) {
  const { error, warning } = row.issues
  const count = error + warning
  if (error > 0)
    return { count, color: 'var(--dh-bad)', title: `⚠ ${count} 问题（${error} 错误 / ${warning} 待补）` }
  if (warning > 0) return { count, color: 'var(--dh-warn)', title: `⚠ ${warning} 待补` }
  return { count: 0, color: 'var(--dh-ok)', title: '✓ 健康' }
}

function worksText(row: DbArtistRow) {
  const w = row.works
  return `${w.albums} 专辑 · ${w.songs} 歌曲 · ${w.videos} 影像`
}

function worksCompact(row: DbArtistRow) {
  const w = row.works
  return `${w.albums}专 · ${w.songs}歌 · ${w.videos}影`
}

function avatarUrl(row: DbArtistRow) {
  // w=256：列表缩略图用缩放变体，不直出 audiodb 高清原图（理由见 GroupsListView）
  return row.avatar_path ? `/api/artists/${row.id}/avatar?w=256` : ''
}

function initials(name: string) {
  return name.trim().slice(0, 2).toUpperCase()
}

function displayName(row: DbArtistRow) {
  return row.stage_name || row.name
}

function subName(row: DbArtistRow) {
  if (row.stage_name && row.stage_name !== row.name) return row.name
  return row.korean_name || row.english_name || ''
}

function profileText(row: DbArtistRow) {
  const parts = [row.birth_date, row.debut_date]
  return parts.filter(Boolean).join(' · ')
}

/** 移动端日期摘要：优先出道，否则出生 */
function dateCompact(row: DbArtistRow) {
  return row.debut_date || row.birth_date || '缺日期'
}

function groupsCompact(row: DbArtistRow) {
  if (!row.groups.length) return 'solo'
  return row.groups
    .map((g) => (g.status === 'Former' ? `${g.name}(前)` : g.name))
    .join('/')
}
</script>

<template>
  <div class="mv-page">
    <DbPageShell title="资料库 · 艺人" :count="data ? `${data.total} 位艺人` : ''" :embedded="embedded">
      <template #actions>
        <button class="create-btn" type="button" @click="createOpen = !createOpen">新增艺人</button>
        <button class="reload-btn" :disabled="loading" @click="load">
          <RefreshOutlined :size="15" />
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </template>

      <div v-if="createOpen" class="create-box">
        <input
          v-model="createName"
          class="create-input"
          type="text"
          placeholder="艺人名称，如 IU"
          @keyup.enter="createArtist"
        />
        <button class="create-btn solid" type="button" :disabled="creating" @click="createArtist">
          {{ creating ? '创建中…' : '创建并打开' }}
        </button>
        <button class="reload-btn" type="button" @click="createOpen = false">取消</button>
        <div v-if="createError" class="create-err">{{ createError }}</div>
      </div>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>

      <DbListToolbar
        v-model:search="search"
        v-model:sort="sort"
        v-model:show-no-video="showNoVideo"
        :hidden-no-video="hiddenNoVideo"
        video-toggle
        placeholder="搜索艺名 / 本名 / 中文名 / 韩文名…"
        :sorts="SORTS"
      />

      <div v-if="data" class="card list-card">
        <div class="tbl-bar">
          <span class="hint">一行看清艺人的所属组合、档案与作品完整度 · 点击行打开工作台</span>
          <span class="sort-hint">共 {{ rows.length }} 位</span>
        </div>

        <div v-for="row in rows" :key="row.uid" class="artist-block">
          <div class="artist-row" @click="openWorkspace(row)">
            <!-- 桌面：实体 -->
            <div class="cell cell-entity">
              <div class="avatar round">
                <img v-if="row.avatar_path" :src="avatarUrl(row)" alt="" />
                <template v-else>{{ initials(row.name) }}</template>
              </div>
              <div class="entity-info">
                <div class="entity-name">
                  <button class="open-ws" type="button" @click.stop="openWorkspace(row)">
                    {{ displayName(row) }}
                  </button>
                  <span v-if="row.chinese_name" class="cn-name">{{ row.chinese_name }}</span>
                </div>
                <div class="entity-sub">{{ subName(row) }}</div>
              </div>
            </div>

            <!-- 桌面：所属组合 -->
            <div class="cell cell-groups">
              <template v-if="row.groups.length">
                <span v-for="g in row.groups" :key="g.group_id" class="tag tag-gold">
                  {{ g.name }}<template v-if="g.status === 'Former'">（前）</template>
                </span>
              </template>
              <span v-else class="muted-text">solo / 未挂组合</span>
            </div>

            <!-- 桌面：出生 / 出道 -->
            <div class="cell cell-profile">
              <template v-if="profileText(row)">{{ profileText(row) }}</template>
              <span v-else class="missing-text">缺出生 / 出道日期</span>
            </div>

            <!-- 桌面：作品 -->
            <div class="cell cell-works">{{ worksText(row) }}</div>

            <!-- 移动端两行网格：
                 ① 头像 · 艺名 · 出道日期 ｜ 右：完整度环
                 ② （头像跨两行）所属组合 · 专/歌/影 ｜ 右：问题徽标 -->
            <div class="cell cell-mobile-line">
              <div class="avatar round m-avatar">
                <img v-if="row.avatar_path" :src="avatarUrl(row)" alt="" />
                <template v-else>{{ initials(row.name) }}</template>
              </div>
              <div class="m-line m-line--main">
                <button class="open-ws m-name" type="button" @click.stop="openWorkspace(row)">
                  {{ displayName(row) }}
                </button>
                <span v-if="row.chinese_name" class="cn-name">{{ row.chinese_name }}</span>
                <span class="m-sep">·</span>
                <span class="m-debut">{{ dateCompact(row) }}</span>
              </div>
              <SaProgressCircle
                class="m-score"
                :size="24"
                :value="row.completeness"
                :color="completenessColor(row.completeness)"
                :label="`完整度 ${row.completeness}`"
              />
              <div class="m-line m-line--sub">
                <span class="m-groups">{{ groupsCompact(row) }}</span>
                <span class="m-sep">·</span>
                <span class="m-works">{{ worksCompact(row) }}</span>
              </div>
              <SaIssueBadge
                class="m-badge"
                :count="issueInfo(row).count"
                :color="issueInfo(row).color"
                :label="issueInfo(row).title"
              />
            </div>

            <!-- 桌面：完整度 + 三角 -->
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

        <div v-if="!rows.length" class="empty">还没有艺人数据</div>
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

.artist-block + .artist-block {
  border-top: 1px solid var(--sa-border-subtle);
}

.artist-row {
  display: grid;
  grid-template-columns:
    minmax(180px, 1.3fr)
    minmax(140px, 1.05fr)
    minmax(130px, 0.95fr)
    minmax(120px, 0.85fr)
    auto;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
  cursor: pointer;
  transition: background 0.12s;
}
.artist-row:hover {
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
.cn-name {
  font-size: 11px;
  font-weight: 600;
  color: var(--sa-text-tertiary);
  margin-left: 3px;
}

.cell-groups {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}
.cell-profile,
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
  text-decoration: none;
  font: inherit;
  font-weight: inherit;
  cursor: pointer;
}
.open-ws:hover {
  color: var(--sa-accent);
}

.create-btn {
  border: 1px solid var(--sa-accent-border);
  background: var(--sa-accent-subtle);
  color: var(--sa-accent);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 700;
  padding: 7px 14px;
  cursor: pointer;
  font-family: inherit;
}
.create-btn.solid {
  background: var(--sa-accent);
  border-color: var(--sa-accent);
  color: #fff;
}
.create-box {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-bottom: 12px;
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
}
.create-input {
  flex: 1;
  min-width: 180px;
  border: 1px solid var(--sa-border);
  border-radius: 9px;
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  padding: 8px 11px;
  font-size: 13px;
  font-family: inherit;
}
.create-err {
  width: 100%;
  font-size: 12px;
  color: #d03050;
}

@media (max-width: 900px) {
  .tbl-bar {
    padding: 10px 14px;
  }
  .tbl-bar .hint {
    display: none;
  }
  .artist-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
  }
  .cell-entity,
  .cell-groups,
  .cell-profile,
  .cell-works,
  .cell-meta {
    display: none;
  }
  /* 两行网格：col1 头像（跨两行）· col2 文字 · col3 状态 */
  .cell-mobile-line {
    display: grid;
    grid-template-columns: 32px minmax(0, 1fr) auto;
    grid-template-rows: auto auto;
    align-items: center;
    column-gap: 9px;
    row-gap: 4px;
    flex: 1;
    min-width: 0;
  }
  .m-avatar {
    grid-column: 1;
    grid-row: 1 / span 2;
    width: 32px;
    height: 32px;
    border-radius: 50%;
    font-size: 11px;
  }
  .m-line {
    display: flex;
    align-items: center;
    gap: 4px;
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
  }
  .m-line--main {
    grid-column: 2;
    grid-row: 1;
    font-size: 12px;
    color: var(--sa-text-secondary);
  }
  .m-line--sub {
    grid-column: 2;
    grid-row: 2;
    font-size: 11.5px;
    color: var(--sa-text-tertiary);
  }
  .m-name {
    font-size: 13px;
    font-weight: 700;
    color: var(--sa-text-primary);
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .m-sep {
    color: var(--sa-text-tertiary);
    flex-shrink: 0;
  }
  .m-debut {
    flex-shrink: 0;
    color: var(--sa-text-tertiary);
    font-size: 11.5px;
  }
  /* 第二行：组合名可截断，作品数字优先完整 */
  .m-groups {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .m-works {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  /* 环与徽标共用右列，居中对齐成一条竖轴 */
  .m-score {
    grid-column: 3;
    grid-row: 1;
    justify-self: center;
  }
  .m-badge {
    grid-column: 3;
    grid-row: 2;
    justify-self: center;
  }
  /* 徽标缩到与第二行文字同高，避免撑高行 */
  .m-badge :deep(svg) {
    width: 17px;
    height: 16px;
  }
  .list-card {
    border-radius: 14px;
    margin-bottom: 16px;
  }
  .create-input {
    min-width: 0;
    width: 100%;
    flex: 1 1 100%;
  }
}
</style>
