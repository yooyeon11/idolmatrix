<script setup lang="ts">
// 资料库「组合列表」面板：从 DbListView 抽出的可复用组件。
// DbListView（/db/list 整页）与 设置页·资料库分区 内嵌都用它，行为一致。
// 跳转不在这里做 —— emit('open'/'created') 交给宿主决定（整页 = 路由跳转，内嵌 = 宿主自定）。
import { onMounted, ref } from 'vue'
import { RefreshOutlined } from '@/components/icons'
import SaProgressCircle from '@/components/SaProgressCircle.vue'
import SaIssueBadge from '@/components/SaIssueBadge.vue'
import DbListToolbar from '@/components/db/DbListToolbar.vue'
import { dbViewsApi, type DbGroupRow, type GroupListResponse } from '@/api/dbViews'
import { groupsApi } from '@/api/groups'
import { useDbListQuery, DB_SORTS_WITH_DATE } from '@/composables/useDbListQuery'

withDefaults(
  defineProps<{
    /** 是否在面板内渲染「新建组合 / 刷新」按钮（整页模式的按钮在 DbPageShell 头部，传 false） */
    showActions?: boolean
  }>(),
  { showActions: true },
)

const emit = defineEmits<{
  (e: 'open', uid: string): void
  (e: 'created', uid: string): void
  /** 每次加载完成（成功或失败）回调，宿主可用来更新页头计数 */
  (e: 'loaded', res: GroupListResponse | null): void
}>()

const data = ref<GroupListResponse | null>(null)
const loading = ref(false)
const loadError = ref('')
const creating = ref(false)
const createOpen = ref(false)
const createName = ref('')
const createError = ref('')

const SORTS = DB_SORTS_WITH_DATE
const { search, sort, rows } = useDbListQuery({
  rows: () => data.value?.items ?? [],
  searchKeys: (r) => [r.name, r.chinese_name, r.korean_name, ...r.companies.map((c) => c.name)],
  nameKey: (r) => r.name,
  dateKey: (r) => r.debut_date ?? null,
})

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    data.value = await dbViewsApi.getGroupRows()
    emit('loaded', data.value)
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载列表失败'
    emit('loaded', null)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createOpen.value = true
}

async function createGroup() {
  const name = createName.value.trim()
  if (!name) {
    createError.value = '请输入组合名称'
    return
  }
  creating.value = true
  createError.value = ''
  try {
    const g = await groupsApi.create({ name })
    createOpen.value = false
    createName.value = ''
    await load()
    emit('created', g.uid)
  } catch (e) {
    createError.value = e instanceof Error ? e.message : '创建失败'
  } finally {
    creating.value = false
  }
}

onMounted(load)

defineExpose({ load, openCreate })

function completenessColor(score: number) {
  if (score >= 80) return 'var(--dh-ok)'
  if (score >= 50) return 'var(--dh-warn)'
  return 'var(--dh-bad)'
}

function issueInfo(row: DbGroupRow) {
  const { error, warning } = row.issues
  const count = error + warning
  if (error > 0)
    return { count, color: 'var(--dh-bad)', title: `⚠ ${count} 问题（${error} 错误 / ${warning} 待补）` }
  if (warning > 0) return { count, color: 'var(--dh-warn)', title: `⚠ ${warning} 待补` }
  return { count: 0, color: 'var(--dh-ok)', title: '✓ 健康' }
}

function worksText(row: DbGroupRow) {
  const w = row.works
  return `${w.albums} 专辑 · ${w.songs} 歌曲 · ${w.videos} 影像`
}

/** 移动端单行：短数字，省宽度 */
function worksCompact(row: DbGroupRow) {
  const w = row.works
  return `${w.albums}专 · ${w.songs}歌 · ${w.videos}影`
}

function avatarUrl(row: DbGroupRow) {
  // w=256：列表缩略图用缩放变体，不直出 audiodb 高清原图（理由见 GroupsListView）
  return row.avatar_path ? `/api/groups/${row.id}/avatar?w=256` : ''
}

function initials(name: string) {
  return name.trim().slice(0, 2).toUpperCase()
}
</script>

<template>
  <div class="dgl">
    <div v-if="createOpen" class="create-box">
      <input v-model="createName" class="create-input" type="text" placeholder="组合名称，如 izan" @keyup.enter="createGroup" />
      <button class="create-btn solid" type="button" :disabled="creating" @click="createGroup">{{ creating ? '创建中…' : '创建并打开' }}</button>
      <button class="reload-btn" type="button" @click="createOpen = false">取消</button>
      <div v-if="createError" class="create-err">{{ createError }}</div>
    </div>

    <div v-if="loadError" class="load-error">{{ loadError }}</div>

    <DbListToolbar
      v-model:search="search"
      v-model:sort="sort"
      placeholder="搜索组合名 / 中文名 / 公司名称…"
      :sorts="SORTS"
    >
      <!-- 新建 / 刷新：插在搜索框与排序之间 -->
      <template #actions>
        <div v-if="showActions" class="dgl__actions">
          <button class="create-btn" type="button" @click="createOpen = true">新建组合</button>
          <button class="reload-btn" :disabled="loading" @click="load">
            <RefreshOutlined :size="15" />
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </template>
    </DbListToolbar>

    <div v-if="data" class="card list-card">
      <div class="tbl-bar">
        <span class="hint">点击行打开工作台</span>
        <span class="sort-hint">共 {{ rows.length }} 个</span>
      </div>

      <div v-for="row in rows" :key="row.uid" class="group-block">
        <div class="group-row" @click="emit('open', row.uid)">
          <div class="cell cell-group">
            <div class="avatar">
              <img v-if="row.avatar_path" :src="avatarUrl(row)" alt="" />
              <template v-else>{{ initials(row.name) }}</template>
            </div>
            <div class="g-info">
              <div class="g-name">
                <button class="open-ws" type="button" @click.stop="emit('open', row.uid)">{{ row.name }}</button>
                <span v-if="row.is_subunit" class="mini-tag">小分队</span>
              </div>
              <div class="g-sub">
                {{ row.debut_date ? `${row.debut_date} 出道` : '缺出道日期' }}
                <template v-if="row.korean_name"> · {{ row.korean_name }}</template>
              </div>
            </div>
          </div>

          <div class="cell cell-works">{{ worksText(row) }}</div>

          <!-- 移动端两行网格：
               ① 头像 · 组合名 · 出道日期 ｜ 右：完整度环
               ② （头像跨两行）专/歌/影 ｜ 右：问题徽标 -->
          <div class="cell cell-mobile-line">
            <div class="avatar m-avatar">
              <img v-if="row.avatar_path" :src="avatarUrl(row)" alt="" />
              <template v-else>{{ initials(row.name) }}</template>
            </div>
            <div class="m-line m-line--main">
              <button class="open-ws m-name" type="button" @click.stop="emit('open', row.uid)">{{ row.name }}</button>
              <span v-if="row.is_subunit" class="mini-tag">小分队</span>
              <span class="m-sep">·</span>
              <span class="m-debut">{{ row.debut_date || '缺日期' }}</span>
            </div>
            <SaProgressCircle
              class="m-score"
              :size="24"
              :value="row.completeness"
              :color="completenessColor(row.completeness)"
              :label="`完整度 ${row.completeness}`"
            />
            <div class="m-line m-line--sub">
              <span class="m-works">{{ worksCompact(row) }}</span>
            </div>
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

      <div v-if="!rows.length" class="empty">还没有组合数据</div>
    </div>

    <div v-else-if="loading" class="list-loading">加载中…</div>
  </div>
</template>

<style scoped>
.dgl {
  /* 健康分档色：面板自带，内嵌到任何页面都不依赖宿主提供 */
  --dh-ok: #18a058;
  --dh-warn: #d97706;
  --dh-bad: #d03050;
}
.dgl__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
/* 工具栏内与搜索框/排序等高（36px） */
.dgl__actions .create-btn,
.dgl__actions .reload-btn {
  height: 36px;
  padding: 0 14px;
  white-space: nowrap;
}
.reload-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border: 1px solid var(--sa-border);
  border-radius: 10px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.reload-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.load-error {
  padding: 12px 16px;
  border-radius: 10px;
  background: rgba(208, 48, 80, 0.1);
  color: var(--dh-bad);
  font-size: 13px;
  margin-bottom: 16px;
}
.card {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
  overflow: hidden;
}
.list-card {
  margin-bottom: 24px;
}
.tbl-bar {
  display: flex;
  justify-content: space-between;
  gap: 6px 12px;
  flex-wrap: wrap;
  padding: 13px 20px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.tbl-bar .hint {
  min-width: 0;
}
.hint {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.sort-hint {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}

.group-block + .group-block {
  border-top: 1px solid var(--sa-border-subtle);
}
.group-row {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(140px, 1.05fr) auto;
  align-items: center;
  gap: 12px;
  padding: 13px 20px;
  cursor: pointer;
  transition: background 0.12s;
}
.group-row:hover {
  background: var(--sa-accent-subtle);
}

.cell-group {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
}
.avatar {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  flex-shrink: 0;
  background: var(--sa-accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  overflow: hidden;
}
.avatar img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.g-info {
  min-width: 0;
}
.g-name {
  font-size: 13.5px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mini-tag {
  font-size: 10px;
  font-weight: 700;
  color: var(--sa-accent);
  background: var(--sa-accent-subtle);
  border-radius: 5px;
  padding: 1px 6px;
  vertical-align: 1px;
  margin-left: 4px;
}
.g-sub {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
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

.empty,
.list-loading {
  padding: 48px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}

@media (max-width: 900px) {
  .tbl-bar {
    padding: 10px 14px;
  }
  .tbl-bar .hint {
    display: none;
  }
  .group-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px;
  }
  .cell-group,
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
    border-radius: 9px;
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
  .m-works {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    font-variant-numeric: tabular-nums;
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
  .create-box {
    padding: 10px;
  }
  .create-input {
    min-width: 0;
    width: 100%;
    flex: 1 1 100%;
  }
  .list-card {
    border-radius: 14px;
    margin-bottom: 16px;
  }
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
</style>
