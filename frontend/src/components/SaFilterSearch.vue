<script setup lang="ts">
import { CloseOutlined, SearchOutlined } from '@/components/icons'

/**
 * 内嵌检索（口径来自「浏览」页搜索胶囊展开态）
 *
 * 无独立边框、无独立底色，直接落在 SaFilterBar 的胶囊里：
 * 放大镜 + 输入 + 清除，12.5px。这样「范围 / 排序 / 检索」是同一颗控件，
 * 而不是「两个胶囊 + 一个带描边的输入框」。
 */
withDefaults(
  defineProps<{
    modelValue: string
    placeholder?: string
    /** 桌面宽度；窄屏由容器决定（flex: 1） */
    width?: string
    ariaLabel?: string
  }>(),
  { placeholder: '检索…', width: '156px', ariaLabel: '检索' },
)
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void }>()

function onInput(e: Event) {
  emit('update:modelValue', (e.target as HTMLInputElement).value)
}
</script>

<template>
  <label class="sa-fs" :style="{ width }">
    <SearchOutlined :size="13" class="sa-fs-glyph" />
    <input
      :value="modelValue"
      type="text"
      :placeholder="placeholder"
      :aria-label="ariaLabel"
      @input="onInput"
    />
    <button
      v-if="modelValue"
      type="button"
      class="sa-fs-clear"
      aria-label="清空"
      @click.prevent="emit('update:modelValue', '')"
    >
      <CloseOutlined :size="10" />
    </button>
  </label>
</template>

<style scoped>
.sa-fs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  /* 34 = 浏览页 .search-pill 的高度，它决定整条胶囊的 44px 总高 */
  height: 34px;
  padding: 0 10px;
  border-radius: 999px;
  color: var(--sa-text-tertiary);
  transition: background-color 0.18s ease;
}
.sa-fs:focus-within {
  background: var(--sa-hover);
}
.sa-fs-glyph {
  flex: none;
}
.sa-fs input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--sa-text-primary);
  font-family: inherit;
  font-size: 12px;
}
.sa-fs input::placeholder {
  color: var(--sa-text-tertiary);
}
.sa-fs-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: var(--sa-subtle);
  color: var(--sa-text-tertiary);
  cursor: pointer;
}
.sa-fs-clear:hover {
  color: var(--sa-text-primary);
}

@media (max-width: 768px) {
  .sa-fs {
    flex: 1 1 auto;
    width: auto !important;
    min-width: 84px;
    padding: 0 8px;
    gap: 5px;
  }
}
</style>
