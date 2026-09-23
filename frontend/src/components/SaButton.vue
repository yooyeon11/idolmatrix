<script setup lang="ts">
/**
 * 按钮（HeroUI v3 Button 复刻）
 *
 * 标志性外观是**胶囊**：`rounded-3xl` = 24px，而按钮高 40/36px —— 圆角比"完美胶囊"的
 * 一半还大，两端接近全圆，这是 HeroUI 的刻意选择，不要按常规改成 8px。
 *
 * 状态（照 status-focused / status-disabled / status-pending）：
 *   hover  = 背景切换（primary 用 mix(accent 90%, snow 10%)）
 *   active = `transform: scale(0.97)`（250ms 缓动；sm 尺寸用 0.98、lg 用 0.96）
 *   focus  = 2px 强调环 + **2px 页面底色 offset**（注意与 Input 不同，Input 是 offset 0）
 *   disabled/pending = opacity .5 / pointer-events none
 *
 * 类名用 `.sa-button`，与项目既有的全局 `.sa-btn`（db-controls.css / settings-shared.css）
 * 是两套东西，不要互相套用。
 */
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger'
    size?: 'sm' | 'md' | 'lg'
    type?: 'button' | 'submit' | 'reset'
    disabled?: boolean
    /** 进行中：显示转圈并屏蔽点击（对应 HeroUI 的 data-pending） */
    loading?: boolean
    /** 撑满整行（HeroUI --full-width） */
    block?: boolean
  }>(),
  { variant: 'primary', size: 'md', type: 'button' },
)
</script>

<template>
  <button
    class="sa-button"
    :class="[`sa-button--${variant}`, `sa-button--${size}`, { 'sa-button--block': block }]"
    :type="type"
    :disabled="disabled || loading"
    :data-pending="loading ? 'true' : undefined"
    :aria-busy="loading || undefined"
  >
    <span v-if="loading" class="sa-button__spinner" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.sa-button {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: fit-content;
  height: 40px; /* h-10 */
  padding: 0 16px; /* px-4 */
  border: none;
  border-radius: 24px; /* rounded-3xl */
  font-family: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  background: var(--btn-bg);
  color: var(--btn-fg);
  transition:
    transform 250ms ease,
    background-color 100ms ease-out,
    box-shadow 100ms ease-out;
}
@media (min-width: 768px) {
  .sa-button {
    height: 36px; /* md:h-9 */
  }
}

/* ---- 尺寸 ---- */
.sa-button--sm {
  height: 36px;
  padding: 0 12px;
}
.sa-button--lg {
  height: 44px;
  font-size: 16px;
}
@media (min-width: 768px) {
  .sa-button--sm {
    height: 32px;
  }
  .sa-button--lg {
    height: 40px;
  }
}
.sa-button--block {
  width: 100%;
}

/* ---- 变体：只改私有变量，公共态在下面的基础类里统一处理 ---- */
.sa-button--primary {
  --btn-bg: var(--sa-accent);
  --btn-bg-hover: var(--sa-accent-hover);
  --btn-fg: #fff;
}
.sa-button--secondary {
  --btn-bg: var(--sa-default);
  --btn-bg-hover: color-mix(in oklab, var(--sa-default) 96%, var(--sa-text-primary) 4%);
  --btn-fg: var(--sa-text-primary);
}
.sa-button--ghost {
  --btn-bg: transparent;
  --btn-bg-hover: var(--sa-default);
  --btn-fg: var(--sa-text-primary);
}
.sa-button--outline {
  --btn-bg: transparent;
  --btn-bg-hover: color-mix(in srgb, var(--sa-default) 60%, transparent);
  --btn-fg: var(--sa-text-primary);
  border: 1px solid var(--sa-border);
}
.sa-button--danger {
  --btn-bg: var(--sa-danger);
  --btn-bg-hover: color-mix(in oklab, var(--sa-danger) 90%, #fdfdfd 10%);
  --btn-fg: #fff;
}

/* ---- 状态 ---- */
@media (hover: hover) {
  .sa-button:hover:not(:disabled) {
    background: var(--btn-bg-hover);
  }
}
/* active 用 :not(:disabled) —— 禁用态不该有按下的缩放反馈 */
.sa-button:active:not(:disabled) {
  transform: scale(0.97);
}
.sa-button--sm:active:not(:disabled) {
  transform: scale(0.98);
}
.sa-button--lg:active:not(:disabled) {
  transform: scale(0.96);
}
/* 焦点环：2px 强调色 + 2px 页面底色 offset（HeroUI focus-ring） */
.sa-button:focus-visible {
  outline: none;
  box-shadow:
    0 0 0 2px var(--sa-bg),
    0 0 0 4px var(--sa-focus);
}
.sa-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.sa-button[data-pending='true'] {
  pointer-events: none;
}

/* ---- 进行中 ---- */
.sa-button__spinner {
  flex: none;
  width: 16px;
  height: 16px;
  border: 2px solid currentColor;
  border-top-color: transparent;
  border-radius: 50%;
  animation: sa-button-spin 0.75s linear infinite;
}
@keyframes sa-button-spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .sa-button {
    transition: none;
  }
  .sa-button__spinner {
    animation-duration: 2.5s;
  }
}
</style>
