<script setup lang="ts">
/**
 * 艺人/组合媒体：HeroUI 图片卡布局复刻
 * 左：横幅大卡（封面 + Footer 操作）
 * 右：组合/艺人头像小卡 + 历史列表
 */
import { computed, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { artistsApi } from '@/api/artists'
import { groupsApi } from '@/api/groups'
import type { EntityImageKind, EntityImageRow } from '@/types/models'
import type { EntityLockMap } from '@/api/entityLocks'
import PhotoSearchCard from '@/components/db/PhotoSearchCard.vue'

const props = defineProps<{
  kind: 'artist' | 'group'
  entityId: number
  avatarPath?: string | null
  bannerPath?: string | null
  locks?: EntityLockMap
  searchQuery?: string
}>()

const emit = defineEmits<{
  (e: 'updated'): void
}>()

const message = useMessage()
const api = computed(() => (props.kind === 'artist' ? artistsApi : groupsApi))

const stamp = ref(Date.now())
const busy = ref<'avatar-up' | 'avatar-rm' | 'banner-up' | 'banner-rm' | number | null>(null)
const error = ref('')

const avatarInput = ref<HTMLInputElement | null>(null)
const bannerInput = ref<HTMLInputElement | null>(null)

/** 右侧历史面板当前看哪种图 */
const histKind = ref<EntityImageKind>('banner')
const historyOpen = ref(false)
const history = ref<{ avatar: EntityImageRow[]; banner: EntityImageRow[] }>({
  avatar: [],
  banner: [],
})
const historyLoading = ref<{ avatar: boolean; banner: boolean }>({ avatar: false, banner: false })

const avatarLocked = computed(() => !!props.locks?.avatar)
const bannerLocked = computed(() => !!props.locks?.banner)

const avatarLabel = computed(() => (props.kind === 'group' ? '组合头像' : '艺人头像'))

const avatarSrc = computed(() => {
  if (!props.avatarPath) return ''
  return `${api.value.avatarUrl(props.entityId)}?t=${stamp.value}`
})
const bannerSrc = computed(() => {
  if (!props.bannerPath) return ''
  return `${api.value.bannerUrl(props.entityId)}?t=${stamp.value}`
})

const activeHistory = computed(() => history.value[histKind.value])
const activeHistoryLoading = computed(() => historyLoading.value[histKind.value])
const activeLocked = computed(() =>
  histKind.value === 'avatar' ? avatarLocked.value : bannerLocked.value,
)

watch(
  () => [props.avatarPath, props.bannerPath, props.entityId] as const,
  () => {
    stamp.value = Date.now()
  },
)

watch(
  () => props.entityId,
  () => {
    void loadHistory('avatar')
    void loadHistory('banner')
  },
  { immediate: true },
)

function bump() {
  stamp.value = Date.now()
  emit('updated')
}

function errMsg(e: unknown, fallback: string) {
  return e instanceof Error ? e.message : fallback
}

async function onUploadAvatar(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || avatarLocked.value) return
  if (!file.type.startsWith('image/')) {
    error.value = '请选择图片文件'
    message.error(error.value)
    return
  }
  busy.value = 'avatar-up'
  error.value = ''
  try {
    await api.value.uploadAvatar(props.entityId, file)
    message.success('头像已上传')
    histKind.value = 'avatar'
    historyOpen.value = true
    await loadHistory('avatar')
    bump()
  } catch (e) {
    error.value = errMsg(e, '上传头像失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

async function onRemoveAvatar() {
  if (avatarLocked.value || !props.avatarPath) return
  busy.value = 'avatar-rm'
  error.value = ''
  try {
    await api.value.removeAvatar(props.entityId)
    message.success('头像已移除')
    await loadHistory('avatar')
    bump()
  } catch (e) {
    error.value = errMsg(e, '移除头像失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

async function onUploadBanner(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || bannerLocked.value) return
  if (!file.type.startsWith('image/')) {
    error.value = '请选择图片文件'
    message.error(error.value)
    return
  }
  busy.value = 'banner-up'
  error.value = ''
  try {
    await api.value.uploadBanner(props.entityId, file)
    message.success('横幅已上传')
    histKind.value = 'banner'
    historyOpen.value = true
    await loadHistory('banner')
    bump()
  } catch (e) {
    error.value = errMsg(e, '上传横幅失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

async function onRemoveBanner() {
  if (bannerLocked.value || !props.bannerPath) return
  busy.value = 'banner-rm'
  error.value = ''
  try {
    await api.value.removeBanner(props.entityId)
    message.success('横幅已移除')
    await loadHistory('banner')
    bump()
  } catch (e) {
    error.value = errMsg(e, '移除横幅失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

async function loadHistory(kind: EntityImageKind) {
  historyLoading.value[kind] = true
  try {
    const r = await api.value.listImages(props.entityId, kind)
    history.value[kind] = r.items || []
  } catch (e) {
    error.value = errMsg(e, '加载历史图片失败')
    message.error(error.value)
  } finally {
    historyLoading.value[kind] = false
  }
}

function showHistory(kind: EntityImageKind) {
  histKind.value = kind
  historyOpen.value = true
  void loadHistory(kind)
}

function pickBannerUpload() {
  if (bannerLocked.value) return
  bannerInput.value?.click()
}

function pickAvatarUpload() {
  if (avatarLocked.value) return
  avatarInput.value?.click()
}

async function promote(img: EntityImageRow) {
  const locked = img.kind === 'avatar' ? avatarLocked.value : bannerLocked.value
  if (locked || img.is_primary) return
  busy.value = img.id
  error.value = ''
  try {
    await api.value.promoteImage(props.entityId, img.id)
    message.success(img.kind === 'avatar' ? '已设为当前头像' : '已设为当前横幅')
    await loadHistory(img.kind)
    bump()
  } catch (e) {
    error.value = errMsg(e, '设为当前失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

async function deleteHist(img: EntityImageRow) {
  const locked = img.kind === 'avatar' ? avatarLocked.value : bannerLocked.value
  if (locked) return
  busy.value = img.id
  error.value = ''
  try {
    await api.value.deleteImage(props.entityId, img.id)
    message.success('已删除历史图片')
    await loadHistory(img.kind)
    bump()
  } catch (e) {
    error.value = errMsg(e, '删除失败')
    message.error(error.value)
  } finally {
    busy.value = null
  }
}

function histUrl(img: EntityImageRow) {
  return `${api.value.imageFileUrl(props.entityId, img.id)}?t=${stamp.value}`
}

function onPhotoApplied() {
  bump()
  void loadHistory('avatar')
  void loadHistory('banner')
}
</script>

<template>
  <div class="emc">
    <div v-if="error" class="emc-error">{{ error }}</div>

    <div class="emc-stage">
      <!-- 左：横幅大卡 -->
      <div class="emc-banner-card" :class="{ locked: bannerLocked, empty: !bannerSrc }">
        <img v-if="bannerSrc" class="emc-banner-img" :src="bannerSrc" alt="" />
        <div v-else class="emc-banner-ph">
          <span>暂无横幅海报</span>
          <small>建议 1.82:1 横图 · 手机横幅 / 首页主舞台</small>
        </div>

        <div class="emc-banner-footer">
          <div class="emc-banner-meta">
            <div class="emc-banner-title">
              横幅海报 · 1.82:1
              <i v-if="bannerLocked" class="emc-lock">🔒</i>
            </div>
            <div class="emc-banner-sub">首页主舞台</div>
          </div>
          <div class="emc-banner-actions">
            <button
              type="button"
              class="emc-pill"
              :disabled="bannerLocked || busy === 'banner-up'"
              @click="pickBannerUpload"
            >
              {{ busy === 'banner-up' ? '上传中…' : bannerSrc ? '换一张' : '上传' }}
            </button>
            <button
              type="button"
              class="emc-pill"
              :disabled="bannerLocked"
              @click="showHistory('banner')"
            >
              历史
            </button>
            <button
              v-if="bannerSrc"
              type="button"
              class="emc-pill ghost"
              :disabled="bannerLocked || busy === 'banner-rm'"
              @click="onRemoveBanner"
            >
              {{ busy === 'banner-rm' ? '…' : '移除' }}
            </button>
          </div>
        </div>

        <input
          ref="bannerInput"
          type="file"
          accept="image/*"
          hidden
          @change="onUploadBanner"
        />
      </div>

      <!-- 右：组合头像（横幅海报同款卡，保持 1:1） -->
      <div class="emc-banner-card emc-media-square" :class="{ locked: avatarLocked, empty: !avatarSrc }">
        <img v-if="avatarSrc" class="emc-banner-img" :src="avatarSrc" alt="" />
        <div v-else class="emc-banner-ph">
          <span>暂无{{ avatarLabel }}</span>
          <small>建议 1:1 方图 · 列表 / 详情页头像</small>
        </div>

        <div class="emc-banner-footer">
          <div class="emc-banner-meta">
            <div class="emc-banner-title">
              {{ avatarLabel }} · 1:1
              <i v-if="avatarLocked" class="emc-lock">🔒</i>
            </div>
            <div class="emc-banner-sub">列表 / 详情页头像</div>
          </div>
          <div class="emc-banner-actions">
            <button
              type="button"
              class="emc-pill"
              :disabled="avatarLocked || busy === 'avatar-up'"
              @click="pickAvatarUpload"
            >
              {{ busy === 'avatar-up' ? '上传中…' : avatarSrc ? '换一张' : '上传' }}
            </button>
            <button type="button" class="emc-pill" :disabled="avatarLocked" @click="showHistory('avatar')">
              历史
            </button>
            <button
              v-if="avatarSrc"
              type="button"
              class="emc-pill ghost"
              :disabled="avatarLocked || busy === 'avatar-rm'"
              @click="onRemoveAvatar"
            >
              {{ busy === 'avatar-rm' ? '…' : '移除' }}
            </button>
          </div>
        </div>

        <input ref="avatarInput" type="file" accept="image/*" hidden @change="onUploadAvatar" />
      </div>
    </div>

    <!-- 历史弹层：各卡片点「历史」打开 -->
    <div v-if="historyOpen" class="emc-hist-overlay" @click.self="historyOpen = false">
      <div class="emc-hist-dialog">
        <div class="emc-hist-dialog-head">
          <b>历史</b>
          <span class="emc-hist-kind">{{ histKind === 'avatar' ? avatarLabel : '横幅海报' }}</span>
          <button type="button" class="emc-hist-close" aria-label="关闭" @click="historyOpen = false">×</button>
        </div>
        <div v-if="activeHistoryLoading" class="emc-hint">加载中…</div>
        <div v-else-if="!activeHistory.length" class="emc-hint">暂无历史</div>
        <ul v-else class="emc-hist-list">
          <li
            v-for="(img, idx) in activeHistory"
            :key="img.id"
            class="emc-hist-row"
            :class="{ primary: img.is_primary }"
          >
            <button
              type="button"
              class="emc-hist-main"
              :disabled="activeLocked || img.is_primary || busy === img.id"
              @click="promote(img)"
            >
              <img
                class="emc-hist-thumb"
                :class="{ wide: histKind === 'banner' }"
                :src="histUrl(img)"
                alt=""
                loading="lazy"
              />
              <div class="emc-hist-text">
                <div class="emc-hist-title">
                  历史 {{ idx + 1 }}
                  <span v-if="img.is_primary" class="emc-badge">当前</span>
                </div>
                <div class="emc-hist-sub">
                  {{ img.is_primary ? '正在使用' : busy === img.id ? '切换中…' : '设为当前' }}
                </div>
              </div>
              <span class="emc-chev" aria-hidden="true">›</span>
            </button>
            <button
              type="button"
              class="emc-hist-del"
              :disabled="activeLocked || busy === img.id"
              title="删除"
              @click="deleteHist(img)"
            >
              删
            </button>
          </li>
        </ul>
      </div>
    </div>

    <div class="emc-search">
      <div class="emc-search-title">外部照片搜索</div>
      <PhotoSearchCard
        :kind="kind"
        :entity-id="entityId"
        :initial-query="searchQuery"
        @applied="onPhotoApplied"
      />
    </div>
  </div>
</template>

<style scoped>
.emc {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.emc-error {
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(208, 48, 80, 0.1);
  color: var(--dh-bad, #d03050);
  font-size: 12px;
}

.emc-stage {
  display: grid;
  /* 横幅 1.82:1 与头像 1:1 等比同高：两卡宽度比取 1.82fr : 1fr 时，
     「横幅高 = 宽/1.82」与「头像高 = 宽/1」恒等 → 无论容器多宽，两卡永远等高。 */
  grid-template-columns: 1.82fr 1fr;
  gap: 14px;
  align-items: start;
}

/* —— 横幅大卡（HeroUI Card + 全铺图 + Footer） —— */
.emc-banner-card {
  position: relative;
  display: flex;
  flex-direction: column;
  aspect-ratio: 1.82 / 1;
  border-radius: 20px;
  overflow: hidden;
  background: var(--sa-subtle, #eef0f4);
  box-shadow:
    0 2px 8px rgba(0, 0, 0, 0.06),
    0 14px 28px rgba(0, 0, 0, 0.06);
}
/* 头像卡：与横幅同高（宽度比 1 : 1.82），但自身保持 1:1，不按 1.82:1 拉伸 */
.emc-banner-card.emc-media-square {
  aspect-ratio: 1 / 1;
}
.emc-banner-card.locked {
  opacity: 0.85;
}
.emc-banner-card.empty {
  border: 1.5px dashed var(--sa-border, rgba(15, 23, 42, 0.12));
  box-shadow: none;
}

.emc-banner-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  pointer-events: none;
  user-select: none;
}

.emc-banner-ph {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
  color: var(--sa-text-tertiary, #77818f);
  z-index: 1;
}
.emc-banner-ph span {
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-text-secondary, #5b6470);
}
.emc-banner-ph small {
  font-size: 12px;
}

.emc-banner-footer {
  position: relative;
  z-index: 2;
  margin-top: auto;
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 14px 16px;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.55) 0%, rgba(0, 0, 0, 0.18) 55%, transparent 100%);
  color: #fff;
}
.emc-banner-card.empty .emc-banner-footer {
  background: transparent;
  color: var(--sa-text-primary, #1f2329);
  border-top: 1px solid var(--sa-border-subtle, rgba(15, 23, 42, 0.06));
}

.emc-banner-title {
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
}
.emc-banner-sub {
  font-size: 12px;
  opacity: 0.7;
  margin-top: 2px;
}

.emc-banner-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.emc-pill {
  height: 30px;
  padding: 0 12px;
  border: none;
  border-radius: 999px;
  background: #fff;
  color: #111;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
}
.emc-pill:hover:not(:disabled) {
  background: #f4f4f5;
}
.emc-pill:disabled {
  opacity: 0.45;
  cursor: default;
}
.emc-pill.ghost {
  background: rgba(255, 255, 255, 0.18);
  color: #fff;
  box-shadow: none;
}
.emc-banner-card.empty .emc-pill.ghost {
  background: var(--sa-subtle, #eef0f4);
  color: var(--sa-text-secondary, #5b6470);
}

/* —— 右侧 —— */
.emc-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.emc-avatar-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-radius: 16px;
  background: var(--sa-elevated, #fff);
  border: 1px solid var(--sa-border-subtle, rgba(15, 23, 42, 0.06));
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
}
.emc-avatar-card.locked {
  opacity: 0.85;
  background: var(--sa-subtle, #eef0f4);
}

.emc-avatar-hit {
  width: 112px;
  height: 112px;
  padding: 0;
  border: none;
  border-radius: 16px;
  overflow: hidden;
  background: var(--sa-subtle, #eef0f4);
  cursor: pointer;
  flex-shrink: 0;
}
.emc-avatar-hit img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.emc-avatar-hit:disabled {
  cursor: not-allowed;
}
.emc-avatar-ph {
  display: flex;
  width: 100%;
  height: 100%;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-tertiary, #77818f);
}

.emc-avatar-cap {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--sa-text-primary, #1f2329);
}
.emc-avatar-cap b {
  font-weight: 600;
}

.emc-avatar-btns {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
}

.emc-link {
  border: none;
  background: none;
  padding: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--sa-accent, #0485f7);
  cursor: pointer;
  font-family: inherit;
}
.emc-link:disabled {
  opacity: 0.45;
  cursor: default;
}
.emc-link.danger {
  color: var(--sa-text-tertiary, #77818f);
}
.emc-link.danger:hover:not(:disabled) {
  color: var(--dh-bad, #d03050);
}

.emc-lock {
  font-style: normal;
  font-size: 11px;
}

/* 历史列 */
.emc-hist-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding: 10px;
  border-radius: 16px;
  background: var(--sa-elevated, #fff);
  border: 1px solid var(--sa-border-subtle, rgba(15, 23, 42, 0.06));
}

.emc-hist-tabs {
  display: flex;
  gap: 4px;
  padding: 2px;
  border-radius: 10px;
  background: var(--sa-subtle, #eef0f4);
}
.emc-tab {
  flex: 1;
  height: 28px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--sa-text-secondary, #5b6470);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
}
.emc-tab.on {
  background: var(--sa-elevated, #fff);
  color: var(--sa-text-primary, #1f2329);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.emc-hint {
  padding: 12px 4px;
  font-size: 12px;
  color: var(--sa-text-tertiary, #77818f);
  text-align: center;
}

.emc-hist-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 280px;
  overflow: auto;
}

.emc-hist-row {
  display: flex;
  align-items: stretch;
  gap: 4px;
  border-radius: 12px;
  border: 1px solid transparent;
  background: transparent;
}
.emc-hist-row.primary {
  border-color: var(--sa-accent-border, rgba(109, 92, 224, 0.35));
  background: var(--sa-accent-subtle, rgba(109, 92, 224, 0.08));
}

.emc-hist-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px 6px 6px;
  border: none;
  border-radius: 12px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-family: inherit;
  color: inherit;
}
.emc-hist-main:disabled {
  cursor: default;
}
.emc-hist-main:hover:not(:disabled) {
  background: var(--sa-hover, #e6e9f0);
}

.emc-hist-thumb {
  width: 48px;
  height: 48px;
  border-radius: 10px;
  object-fit: cover;
  flex-shrink: 0;
  background: var(--sa-subtle, #eef0f4);
}
.emc-hist-thumb.wide {
  width: 64px;
  height: auto;
  aspect-ratio: 1.82 / 1;
  border-radius: 8px;
}

.emc-hist-text {
  flex: 1;
  min-width: 0;
}
.emc-hist-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-primary, #1f2329);
  display: flex;
  align-items: center;
  gap: 6px;
}
.emc-hist-sub {
  font-size: 11px;
  color: var(--sa-text-tertiary, #77818f);
  margin-top: 2px;
}
.emc-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--sa-accent, #0485f7);
  color: #fff;
}
.emc-chev {
  color: var(--sa-text-tertiary, #77818f);
  font-size: 18px;
  line-height: 1;
}

.emc-hist-del {
  border: none;
  background: transparent;
  color: var(--sa-text-tertiary, #77818f);
  font-size: 11px;
  font-weight: 700;
  padding: 0 8px;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
}
.emc-hist-del:hover:not(:disabled) {
  color: var(--dh-bad, #d03050);
  background: rgba(208, 48, 80, 0.08);
}
.emc-hist-del:disabled {
  opacity: 0.4;
  cursor: default;
}

.emc-search {
  padding: 12px 14px;
  border: 1px solid var(--sa-border-subtle, rgba(15, 23, 42, 0.06));
  border-radius: 14px;
  background: var(--sa-bg, #f7f8fa);
}
.emc-search-title {
  font-size: 12px;
  font-weight: 700;
  margin-bottom: 8px;
  color: var(--sa-text-secondary, #5b6470);
}

/* —— 历史弹层（各卡片「历史」按钮打开） —— */
.emc-hist-overlay {
  position: fixed;
  inset: 0;
  z-index: 300;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(0, 0, 0, 0.45);
}
.emc-hist-dialog {
  width: min(520px, 100%);
  max-height: min(80vh, 640px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border-radius: 16px;
  background: var(--sa-elevated, #fff);
  border: 1px solid var(--sa-border-subtle, rgba(15, 23, 42, 0.06));
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.emc-hist-dialog-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.emc-hist-dialog-head b {
  font-size: 14px;
  color: var(--sa-text-primary, #1f2329);
}
.emc-hist-kind {
  font-size: 12px;
  color: var(--sa-text-secondary, #5b6470);
}
.emc-hist-close {
  margin-left: auto;
  border: none;
  background: none;
  padding: 0 4px;
  font-size: 20px;
  line-height: 1;
  color: var(--sa-text-tertiary, #77818f);
  cursor: pointer;
}
.emc-hist-close:hover {
  color: var(--sa-text-primary, #1f2329);
}

@media (max-width: 860px) {
  .emc-stage {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .emc-hist-list {
    max-height: 220px;
  }
}
</style>
