import { defineStore } from 'pinia'
import { ref } from 'vue'
import { systemApi } from '@/api/system'
import type { FFmpegStatus } from '@/types/models'

export const useAppStore = defineStore('app', () => {
  const sidebarCollapsed = ref(false)
  const ffmpeg = ref<FFmpegStatus | null>(null)
  const ffmpegLoaded = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  async function loadFFmpegStatus() {
    try {
      ffmpeg.value = await systemApi.ffmpeg()
    } catch {
      ffmpeg.value = {
        available: false,
        message: '无法连接后端，请确认后端已启动',
      }
    } finally {
      ffmpegLoaded.value = true
    }
  }

  return { sidebarCollapsed, ffmpeg, ffmpegLoaded, toggleSidebar, loadFFmpegStatus }
})
