<script setup lang="ts">
// 资料库列表页通用工具栏：客户端搜索 + 排序。四个列表页共用。
defineProps<{
  search: string
  sort: string
  placeholder: string
  sorts: { value: string; label: string }[]
  /** 是否显示「无影像关联」开关（艺人/专辑/歌曲页） */
  videoToggle?: boolean
  showNoVideo?: boolean
  /** 当前搜索结果里被「无影像」过滤掉的条数 */
  hiddenNoVideo?: number
}>()

const emit = defineEmits<{
  (e: 'update:search', value: string): void
  (e: 'update:sort', value: string): void
  (e: 'update:showNoVideo', value: boolean): void
}>()
</script>

<template>
  <div class="db-toolbar">
    <input
      class="db-search"
      type="text"
      :value="search"
      :placeholder="placeholder"
      @input="emit('update:search', ($event.target as HTMLInputElement).value)"
    />
    <!-- 搜索与排序之间：宿主可塞新建/刷新等操作按钮 -->
    <slot name="actions" />
    <select
      class="db-sort"
      :value="sort"
      @change="emit('update:sort', ($event.target as HTMLSelectElement).value)"
    >
      <option v-for="s in sorts" :key="s.value" :value="s.value">{{ s.label }}</option>
    </select>
    <label v-if="videoToggle" class="db-nv-toggle">
      <input type="checkbox" :checked="showNoVideo" @change="emit('update:showNoVideo', ($event.target as HTMLInputElement).checked)" />
      <span>显示无影像关联</span>
    </label>
    <span
      v-if="videoToggle && !showNoVideo && (hiddenNoVideo ?? 0) > 0"
      class="db-hidden-hint"
    >
      已隐藏 {{ hiddenNoVideo }} 条无影像关联
    </span>
  </div>
</template>

<style scoped>
.db-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.db-search {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s;
}
.db-search:focus {
  border-color: var(--sa-accent);
}
.db-search::placeholder {
  color: var(--sa-text-tertiary);
}
.db-sort {
  flex-shrink: 0;
  height: 36px;
  padding: 0 10px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 10px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  outline: none;
}
.db-nv-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}
.db-nv-toggle input {
  width: 15px;
  height: 15px;
  accent-color: var(--sa-accent);
  cursor: pointer;
}
.db-hidden-hint {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
  white-space: nowrap;
}
@media (max-width: 768px) {
  .db-toolbar {
    flex-wrap: wrap;
  }
  .db-search {
    flex: 1 1 100%;
  }
  .db-sort {
    flex: 1 1 auto;
    min-width: 140px;
  }
  .db-hidden-hint {
    width: 100%;
    white-space: normal;
  }
}
@media (max-width: 560px) {
  .db-sort {
    flex: 1 1 100%;
  }
}
</style>
