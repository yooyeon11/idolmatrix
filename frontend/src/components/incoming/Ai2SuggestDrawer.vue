<script setup lang="ts">
/**
 * ai2 建议抽屉：展示任务执行过程 → 字段差异 → 逐字段采纳；核验存疑单独列出。
 *
 * 设计口径：抽屉只「建议」，不直接写表单；用户勾选后一次性 patch 回父组件。
 */
import { computed, ref, watch } from 'vue'
import { ai2Api, type Ai2Step, type Ai2TaskRun } from '@/api/ai2'

const props = defineProps<{
  show: boolean
  runId: string | null
  current: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:show', v: boolean): void
  (e: 'adopt', patch: Record<string, unknown>): void
}>()

const FIELD_LABELS: Record<string, string> = {
  name: '标题',
  chinese_name: '中文名',
  event_name: '事件名',
  performance_date: '演出日期',
  chinese_description: '中文描述',
  camera_angle: '机位',
  video_types: '视频类型',
  is_solo: 'Solo',
}

const run = ref<Ai2TaskRun | null>(null)
const loading = ref(false)
const skipped = ref<Record<string, boolean>>({})

let timer: number | null = null

function stopPolling() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

async function poll() {
  if (!props.runId) return
  loading.value = true
  try {
    const r = await ai2Api.getRun(props.runId)
    run.value = r
    if (r.status !== 'running') stopPolling()
  } catch {
    /* 轮询失败不打断，下次再试 */
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.show, props.runId] as const,
  ([open, id]) => {
    stopPolling()
    if (!open || !id) return
    run.value = null
    skipped.value = {}
    void poll()
    timer = window.setInterval(() => void poll(), 1500)
  },
  { immediate: true },
)

const suggestion = computed<Record<string, unknown>>(() => {
  const s = run.value?.result?.suggestion
  return s && typeof s === 'object' ? (s as Record<string, unknown>) : {}
})

const issues = computed<string[]>(() => {
  const list = run.value?.result?.verify_issues
  return Array.isArray(list) ? list.map((x) => String(x)) : []
})

const steps = computed<Ai2Step[]>(() => run.value?.steps || [])

interface DiffRow {
  key: string
  label: string
  before: string
  after: string
  conflict: boolean
}

const rows = computed<DiffRow[]>(() => {
  const out: DiffRow[] = []
  for (const [key, value] of Object.entries(suggestion.value)) {
    if (value === null || value === undefined || value === '') continue
    const label = FIELD_LABELS[key]
    if (!label) continue
    const beforeRaw = props.current[key]
    const before = Array.isArray(beforeRaw) ? beforeRaw.join('、') : String(beforeRaw ?? '')
    const after = Array.isArray(value) ? value.join('、') : String(value)
    if (!after.trim() || after === before) continue
    out.push({ key, label, before, after, conflict: Boolean(before.trim()) && before !== after })
  }
  return out
})

const statusText = computed(() => {
  const s = run.value?.status
  if (s === 'done') return '已完成'
  if (s === 'failed') return '失败'
  if (s === 'skipped') return '已跳过'
  return '执行中…'
})

function isChecked(key: string): boolean {
  return !skipped.value[key]
}

function toggle(key: string) {
  skipped.value = { ...skipped.value, [key]: !skipped.value[key] }
}

function adopt() {
  const patch: Record<string, unknown> = {}
  for (const row of rows.value) {
    if (isChecked(row.key)) patch[row.key] = suggestion.value[row.key]
  }
  emit('adopt', patch)
  emit('update:show', false)
}

function close() {
  emit('update:show', false)
}
</script>

<template>
  <div v-if="show" class="ai2-drawer-mask" @click.self="close">
    <aside class="ai2-drawer">
      <header class="ai2-drawer-head">
        <div>
          <div class="ai2-drawer-title">ai2 建议</div>
          <div class="ai2-drawer-sub">{{ statusText }}</div>
        </div>
        <button class="ai2-icon-btn" type="button" aria-label="关闭" @click="close">✕</button>
      </header>

      <div class="ai2-drawer-body">
        <section class="ai2-block">
          <div class="ai2-block-title">执行过程</div>
          <ul class="ai2-steps">
            <li v-for="(s, i) in steps" :key="i" :class="{ fail: s.ok === false }">
              <span class="ai2-step-dot" />
              <span class="ai2-step-name">{{ s.step }}</span>
              <span class="ai2-step-detail">{{ s.detail }}</span>
            </li>
            <li v-if="!steps.length" class="ai2-empty">等待任务开始…</li>
          </ul>
        </section>

        <section v-if="run?.status === 'failed'" class="ai2-block">
          <div class="ai2-block-title">失败原因</div>
          <div class="ai2-error">{{ run.error || '未知错误' }}</div>
        </section>

        <section v-if="rows.length" class="ai2-block">
          <div class="ai2-block-title">字段差异（勾选后采纳）</div>
          <div v-for="row in rows" :key="row.key" class="ai2-diff-row" :class="{ conflict: row.conflict }">
            <label class="ai2-diff-check">
              <input type="checkbox" :checked="isChecked(row.key)" @change="toggle(row.key)" />
            </label>
            <div class="ai2-diff-main">
              <div class="ai2-diff-label">
                {{ row.label }}
                <span v-if="row.conflict" class="ai2-badge">覆盖原值</span>
              </div>
              <div class="ai2-diff-values">
                <span v-if="row.before" class="ai2-before">{{ row.before }}</span>
                <span v-if="row.before" class="ai2-arrow">→</span>
                <span class="ai2-after">{{ row.after }}</span>
              </div>
            </div>
          </div>
        </section>

        <section v-if="issues.length" class="ai2-block">
          <div class="ai2-block-title">核验存疑（{{ issues.length }}）</div>
          <ul class="ai2-issues">
            <li v-for="(it, i) in issues" :key="i">{{ it }}</li>
          </ul>
        </section>

        <section v-if="run?.status === 'done' && !rows.length" class="ai2-block">
          <div class="ai2-empty">建议与当前表单一致，或没有可采纳的字段。</div>
        </section>
      </div>

      <footer class="ai2-drawer-foot">
        <button class="sa-btn sa-btn--ghost" type="button" @click="close">取消</button>
        <button class="sa-btn sa-btn--primary" type="button" :disabled="!rows.length" @click="adopt">
          采纳选中（{{ rows.filter((r) => isChecked(r.key)).length }}）
        </button>
      </footer>
    </aside>
  </div>
</template>

<style scoped>
.ai2-drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  justify-content: flex-end;
  background: rgb(0 0 0 / 32%);
}
.ai2-drawer {
  display: flex;
  flex-direction: column;
  width: min(480px, 92vw);
  height: 100%;
  background: var(--sa-bg);
  border-left: 1px solid var(--sa-border);
  box-shadow: -8px 0 24px rgb(0 0 0 / 12%);
}
.ai2-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 16px 18px 12px;
  border-bottom: 1px solid var(--sa-border);
}
.ai2-drawer-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--sa-text);
}
.ai2-drawer-sub {
  margin-top: 2px;
  font-size: 12px;
  color: var(--sa-text-3);
}
.ai2-icon-btn {
  border: 0;
  background: transparent;
  color: var(--sa-text-3);
  cursor: pointer;
  font-size: 14px;
}
.ai2-drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.ai2-block-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--sa-text-2);
  margin-bottom: 8px;
}
.ai2-steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ai2-steps li {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  color: var(--sa-text-2);
}
.ai2-step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--sa-success, #18a058);
  flex: none;
}
.ai2-steps li.fail .ai2-step-dot {
  background: var(--sa-danger, #d03050);
}
.ai2-step-name {
  font-weight: 600;
  color: var(--sa-text);
}
.ai2-step-detail {
  color: var(--sa-text-3);
}
.ai2-diff-row {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--sa-border);
  border-radius: 8px;
  margin-bottom: 8px;
}
.ai2-diff-row.conflict {
  border-color: rgb(240 160 32 / 45%);
  background: rgb(240 160 32 / 6%);
}
.ai2-diff-main {
  flex: 1;
  min-width: 0;
}
.ai2-diff-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--sa-text);
  margin-bottom: 4px;
}
.ai2-badge {
  margin-left: 6px;
  padding: 0 6px;
  font-size: 11px;
  font-weight: 400;
  border-radius: 4px;
  color: #b26a00;
  background: rgb(240 160 32 / 18%);
}
.ai2-diff-values {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 12px;
  word-break: break-all;
}
.ai2-before {
  color: var(--sa-text-3);
  text-decoration: line-through;
}
.ai2-arrow {
  color: var(--sa-text-3);
}
.ai2-after {
  color: var(--sa-text);
}
.ai2-issues {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--sa-text-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ai2-error {
  font-size: 12px;
  color: var(--sa-danger, #d03050);
  word-break: break-all;
}
.ai2-empty {
  font-size: 12px;
  color: var(--sa-text-3);
}
.ai2-drawer-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 18px;
  border-top: 1px solid var(--sa-border);
}
</style>
