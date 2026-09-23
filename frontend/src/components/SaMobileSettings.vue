<script setup lang="ts">
import { ref } from 'vue'

// 移动端工具栏收纳入口：⚙ 按钮 + 底部抽屉。
// breakpoint 决定按钮在哪个断点以上隐藏（与各页面自身移动端断点一致），
// 桌面端布局完全不受影响；抽屉内容由使用方通过默认插槽提供。
withDefaults(
  defineProps<{
    badge?: number
    title?: string
    breakpoint?: 640 | 768
  }>(),
  {
    badge: 0,
    title: '筛选与显示',
    breakpoint: 640,
  },
)

const open = ref(false)
</script>

<template>
  <span class="sms-wrap" :class="breakpoint === 768 ? 'sms-wrap--768' : 'sms-wrap--640'">
    <button class="sms-gear" type="button" aria-label="筛选与显示" @click="open = true">
      <svg
        viewBox="0 0 24 24"
        width="16"
        height="16"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
      >
        <path d="M4 7h9M19 7h1M4 17h3M13 17h7" />
        <circle cx="16" cy="7" r="2.4" />
        <circle cx="10" cy="17" r="2.4" />
      </svg>
      <span v-if="badge > 0" class="sms-badge">{{ badge > 9 ? '9+' : badge }}</span>
    </button>

    <Teleport to="body">
      <Transition name="sms">
        <div v-if="open" class="sms-mask" @click="open = false">
          <div
            class="sms-sheet"
            role="dialog"
            aria-modal="true"
            :aria-label="title"
            @click.stop
          >
            <div class="sms-handle"></div>
            <div class="sms-title">{{ title }}</div>
            <div class="sms-body">
              <slot />
            </div>
            <button class="sms-done" type="button" @click="open = false">完成</button>
          </div>
        </div>
      </Transition>
    </Teleport>
  </span>
</template>

<style>
.sms-wrap {
  display: inline-flex;
}
@media (min-width: 641px) {
  .sms-wrap--640 {
    display: none;
  }
}
@media (min-width: 769px) {
  .sms-wrap--768 {
    display: none;
  }
}
.sms-gear {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  cursor: pointer;
  flex: none;
}
.sms-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 9999px;
  background: var(--sa-accent);
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  font-weight: 600;
  text-align: center;
}
.sms-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.sms-sheet {
  width: 100%;
  max-height: 82vh;
  overflow-y: auto;
  border-radius: 16px 16px 0 0;
  background: var(--sa-bg);
  padding: 10px 20px calc(16px + env(safe-area-inset-bottom));
}
.sms-handle {
  width: 36px;
  height: 4px;
  border-radius: 9999px;
  background: var(--sa-border);
  margin: 0 auto 10px;
}
.sms-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--sa-text-primary);
  margin-bottom: 4px;
}
.sms-group {
  padding: 12px 0;
  border-top: 1px solid var(--sa-border-subtle);
}
.sms-group:first-child {
  border-top: none;
}
.sms-label {
  font-size: 12px;
  color: var(--sa-text-tertiary);
  margin-bottom: 8px;
}
.sms-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.sms-chip {
  padding: 6px 14px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.sms-chip--on {
  color: var(--sa-accent);
  border-color: var(--sa-accent);
  background: color-mix(in srgb, var(--sa-accent) 10%, transparent);
}
.sms-opt {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 2px;
  border: none;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}
.sms-opt--on {
  color: var(--sa-accent);
  font-weight: 600;
}
.sms-opt-dot {
  width: 14px;
  height: 14px;
  border-radius: 9999px;
  border: 1.5px solid var(--sa-border);
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.sms-opt--on .sms-opt-dot {
  border-color: var(--sa-accent);
}
.sms-opt--on .sms-opt-dot::after {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 9999px;
  background: var(--sa-accent);
}
.sms-seg {
  display: flex;
  padding: 3px;
  gap: 3px;
  border: 1px solid var(--sa-border-subtle);
  border-radius: 9999px;
  background: var(--sa-elevated);
}
.sms-seg button {
  flex: 1;
  padding: 6px 0;
  border: none;
  border-radius: 9999px;
  background: transparent;
  color: var(--sa-text-secondary);
  font-size: 13px;
  cursor: pointer;
}
.sms-seg button.on {
  background: var(--sa-accent);
  color: #fff;
  font-weight: 600;
}
.sms-done {
  width: 100%;
  margin-top: 14px;
  padding: 10px 0;
  border: none;
  border-radius: 9999px;
  background: var(--sa-accent);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.sms-enter-active,
.sms-leave-active {
  transition: opacity 0.18s ease;
}
.sms-enter-active .sms-sheet,
.sms-leave-active .sms-sheet {
  transition: transform 0.18s ease;
}
.sms-enter-from,
.sms-leave-to {
  opacity: 0;
}
.sms-enter-from .sms-sheet,
.sms-leave-to .sms-sheet {
  transform: translateY(30%);
}
</style>
