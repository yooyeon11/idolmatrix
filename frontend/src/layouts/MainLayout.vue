<script setup lang="ts">
import { onMounted } from 'vue'
import { useAppStore } from '@/stores/app'

const app = useAppStore()

onMounted(() => {
  app.loadFFmpegStatus()
})
</script>

<template>
  <router-view />
</template>
