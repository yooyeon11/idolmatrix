<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { aiApi } from '@/api/ai'
import { LinkOutlined } from '@/components/icons'

const props = defineProps<{
  entityType: 'artist' | 'group'
  entityId: number
  modelValue: Record<string, string> | null | undefined
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, string>]
  save: [value: Record<string, string>]
}>()

const KNOWN = [
  { key: 'instagram', label: 'Instagram', placeholder: 'https://instagram.com/...' },
  { key: 'x', label: 'X', placeholder: 'https://x.com/...' },
  { key: 'weibo', label: '微博', placeholder: 'https://weibo.com/...' },
  { key: 'youtube', label: 'YouTube', placeholder: 'https://youtube.com/...' },
  { key: 'tiktok', label: 'TikTok', placeholder: 'https://tiktok.com/@...' },
  { key: 'bilibili', label: 'Bilibili', placeholder: 'https://space.bilibili.com/...' },
  { key: 'facebook', label: 'Facebook', placeholder: 'https://facebook.com/...' },
  { key: 'website', label: '官网', placeholder: 'https://...' },
]

type Row = { key: string; label: string; url: string; custom?: boolean }

const rows = ref<Row[]>([])
const error = ref('')
const proposeOpen = ref(false)
const proposeBusy = ref(false)
const proposeBasis = ref('')
const proposeLinks = ref<{ key: string; label: string; url: string; checked: boolean }[]>([])

function labelOf(key: string) {
  return KNOWN.find((k) => k.key === key)?.label || key
}

function hydrate(raw: Record<string, string> | null | undefined) {
  const map = raw && typeof raw === 'object' ? { ...raw } : {}
  const next: Row[] = []
  const seen = new Set<string>()
  for (const k of KNOWN) {
    const url = String(map[k.key] || (k.key === 'x' ? map.twitter : '') || '').trim()
    next.push({ key: k.key, label: k.label, url })
    seen.add(k.key)
    if (k.key === 'x') seen.add('twitter')
  }
  for (const [key, url] of Object.entries(map)) {
    const norm = key === 'twitter' ? 'x' : key
    if (seen.has(norm) || seen.has(key)) continue
    next.push({ key, label: labelOf(key), url: String(url || ''), custom: true })
    seen.add(key)
  }
  rows.value = next
}

watch(
  () => props.modelValue,
  (v) => hydrate(v as Record<string, string> | null | undefined),
  { immediate: true, deep: true },
)

function toMap(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const r of rows.value) {
    const url = r.url.trim()
    if (!url) continue
    const key = r.key === 'twitter' ? 'x' : r.key.trim()
    if (!key) continue
    out[key] = url
  }
  return out
}

function onBlur() {
  const map = toMap()
  emit('update:modelValue', map)
  emit('save', map)
}

function addCustom() {
  rows.value = [...rows.value, { key: '', label: '自定义', url: '', custom: true }]
}

async function searchSocial() {
  proposeBusy.value = true
  proposeOpen.value = true
  proposeBasis.value = ''
  proposeLinks.value = []
  error.value = ''
  try {
    const r = await aiApi.suggestSocial({
      entity_type: props.entityType,
      entity_id: props.entityId,
      use_saved_ingest_ai: true,
    })
    proposeBasis.value = r.basis || ''
    const links = r.links || {}
    proposeLinks.value = Object.entries(links).map(([key, url]) => ({
      key,
      label: labelOf(key),
      url: String(url),
      checked: true,
    }))
    if (!proposeLinks.value.length) {
      error.value = '未找到可用的社交媒体链接，可稍后重试或手动填写'
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '社交媒体搜索失败'
  } finally {
    proposeBusy.value = false
  }
}

function applyProposed() {
  const picked = proposeLinks.value.filter((p) => p.checked && p.url.trim())
  if (!picked.length) {
    proposeOpen.value = false
    return
  }
  const map = toMap()
  for (const p of picked) {
    const key = p.key === 'twitter' ? 'x' : p.key
    map[key] = p.url.trim()
  }
  hydrate(map)
  emit('update:modelValue', map)
  emit('save', map)
  proposeOpen.value = false
}

const hasRows = computed(() => rows.value.length > 0)
</script>

<template>
  <div class="social-ed">
    <div class="social-toolbar">
      <button type="button" class="social-search" :disabled="disabled || proposeBusy" @click="searchSocial">
        {{ proposeBusy ? '搜索中…' : '搜索社交媒体' }}
      </button>
      <button type="button" class="social-add" :disabled="disabled" @click="addCustom">自定义平台</button>
    </div>
    <div v-if="error" class="social-error">{{ error }}</div>

    <div v-if="proposeOpen" class="social-propose">
      <div class="propose-head">
        <b>AI 提议的社交链接</b>
        <span v-if="proposeBasis" class="propose-basis">{{ proposeBasis }}</span>
      </div>
      <div v-if="proposeBusy" class="propose-empty">正在搜索…</div>
      <div v-else-if="!proposeLinks.length" class="propose-empty">暂无提议</div>
      <label v-for="(p, i) in proposeLinks" :key="p.key + i" class="propose-row">
        <input v-model="p.checked" type="checkbox" />
        <span class="propose-label">{{ p.label }}</span>
        <a class="propose-url" :href="p.url" target="_blank" rel="noopener noreferrer">{{ p.url }}</a>
      </label>
      <div class="propose-actions">
        <button type="button" class="social-add" @click="proposeOpen = false">取消</button>
        <button
          type="button"
          class="social-search primary"
          :disabled="!proposeLinks.some((p) => p.checked)"
          @click="applyProposed"
        >
          应用所选
        </button>
      </div>
    </div>

    <div v-if="hasRows" class="social-rows">
      <div v-for="(r, idx) in rows" :key="idx" class="social-row">
        <input
          v-if="r.custom"
          v-model="r.key"
          class="social-key"
          placeholder="平台名"
          :disabled="disabled"
          @change="onBlur"
        />
        <span v-else class="social-key-label">{{ r.label }}</span>
        <input
          v-model="r.url"
          class="social-url"
          type="url"
          :placeholder="KNOWN.find((k) => k.key === r.key)?.placeholder || 'https://...'"
          :disabled="disabled"
          @change="onBlur"
        />
        <a
          v-if="r.url.trim()"
          class="social-open"
          :href="r.url.trim()"
          target="_blank"
          rel="noopener noreferrer"
          title="打开"
        >
          <LinkOutlined :size="14" />
        </a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.social-ed {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.social-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.social-search,
.social-add {
  border-radius: 999px;
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.16));
  background: transparent;
  color: var(--sa-text-primary, #fff);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  padding: 7px 12px;
  cursor: pointer;
}
.social-search.primary,
.social-search:not(:disabled):hover {
  border-color: var(--sa-accent, #0485f7);
  background: var(--sa-accent-subtle, rgba(139, 124, 240, 0.12));
}
.social-search:disabled,
.social-add:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.social-error {
  color: var(--dh-bad, #f07178);
  font-size: 12px;
}
.social-propose {
  border: 1px solid var(--sa-border-subtle, rgba(255, 255, 255, 0.08));
  border-radius: 12px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.02);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.propose-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}
.propose-basis {
  color: var(--sa-text-secondary, #9898a8);
  font-size: 12px;
}
.propose-empty {
  font-size: 12px;
  color: var(--sa-text-secondary, #9898a8);
}
.propose-row {
  display: grid;
  grid-template-columns: auto 72px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.propose-url {
  color: var(--sa-accent, #0485f7);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.propose-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 4px;
}
.social-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.social-row {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr) 28px;
  gap: 8px;
  align-items: center;
}
.social-key-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--sa-text-secondary, #9898a8);
}
.social-key,
.social-url {
  width: 100%;
  border-radius: 8px;
  border: 1px solid var(--sa-border, rgba(255, 255, 255, 0.14));
  background: rgba(0, 0, 0, 0.2);
  color: var(--sa-text-primary, #fff);
  font: inherit;
  font-size: 12px;
  padding: 8px 10px;
}
.social-open {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--sa-text-secondary, #9898a8);
}
</style>
