<script setup lang="ts">
import { ref } from 'vue'
import { membershipsApi } from '@/api/memberships'
import type { DbMemberRow } from '@/api/dbViews'
import { formatPositions, parsePositionsInput } from '@/utils/positions'

defineProps<{
  members: DbMemberRow[]
  busy?: boolean
}>()

const emit = defineEmits<{ changed: [] }>()

const savingId = ref<number | null>(null)
const error = ref('')
const removingId = ref<number | null>(null)
const expandedId = ref<number | null>(null)

const DETAIL_FIELDS: { key: keyof DbMemberRow; label: string }[] = [
  { key: 'korean_name', label: '韩文名' },
  { key: 'english_name', label: '英文名' },
  { key: 'chinese_name', label: '中文名' },
  { key: 'birth_place', label: '出生地' },
  { key: 'gender', label: '性别' },
  { key: 'debut_date', label: '出道日期' },
  { key: 'occupation', label: '职业' },
  { key: 'stage_name', label: '艺名' },
]

function toggleExpand(id: number) {
  expandedId.value = expandedId.value === id ? null : id
}

function fieldText(m: DbMemberRow, key: keyof DbMemberRow): string {
  const v = m[key]
  return v === null || v === undefined || v === '' ? '—' : String(v)
}

function dateOrNull(v: string): string | null {
  return v === '' ? null : v
}

async function save(m: DbMemberRow, patch: Record<string, unknown>) {
  error.value = ''
  savingId.value = m.membership_id
  try {
    await membershipsApi.update(m.membership_id, patch)
    emit('changed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    savingId.value = null
  }
}

function onJoinDate(m: DbMemberRow, ev: Event) {
  save(m, { join_date: dateOrNull((ev.target as HTMLInputElement).value) })
}

function onLeaveDate(m: DbMemberRow, ev: Event) {
  save(m, { leave_date: dateOrNull((ev.target as HTMLInputElement).value) })
}

function onStatus(m: DbMemberRow, ev: Event) {
  save(m, { status: (ev.target as HTMLSelectElement).value })
}

function onPositions(m: DbMemberRow, ev: Event) {
  save(m, { positions: parsePositionsInput((ev.target as HTMLInputElement).value) })
}

async function removeMember(m: DbMemberRow) {
  if (!window.confirm(`确定删除「${m.name}」的这条成员记录？该操作可在回收站恢复。`)) return
  error.value = ''
  removingId.value = m.membership_id
  try {
    await membershipsApi.remove(m.membership_id)
    emit('changed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
  } finally {
    removingId.value = null
  }
}
</script>

<template>
  <div class="mt-wrap">
    <div v-if="error" class="mt-error">{{ error }}</div>
    <table class="sub-table">
      <thead>
        <tr>
          <th>成员</th>
          <th>出生日期</th>
          <th>加入</th>
          <th>退出</th>
          <th>状态</th>
          <th>担当</th>
          <th class="op-col"></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="m in members" :key="m.membership_id">
        <tr :class="{ dangling: m.is_dangling }">
          <td>
            <button class="expand-btn" :class="{ open: expandedId === m.membership_id }" @click="toggleExpand(m.membership_id)">
              ▸
            </button>
            <span class="member-name">{{ m.name }}</span>
            <span v-if="m.stage_name && m.stage_name !== m.name" class="member-sub">{{ m.stage_name }}</span>
            <span v-if="m.is_dangling" class="tag tag-bad">已删除</span>
            <span v-if="savingId === m.membership_id" class="saving">保存中…</span>
          </td>
          <td :class="{ 'cell-empty': !m.birth_date }">{{ m.birth_date || '—' }}</td>
          <td>
            <input
              v-if="!m.is_dangling"
              class="cell-input"
              type="date"
              :value="m.join_date ?? ''"
              :disabled="busy || savingId === m.membership_id"
              @change="onJoinDate(m, $event)"
            />
            <span v-else class="missing-text">{{ m.join_date || '—' }}</span>
          </td>
          <td>
            <input
              v-if="!m.is_dangling"
              class="cell-input"
              type="date"
              :value="m.leave_date ?? ''"
              :disabled="busy || savingId === m.membership_id"
              @change="onLeaveDate(m, $event)"
            />
            <span v-else class="missing-text">{{ m.leave_date || '—' }}</span>
          </td>
          <td>
            <select
              v-if="!m.is_dangling"
              class="cell-input cell-select"
              :value="m.status"
              :disabled="busy || savingId === m.membership_id"
              @change="onStatus(m, $event)"
            >
              <option value="Active">在籍</option>
              <option value="Former">已退出</option>
              <option value="Inactive">暂停活动</option>
            </select>
            <span v-else class="missing-text">{{ m.status }}</span>
          </td>
          <td>
            <input
              v-if="!m.is_dangling"
              class="cell-input"
              type="text"
              :value="formatPositions(m.positions)"
              placeholder="如：主唱 / 中心"
              :disabled="busy || savingId === m.membership_id"
              @change="onPositions(m, $event)"
            />
            <span v-else class="missing-text">—</span>
          </td>
          <td class="op-col">
            <button
              v-if="!m.is_dangling"
              class="del-btn"
              :disabled="busy || removingId === m.membership_id"
              title="删除这条成员记录"
              @click.stop="removeMember(m)"
            >
              删除
            </button>
          </td>
        </tr>
        <tr v-if="expandedId === m.membership_id" class="detail-row">
          <td :colspan="7">
            <div class="member-detail">
              <div class="md-grid">
                <div v-for="f in DETAIL_FIELDS" :key="f.key" class="md-field">
                  <span class="md-k">{{ f.label }}</span>
                  <span class="md-v" :class="{ empty: fieldText(m, f.key) === '—' }">{{ fieldText(m, f.key) }}</span>
                </div>
                <div class="md-field">
                  <span class="md-k">社交账号</span>
                  <span class="md-v">{{ m.social_media ? Object.keys(m.social_media).length + ' 个' : '—' }}</span>
                </div>
                <div class="md-field">
                  <span class="md-k">来源</span>
                  <span class="md-v">{{ m.external_links.length || '—' }}</span>
                </div>
              </div>
              <div v-if="m.description" class="md-desc">{{ m.description }}</div>
              <div v-if="m.external_links.length" class="md-links">
                来源：
                <a v-for="(l, i) in m.external_links" :key="i" :href="l.url" target="_blank" rel="noreferrer">
                  {{ l.url }}
                </a>
              </div>
              <div v-if="m.artist_uid" class="md-more">
                <router-link :to="`/artists/${m.artist_uid}`" class="md-link">在旧版艺人页查看（含图库/影像）→</router-link>
              </div>
            </div>
          </td>
        </tr>
        </template>
        <tr v-if="!members.length">
          <td colspan="6" class="missing-text">没有任何成员记录</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.mt-wrap {
  min-width: 0;
}
.mt-error {
  margin-bottom: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(208, 48, 80, 0.1);
  color: var(--dh-bad, #d03050);
  font-size: 12px;
}
.sub-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--sa-elevated);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 12px;
  overflow: hidden;
}
.sub-table th {
  text-align: left;
  font-size: 10.5px;
  letter-spacing: 1.5px;
  color: var(--sa-text-tertiary);
  font-weight: 800;
  padding: 8px 14px;
  background: var(--sa-subtle);
}
.sub-table td {
  padding: 6px 10px;
  font-size: 12.5px;
  border-top: 1px solid var(--sa-border-subtle);
  vertical-align: middle;
}
.sub-table tr.dangling td {
  opacity: 0.55;
}
.member-name {
  font-weight: 700;
}
.member-sub {
  margin-left: 6px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.tag {
  display: inline-block;
  font-size: 10.5px;
  font-weight: 700;
  border-radius: 6px;
  padding: 2px 8px;
  margin-left: 6px;
  white-space: nowrap;
}
.tag-bad {
  background: rgba(208, 48, 80, 0.12);
  color: var(--dh-bad, #d03050);
}
.cell-input {
  width: 100%;
  min-width: 96px;
  border: 1px solid transparent;
  border-bottom: 1px dashed var(--sa-border);
  border-radius: 6px;
  background: transparent;
  color: var(--sa-text-primary);
  font-size: 12.5px;
  font-family: inherit;
  padding: 4px 6px;
  outline: none;
}
.cell-input:hover {
  border-color: var(--sa-border);
}
.cell-input:focus {
  border-color: var(--sa-accent);
  background: var(--sa-bg);
}
.cell-select {
  appearance: auto;
}
.op-col {
  width: 44px;
  text-align: right;
}
.del-btn {
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  font-size: 11.5px;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 6px;
}
.del-btn:hover {
  color: var(--dh-bad, #d03050);
  background: rgba(208, 48, 80, 0.08);
}
.del-btn:disabled {
  opacity: 0.4;
  cursor: default;
}
.saving {
  margin-left: 6px;
  font-size: 10.5px;
  color: var(--sa-accent);
}
.cell-empty {
  color: var(--sa-text-tertiary);
}
.missing-text {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  font-style: italic;
}
</style>

<style scoped>
.expand-btn {
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  cursor: pointer;
  font-size: 11px;
  padding: 0 4px;
  transition: transform 0.15s;
  display: inline-block;
}
.expand-btn.open {
  transform: rotate(90deg);
  color: var(--sa-accent);
}
.member-detail {
  padding: 10px 12px;
  background: var(--sa-subtle);
  border-radius: 10px;
}
.md-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 16px;
}
.md-field {
  display: flex;
  gap: 8px;
  font-size: 12px;
  padding: 2px 0;
}
.md-k {
  flex-shrink: 0;
  width: 62px;
  color: var(--sa-text-tertiary);
  font-size: 11px;
}
.md-v {
  font-weight: 600;
  word-break: break-all;
}
.md-v.empty {
  color: var(--sa-text-tertiary);
  font-weight: 400;
}
.md-desc {
  margin-top: 8px;
  font-size: 12px;
  color: var(--sa-text-secondary);
  line-height: 1.6;
}
.md-links {
  margin-top: 8px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  word-break: break-all;
}
.md-links a {
  color: var(--sa-accent);
  text-decoration: none;
  margin-right: 8px;
}
.md-more {
  margin-top: 6px;
  font-size: 11px;
}
.md-link {
  color: var(--sa-accent);
  text-decoration: none;
  font-weight: 700;
}
.detail-row td {
  padding: 8px 14px 12px 30px !important;
  background: var(--sa-bg);
}
</style>
