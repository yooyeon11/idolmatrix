<script setup lang="ts">
import { ref, watch } from 'vue'
import { NModal, NButton, NSpin, NSpace, useMessage } from 'naive-ui'
import { musicVideosApi, type CoverFrameItem } from '@/api/musicVideos'
import { formatDuration } from '@/utils/format'

const props = defineProps<{
  show: boolean
  videoId: number
  /** 当前是否为手动选帧封面（用于显示恢复自动入口） */
  coverManual: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'applied'): void
  (e: 'reset-auto'): void
}>()

const message = useMessage()

const loading = ref(false)
const applying = ref(false)
const frames = ref<CoverFrameItem[]>([])
// 所有已抽过的时间点（含换批），「换一批」时排除避免重复
const seenTimes = ref<number[]>([])
const selected = ref<CoverFrameItem | null>(null)
const resetLoading = ref(false)

function fmt(sec: number) {
  return formatDuration(Math.round(sec))
}

async function loadFrames() {
  loading.value = true
  selected.value = null
  try {
    const res = await musicVideosApi.generateCoverFrames(props.videoId, seenTimes.value)
    frames.value = res.items
    seenTimes.value = [...seenTimes.value, ...res.items.map((i) => i.at)]
  } catch (e) {
    message.error((e as Error).message)
    frames.value = []
  } finally {
    loading.value = false
  }
}

function pick(f: CoverFrameItem) {
  selected.value = selected.value?.name === f.name ? null : f
}

async function apply() {
  if (!selected.value) return
  applying.value = true
  try {
    await musicVideosApi.applyCoverFrame(props.videoId, selected.value.at)
    message.success('封面已更新')
    emit('applied')
    emit('update:show', false)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    applying.value = false
  }
}

async function resetAuto() {
  resetLoading.value = true
  try {
    await musicVideosApi.clearManualCover(props.videoId)
    message.success('已恢复自动封面')
    emit('reset-auto')
    emit('update:show', false)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    resetLoading.value = false
  }
}

watch(
  () => props.show,
  (open) => {
    if (open) {
      frames.value = []
      seenTimes.value = []
      selected.value = null
      void loadFrames()
    }
  },
)
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="选择封面"
    style="width: min(860px, 94vw)"
    :bordered="false"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <div class="cfp">
      <div class="cfp-head">
        <span class="cfp-tip">从视频随机抽取的画面中挑一张作为封面；选中的帧会以原始分辨率重新截取。</span>
        <n-button size="small" :loading="loading" @click="loadFrames">换一批</n-button>
      </div>

      <div v-if="loading" class="cfp-empty">
        <n-spin size="medium" />
        <p>正在抽取画面…</p>
      </div>

      <template v-else>
        <div v-if="frames.length" class="cfp-grid">
          <button
            v-for="f in frames"
            :key="f.name"
            type="button"
            class="cfp-cell"
            :class="{ 'cfp-cell--on': selected?.name === f.name }"
            @click="pick(f)"
          >
            <img
              :src="musicVideosApi.coverFrameUrl(videoId, f.name)"
              :alt="`候选帧 ${fmt(f.at)}`"
              loading="lazy"
            />
            <span class="cfp-time">{{ fmt(f.at) }}</span>
          </button>
        </div>
        <div v-else class="cfp-empty">
          <p>未能抽取候选帧，请点击「换一批」重试</p>
        </div>
      </template>
    </div>

    <template #footer>
      <div class="cfp-foot">
        <n-space align="center">
          <n-button
            v-if="coverManual"
            size="small"
            quaternary
            :loading="resetLoading"
            @click="resetAuto"
          >
            恢复自动封面
          </n-button>
        </n-space>
        <n-space>
          <n-button @click="emit('update:show', false)">取消</n-button>
          <n-button type="primary" :disabled="!selected" :loading="applying" @click="apply">
            使用此画面
          </n-button>
        </n-space>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.cfp {
  min-height: 200px;
}

.cfp-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.cfp-tip {
  font-size: 12px;
  color: var(--n-text-color-disabled, #999);
}

.cfp-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

@media (max-width: 640px) {
  .cfp-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.cfp-cell {
  position: relative;
  border: 2px solid transparent;
  border-radius: 8px;
  overflow: hidden;
  padding: 0;
  background: #000;
  cursor: pointer;
  aspect-ratio: 16 / 9;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.cfp-cell:hover {
  transform: translateY(-2px);
}

.cfp-cell--on {
  border-color: var(--primary-color, #18a058);
}

.cfp-cell img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cfp-time {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 1px 7px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.cfp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 48px 0;
  color: var(--n-text-color-disabled, #999);
  font-size: 13px;
}

.cfp-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
