<script setup lang="ts">
import { computed, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    total: number
    page: number
    pageSize: number
    disabled?: boolean
    showTotal?: boolean
  }>(),
  { disabled: false, showTotal: true },
)

const emit = defineEmits<{
  'update:page': [page: number]
}>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const hasPrev = computed(() => props.page > 1)
const hasNext = computed(() => props.page < totalPages.value)

function go(target: number) {
  if (props.disabled) return
  const clamped = Math.min(Math.max(1, Math.round(target)), totalPages.value)
  if (!Number.isFinite(clamped) || clamped === props.page) return
  emit('update:page', clamped)
}

// 桌面端页码序列：首尾固定，当前页左右各一页，缺口以省略号填充
const pages = computed<(number | '…')[]>(() => {
  const n = totalPages.value
  const c = Math.min(Math.max(props.page, 1), n)
  if (n <= 7) return Array.from({ length: n }, (_, i) => i + 1)
  const start = Math.max(2, c - 1)
  const end = Math.min(n - 1, c + 1)
  const list: (number | '…')[] = [1]
  if (start > 2) list.push('…')
  for (let p = start; p <= end; p++) list.push(p)
  if (end < n - 1) list.push('…')
  list.push(n)
  return list
})

const jumpDraft = ref('')

function commitJump() {
  const raw = jumpDraft.value.trim()
  jumpDraft.value = ''
  if (!raw) return
  const parsed = parseInt(raw, 10)
  if (Number.isNaN(parsed)) return
  go(parsed)
}
</script>

<template>
  <nav class="sa-pagination" aria-label="分页">
    <span v-if="showTotal" class="sa-pg-total">共 {{ total }} 条</span>
    <button
      class="sa-pg-btn sa-pg-flip"
      type="button"
      :disabled="disabled || !hasPrev"
      @click="go(page - 1)"
    >
      ‹ 上一页
    </button>
    <span class="sa-pg-indicator" aria-live="polite">第 {{ page }} / {{ totalPages }} 页</span>
    <div class="sa-pg-pages">
      <template v-for="(item, i) in pages" :key="`${item}-${i}`">
        <span v-if="item === '…'" class="sa-pg-ellipsis">…</span>
        <button
          v-else
          class="sa-pg-num"
          type="button"
          :class="{ 'sa-pg-num--active': item === page }"
          :disabled="disabled"
          :aria-current="item === page ? 'page' : undefined"
          @click="go(item)"
        >
          {{ item }}
        </button>
      </template>
    </div>
    <button
      class="sa-pg-btn sa-pg-flip"
      type="button"
      :disabled="disabled || !hasNext"
      @click="go(page + 1)"
    >
      下一页 ›
    </button>
    <label class="sa-pg-jump">
      <span class="sa-pg-jump-label">跳至</span>
      <input
        v-model="jumpDraft"
        class="sa-pg-jump-input"
        type="text"
        inputmode="numeric"
        :placeholder="String(page)"
        :disabled="disabled"
        @keydown.enter.prevent="commitJump"
        @blur="commitJump"
      />
      <span class="sa-pg-jump-unit">页</span>
      <!-- 手机输入法按「前往」不一定派发 Enter（isComposing），给一个可点按钮兜底 -->
      <button
        class="sa-pg-btn sa-pg-jump-go"
        type="button"
        :disabled="disabled"
        @click="commitJump"
      >
        跳转
      </button>
    </label>
  </nav>
</template>

<style scoped>
.sa-pagination {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 36px 0 0;
  font-size: 13px;
}
.sa-pg-total {
  color: var(--sa-text-tertiary);
}
.sa-pg-flip,
.sa-pg-jump-go {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  font-size: 13px;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s;
}
.sa-pg-flip:hover:not(:disabled),
.sa-pg-jump-go:hover:not(:disabled) {
  border-color: var(--sa-accent);
  color: var(--sa-accent);
}
.sa-pg-flip:focus-visible,
.sa-pg-num:focus-visible,
.sa-pg-jump-go:focus-visible {
  outline: 2px solid var(--sa-accent);
  outline-offset: 1px;
}
.sa-pg-flip:disabled,
.sa-pg-jump-go:disabled {
  opacity: 0.45;
  cursor: default;
}
.sa-pg-indicator {
  display: none;
  color: var(--sa-text-secondary);
  white-space: nowrap;
}
.sa-pg-pages {
  display: flex;
  align-items: center;
  gap: 6px;
}
.sa-pg-num {
  min-width: 32px;
  height: 32px;
  padding: 0 6px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  cursor: pointer;
  transition: border-color 0.2s, color 0.2s, background 0.2s;
}
.sa-pg-num:hover:not(:disabled) {
  border-color: var(--sa-border-subtle);
  color: var(--sa-text-primary);
}
.sa-pg-num--active {
  border-color: var(--sa-accent);
  background: var(--sa-accent);
  color: #fff;
  font-weight: 600;
}
.sa-pg-num--active:hover:not(:disabled) {
  color: #fff;
}
.sa-pg-ellipsis {
  padding: 0 2px;
  color: var(--sa-text-tertiary);
}
.sa-pg-jump {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--sa-text-tertiary);
}
.sa-pg-jump-input {
  width: 52px;
  height: 30px;
  padding: 0 6px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 8px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 13px;
  text-align: center;
  outline: none;
  transition: border-color 0.2s;
}
.sa-pg-jump-input:focus {
  border-color: var(--sa-accent);
}
.sa-pg-jump-input::placeholder {
  color: var(--sa-text-tertiary);
  opacity: 0.6;
}

@media (max-width: 640px) {
  .sa-pagination {
    row-gap: 12px;
    padding: 28px 0 0;
  }
  .sa-pg-total,
  .sa-pg-pages {
    display: none;
  }
  .sa-pg-indicator {
    display: inline-flex;
  }
  .sa-pg-flip,
  .sa-pg-jump-go {
    height: 40px;
    padding: 0 16px;
    flex: none;
  }
  .sa-pg-jump {
    width: 100%;
    justify-content: center;
  }
}
</style>
