<script setup lang="ts" generic="T extends string | number | null | (string | number)[]">
import { computed, onMounted, ref, type VNodeChild } from 'vue'
import { NSelect } from 'naive-ui'
import type { SelectOption } from 'naive-ui'

/**
 * 全项目统一选择器（单选 / 多选 / 静态选项 / 远程搜索 / 自由输入）。
 *
 * 样板 = 待整理页「组合」那个选择器（原 `combo-select` / `RemoteMultiSelect` 家族），
 * 皮肤统一在 styles/db-controls.css 的 `.sa-select` + `.sa-select-menu`：
 * 白底 + 1px --sa-border + 12px 圆角 + 36px 高 + 13px 字 + 轻投影 + 中性聚焦；
 * 多选**已选项** = 纯文本「名字 ×」，**网格排版**（按控件宽度每行 1~4 个，选多了多行铺开，
 * 竖分隔线只画在同一行的两项之间，行尾行首都不会多出来），不要胶囊框。
 *
 * 约定：**所有选择器都用这个组件** —— 单选 `multiple=false`，多选加 `multiple`，
 * 远程搜索给 `fetchOptions`，自由输入给 `tag`。
 */
export interface SaOption {
  label: string
  value: string | number
  /** 附加提示，渲染为「label（tag）」；用于区分同名条目（如不同主体的同名歌曲） */
  tag?: string
  disabled?: boolean
}

/** 自定义已选项渲染的入参（结构对齐 naive-ui 的 renderTag，只取用得到的字段） */
export interface SaTagInfo {
  option: { label?: unknown; value?: unknown }
  handleClose: () => void
}

const props = withDefaults(
  defineProps<{
    modelValue?: T
    /** 静态选项 */
    options?: SaOption[]
    /** 远程搜索函数：给了就自动 remote + filterable */
    fetchOptions?: (q: string) => Promise<SaOption[]>
    /** 已选值兜底选项：远程模式下保证回显 label（如已关联但不在搜索结果里的条目） */
    presetOptions?: SaOption[]
    /** 多选 */
    multiple?: boolean
    /** 允许自由输入（仅多选生效），如「别名」 */
    tag?: boolean
    /** 强制可搜索（本地过滤，选项已全量给出时用；远程搜索自动开启） */
    filterable?: boolean
    /** 强制远程模式（选项由外部 `search` 事件自带处理时用；给了 fetchOptions 会自动开启） */
    remote?: boolean
    placeholder?: string
    clearable?: boolean
    disabled?: boolean
    /** 外部加载态（如相册列表正在拉取） */
    loading?: boolean
    /** 已选项上限；**一般不要用** —— 多选默认按 1~2 列多行铺开，全部可见，
        只有确实需要「+N」折叠时（超长列表）才给数字或 'responsive' */
    maxTagCount?: number | 'responsive'
    /** 挂载 / 聚焦时预取一次（空关键字），让下拉一点开就有候选 */
    prefetch?: boolean
    /** 自定义已选项渲染（如视频类型的纯文本枚举）；不传则用统一的多选样式 */
    renderTag?: (info: SaTagInfo) => VNodeChild
    /** 自定义选项 / 选中值的 label 渲染（如下拉里画色点）。
        ⚠ 下拉菜单挂在 body 下，这里渲染出的节点**必须用全局样式**，scoped 不生效。 */
    renderLabel?: (option: SelectOption) => VNodeChild
  }>(),
  {
    options: () => [],
    presetOptions: () => [],
    multiple: false,
    tag: false,
    filterable: false,
    placeholder: '请选择',
    clearable: true,
    disabled: false,
    loading: false,
    prefetch: false,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: T): void
  (e: 'change', value: T): void
  /** 搜索词变化（远程模式下透传给父级，父级可自行维护选项） */
  (e: 'search', q: string): void
}>()

/** 远程模式：显式 remote=true，或给了 fetchOptions 就自动开。
   ⚠ 不能用 `props.remote ?? ...`：Vue 对 Boolean 且无 default 的 prop「缺省即 false」，
   `??` 会永远短路到 false（踩过：远程搜索退化成不可搜索的下拉）。 */
const isRemote = computed(() => props.remote || !!props.fetchOptions)

const fetched = ref<SaOption[]>([])
const searching = ref(false)

function toOption(o: SaOption): SelectOption {
  return { label: o.tag ? `${o.label}（${o.tag}）` : o.label, value: o.value, disabled: o.disabled }
}

/** 显示选项 = 已选兜底 +（远程结果 or 静态选项），按 value 去重 */
const displayOptions = computed<SelectOption[]>(() => {
  const map = new Map<string | number, SelectOption>()
  for (const o of props.presetOptions) map.set(o.value, toOption(o))
  const list = props.fetchOptions ? fetched.value : props.options
  for (const o of list) if (!map.has(o.value)) map.set(o.value, toOption(o))
  return [...map.values()]
})

async function runSearch(q: string) {
  if (!props.fetchOptions) return
  searching.value = true
  try {
    fetched.value = await props.fetchOptions(q)
  } catch {
    // 搜索失败时保留已有选项，不覆盖（保持原 RemoteMultiSelect 行为）
  } finally {
    searching.value = false
  }
}

function onSearch(q: string) {
  emit('search', q)
  if (props.fetchOptions) void runSearch(q)
}

function onFocus() {
  if (props.prefetch) void runSearch('')
}

function onUpdate(value: string | number | (string | number)[] | null) {
  emit('update:modelValue', value as T)
  emit('change', value as T)
}

onMounted(() => {
  if (props.prefetch) void runSearch('')
})
</script>

<template>
  <n-select
    :value="modelValue"
    :options="displayOptions"
    :loading="loading || searching"
    :placeholder="placeholder"
    :clearable="clearable"
    :disabled="disabled"
    :multiple="multiple"
    :tag="tag"
    :max-tag-count="maxTagCount"
    :filterable="filterable || isRemote || tag"
    :remote="isRemote"
    :render-tag="renderTag"
    :render-label="renderLabel"
    class="sa-select"
    :menu-props="{ class: 'sa-select-menu' }"
    @search="onSearch"
    @focus="onFocus"
    @update:value="onUpdate"
  >
    <template v-if="$slots.action" #action>
      <slot name="action" />
    </template>
  </n-select>
</template>
