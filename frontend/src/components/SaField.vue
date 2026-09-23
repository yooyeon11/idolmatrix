<script setup lang="ts">
/**
 * 表单字段（HeroUI v3 TextField + Label + Description + FieldError 复刻）
 *
 * 结构：flex 纵排、间隔 **4px**（HeroUI `.textfield` 的 gap-1，比项目其他表单紧）。
 * label 14px/500 且用**主文本色** —— HeroUI 就是这样，这点和项目其他地方的灰色 label 不同。
 *
 * a11y：label 元素**包住**控件（隐式关联，点文字即聚焦，无需 for/id），
 * 但 description / error 放在 label **外面** —— 否则屏幕阅读器会把错误文案读进字段名里。
 *
 * 错误提示是**折叠**的：无错误时 height:0 + opacity:0 不占位，
 * 出现时靠 `interpolate-size: allow-keywords`（Chrome 129+）让 height:auto 也能过渡；
 * 老浏览器不支持该属性时只是不做动画，布局仍然正确。
 */
withDefaults(
  defineProps<{
    label?: string
    /** 字段下方的说明文字；进入错误态时自动隐藏（HeroUI 行为） */
    description?: string
    /** 错误文案；非空即进入错误态 */
    error?: string
    required?: boolean
    disabled?: boolean
  }>(),
  { error: '' },
)
</script>

<template>
  <div
    class="sa-field"
    :data-invalid="error ? 'true' : undefined"
    :data-disabled="disabled ? 'true' : undefined"
  >
    <label v-if="label || $slots.default" class="sa-field__wrap">
      <span v-if="label" class="sa-field__label" :class="{ 'sa-field__label--required': required }">
        {{ label }}
      </span>
      <slot />
    </label>
    <p v-if="description && !error" class="sa-field__desc">{{ description }}</p>
    <p class="sa-field__error" :class="{ 'sa-field__error--visible': !!error }" role="alert">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.sa-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  /* 让下面的 height: auto 错误折叠能真正过渡（不支持则退化为直接显隐，布局无损） */
  interpolate-size: allow-keywords;
}
.sa-field__wrap {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sa-field__label {
  font-size: 14px;
  font-weight: 500;
  line-height: 20px;
  color: var(--sa-text-primary);
}
/* 必填星号：HeroUI .label--required 的 after:ms-0.5 after:content-['*'] */
.sa-field__label--required::after {
  content: '*';
  margin-inline-start: 2px;
  color: var(--sa-danger);
}
/* 错误态：label 一起变 danger（HeroUI [data-invalid] .label） */
.sa-field[data-invalid='true'] .sa-field__label {
  color: var(--sa-danger);
}
.sa-field[data-disabled='true'] .sa-field__label {
  opacity: 0.5;
}
.sa-field__desc {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--sa-text-secondary);
  word-break: break-word;
}
.sa-field__error {
  margin: 0;
  /* HeroUI .field-error = px-1 text-xs text-danger */
  padding: 0 4px;
  font-size: 12px;
  line-height: 16px;
  color: var(--sa-danger);
  word-break: break-word;
  height: 0;
  opacity: 0;
  overflow: hidden;
  transition:
    opacity 150ms ease-out,
    height 350ms ease;
}
.sa-field__error--visible {
  height: auto;
  opacity: 1;
}
@media (prefers-reduced-motion: reduce) {
  .sa-field__error {
    transition: none;
  }
}
</style>
