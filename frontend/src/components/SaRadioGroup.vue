<script setup lang="ts">
/**
 * 单选组（HeroUI Radio 规格）
 *
 * 结构对齐 HeroUI v3 的 RadioField：可点击行 = 控件(16px 圆) + 标签，
 * 说明文字在行下方并缩进到与标签左对齐（ps-7 = 16 控件 + 12 间距）。
 * 视觉沿用项目的 --sa-* 变量，与待整理页 / AI 设置页 / 基础设置页同一套口径。
 */
export interface SaRadioOption {
  value: string
  label: string
  /** 行下方的说明文字（可选） */
  desc?: string
  /** 标签前的色点颜色，如强调色选项（可选） */
  dot?: string
}

withDefaults(
  defineProps<{
    modelValue: string
    options: SaRadioOption[]
    /** 原生 radio 的 name，同一组必须唯一 */
    name: string
    /**
     * vertical（默认）= HeroUI 的 mt-4 纵排；
     * horizontal = HeroUI 的横向 gap-4（16px）排列。
     * 横排时说明文字不渲染 —— 并排的两个说明长度不同会把行高撑乱。
     */
    orientation?: 'vertical' | 'horizontal'
  }>(),
  { orientation: 'vertical' },
)

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <div class="sa-radio-group" :class="`sa-radio-group--${orientation}`" role="radiogroup">
    <label v-for="o in options" :key="o.value" class="sa-radio">
      <input
        class="sa-radio__input"
        type="radio"
        :name="name"
        :value="o.value"
        :checked="modelValue === o.value"
        @change="emit('update:modelValue', o.value)"
      />
      <span class="sa-radio__row">
        <span class="sa-radio__control"><span class="sa-radio__dot" /></span>
        <span class="sa-radio__label">
          <span v-if="o.dot" class="sa-radio__swatch" :style="{ background: o.dot }" />
          {{ o.label }}
        </span>
      </span>
      <span v-if="o.desc && orientation === 'vertical'" class="sa-radio__desc">{{ o.desc }}</span>
    </label>
  </div>
</template>

<style scoped>
.sa-radio-group {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.sa-radio-group--horizontal {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 16px;
}
.sa-radio-group--vertical .sa-radio + .sa-radio {
  margin-top: 16px;
}
.sa-radio {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-width: 100%;
  cursor: pointer;
}
/* 原生 radio 只作为可访问性与键盘操作的载体，视觉全部由 __control 承担 */
.sa-radio__input {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: 0;
  opacity: 0;
  pointer-events: none;
}
.sa-radio__row {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.4;
  color: var(--sa-text-primary);
}
.sa-radio__control {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex: none;
  border-radius: 999px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.04),
    0 1px 2px rgba(0, 0, 0, 0.06),
    0 0 1px rgba(0, 0, 0, 0.06);
  transition:
    background-color 0.2s,
    border-color 0.2s,
    transform 0.1s;
}
html[data-theme='dark'] .sa-radio__control {
  /* 深色下 HeroUI 的 --field-shadow 为 none，照做 */
  box-shadow: none;
}
.sa-radio__dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: transparent;
  transition: background-color 0.2s;
}
/* 悬停：边框加深（不放大、不加光晕） */
.sa-radio:hover .sa-radio__control {
  border-color: color-mix(in srgb, var(--sa-text-primary) 18%, transparent);
}
.sa-radio:active .sa-radio__control {
  transform: scale(0.95);
}
.sa-radio__input:focus-visible + .sa-radio__row .sa-radio__control {
  border-color: color-mix(in srgb, var(--sa-text-primary) 30%, transparent);
}
/* 选中：控件填充主题色 + 6px 白点（HeroUI 的 bg-accent + accent-foreground dot） */
.sa-radio__input:checked + .sa-radio__row .sa-radio__control {
  background: var(--sa-accent);
  border-color: var(--sa-accent);
}
.sa-radio__input:checked + .sa-radio__row .sa-radio__dot {
  background: #fff;
}
.sa-radio__label {
  display: inline-flex;
  align-items: center;
}
.sa-radio__swatch {
  width: 12px;
  height: 12px;
  margin-right: 8px;
  border-radius: 999px;
  flex: none;
}
.sa-radio__desc {
  padding-left: 28px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--sa-text-tertiary);
}
</style>
