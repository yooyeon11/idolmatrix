<script setup lang="ts">
import { useThemeStore } from '@/stores/theme'
import { SunOutlined, MoonOutlined } from '@/components/icons'

const themeStore = useThemeStore()
</script>

<template>
  <button
    class="theme-toggle"
    type="button"
    :title="themeStore.mode === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
    @click="themeStore.toggle()"
  >
    <SunOutlined v-if="themeStore.mode === 'dark'" class="theme-toggle__icon" />
    <MoonOutlined v-else class="theme-toggle__icon" />
  </button>
</template>

<style scoped>
.theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: none;
  background: none;
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.15s;
}
.theme-toggle:hover {
  color: var(--sa-accent);
}
.theme-toggle__icon {
  width: 18px;
  height: 18px;
  display: block;
}
</style>
