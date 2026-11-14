<script setup lang="ts">
/**
 * 设置 → API 令牌（发卡）
 *
 * 给 AI / 脚本用的「门禁卡」：每张卡自带权限范围、有效期，可单独撤销。
 * 明文只在创建成功的那一刻显示一次，之后列表只留前 12 位提示。
 *
 * 和旧 `app_settings.external_api.token` 的关系：旧 token 仍然有效（等价 admin），
 * 但没有分级与撤销记录，建议签发新卡后在「视频评分」里清空旧值。
 */
import { computed, onMounted, ref } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import { apiTokensApi, type ApiTokenRow } from '@/api/apiTokens'
import { RefreshOutlined } from '@/components/icons'

const message = useMessage()
const dialog = useDialog()

const rows = ref<ApiTokenRow[]>([])
const loading = ref(false)
const creating = ref(false)
const revoking = ref<number | null>(null)

/** 新建成功后展示一次的明文 */
const freshToken = ref('')
const freshName = ref('')
const copied = ref(false)

const form = ref({
  name: '',
  scopes: ['read', 'edit'] as string[],
  expires_days: 90,
  note: '',
})

const SCOPE_OPTIONS = [
  { key: 'read', label: '只读', desc: '查艺人 / 组合 / 视频 / 入库清单' },
  { key: 'edit', label: '改内容', desc: '改元数据、关联、传头像（含只读）' },
  { key: 'ingest', label: '入库', desc: '扫描待整理、写清单、确认入库（含只读）' },
  { key: 'admin', label: '管理', desc: '删除 / 回收站 / 批量执行（全部）' },
]

const EXPIRY_OPTIONS = [
  { value: 30, label: '30 天' },
  { value: 90, label: '90 天' },
  { value: 365, label: '1 年' },
  { value: 0, label: '长期有效' },
]

const canCreate = computed(
  () => form.value.name.trim().length >= 2 && form.value.scopes.length > 0,
)

function toggleScope(key: string) {
  const list = form.value.scopes
  const idx = list.indexOf(key)
  if (idx >= 0) {
    if (list.length === 1) return
    list.splice(idx, 1)
  } else {
    list.push(key)
  }
}

async function load() {
  loading.value = true
  try {
    rows.value = await apiTokensApi.list()
  } catch (e) {
    message.error((e as Error).message || '读取令牌失败')
  } finally {
    loading.value = false
  }
}

async function create() {
  if (!canCreate.value) return
  creating.value = true
  copied.value = false
  try {
    const created = await apiTokensApi.create({
      name: form.value.name.trim(),
      scopes: form.value.scopes,
      expires_days: form.value.expires_days || null,
      note: form.value.note.trim() || null,
    })
    freshToken.value = created.token
    freshName.value = created.name
    form.value.name = ''
    form.value.note = ''
    await load()
  } catch (e) {
    message.error((e as Error).message || '签发失败')
  } finally {
    creating.value = false
  }
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(freshToken.value)
    copied.value = true
    message.success('已复制')
  } catch {
    message.warning('浏览器不允许自动复制，请手动选中复制')
  }
}

function revoke(row: ApiTokenRow) {
  dialog.warning({
    title: '撤销令牌',
    content: `撤销「${row.name}」后，正在用这张卡的 AI / 脚本会立刻失去访问权限。`,
    positiveText: '确认撤销',
    negativeText: '取消',
    onPositiveClick: async () => {
      revoking.value = row.id
      try {
        await apiTokensApi.revoke(row.id)
        message.success('已撤销')
        await load()
      } catch (e) {
        message.error((e as Error).message || '撤销失败')
      } finally {
        revoking.value = null
      }
    },
  })
}

function scopeLabel(key: string) {
  return SCOPE_OPTIONS.find((s) => s.key === key)?.label || key
}

function statusText(row: ApiTokenRow) {
  if (row.status === 'revoked') return '已撤销'
  if (row.status === 'expired') return '已过期'
  return '可用'
}

function fmt(value: string | null) {
  if (!value) return '--'
  return value.replace('T', ' ').slice(0, 16)
}

onMounted(load)
</script>

<template>
  <section class="sa-section at-panel">
    <div class="panel-group-title">API 令牌</div>

    <div class="panel">
      <div class="panel-subtitle">签发新卡</div>
      <p class="at-hint">
        给 AI / 脚本用的访问凭证。明文只在签发成功后显示一次，请立即复制保存；
        列表里只保留前 12 位用于分辨是哪张卡。
      </p>

      <div class="at-form">
        <label class="at-field">
          <span class="at-label">名称</span>
          <input
            v-model="form.name"
            class="sa-input"
            maxlength="80"
            placeholder="例如：WorkBuddy 本机"
          />
        </label>
        <label class="at-field">
          <span class="at-label">有效期</span>
          <select v-model.number="form.expires_days" class="sa-input">
            <option v-for="o in EXPIRY_OPTIONS" :key="o.value" :value="o.value">
              {{ o.label }}
            </option>
          </select>
        </label>
        <label class="at-field at-field--wide">
          <span class="at-label">备注（可选）</span>
          <input
            v-model="form.note"
            class="sa-input"
            maxlength="200"
            placeholder="用途说明，方便以后判断这张卡还能不能撤"
          />
        </label>
      </div>

      <div class="at-scopes">
        <button
          v-for="s in SCOPE_OPTIONS"
          :key="s.key"
          class="at-scope"
          :class="{ 'at-scope--on': form.scopes.includes(s.key) }"
          type="button"
          @click="toggleScope(s.key)"
        >
          <span class="at-scope-name">{{ s.label }}</span>
          <span class="at-scope-desc">{{ s.desc }}</span>
        </button>
      </div>

      <div class="at-actions">
        <button class="sa-btn at-btn" :disabled="!canCreate || creating" @click="create">
          {{ creating ? '签发中…' : '签发令牌' }}
        </button>
        <span class="at-tip">至少要选一项权限；「改内容」已包含「只读」。</span>
      </div>

      <div v-if="freshToken" class="at-fresh">
        <div class="at-fresh-head">
          <span>「{{ freshName }}」已创建 —— 这串明文只显示这一次</span>
          <button class="at-btn at-btn--ghost" type="button" @click="copyToken">
            {{ copied ? '已复制' : '复制' }}
          </button>
        </div>
        <code class="at-fresh-token">{{ freshToken }}</code>
      </div>
    </div>

    <div class="panel">
      <div class="panel-subtitle at-list-head">
        <span>已签发的卡（{{ rows.length }}）</span>
        <button class="at-refresh" type="button" :disabled="loading" title="刷新" @click="load">
          <RefreshOutlined :size="14" />
        </button>
      </div>

      <div v-if="!rows.length" class="at-empty">
        还没有签发过令牌。旧版的单一 Token 仍然可用，但没有权限分级与撤销记录。
      </div>

      <div v-else class="at-list">
        <div
          v-for="row in rows"
          :key="row.id"
          class="at-row"
          :class="{ 'at-row--off': row.status !== 'active' }"
        >
          <div class="at-row-main">
            <div class="at-row-name">
              {{ row.name }}
              <code class="at-hint-code">{{ row.hint }}…</code>
            </div>
            <div class="at-row-meta">
              <span v-for="s in row.scopes" :key="s" class="at-pill">{{ scopeLabel(s) }}</span>
              <span class="at-dot">·</span>
              <span>最后使用 {{ fmt(row.last_used_at) }}</span>
              <span class="at-dot">·</span>
              <span>{{ row.expires_at ? `到期 ${fmt(row.expires_at)}` : '长期有效' }}</span>
            </div>
            <div v-if="row.note" class="at-row-note">{{ row.note }}</div>
          </div>
          <div class="at-row-side">
            <span class="at-status" :class="`at-status--${row.status}`">
              {{ statusText(row) }}
            </span>
            <button
              v-if="row.status === 'active'"
              class="at-btn at-btn--danger"
              :disabled="revoking === row.id"
              @click="revoke(row)"
            >
              撤销
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.at-hint {
  margin: 0 0 16px;
  max-width: 720px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
}
.at-form {
  display: flex;
  flex-wrap: wrap;
  gap: 14px 18px;
}
.at-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 200px;
}
.at-field--wide {
  flex: 1 1 320px;
}
.at-label {
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.at-scopes {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 16px;
}
.at-scope {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  text-align: left;
  border-radius: 10px;
  border: 1px solid var(--sa-border-subtle);
  background: transparent;
  color: var(--sa-text-primary);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.at-scope--on {
  border-color: color-mix(in oklab, var(--sa-accent, #7c5cff) 55%, transparent);
  background: color-mix(in oklab, var(--sa-accent, #7c5cff) 10%, transparent);
}
.at-scope-name {
  font-size: 13px;
  font-weight: 500;
}
.at-scope-desc {
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--sa-text-secondary);
}
.at-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
}
.at-tip {
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.at-btn {
  padding: 8px 16px;
  border-radius: 9px;
  border: 1px solid transparent;
  font-size: 13px;
  background: var(--sa-text-primary);
  color: var(--sa-bg, #fff);
  cursor: pointer;
}
.at-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.at-btn--ghost {
  background: transparent;
  border-color: var(--sa-border-subtle);
  color: var(--sa-text-primary);
}
.at-btn--danger {
  background: transparent;
  border-color: color-mix(in oklab, #d9534f 45%, transparent);
  color: #c9433f;
  padding: 5px 12px;
  font-size: 12px;
}
.at-fresh {
  margin-top: 18px;
  padding: 14px;
  border-radius: 12px;
  border: 1px solid color-mix(in oklab, #d8a12a 45%, transparent);
  background: color-mix(in oklab, #d8a12a 8%, transparent);
}
.at-fresh-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12.5px;
  color: var(--sa-text-primary);
}
.at-fresh-token {
  display: block;
  margin-top: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--sa-subtle, rgba(0, 0, 0, 0.05));
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  word-break: break-all;
  color: var(--sa-text-primary);
}
.at-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.at-refresh {
  display: inline-flex;
  align-items: center;
  padding: 4px;
  border: none;
  background: transparent;
  color: var(--sa-text-secondary);
  cursor: pointer;
}
.at-empty {
  padding: 16px 0;
  font-size: 12.5px;
  color: var(--sa-text-tertiary);
}
.at-list {
  display: flex;
  flex-direction: column;
}
.at-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 0;
  border-top: 1px solid var(--sa-border-subtle);
}
.at-row--off {
  opacity: 0.55;
}
.at-row-main {
  min-width: 0;
}
.at-row-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  font-weight: 500;
}
.at-hint-code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}
.at-row-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.at-pill {
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--sa-border-subtle);
  font-size: 11px;
}
.at-dot {
  color: var(--sa-text-tertiary);
}
.at-row-note {
  margin-top: 6px;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.at-row-side {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.at-status {
  font-size: 12px;
  color: var(--sa-text-secondary);
}
.at-status--active {
  color: #2f8f5b;
}
.at-status--revoked,
.at-status--expired {
  color: var(--sa-text-tertiary);
}
</style>
