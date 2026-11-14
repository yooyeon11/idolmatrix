<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MusicVideo, PlaybackSession } from '@/types/models'
import { CAMERA_ANGLE_LABEL, QUALITY_LABEL, VIDEO_TYPE_LABEL } from '@/types/models'
import { formatBitrate, formatDate, formatDuration, formatFileSize } from '@/utils/format'
import { songPath } from '@/utils/routes'

const props = defineProps<{
  video: MusicVideo
  session: PlaybackSession | null
  artists: CastItem[]
  /** 视频标题：由播放页传入，档案舱顶部唯一展示位（播放器下方不再重复渲染） */
  title: string
  /** 中文简介（主展示）；为空时回退到原文简介 */
  chineseDescription?: string | null
  /** 原文简介（来源 json 原文）；点击中文简介展开 */
  description?: string | null
  /** 是否播放「波浪入场」动画：切场次/机位时不重放（false），仅在首次进入/切 Tab 播 */
  wave?: boolean
}>()
const emit = defineEmits<{ (e: 'go-entity', item: { uid?: string; kind: 'artist' | 'group' }): void }>()

// 艺人/组合双联胶囊（自标题块移入档案舱）
interface CastItem {
  key: string
  id: number | null
  uid: string
  kind: 'artist' | 'group'
  name: string
  index: number
  avatar: string | null
}
const palette = [
  'linear-gradient(135deg, #2a2140, #15151b)',
  'linear-gradient(135deg, #22242a, #15151b)',
  'linear-gradient(135deg, #2a2220, #15151b)',
  'linear-gradient(135deg, #1e2a2e, #15151b)',
  'linear-gradient(135deg, #241e2e, #15151b)',
]
function avatarStyle(i: number) {
  return { background: palette[i % palette.length] }
}
function avatarUrl(item: CastItem) {
  if (item.id === null) return ''
  // w=96：内联小头像走缩放变体；带参数同时让 URL 与旧缓存区分开
  return item.kind === 'group'
    ? `/api/groups/${item.id}/avatar?w=96`
    : `/api/artists/${item.id}/avatar?w=96`
}
function initialOf(name?: string | null) {
  return (name || '?').trim().charAt(0).toUpperCase()
}

/** 原文简介里的 {BR} 是入库管线写的换行占位 */
function plainDesc(raw?: string | null): string {
  return (raw || '').replace(/\{BR\}/g, '\n').trim()
}
const chineseText = computed(() => plainDesc(props.chineseDescription))
const originalText = computed(() => plainDesc(props.description))
/** 主展示：中文简介优先；没有中文才退回原文 */
const descriptionText = computed(
  () => chineseText.value || originalText.value || '暂无简介，可在编辑中补充',
)
/** 有原文且与主展示不同 → 可点击展开原文 */
const hasOriginal = computed(
  () => !!originalText.value && originalText.value !== descriptionText.value,
)
const descOpen = ref(false)
function toggleDesc() {
  if (hasOriginal.value) descOpen.value = !descOpen.value
}
// 换视频 / 换机位时收起，避免把上一支的原文状态带过来
watch(
  () => props.video?.id,
  () => {
    descOpen.value = false
  },
)

type RowPart = { text: string; to?: string }
interface InfoRow {
  label: string
  value?: string
  parts?: RowPart[]
  /** 等宽字体（日期 / 规格 / 编码这类） */
  mono?: boolean
}

// 档案项（与详情页既有 metaFields 同口径，收纳进右侧档案舱的小卡里；
// 艺人/组合已由顶部双联胶囊承载，分辨率/编码由格式卡承载，不再重复出卡）
const rows = computed<InfoRow[]>(() => {
  const v = props.video
  const list: InfoRow[] = []
  const types = v.video_types && v.video_types.length ? v.video_types : v.video_type ? [v.video_type] : []
  list.push({ label: '视频类型', value: types.map((t) => VIDEO_TYPE_LABEL[t] || t).join(' / ') || '--' })
  const songParts: RowPart[] = (v.songs || [])
    .map((s) => ({ text: s.chinese_name || s.name, to: s.uid ? songPath(s.uid) : undefined }))
    .filter((p) => p.text)
  if (songParts.length) {
    list.push({ label: '曲目', parts: songParts })
  } else if (v.song_chinese_name || v.song_name) {
    list.push({ label: '曲目', value: v.song_chinese_name || v.song_name || '' })
  }
  if (v.event_name) list.push({ label: '活动 / 节目', value: v.event_name })
  // 演出日期紧跟活动，其后是源站上传（发布）日期
  if (v.performance_date) list.push({ label: '演出日期', value: formatDate(v.performance_date), mono: true })
  if (v.published_date) list.push({ label: '发布日期', value: formatDate(v.published_date), mono: true })
  // 机位：中文名 + 英文原值（对齐中文简介里的写法）；画幅由宽高派生，附在后面
  if (v.camera_angle) {
    const label = CAMERA_ANGLE_LABEL[v.camera_angle] || v.camera_angle
    const portrait = (v.height || 0) > (v.width || 0) && (v.width || 0) > 0
    list.push({
      label: '机位',
      value: `${label}（${v.camera_angle}）${portrait ? ' · 竖版' : ' · 横版'}`,
    })
  }
  if (v.session_label) list.push({ label: '场次', value: v.session_label, mono: true })
  if (v.duration) list.push({ label: '时长', value: formatDuration(v.duration), mono: true })
  if (v.original_uploader) {
    list.push({
      label: '频道 / 博主',
      parts: [{ text: v.original_uploader, to: `/uploaders/${encodeURIComponent(v.original_uploader)}` }],
    })
  }
  if (v.release_date) list.push({ label: '发行日期', value: formatDate(v.release_date), mono: true })
  if (v.source_platform) {
    list.push({ label: '来源平台', parts: [{ text: v.source_platform, to: v.source_url || undefined }] })
  }
  return list
})

// 母带规格：分辨率 · 编码 · 体积
const formatCard = computed(() => {
  const v = props.video
  const items: string[] = []
  if (v.width) items.push(`${v.width}×${v.height}`)
  if (v.video_codec) items.push(v.video_codec.toUpperCase())
  if (v.audio_codec) items.push(v.audio_codec.toUpperCase())
  if (v.file_size) items.push(formatFileSize(v.file_size))
  return items
})

const playbackSpec = computed(() => {
  const s = props.session
  if (!s) return ''
  const parts: string[] = []
  parts.push(playModeLabel(s.play_mode))
  if (s.quality && s.quality !== 'original') parts.push(QUALITY_LABEL[s.quality] || s.quality.toUpperCase())
  else parts.push('原画')
  if (s.video_codec) parts.push(s.video_codec)
  if (s.video_bitrate) parts.push(formatBitrate(s.video_bitrate))
  return parts.join(' · ')
})

function playModeLabel(mode: string) {
  if (mode === 'direct_play') return '原画直连'
  if (mode === 'direct_stream') return '原画直出'
  return '转码'
}

// 文件名复制（成功后 ✓ 1.8s）
const copied = ref(false)
let copyTimer: number | null = null
async function copyFileName() {
  const name = props.video.file_name
  if (!name) return
  let ok = false
  try {
    await navigator.clipboard.writeText(name)
    ok = true
  } catch {
    // 剪贴板 API 不可用（非安全上下文/权限拒绝）：回退隐藏 textarea
    try {
      const ta = document.createElement('textarea')
      ta.value = name
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      ok = document.execCommand('copy')
      document.body.removeChild(ta)
    } catch {
      ok = false
    }
  }
  if (!ok) return
  copied.value = true
  if (copyTimer !== null) window.clearTimeout(copyTimer)
  copyTimer = window.setTimeout(() => {
    copied.value = false
    copyTimer = null
  }, 1800)
}
</script>

<template>
  <div class="dossier">
    <!-- 档案舱抬头：标题 + 简介（播放页播放器下方原大标题/简介段落已移到这里） -->
    <header class="dossier-head">
      <div class="dossier-title-row">
        <h2 class="dossier-title">{{ title }}</h2>
        <div v-if="$slots.actions" class="dossier-actions">
          <slot name="actions" />
        </div>
      </div>
      <div
        class="dossier-desc-block"
        :class="{ 'is-expandable': hasOriginal }"
        :role="hasOriginal ? 'button' : undefined"
        :tabindex="hasOriginal ? 0 : undefined"
        :aria-expanded="hasOriginal ? descOpen : undefined"
        :title="hasOriginal ? (descOpen ? '收起原文简介' : '查看原文简介') : undefined"
        @click="toggleDesc"
        @keydown.enter.prevent="toggleDesc"
        @keydown.space.prevent="toggleDesc"
      >
        <p class="dossier-desc">
          {{ descriptionText }}<span
            v-if="hasOriginal"
            class="desc-arrow"
            :class="{ 'is-open': descOpen }"
            aria-hidden="true"
          >▾</span>
        </p>
      </div>
      <div v-if="descOpen && hasOriginal" class="dossier-desc-orig">
        <p class="dossier-desc is-orig">{{ originalText }}</p>
      </div>
    </header>

    <!-- 单列细线账本（零套娃小方框） -->
    <div class="ds-ledger">
      <!-- 艺人 / 组合：内联文字 + 小圆头像，仍是可点入口 -->
      <div v-if="artists.length" class="ds-row">
        <span class="ds-label">艺人 / 组合</span>
        <span class="ds-value ds-cast">
          <template v-for="(a, i) in artists" :key="a.key">
            <span v-if="i" class="ds-cast-sep">·</span>
            <button
              type="button"
              class="ds-cast-item"
              :class="{ 'is-link': !!a.uid }"
              :disabled="!a.uid"
              @click="a.uid && emit('go-entity', a)"
            >
              <span class="ds-cast-av" :style="avatarStyle(a.index)">
                <img v-if="a.avatar && avatarUrl(a)" :src="avatarUrl(a)" alt="" loading="lazy" />
                <template v-else>{{ initialOf(a.name) }}</template>
              </span>
              <span class="ds-cast-name">{{ a.name }}</span>
            </button>
          </template>
        </span>
      </div>

      <div
        v-for="(row, i) in rows"
        :key="row.label"
        class="ds-row"
        :class="{ 'dock-wave-item': wave !== false }"
        :style="{ animationDelay: `${i * 22}ms` }"
      >
        <span class="ds-label">{{ row.label }}</span>
        <span class="ds-value">
          <template v-if="row.parts">
            <template v-for="(part, pi) in row.parts" :key="pi">
              <a
                v-if="part.to && part.to.startsWith('http')"
                :href="part.to"
                target="_blank"
                rel="noopener noreferrer"
                class="ds-link"
              >{{ part.text }}</a>
              <router-link v-else-if="part.to" :to="part.to" class="ds-link">{{ part.text }}</router-link>
              <span v-else>{{ part.text }}</span>
              <span v-if="pi < row.parts.length - 1" class="ds-sep">·</span>
            </template>
          </template>
          <span v-else :class="{ 'ds-mono': row.mono }">{{ row.value }}</span>
        </span>
      </div>

      <div v-if="formatCard.length" class="ds-row">
        <span class="ds-label">母带规格</span>
        <span class="ds-value ds-mono ds-ok">{{ formatCard.join(' · ') }}</span>
      </div>
      <div v-if="video.bitrate" class="ds-row">
        <span class="ds-label">码率</span>
        <span class="ds-value ds-mono">{{ formatBitrate(video.bitrate) }}</span>
      </div>
      <div v-if="playbackSpec" class="ds-row">
        <span class="ds-label">播放规格</span>
        <span class="ds-value ds-mono ds-ok">{{ playbackSpec }}</span>
      </div>
    </div>

    <!-- 原始文件名：细边框 + 复制 -->
    <div v-if="video.file_name" class="ds-file" :class="{ 'dock-wave-item': wave !== false }">
      <div class="ds-file-head">
        <span class="ds-label">原始文件名</span>
        <button type="button" class="ds-copy" :class="{ 'is-ok': copied }" @click="copyFileName">
          {{ copied ? '✓ 已复制' : '复制' }}
        </button>
      </div>
      <div class="ds-file-name">{{ video.file_name }}</div>
    </div>
  </div>
</template>

<style scoped>
/* ===== 方案 I 档案页：高定出版物版权页式单列细线账本 ===== */
.dossier {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
/* 档案下沉到播放器下方后，桌面宽度从 ~320px 变成 ~900px：
   简介与字段账本并排，避免单列被拉长、右半边大片留白。 */
@media (min-width: 1024px) {
  /* 档案下沉后是播放器下方的主标题，15px 偏小。
     基础规则在本段之后，必须提高特异性才不会被覆盖。 */
  .dossier .dossier-title {
    font-size: 19px;
    line-height: 1.45;
  }
}

/* 标题行：标题 + 右侧操作（收藏 / 评分 / 编辑，由播放页通过 #actions 传入） */
.dossier-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  min-width: 0;
}
.dossier-title-row .dossier-title {
  flex: 1 1 auto;
  min-width: 0;
}
.dossier-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 6px;
}

/* 档案抬头：标题是播放页唯一标题展示位 */
.dossier-head {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  margin-top: 4px;
}
.dossier-title {
  margin: 0;
  /* 与浏览页卡片标题 .gc-title 同一字体口径（继承站内正文字体） */
  font-family: inherit;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.5;
  letter-spacing: normal;
  color: var(--sa-text-primary);
  word-break: break-word;
}
.dossier-desc-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  border-radius: 8px;
}
.dossier-desc-block.is-expandable {
  cursor: pointer;
}
.dossier-desc-block.is-expandable:hover .dossier-desc {
  color: var(--sa-accent);
}
.dossier-desc-block.is-expandable:focus-visible {
  outline: 1px solid var(--sa-border);
  outline-offset: 4px;
}
.dossier-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--sa-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  transition: color 0.15s ease;
}
.desc-arrow {
  margin-left: 6px;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  display: inline-block;
  transition: transform 0.2s ease;
}
.desc-arrow.is-open {
  transform: rotate(180deg);
}
.dossier-desc-orig {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--sa-border-subtle);
}
.dossier-desc.is-orig {
  max-height: 42vh;
  overflow: auto;
  overscroll-behavior: contain;
  font-size: 13px;
  color: var(--sa-text-tertiary);
}

.ds-ledger {
  border-top: 1px solid var(--sa-border-subtle);
  border-bottom: 1px solid var(--sa-border-subtle);
}

.ds-row {
  display: flex;
  align-items: baseline;
  justify-content: flex-start;
  gap: 20px;
  padding: 9px 0;
  border-top: 1px solid var(--sa-border-subtle);
}
.ds-row:first-child {
  border-top: none;
}

.ds-label {
  /* 一行一条信息：标签固定一列，值在右侧自然展开 */
  flex: 0 0 112px;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0;
  color: var(--sa-text-secondary);
  white-space: nowrap;
}

.ds-value {
  flex: 1 1 auto;
  font-size: 13px;
  line-height: 1.6;
  color: var(--sa-text-primary);
  text-align: left;
  min-width: 0;
  word-break: break-word;
}
.ds-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}
.ds-ok {
  /* 母带规格/播放规格不再用成功色高亮，与普通字段同色 */
  color: var(--sa-text-primary);
}
.ds-sep {
  margin: 0 5px;
  color: var(--sa-text-tertiary);
}
.ds-link {
  color: var(--sa-text-primary);
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: var(--sa-border);
  transition: text-decoration-color 0.2s ease;
}
.ds-link:hover {
  text-decoration-color: var(--sa-text-primary);
}

/* 艺人 / 组合：内联，不用胶囊；值一列改为左对齐（一行一条信息） */
.ds-cast {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
}
.ds-cast-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  color: var(--sa-text-primary);
  cursor: default;
}
.ds-cast-item.is-link {
  cursor: pointer;
}
.ds-cast-item.is-link:hover .ds-cast-name {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.ds-cast-av {
  width: 18px;
  height: 18px;
  border-radius: 999px;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 9px;
  font-weight: 700;
  color: rgba(255, 255, 255, 0.85);
}
.ds-cast-av img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.ds-cast-name {
  font-size: 13px;
}
.ds-cast-sep {
  color: var(--sa-text-tertiary);
}

/* 原始文件名 */
.ds-file {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ds-file-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.ds-copy {
  padding: 0;
  border: none;
  background: none;
  font-family: inherit;
  font-size: 12px;
  color: var(--sa-text-secondary);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
  transition: color 0.2s ease;
}
.ds-copy:hover {
  color: var(--sa-text-primary);
}
.ds-copy.is-ok {
  color: var(--sa-success, #34d399);
  text-decoration: none;
}
.ds-file-name {
  padding: 10px 12px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-subtle);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.65;
  color: var(--sa-text-secondary);
  word-break: break-all;
  user-select: all;
}
</style>
