<script setup lang="ts">
/**
 * 入库清单卡片：待整理详情页的「建议视图」。
 *
 * 只展示建议，不写库：确认入库由父组件把 payload 灌进表单后走既有入库流程。
 */
import { computed } from 'vue'
import type { IngestManifest } from '@/api/ingest'

const props = defineProps<{
  manifest: IngestManifest
  busy?: boolean
}>()

const emit = defineEmits<{
  apply: []
  edit: []
  rebuild: []
  feedback: []
}>()

const p = computed(() => props.manifest.payload || {})

/** 数组字段兜底：后端/模型可能给字符串，直接 .join 会崩（卡片整块空白） */
function asList<T = unknown>(v: unknown): T[] {
  if (Array.isArray(v)) return v as T[]
  if (typeof v === 'string' && v.trim()) return v.split(/[,，、/]/).map((x) => x.trim()) as T[]
  return []
}
const videoTypes = computed(() => asList<string>(p.value.video_types))
const conf = computed(() => Math.round((props.manifest.confidence ?? 0) * 100))
const confLevel = computed(() => {
  const c = props.manifest.confidence ?? 0
  if (c >= 0.8) return 'high'
  if (c >= 0.6) return 'mid'
  return 'low'
})
const statusText = computed(() =>
  props.manifest.source === 'agent'
    ? 'Agent 补全'
    : props.manifest.source === 'ai'
      ? 'AI 填写'
      : '规则草稿',
)

const entityNames = (list?: { name: string }[]) => (list || []).map((x) => x.name).join('、')

const albumNames = computed(() =>
  asList<{ albums?: string[] }>(p.value.suggested_tracks)
    .flatMap((t) => t.albums || [])
    .filter((x, i, arr) => arr.indexOf(x) === i)
    .join('、'),
)

const rows = computed(() => {
  const v = p.value
  const out: { k: string; v: string; missing?: boolean }[] = []
  out.push({ k: '类型', v: videoTypes.value.join(' / ') || v.video_type || '—', missing: !v.video_type })
  out.push({ k: '机位', v: v.camera_angle || '—', missing: !v.camera_angle })
  out.push({ k: '表演日期', v: v.performance_date || '—', missing: !v.performance_date })
  out.push({ k: '发布/上传', v: v.published_date || '—', missing: !v.published_date })
  out.push({ k: '活动', v: v.event_name || '—', missing: !v.event_name })
  out.push({ k: '场次', v: v.session_label || '—', missing: !v.session_label })
  out.push({ k: '组合', v: entityNames(v.groups) || '—', missing: !(v.groups || []).length })
  out.push({ k: '艺人', v: entityNames(v.artists) || '—', missing: !(v.artists || []).length })
  out.push({ k: '歌曲', v: entityNames(v.songs) || '—', missing: !(v.songs || []).length })
  out.push({ k: '专辑', v: albumNames.value || '—', missing: !albumNames.value })
  return out
})
</script>

<template>
  <section class="mc">
    <header class="mc-head">
      <div class="mc-title">
        <span class="mc-badge">入库清单</span>
        <span class="mc-source">{{ statusText }}</span>
      </div>
      <div class="mc-conf" :class="`mc-conf--${confLevel}`">
        置信度 <strong>{{ conf }}%</strong>
      </div>
    </header>

    <h2 class="mc-name">{{ p.name || '（未解析出标题）' }}</h2>

    <dl class="mc-rows">
      <div v-for="r in rows" :key="r.k" class="mc-row" :class="{ 'is-missing': r.missing }">
        <dt>{{ r.k }}</dt>
        <dd>{{ r.v }}</dd>
      </div>
    </dl>

    <div v-if="(p.new_entities || []).length" class="mc-new">
      <span class="mc-new-label">待新建</span>
      <span v-for="(e, i) in p.new_entities" :key="i" class="mc-new-item">
        {{ e.kind === 'song' ? '歌曲' : e.kind === 'group' ? '组合' : '艺人' }}「{{ e.name }}」
      </span>
      <span class="mc-new-hint">确认入库后才会创建</span>
    </div>

    <div v-if="(p.warnings || []).length" class="mc-warn">
      ⚠ {{ (p.warnings || []).join(' · ') }}
    </div>

    <div v-if="p.destination_rel" class="mc-path">
      <span class="mc-path-label">入库路径</span>
      <code class="mc-path-value">{{ p.destination_rel }}</code>
      <span v-if="p.path_notice" class="mc-path-note">{{ p.path_notice }}</span>
    </div>

    <p v-if="p.chinese_description" class="mc-desc">{{ p.chinese_description }}</p>

    <div v-if="(p.evidence || []).length" class="mc-evidence">
      <span class="mc-evidence-label">依据</span>
      <span v-for="(e, i) in p.evidence" :key="i" class="mc-evidence-item">
        {{ e.field }} ← {{ e.source }}
      </span>
    </div>

    <footer class="mc-actions">
      <button class="mc-btn mc-btn--primary" type="button" :disabled="busy" @click="emit('apply')">
        确认入库
      </button>
      <button class="mc-btn" type="button" :disabled="busy" @click="emit('edit')">修改</button>
      <button class="mc-btn" type="button" :disabled="busy" @click="emit('rebuild')">重新生成</button>
      <button class="mc-btn mc-btn--ghost" type="button" :disabled="busy" @click="emit('feedback')">
        有问题
      </button>
    </footer>
  </section>
</template>

<style scoped>
.mc {
  border: 1px solid var(--sa-border);
  border-radius: 16px;
  background: var(--sa-elevated);
  padding: 18px 20px;
  margin-bottom: 18px;
}
.mc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.mc-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mc-badge {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10.5px;
  letter-spacing: 0.08em;
  padding: 2px 8px;
  border-radius: 9999px;
  background: var(--sa-subtle);
  border: 1px solid var(--sa-border-subtle);
  color: var(--sa-text-secondary);
}
.mc-source {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.mc-conf {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.mc-conf strong {
  font-size: 13px;
}
.mc-conf--high strong {
  color: #17a673;
}
.mc-conf--mid strong {
  color: #d99100;
}
.mc-conf--low strong {
  color: #e0574a;
}
.mc-name {
  margin: 12px 0 14px;
  font-size: 17px;
  font-weight: 700;
  line-height: 1.45;
  color: var(--sa-text-primary);
  word-break: break-word;
}
.mc-sub {
  margin: -6px 0 12px;
  font-size: 12.5px;
  color: var(--sa-text-secondary);
  line-height: 1.5;
}
.mc-rows {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 24px;
  margin: 0 0 14px;
}
.mc-row {
  display: flex;
  gap: 10px;
  align-items: baseline;
  min-width: 0;
  padding-bottom: 6px;
  border-bottom: 1px dashed var(--sa-border-subtle);
}
.mc-row dt {
  flex: none;
  width: 40px;
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.mc-row dd {
  margin: 0;
  min-width: 0;
  font-size: 12.5px;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-row.is-missing dd {
  color: var(--sa-text-tertiary);
}
.mc-new {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  margin-bottom: 10px;
  border-radius: 10px;
  background: rgba(217, 145, 0, 0.08);
  border: 1px solid rgba(217, 145, 0, 0.25);
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.mc-new-label {
  font-weight: 700;
  color: #d99100;
}
.mc-new-item {
  color: var(--sa-text-primary);
}
.mc-new-hint {
  margin-left: auto;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.mc-warn {
  font-size: 12px;
  color: #d99100;
  margin-bottom: 10px;
}
.mc-evidence {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  margin-bottom: 14px;
}
.mc-evidence-label {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.mc-evidence-item {
  padding: 1px 7px;
  border-radius: 9999px;
  background: var(--sa-subtle);
}
.mc-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.mc-btn {
  height: 32px;
  padding: 0 16px;
  border-radius: 9999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: color 0.16s ease, border-color 0.16s ease, opacity 0.16s ease;
}
.mc-btn:hover:not(:disabled) {
  color: var(--sa-text-primary);
  border-color: var(--sa-text-tertiary);
}
.mc-btn--primary {
  background: var(--sa-text-primary);
  border-color: var(--sa-text-primary);
  color: var(--sa-bg);
  font-weight: 600;
}
.mc-btn--primary:hover:not(:disabled) {
  opacity: 0.88;
  color: var(--sa-bg);
}
.mc-btn--ghost {
  border-color: transparent;
  background: transparent;
}
.mc-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.mc-path {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  margin-bottom: 10px;
  border-radius: 10px;
  background: var(--sa-subtle);
  font-size: 11.5px;
}
.mc-path-label {
  flex: none;
  color: var(--sa-text-tertiary);
}
.mc-path-value {
  flex: 1;
  min-width: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--sa-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mc-path-note {
  color: #d99100;
}
.mc-desc {
  margin: 0 0 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--sa-text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
