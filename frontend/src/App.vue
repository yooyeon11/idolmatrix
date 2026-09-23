<script setup lang="ts">
import { computed } from 'vue'
import { darkTheme, zhCN, dateZhCN } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import { useThemeStore } from '@/stores/theme'

const themeStore = useThemeStore()

// Naive UI 主题随全局主题切换
const theme = computed(() => (themeStore.mode === 'dark' ? darkTheme : null))

// 全局主题微调：更接近现代干净风格
const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#5b8def',
    primaryColorHover: '#6f9bf2',
    primaryColorPressed: '#4a7ce0',
    borderRadius: '6px',
    fontFamily:
      "'Alibaba PuHuiTi 3.0', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    // naive 内部把 fontFamilyMono 的默认值写死为 'v-mono, …'（由 vfonts 提供）。
    // 项目已不再引入 vfonts，这里显式改成系统等宽栈，避免引用不存在的家族名；
    // 与本项目各处 .woff2/路径等文字用的等宽栈保持一致。
    fontFamilyMono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  },
}
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <n-loading-bar-provider>
      <n-message-provider>
        <n-dialog-provider>
          <n-notification-provider>
            <router-view />
          </n-notification-provider>
        </n-dialog-provider>
      </n-message-provider>
    </n-loading-bar-provider>
  </n-config-provider>
</template>
