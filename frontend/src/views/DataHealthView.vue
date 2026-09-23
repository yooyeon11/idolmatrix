<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { DeleteOutlined, RefreshOutlined, WarningAmberOutlined } from '@/components/icons'
import DbPageShell from '@/components/db/DbPageShell.vue'
import { dataHealthApi, type DataHealthIssue, type DataHealthReport, type PurgeDanglingResult } from '@/api/dataHealth'

const router = useRouter()

// 内嵌进设置页·资料库时为 true：去掉整屏外壳；问题深链改为向上抛出，由宿主内嵌打开
const props = defineProps<{ embedded?: boolean }>()
const emit = defineEmits<{ (e: 'open', path: string): void }>()

const report = ref<DataHealthReport | null>(null)
const loading = ref(false)
const loadError = ref('')
const severityFilter = ref<'all' | 'error' | 'warning' | 'hint'>('all')
const purging = ref(false)
const purgeMsg = ref('')
const ignoringKey = ref('')
const ignoreMsg = ref('')

/** 标注「无问题」：之后不再提示、不计分；若该条数据后来变了会自动重新提示 */
async function ignoreIssue(issue: DataHealthIssue) {
  if (!issue.issue_key || ignoringKey.value) return
  ignoringKey.value = issue.issue_key
  ignoreMsg.value = ''
  try {
    await dataHealthApi.ignoreIssue(issue)
    ignoreMsg.value = `已标注无问题：${issue.title}`
    await load()
  } catch (e) {
    ignoreMsg.value = e instanceof Error ? e.message : '标注失败'
  } finally {
    ignoringKey.value = ''
  }
}

async function restoreIgnore(issue: DataHealthIssue) {
  if (ignoringKey.value) return
  ignoringKey.value = issue.issue_key
  ignoreMsg.value = ''
  try {
    await dataHealthApi.restoreIssue(issue.issue_key)
    ignoreMsg.value = `已恢复提示：${issue.title}`
    await load()
  } catch (e) {
    ignoreMsg.value = e instanceof Error ? e.message : '恢复失败'
  } finally {
    ignoringKey.value = ''
  }
}

async function clearIgnores() {
  if (ignoringKey.value) return
  ignoringKey.value = '__clear__'
  ignoreMsg.value = ''
  try {
    await dataHealthApi.clearIgnores()
    ignoreMsg.value = '已恢复全部提示'
    await load()
  } catch (e) {
    ignoreMsg.value = e instanceof Error ? e.message : '恢复失败'
  } finally {
    ignoringKey.value = ''
  }
}

async function purgeDangling() {
  if (purging.value) return
  purging.value = true
  purgeMsg.value = ''
  try {
    const r: PurgeDanglingResult = await dataHealthApi.purgeDangling()
    const total =
      r.memberships_deleted +
      r.mv_subjects_cleared +
      r.subunit_parents_cleared +
      r.artist_company_relations_deleted +
      r.group_company_relations_deleted
    purgeMsg.value =
      total === 0
        ? '没有需要清理的悬空残留'
        : `已清理 ${total} 条：成员 ${r.memberships_deleted} · MV主体 ${r.mv_subjects_cleared} · 小分队上级 ${r.subunit_parents_cleared} · 公司关系 ${r.artist_company_relations_deleted + r.group_company_relations_deleted}`
    await load()
  } catch (e) {
    purgeMsg.value = e instanceof Error ? e.message : '清理失败'
  } finally {
    purging.value = false
  }
}


async function load() {
  loading.value = true
  loadError.value = ''
  try {
    report.value = await dataHealthApi.getReport()
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载体检报告失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const sevMeta: Record<string, { label: string; cls: string }> = {
  error: { label: '错误', cls: 'sev-error' },
  warning: { label: '警告', cls: 'sev-warning' },
  hint: { label: '提示', cls: 'sev-hint' },
}

const filteredIssues = computed<DataHealthIssue[]>(() => {
  const issues = report.value?.issues ?? []
  if (severityFilter.value === 'all') return issues
  return issues.filter((i) => i.severity === severityFilter.value)
})

const scoreColor = computed(() => {
  const s = report.value?.score ?? 0
  if (s >= 90) return 'var(--ok-color)'
  if (s >= 70) return 'var(--warn-color)'
  return 'var(--error-color)'
})

const ringStyle = computed(() => {
  const s = report.value?.score ?? 0
  return {
    background: `conic-gradient(${scoreColor.value} 0 ${s}%, var(--sa-subtle) ${s}% 100%)`,
  }
})

function openIssue(issue: DataHealthIssue) {
  if (!issue.deep_link) return
  if (props.embedded) emit('open', issue.deep_link)
  else router.push(issue.deep_link)
}
</script>

<template>
  <div class="mv-page">
    <DbPageShell title="资料库 · 数据体检" :count="report ? `${report.total_issues} 个问题` : ''" :embedded="embedded">
      <template #actions>
        <button class="reload-btn purge-btn" :disabled="loading || purging" @click="purgeDangling">
          <DeleteOutlined :size="15" />
          {{ purging ? '清理中…' : '清理悬空残留' }}
        </button>
        <button class="reload-btn" :disabled="loading" @click="load">
          <RefreshOutlined :size="15" />
          {{ loading ? '体检中…' : '重新体检' }}
        </button>
      </template>

      <div v-if="loadError" class="load-error">{{ loadError }}</div>
      <div v-if="purgeMsg" class="purge-msg">{{ purgeMsg }}</div>

      <template v-if="report">
        <div class="health-grid">
          <div class="card score-card">
            <div class="ring" :style="ringStyle">
              <div class="ring-inner">
                <b>{{ report.score }}</b>
                <span>健康分</span>
              </div>
            </div>
            <div class="entity-stats">
              <div class="stat"><b>{{ report.entity_stats.groups }}</b><span>组合</span></div>
              <div class="stat"><b>{{ report.entity_stats.artists }}</b><span>艺人</span></div>
              <div class="stat"><b>{{ report.entity_stats.albums }}</b><span>专辑</span></div>
              <div class="stat"><b>{{ report.entity_stats.songs }}</b><span>歌曲</span></div>
              <div class="stat"><b>{{ report.entity_stats.music_videos }}</b><span>影像</span></div>
            </div>
          </div>

          <div class="summary-col">
            <button
              v-for="sev in (['error', 'warning', 'hint'] as const)"
              :key="sev"
              class="card sev-card"
              :class="[sevMeta[sev].cls, { 'is-active': severityFilter === sev }]"
              @click="severityFilter = severityFilter === sev ? 'all' : sev"
            >
              <span class="sev-dot" />
              <div class="sev-info">
                <div class="sev-name">{{ sevMeta[sev].label }}</div>
                <div class="sev-desc">
                  {{ sev === 'error' ? '矛盾、悬空、孤立、重复，影响正确性' : sev === 'warning' ? '路径 / 日期 / 检索与浏览相关的问题' : '可以补全但不紧急' }}
                </div>
              </div>
              <b class="sev-count">{{ report.counts[sev] }}</b>
            </button>
            <div v-if="severityFilter !== 'all'" class="filter-tip">
              已筛选：{{ sevMeta[severityFilter].label }} ·
              <button class="link-btn" @click="severityFilter = 'all'">显示全部</button>
            </div>
          </div>
        </div>

        <div class="card issue-card">
          <div class="issue-head">
            <b>问题清单</b>
            <span class="issue-sub">按严重度排序 · 点击直达对应详情页 · 拿不准的可标注无问题</span>
          </div>
          <div v-if="!filteredIssues.length" class="issue-empty">
            <WarningAmberOutlined :size="28" />
            <p>{{ severityFilter === 'all' ? '没有发现问题，资料库很干净' : '该类别下没有问题' }}</p>
          </div>
          <div
            v-for="(issue, idx) in filteredIssues"
            :key="`${issue.issue_key}-${idx}`"
            class="issue-row"
            :class="{ 'no-link': !issue.deep_link }"
            role="button"
            tabindex="0"
            @click="openIssue(issue)"
            @keydown.enter="openIssue(issue)"
          >
            <span class="sev-badge" :class="sevMeta[issue.severity].cls">
              {{ sevMeta[issue.severity].label }}
            </span>
            <div class="issue-main">
              <div class="issue-title">
                <span
                  v-if="issue.reopened"
                  class="reopened-tag"
                  title="这条曾被标注无问题，但相关数据后来变了，所以重新提示"
                >
                  已重新出现
                </span>
                {{ issue.title }}
              </div>
              <div class="issue-detail">
                {{ issue.detail }}
                <template v-if="issue.suggestion"> · 建议：{{ issue.suggestion }}</template>
              </div>
            </div>
            <span v-if="issue.deep_link" class="issue-go">去处理 →</span>
            <span v-else class="issue-go muted">待新版工作台</span>
            <button
              class="ignore-btn"
              type="button"
              :disabled="!!ignoringKey"
              title="这条其实没问题，以后不再提示（数据变了会自动重新出现）"
              @click.stop="ignoreIssue(issue)"
            >
              {{ ignoringKey === issue.issue_key ? '标注中…' : '标注无问题' }}
            </button>
          </div>
        </div>

        <div v-if="report.ignored_issues.length" class="card ignored-card">
          <div class="issue-head">
            <b>已标注无问题</b>
            <span class="issue-sub">
              {{ report.ignored_count }} 条 · 不再提示，也不计入健康分
            </span>
            <button class="link-btn" type="button" :disabled="!!ignoringKey" @click="clearIgnores">
              全部恢复提示
            </button>
          </div>
          <div
            v-for="issue in report.ignored_issues"
            :key="`ig-${issue.issue_key}`"
            class="issue-row is-ignored"
          >
            <span class="sev-badge sev-hint">已忽略</span>
            <div class="issue-main">
              <div class="issue-title">{{ issue.title }}</div>
              <div class="issue-detail">{{ issue.detail }}</div>
            </div>
            <button
              class="ignore-btn"
              type="button"
              :disabled="!!ignoringKey"
              @click="restoreIgnore(issue)"
            >
              {{ ignoringKey === issue.issue_key ? '恢复中…' : '恢复提示' }}
            </button>
          </div>
        </div>

        <div v-if="ignoreMsg" class="ignore-msg">{{ ignoreMsg }}</div>

        <div class="card checks-card">
          <div class="issue-head">
            <b>检查项</b>
            <span class="issue-sub">
              共 {{ report.checks.length }} 项 · 全部只读 · 已标注无问题的条目不计入
            </span>
          </div>
          <div class="checks-grid">
            <div
              v-for="c in report.checks"
              :key="c.key"
              class="check-item"
              :class="{ 'has-issues': c.count > 0 }"
            >
              <span class="check-name">{{ c.name }}</span>
              <span class="check-count" :class="sevMeta[c.severity]?.cls">{{ c.count }}</span>
            </div>
          </div>
        </div>
      </template>

      <div v-else-if="loading" class="list-loading">体检运行中…</div>
    </DbPageShell>
  </div>
</template>

<style scoped>
.mv-page {
  min-height: 100vh;
  --ok-color: #18a058;
  --warn-color: #d97706;
  --error-color: #d03050;
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
.purge-btn {
  border-color: rgba(208, 48, 80, 0.35);
  color: var(--error-color);
}
.purge-msg {
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(24, 160, 88, 0.1);
  color: var(--ok-color);
  font-size: 13px;
  margin-bottom: 16px;
}
.load-error {
  padding: 12px 16px;
  border-radius: 10px;
  background: rgba(208, 48, 80, 0.1);
  color: var(--error-color);
  font-size: 13px;
  margin-bottom: 16px;
}

.health-grid {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 16px;
  align-items: stretch;
}
.card {
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 16px;
}
.score-card {
  padding: 22px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
}
.ring {
  width: 140px;
  height: 140px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.ring-inner {
  width: 106px;
  height: 106px;
  border-radius: 50%;
  background: var(--sa-elevated);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.ring-inner b {
  font-size: 30px;
  font-weight: 700;
}
.ring-inner span {
  font-size: 11px;
  color: var(--sa-text-tertiary);
  letter-spacing: 2px;
}
.entity-stats {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}
.stat {
  text-align: center;
  min-width: 44px;
}
.stat b {
  display: block;
  font-size: 16px;
}
.stat span {
  font-size: 10.5px;
  color: var(--sa-text-tertiary);
}

.summary-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sev-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 18px;
  cursor: pointer;
  text-align: left;
  transition: border-color 0.15s;
}
.sev-card.is-active {
  border-color: var(--sa-accent);
}
.sev-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.sev-error .sev-dot {
  background: var(--error-color);
}
.sev-warning .sev-dot {
  background: var(--warn-color);
}
.sev-hint .sev-dot {
  background: var(--sa-text-tertiary);
}
.sev-info {
  flex: 1;
  min-width: 0;
}
.sev-name {
  font-size: 14px;
  font-weight: 700;
}
.sev-desc {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
  margin-top: 2px;
}
.sev-count {
  font-size: 20px;
  font-weight: 700;
}
.sev-error .sev-count {
  color: var(--error-color);
}
.sev-warning .sev-count {
  color: var(--warn-color);
}
.filter-tip {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  padding-left: 4px;
}
.link-btn {
  border: none;
  background: none;
  color: var(--sa-accent);
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.issue-card {
  margin-top: 16px;
  overflow: hidden;
}
.issue-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 16px 20px 12px;
}
.issue-head b {
  font-size: 15px;
}
.issue-sub {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.issue-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 32px 0 36px;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}
.issue-row {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  padding: 13px 20px;
  border: none;
  border-top: 1px solid var(--sa-border-subtle);
  background: none;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
  color: var(--sa-text-primary);
  transition: background 0.12s;
}
.issue-row:hover {
  background: var(--sa-accent-subtle);
}
.issue-row.no-link {
  cursor: default;
}
.sev-badge {
  flex-shrink: 0;
  width: 48px;
  text-align: center;
  font-size: 11px;
  font-weight: 700;
  border-radius: 7px;
  padding: 4px 0;
}
.sev-badge.sev-error {
  background: rgba(208, 48, 80, 0.12);
  color: var(--error-color);
}
.sev-badge.sev-warning {
  background: rgba(217, 119, 6, 0.12);
  color: var(--warn-color);
}
.sev-badge.sev-hint {
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
}
.issue-main {
  flex: 1;
  min-width: 0;
}
.issue-title {
  font-size: 13.5px;
  font-weight: 600;
  line-height: 1.45;
}
.issue-detail {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
  margin-top: 3px;
  line-height: 1.5;
}
.issue-go {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 700;
  color: var(--sa-accent);
}
.issue-go.muted {
  color: var(--sa-text-tertiary);
  font-weight: 400;
}
.issue-row:focus-visible {
  outline: 2px solid var(--sa-accent);
  outline-offset: -2px;
}
.reopened-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 1px 6px;
  border-radius: 6px;
  font-size: 10.5px;
  font-weight: 700;
  background: rgba(217, 119, 6, 0.14);
  color: var(--warn-color);
}
.ignore-btn {
  flex-shrink: 0;
  padding: 5px 10px;
  border: 1px solid var(--sa-border);
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 11.5px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.ignore-btn:hover:not(:disabled) {
  border-color: var(--sa-accent);
  color: var(--sa-accent);
}
.ignore-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.ignored-card {
  margin-top: 16px;
}
.issue-row.is-ignored {
  cursor: default;
}
.issue-row.is-ignored:hover {
  background: none;
}
.issue-head .link-btn {
  margin-left: auto;
}
.ignore-msg {
  margin-top: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 12.5px;
}

.checks-card {
  margin-top: 16px;
}
.checks-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 6px 18px;
  padding: 4px 20px 18px;
}
.check-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  padding: 6px 10px;
  border-radius: 8px;
  color: var(--sa-text-secondary);
}
.check-item.has-issues {
  background: var(--sa-subtle);
  color: var(--sa-text-primary);
}
.check-count {
  font-weight: 700;
  font-size: 13px;
}
.check-count.sev-error {
  color: var(--error-color);
}
.check-count.sev-warning {
  color: var(--warn-color);
}
.list-loading {
  padding: 60px 0;
  text-align: center;
  color: var(--sa-text-tertiary);
  font-size: 13px;
}

@media (max-width: 768px) {
  .health-grid {
    grid-template-columns: 1fr;
  }
  .sev-card {
    flex-wrap: wrap;
  }
  .sev-name {
    overflow-wrap: anywhere;
  }
  .issue-title,
  .issue-detail {
    overflow-wrap: anywhere;
  }
  .issue-row {
    flex-wrap: wrap;
  }
  .ignore-btn {
    margin-left: auto;
  }
  .checks-grid {
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  }
}
</style>
