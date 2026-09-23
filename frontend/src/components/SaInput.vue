<script setup lang="ts">
/**
 * 文本输入框（HeroUI v3 Input 复刻）
 *
 * ⚠ 最反直觉的一点：HeroUI 的字段**没有边框** —— `--field-border-width: 0px`，
 * 与底色的区分靠 field 背景 + 三层浅投影（`--field-shadow`，与卡片投影**是同一串值**）。
 * 不要按惯例给它加 1px 描边，那样就不是 HeroUI 了。
 *
 * 两个变体（对应 HeroUI 的 `.input` 与 `.input--secondary`）：
 *   primary（HeroUI 默认）= surface 底 + 三层浅投影，靠投影"浮"在**灰页底**上；
 *     ⚠ 放进白卡片里会与卡片同色、几乎隐形，所以白底容器别用它。
 *   secondary = `--default` 灰底 + **无投影**（HeroUI 为这种情况本就提供了这个变体）。
 *     浅色下比白卡片暗、深色下比深卡片亮，两个主题都有明确明度差。
 * 本项目的容器基本都是 `--sa-elevated` 卡片，故默认取 secondary。
 *
 * 状态（照 focus-field-ring / invalid-field-ring）：
 *   焦点       = 2px 实色强调环、**offset 0**（紧贴元素，无缝隙）
 *   错误未聚焦 = 1px danger 外描边
 *   错误聚焦   = 换成 2px danger 环并去掉描边
 */
withDefaults(
  defineProps<{
    modelValue?: string
    type?: string
    placeholder?: string
    autocomplete?: string
    name?: string
    required?: boolean
    disabled?: boolean
    /** 校验失败态；应与外层 SaField 的 error 保持一致 */
    invalid?: boolean
    /** primary = HeroUI 默认（surface 底 + 投影）；secondary = --default 灰底（白底容器用） */
    variant?: 'primary' | 'secondary'
  }>(),
  { modelValue: '', type: 'text', variant: 'secondary' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <input
    class="sa-input"
    :class="[`sa-input--${variant}`, { 'sa-input--invalid': invalid }]"
    :type="type"
    :value="modelValue"
    :placeholder="placeholder"
    :autocomplete="autocomplete"
    :name="name"
    :required="required"
    :disabled="disabled"
    :aria-invalid="invalid || undefined"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>

<style scoped>
.sa-input {
  /* 变体只改这几个私有变量，公共态在下面统一处理。
     ⚠ --input-focus-ring 必须**整体**是一个合法的 box-shadow 值：不能写成
     `0 0 0 2px X, var(--input-shadow)` —— secondary 的 --input-shadow 是 `none`，
     而 `none` 不允许出现在阴影列表里，会让**整条声明失效、焦点环直接消失**。 */
  --input-bg: var(--sa-field-bg);
  --input-bg-hover: var(--sa-field-hover);
  --input-shadow: var(--sa-field-shadow);
  --input-focus-ring: 0 0 0 2px var(--sa-focus);

  display: block;
  width: 100%;
  box-sizing: border-box;
  /* HeroUI .input = px-3 py-2 text-base sm:text-sm，border-width: 0 */
  padding: 8px 12px;
  border: none;
  border-radius: var(--sa-field-radius); /* 12px */
  background: var(--input-bg);
  color: var(--sa-field-fg);
  font-family: inherit;
  font-size: 16px;
  line-height: 24px;
  box-shadow: var(--input-shadow);
  outline: none;
  transition:
    background-color 150ms ease,
    box-shadow 150ms ease-out;
}
/* HeroUI .input--secondary：--default 底 + shadow-none */
.sa-input--secondary {
  --input-bg: var(--sa-default);
  --input-bg-hover: var(--sa-default-hover);
  --input-shadow: none;
}
/* primary 变体在环之外保留自己的 field 投影 */
.sa-input--primary {
  --input-focus-ring: 0 0 0 2px var(--sa-focus), var(--sa-field-shadow);
}
/* ≥640px 降到 14px（HeroUI 桌面口径） */
@media (min-width: 640px) {
  .sa-input {
    font-size: 14px;
    line-height: 20px;
  }
}
.sa-input::placeholder {
  color: var(--sa-field-placeholder);
}
/* hover 只在真的能悬停的设备上生效，避免触屏点完留下 hover 态 */
@media (hover: hover) {
  .sa-input:hover:not(:focus):not(:focus-visible) {
    background: var(--input-bg-hover);
  }
}
.sa-input:focus,
.sa-input:focus-visible {
  box-shadow: var(--input-focus-ring);
}
/* 错误未聚焦：1px danger 外描边；聚焦时换成 2px danger 环、去掉描边 */
.sa-input--invalid {
  --input-focus-ring: 0 0 0 2px var(--sa-danger);
  outline: 1px solid var(--sa-danger);
}
.sa-input--invalid.sa-input--primary {
  --input-focus-ring: 0 0 0 2px var(--sa-danger), var(--sa-field-shadow);
}
.sa-input--invalid:focus,
.sa-input--invalid:focus-visible {
  /* `outline: none` 只把 outline-style 置 none，outline-width 的 computed 值会回退到初始值
     medium(3px)；补一条 width: 0 让"无描边"在 computed style 里也明确可见 */
  outline: none;
  outline-width: 0;
}
.sa-input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
