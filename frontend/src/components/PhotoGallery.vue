<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { photosApi } from '@/api/photos'
import type {
  PhotoAnalysis,
  PhotoAuthorOption,
  PhotoFeedFilter,
  PhotoFeedItem,
  PhotoItem,
  PhotoOwnerType,
  PhotoSection,
  PhotoTimelineDay,
} from '@/types/models'
import { formatDateTime, formatPhotoDay, formatPhotoMonth } from '@/utils/format'
import type { DropdownOption } from 'naive-ui'
import {
  BookmarkFilled,
  BookmarkOutlined,
  CalendarOutlined,
  CloseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  ImageOutlined,
  InfoOutlined,
  ListOutlined,
  OpenInNewOutlined,
  PersonOutlined,
  PlayArrowOutlined,
  RobotOutlined,
  SortOutlined,
} from '@/components/icons'
import PhotoCollectPicker from '@/components/PhotoCollectPicker.vue'
import CropDialog from '@/components/CropDialog.vue'
import SaPagination from '@/components/SaPagination.vue'
import SaSelect from '@/components/SaSelect.vue'
import SaDatePicker from '@/components/SaDatePicker.vue'

const props = defineProps<{
  ownerType?: PhotoOwnerType
  ownerId?: number
  section?: PhotoSection
  collectionId?: number | null
}>()

const emit = defineEmits<{
  portraitApplied: [payload: { kind: 'avatar' | 'banner' }]
}>()

const loading = ref(false)
const items = ref<PhotoFeedItem[]>([])
const total = ref(0)
const pageNum = ref(1)
const pageSize = 24
const sourceCount = ref(0)
const photoCount = ref(0)
const lastScannedAt = ref<string | null>(null)
const readonly = ref(false)
const timeline = ref<PhotoTimelineDay[]>([])
const selectedDate = ref('all')
const searchText = ref('')
const searchApplied = ref('')
const selectedAuthor = ref('')
const mobileInfoOpen = ref(false)
const isNarrow = ref(false)
const errorText = ref('')

const POST_PREVIEW_LIMIT = 6

const lightboxOpen = ref(false)
const lightboxIndex = ref(0)
const lightboxPhotos = ref<PhotoItem[]>([])
// 帖子大卡片：点击帖子卡片弹出，图片全部平铺展示（取代原来的「+N」藏图）
const postDetailItem = ref<PhotoFeedItem | null>(null)
const postDetailPhotos = computed(() => postDetailItem.value?.photos ?? [])
const pickerOpen = ref(false)
const memberships = ref<Record<number, number[]>>({})
const authorOptions = ref<PhotoAuthorOption[]>([])
const filterAnalyzed = ref('')
const message = useMessage()

const deleting = ref(false)
const editing = ref(false)
const savingEdit = ref(false)
const editCaption = ref('')
const cropKind = ref<'avatar' | 'banner' | null>(null)
const cropSaving = ref(false)
const editTags = ref<string[]>([])
const editTagInput = ref('')

const isCollection = computed(() => props.collectionId != null && props.collectionId > 0)
const isWall = computed(() => isCollection.value || props.section === 'wall')
const galleryKind = computed(() => (isCollection.value ? 'collection' : props.section || 'wall'))

type PhotoViewMode = 'post' | 'month' | 'wall'
const VIEW_MODES: PhotoViewMode[] = ['post', 'month', 'wall']
const SORT_KEYS = ['date_desc', 'date_asc', 'name_asc', 'name_desc']

function readStoredPref(key: string, fallback: string, valid: string[]) {
  const raw = localStorage.getItem(key)
  return raw && valid.includes(raw) ? raw : fallback
}

// 旧版全局偏好迁移（kpml_photo_wall_view 的 flat 即现在的 wall）
const legacyView = readStoredPref('kpml_photo_wall_view', 'month', ['month', 'flat'])
const legacySort = readStoredPref('kpml_photo_wall_sort', '', SORT_KEYS)
const viewMode = ref<PhotoViewMode>(
  readStoredPref(
    `kpml_photo_view_${galleryKind.value}`,
    isWall.value ? (legacyView === 'flat' ? 'wall' : 'month') : 'post',
    VIEW_MODES,
  ) as PhotoViewMode,
)
const sortKey = ref(
  readStoredPref(`kpml_photo_sort_${galleryKind.value}`, legacySort || 'date_desc', SORT_KEYS),
)

// 切换分区时按各自偏好恢复视图与排序
watch(galleryKind, (kind) => {
  const defaultView: PhotoViewMode =
    kind === 'official' || kind === 'fan' ? 'post' : legacyView === 'flat' ? 'wall' : 'month'
  viewMode.value = readStoredPref(
    `kpml_photo_view_${kind}`,
    defaultView,
    VIEW_MODES,
  ) as PhotoViewMode
  sortKey.value = readStoredPref(`kpml_photo_sort_${kind}`, legacySort || 'date_desc', SORT_KEYS)
})
const canUpload = computed(
  () =>
    !isCollection.value &&
    !readonly.value &&
    props.section === 'wall' &&
    !!props.ownerType &&
    !!props.ownerId,
)
function isRemotePhoto(photo: PhotoItem | null) {
  return photo?.provider === 'mtphotos'
}
const uploading = ref(false)

const currentPhoto = computed(() => lightboxPhotos.value[lightboxIndex.value] || null)
const lightboxCount = computed(() => lightboxPhotos.value.length)
const currentAnalysis = computed(() => currentPhoto.value?.analysis || null)

// ===== 沉浸模式 + 缩放（上限 1:1 原图像素） =====
const immersive = ref(false)
const zoomScale = ref(1)
const zoomX = ref(0)
const zoomY = ref(0)
const origLoaded = ref(false)
const origFailed = ref(false)
// 缩略图取不到（如源文件已删但缓存没命中）→ 才退到原图；两路失败各自记，不要互相顶
const thumbFailed = ref(false)
const zoomHint = ref('')
const stageEl = ref<HTMLElement | null>(null)
const mediaEl = ref<HTMLImageElement | null>(null)
let zoomHintTimer: number | null = null

const isVideoPhoto = computed(() => currentPhoto.value?.media_kind === 'video')
// 1:1 = 图片自然宽度 / 当前适配显示宽度；图片本身不大于视口时无需放大
const maxZoom = computed(() => {
  const img = mediaEl.value
  if (!img || !img.naturalWidth || !img.offsetWidth) return 1
  return Math.min(8, Math.max(1, img.naturalWidth / img.offsetWidth))
})

// 缩略图与原图共用同一变换，放大过程中两层保持重合
const zoomStyle = computed(() => {
  if (isVideoPhoto.value) return undefined
  return {
    transform: `translate(${zoomX.value}px, ${zoomY.value}px) scale(${zoomScale.value})`,
    transformOrigin: 'center center',
    willChange: 'transform',
  }
})

// 渐进加载：先显示已缓存的 h220 缩略图（与原图同比例，布局不跳动），
// 原图在后台预载完成后无缝换源。
// ⚠ 原图预载失败时**必须留在缩略图上**：旧版是 `origFailed ? fileUrl(p) : …`，
// 而 fileUrl 正是刚刚失败的那条 URL → 好好的缩略图被换成裂图（2026-09-21 修）。
// 只有缩略图自己取不到（thumbFailed）才值得改用原图。
const displaySrc = computed(() => {
  const p = currentPhoto.value
  if (!p) return ''
  if (thumbFailed.value) return fileUrl(p)
  if (origLoaded.value && !origFailed.value) return fileUrl(p)
  return thumbUrl(p)
})

// 当前显示的是哪一路，就标记哪一路失败（缩略图失败 → 换原图；原图失败 → 保持缩略图）
function onMediaError() {
  if (!currentPhoto.value) return
  if (thumbFailed.value) origFailed.value = true
  else thumbFailed.value = true
}

function onMediaLoad() {
  const p = currentPhoto.value
  if (!p || origLoaded.value || origFailed.value) return
  const probe = new Image()
  probe.onload = () => {
    if (currentPhoto.value?.id === p.id) origLoaded.value = true
  }
  probe.onerror = () => {
    if (currentPhoto.value?.id === p.id) origFailed.value = true
  }
  probe.src = fileUrl(p)
}

function resetZoom() {
  zoomScale.value = 1
  zoomX.value = 0
  zoomY.value = 0
}

function showZoomHint() {
  zoomHint.value = zoomScale.value > 1.02 ? `${zoomScale.value.toFixed(1)}x` : ''
  if (zoomHintTimer !== null) window.clearTimeout(zoomHintTimer)
  zoomHintTimer = window.setTimeout(() => {
    zoomHint.value = ''
    zoomHintTimer = null
  }, 900)
}

// 以 (clientX, clientY) 为不动点缩放到 next；平移始终夹在「图片不离开舞台」范围内
function zoomAt(clientX: number, clientY: number, next: number) {
  const img = mediaEl.value
  const stage = stageEl.value
  if (!img || !stage) return
  const s0 = zoomScale.value
  const s = Math.min(maxZoom.value, Math.max(1, next))
  const stR = stage.getBoundingClientRect()
  const bw = img.offsetWidth
  const bh = img.offsetHeight
  const rx = clientX - (stR.x + (stR.width - bw) / 2 + bw / 2)
  const ry = clientY - (stR.y + (stR.height - bh) / 2 + bh / 2)
  const rangeX = Math.max(0, (bw * s - stR.width) / 2)
  const rangeY = Math.max(0, (bh * s - stR.height) / 2)
  zoomX.value = Math.min(rangeX, Math.max(-rangeX, zoomX.value + rx * (s0 - s)))
  zoomY.value = Math.min(rangeY, Math.max(-rangeY, zoomY.value + ry * (s0 - s)))
  zoomScale.value = s
  showZoomHint()
}

function panBy(dx: number, dy: number) {
  const img = mediaEl.value
  const stage = stageEl.value
  if (!img || !stage) return
  const s = zoomScale.value
  const stR = stage.getBoundingClientRect()
  const rangeX = Math.max(0, (img.offsetWidth * s - stR.width) / 2)
  const rangeY = Math.max(0, (img.offsetHeight * s - stR.height) / 2)
  zoomX.value = Math.min(rangeX, Math.max(-rangeX, zoomX.value + dx))
  zoomY.value = Math.min(rangeY, Math.max(-rangeY, zoomY.value + dy))
}

function onWheel(e: WheelEvent) {
  if (isVideoPhoto.value || editing.value) return
  const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
  zoomAt(e.clientX, e.clientY, zoomScale.value * factor)
}

function toggleImmersive() {
  immersive.value = !immersive.value
  if (immersive.value) {
    pickerOpen.value = false
    mobileInfoOpen.value = false
    void enterFullscreen()
  } else {
    void exitFullscreen()
  }
}

// 元素级全屏 iOS Safari 不支持：失败时静默退化为纯沉浸模式（隐藏全部 UI）
async function enterFullscreen() {
  try {
    await stageEl.value?.requestFullscreen?.()
  } catch {
    /* ignore */
  }
}

async function exitFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
  } catch {
    /* ignore */
  }
}

// 手势统一在指针事件里判定（setPointerCapture 会重定向 click，不能用 click 区分点按目标）：
// 适配态左右滑=翻页；缩放态单指=平移、双指=捏合；未移动的点按=切沉浸（图上）/关查看器（空白处）。
// 视频完全交给原生控制条。
const pointers = new Map<number, { x: number; y: number }>()
let pinchDist = 0
let swipeOriginX = 0
let gestureHitMedia = false
let gestureMoved = false
let tapTimer: number | null = null
let lastTapAt = 0
let lastTapX = 0
let lastTapY = 0

function onLightboxPointerDown(e: PointerEvent) {
  if (isVideoPhoto.value) return
  const target = e.target as HTMLElement
  if (target.closest('button, select, textarea, input, a, .lightbox-aside')) return
  gestureHitMedia = !!target.closest('.lightbox-media')
  gestureMoved = false
  swipeOriginX = e.clientX
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  try {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  } catch {
    /* ignore */
  }
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()]
    pinchDist = Math.hypot(a.x - b.x, a.y - b.y)
  }
}

function onLightboxPointerMove(e: PointerEvent) {
  const prev = pointers.get(e.pointerId)
  if (!prev) return
  pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
  if (Math.abs(e.clientX - swipeOriginX) > 8 || Math.abs(e.clientY - prev.y) > 8) gestureMoved = true
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()]
    const d = Math.hypot(a.x - b.x, a.y - b.y)
    if (pinchDist > 0 && d > 0) {
      zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, zoomScale.value * (d / pinchDist))
    }
    pinchDist = d
  } else if (pointers.size === 1 && zoomScale.value > 1) {
    panBy(e.clientX - prev.x, e.clientY - prev.y)
  }
}

function onLightboxPointerUp(e: PointerEvent) {
  if (!pointers.delete(e.pointerId)) return
  if (pointers.size < 2) pinchDist = 0
  if (pointers.size > 0) return
  const dx = e.clientX - swipeOriginX
  if (gestureMoved) {
    if (zoomScale.value === 1 && Math.abs(dx) > 48) stepLightbox(dx < 0 ? 1 : -1)
    return
  }
  // 未移动的「点按」：连续两次且位置接近 = 双击缩放切换；
  // 单点动作延迟执行，避免双击时先触发两次沉浸切换
  const now = Date.now()
  const nearLast = Math.hypot(e.clientX - lastTapX, e.clientY - lastTapY) < 24
  if (now - lastTapAt < 320 && nearLast) {
    lastTapAt = 0
    if (tapTimer !== null) {
      window.clearTimeout(tapTimer)
      tapTimer = null
    }
    if (isVideoPhoto.value || editing.value) return
    if (zoomScale.value > 1) resetZoom()
    else zoomAt(e.clientX, e.clientY, maxZoom.value)
    return
  }
  lastTapAt = now
  lastTapX = e.clientX
  lastTapY = e.clientY
  const hitMedia = gestureHitMedia
  if (tapTimer !== null) window.clearTimeout(tapTimer)
  tapTimer = window.setTimeout(() => {
    tapTimer = null
    if (!lightboxOpen.value || editing.value) return
    if (hitMedia) {
      toggleImmersive()
    } else if (immersive.value) {
      immersive.value = false
      void exitFullscreen()
    } else {
      closeLightbox()
    }
  }, 280)
}

function onLightboxPointerCancel(e: PointerEvent) {
  pointers.delete(e.pointerId)
  pinchDist = 0
}
const filterParams = computed((): PhotoFeedFilter => {
  const params: PhotoFeedFilter = {}
  if (filterAnalyzed.value === '1') params.analyzed = true
  if (filterAnalyzed.value === '0') params.analyzed = false
  if (selectedDate.value !== 'all') params.on_date = selectedDate.value || 'none'
  if (searchApplied.value) params.q = searchApplied.value
  if (selectedAuthor.value) params.author = selectedAuthor.value
  params.sort_by = sortKey.value.startsWith('name') ? 'name' : 'date'
  params.sort_dir = sortKey.value.endsWith('asc') ? 'asc' : 'desc'
  return params
})

// 搜索防抖：停止输入 350ms 后再触发请求
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(searchText, (v) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    searchApplied.value = v.trim()
  }, 350)
})

function photoDay(photo: PhotoItem) {
  const raw = photo.published_at || ''
  return raw.length >= 10 ? raw.slice(0, 10) : ''
}

function photoMonth(photo: PhotoItem) {
  const day = photoDay(photo)
  return day ? day.slice(0, 7) : ''
}

// 按月 / 照片墙视图：打平到单张照片
const flatPhotos = computed(() => items.value.flatMap((it) => it.photos))

const wallGroups = computed(() => {
  if (viewMode.value === 'wall') {
    return [{ key: '__flat__', title: '', photos: flatPhotos.value }]
  }
  const groups: { key: string; title: string; photos: PhotoItem[] }[] = []
  const index = new Map<string, PhotoItem[]>()
  for (const photo of flatPhotos.value) {
    const month = photoMonth(photo)
    let list = index.get(month)
    if (!list) {
      list = []
      index.set(month, list)
      groups.push({ key: month || 'none', title: formatPhotoMonth(month), photos: list })
    }
    list.push(photo)
  }
  return groups
})

watch(viewMode, (v) =>
  localStorage.setItem(`kpml_photo_view_${galleryKind.value}`, v),
)
watch(sortKey, (v) => localStorage.setItem(`kpml_photo_sort_${galleryKind.value}`, v))
const hasFilters = computed(() => {
  const p = filterParams.value
  return Boolean(
    p.tags?.length || p.analyzed !== undefined || p.on_date || p.q || p.author,
  )
})

const emptyHint = computed(() => {
  if (hasFilters.value) return '没有符合筛选的照片'
  if (isCollection.value) return '这个收藏夹还没有照片'
  if (!isCollection.value && props.section === 'wall' && sourceCount.value === 0) {
    return '尚未绑定 MT Photos 相册，请在设置中为照片墙选择相册'
  }
  if (canUpload.value && sourceCount.value === 0 && photoCount.value === 0) {
    return '还没有照片。可以点右上角上传，或在设置里绑定文件夹后扫描'
  }
  if (sourceCount.value === 0) return '尚未关联图片文件夹，请在设置中绑定后扫描'
  if (photoCount.value === 0) {
    return lastScannedAt.value
      ? '扫描完成，该分区没有可展示的照片'
      : '文件夹已绑定，请在设置中扫描建立索引'
  }
  return '该分区暂无照片'
})

function savedIds(photoId: number) {
  return memberships.value[photoId] || []
}

function isSaved(photoId: number) {
  return savedIds(photoId).length > 0
}

function applyMembership(photoId: number, ids: number[]) {
  memberships.value = { ...memberships.value, [photoId]: ids }
}

async function loadMemberships(photos: PhotoItem[]) {
  const ids = [...new Set(photos.map((p) => p.id))]
  if (!ids.length) return
  try {
    const r = await photosApi.memberships(ids)
    const next = { ...memberships.value }
    for (const id of ids) {
      next[id] = r.memberships[String(id)] || r.memberships[id as unknown as string] || []
    }
    memberships.value = next
  } catch {
    /* 收藏状态失败不阻断浏览 */
  }
}

function fileUrl(photo: PhotoItem) {
  return photosApi.fileUrl(photo.id)
}

function thumbUrl(photo: PhotoItem) {
  return photosApi.thumbUrl(photo.id)
}

function onThumbError(ev: Event, photo: PhotoItem) {
  const el = ev.target as HTMLImageElement | null
  if (!el || el.dataset.fallback) return
  el.dataset.fallback = '1'
  el.src = fileUrl(photo)
}

// 加载序列号：丢弃过期的异步回调（筛选条件快速变化时旧请求不应覆盖新数据）
let loadSeq = 0

async function load(targetPage: number) {
  const seq = ++loadSeq
  if (!isCollection.value && !props.ownerId) return
  loading.value = true
  errorText.value = ''
  try {
    const r = isCollection.value
      ? await photosApi.collectionPhotos(props.collectionId as number, {
          page: targetPage,
          page_size: pageSize,
          ...filterParams.value,
        })
      : await photosApi.feed({
          owner_type: props.ownerType as PhotoOwnerType,
          owner_id: props.ownerId as number,
          section: props.section as PhotoSection,
          page: targetPage,
          page_size: pageSize,
          ...filterParams.value,
        })
    if (seq !== loadSeq) return
    items.value = r.items
    total.value = r.total
    pageNum.value = targetPage
    sourceCount.value = r.source_count
    photoCount.value = r.photo_count
    lastScannedAt.value = r.last_scanned_at || null
    readonly.value = !!r.readonly
    timeline.value = r.timeline || []
    await loadMemberships(items.value.flatMap((it) => it.photos))
  } catch (e) {
    if (seq !== loadSeq) return
    errorText.value = (e as Error).message
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function onPageChange(p: number) {
  void load(p).then(() => window.scrollTo({ top: 0 }))
}

async function refreshFilterOptions() {
  if (!isCollection.value && !props.ownerId) return
  try {
    const r = isCollection.value
      ? await photosApi.collectionFilterOptions(props.collectionId as number)
      : await photosApi.feedFilterOptions({
          owner_type: props.ownerType as PhotoOwnerType,
          owner_id: props.ownerId as number,
          section: props.section as PhotoSection,
        })
    authorOptions.value = r.authors || []
    const validAuthors = new Set(authorOptions.value.map((o) => o.author))
    if (selectedAuthor.value && !validAuthors.has(selectedAuthor.value)) {
      selectedAuthor.value = ''
    }
  } catch {
    /* 聚合失败不影响浏览 */
  }
}

function extraCount(it: PhotoFeedItem) {
  return Math.max(0, it.photos.length - POST_PREVIEW_LIMIT)
}

function naturalColor(color?: string | null) {
  if (!color || color === '看不清') return ''
  if (color.endsWith('色') || color === '格纹' || color === '豹纹') return color
  return `${color}色`
}

function garmentText(part?: { desc?: string | null; color?: string | null } | null) {
  if (!part) return ''
  const desc = part.desc && part.desc !== '看不清' ? part.desc : ''
  return `${naturalColor(part.color)}${desc}`.trim()
}

function analysisChips(analysis: PhotoAnalysis | null | undefined) {
  if (!analysis) return []
  if (analysis.tags?.length) return [...new Set(analysis.tags)]
  const chips: string[] = []
  for (const name of analysis.people || []) chips.push(name)
  if (analysis.scene && analysis.scene !== '看不清') chips.push(analysis.scene)
  if (analysis.shot && analysis.shot !== '看不清') chips.push(analysis.shot)
  for (const part of [analysis.top, analysis.bottom, analysis.outer]) {
    const text = garmentText(part)
    if (text) chips.push(text)
  }
  const shoes = analysis.shoes
  if (shoes) {
    const type = shoes.type && shoes.type !== '看不清' ? shoes.type : ''
    const main = `${naturalColor(shoes.color)}${type}`.trim()
    const detail = shoes.detail && shoes.detail !== '看不清' ? shoes.detail : ''
    const text = [main, detail].filter(Boolean).join(' · ')
    if (text) chips.push(text)
  }
  const hosiery = analysis.hosiery
  if (hosiery?.present === '有') {
    const main = `${naturalColor(hosiery.color)}${hosiery.type || ''}`.trim() || '丝袜'
    const opacity = hosiery.opacity && hosiery.opacity !== '看不清' ? hosiery.opacity : ''
    chips.push([main, opacity].filter(Boolean).join(' · '))
  }
  for (const acc of analysis.accessories || []) chips.push(acc)
  for (const q of analysis.quality || []) chips.push(q)
  return [...new Set(chips)]
}

function patchPhoto(next: PhotoItem) {
  lightboxPhotos.value = lightboxPhotos.value.map((p) => (p.id === next.id ? { ...p, ...next } : p))
  items.value = items.value.map((it) => ({
    ...it,
    photos: it.photos.map((p) => (p.id === next.id ? { ...p, ...next } : p)),
  }))
}

async function onUpload(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = [...(input.files || [])]
  input.value = ''
  if (!files.length || !canUpload.value || !props.ownerType || !props.ownerId) return
  uploading.value = true
  try {
    const r = await photosApi.upload(props.ownerType, props.ownerId, files)
    message.success(`已上传 ${r.created} 张`)
    await load(1)
    void refreshFilterOptions()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    uploading.value = false
  }
}

function openCrop(kind: 'avatar' | 'banner') {
  if (!currentPhoto.value || currentPhoto.value.media_kind === 'video') {
    message.warning('请选择图片')
    return
  }
  cropKind.value = kind
}

async function confirmCrop(rect: { x: number; y: number; width: number; height: number }) {
  const photo = currentPhoto.value
  const kind = cropKind.value
  if (!photo || !kind || cropSaving.value) return
  cropSaving.value = true
  try {
    await photosApi.cropPortrait(photo.id, { kind, ...rect })
    cropKind.value = null
    message.success(kind === 'avatar' ? '已保存为头像' : '已保存为手机横幅')
    emit('portraitApplied', { kind })
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    cropSaving.value = false
  }
}

function startEdit() {
  const analysis = currentAnalysis.value
  editCaption.value = analysis?.caption_zh || ''
  editTags.value = [...(analysis?.tags || [])]
  editTagInput.value = ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
}

function commitTagInput() {
  const raw = editTagInput.value.trim()
  if (!raw) return
  for (const part of raw.split(/[,，]/)) {
    const text = part.trim().slice(0, 16)
    if (text && !editTags.value.includes(text) && editTags.value.length < 10) {
      editTags.value.push(text)
    }
  }
  editTagInput.value = ''
}

function addEditTag() {
  commitTagInput()
}

function removeEditTag(tag: string) {
  editTags.value = editTags.value.filter((t) => t !== tag)
}

async function saveEdit() {
  const photo = currentPhoto.value
  if (!photo || savingEdit.value) return
  commitTagInput()
  savingEdit.value = true
  try {
    const r = await photosApi.updateAnalysis(photo.id, {
      caption_zh: editCaption.value.trim(),
      tags: [...editTags.value],
    })
    patchPhoto({ ...photo, ...r.photo, analysis: r.analysis || r.photo.analysis })
    message.success('已保存修改')
    editing.value = false
    void refreshFilterOptions()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    savingEdit.value = false
  }
}

function mediaGridClass(count: number) {
  const n = Math.min(count, POST_PREVIEW_LIMIT)
  if (n <= 1) return 'post-grid--1'
  if (n === 2 || n === 4) return 'post-grid--2'
  return 'post-grid--3'
}

function openLightbox(photo: PhotoItem, context: PhotoItem[]) {
  const list = context.length ? context : [photo]
  lightboxPhotos.value = list
  const idx = list.findIndex((p) => p.id === photo.id)
  lightboxIndex.value = idx >= 0 ? idx : 0
  lightboxOpen.value = true
  immersive.value = false
  resetZoom()
  resetMediaState()
  lastTapAt = 0
}

// 渐进加载状态复位（缩略图/原图两路各自的重试机会）
function resetMediaState() {
  origLoaded.value = false
  origFailed.value = false
  thumbFailed.value = false
}

function closeLightbox() {
  lightboxOpen.value = false
  pickerOpen.value = false
  editing.value = false
  mobileInfoOpen.value = false
  immersive.value = false
  void exitFullscreen()
  resetZoom()
  resetMediaState()
  if (tapTimer !== null) {
    window.clearTimeout(tapTimer)
    tapTimer = null
  }
  lastTapAt = 0
}

// 切换照片：缩放/原图加载状态从头开始
watch(() => currentPhoto.value?.id, () => {
  resetZoom()
  resetMediaState()
})

// ===== 帖子大卡片 =====
function openPostDetail(it: PhotoFeedItem) {
  postDetailItem.value = it
}

function closePostDetail() {
  postDetailItem.value = null
}

// 大卡片里点某张图 → 仍走灯箱，上下文是「本帖全部图片」，可左右翻
function openDetailPhoto(photo: PhotoItem) {
  openLightbox(photo, postDetailPhotos.value)
}

// 大卡片平铺列数：按张数定列（1 张整幅、≤4 张两列、≤9 张三列、更多四列）
function detailGridClass(count: number) {
  if (count <= 1) return 'post-detail-grid--1'
  if (count <= 4) return 'post-detail-grid--2'
  if (count <= 9) return 'post-detail-grid--3'
  return 'post-detail-grid--4'
}

// 卡片右上角入口是个纯图标按钮（2026-09-21 起不再写「查看 N 张」文字），
// 张数等完整语义只能挂在 title / aria-label 上——这两个函数是唯一出处。
function postOpenTitle(it: PhotoFeedItem) {
  return it.photos.length > 1 ? `查看全部 ${it.photos.length} 张` : '查看详情'
}

function onRemovedFromCurrent() {
  const photo = currentPhoto.value
  if (!photo || !isCollection.value) return
  applyDeletedIds([photo.id])
}

function applyDeletedIds(ids: number[]) {
  const gone = new Set(ids)
  if (!gone.size) return
  const beforeItems = items.value.length
  const beforePhotos = items.value.reduce((n, it) => n + it.photos.length, 0)
  items.value = items.value
    .map((it) => ({ ...it, photos: it.photos.filter((p) => !gone.has(p.id)) }))
    .filter((it) => it.photos.length)
  const afterPhotos = items.value.reduce((n, it) => n + it.photos.length, 0)
  photoCount.value = Math.max(0, photoCount.value - (beforePhotos - afterPhotos))
  total.value = Math.max(0, total.value - (beforeItems - items.value.length))
  // 大卡片开着时同步剔除被删的图；整帖删空则关掉大卡片
  if (postDetailItem.value) {
    const kept = postDetailItem.value.photos.filter((p) => !gone.has(p.id))
    postDetailItem.value = kept.length
      ? { ...postDetailItem.value, photos: kept }
      : null
  }
  lightboxPhotos.value = lightboxPhotos.value.filter((p) => !gone.has(p.id))
  if (!lightboxPhotos.value.length) {
    closeLightbox()
    return
  }
  if (lightboxIndex.value >= lightboxPhotos.value.length) {
    lightboxIndex.value = lightboxPhotos.value.length - 1
  }
}

async function deleteCurrentPhoto() {
  const photo = currentPhoto.value
  if (!photo || deleting.value) return
  if (!window.confirm('将从磁盘删除这张图片，并从索引中移除。无法恢复。')) return
  deleting.value = true
  try {
    await photosApi.remove(photo.id)
    applyDeletedIds([photo.id])
    message.success('已删除')
    void refreshFilterOptions()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    deleting.value = false
  }
}

async function deletePostItem(it: PhotoFeedItem) {
  const first = it.photos[0]
  if (!first || deleting.value) return
  const n = it.photos.length
  const hint = it.post_key
    ? `将从磁盘删除这个帖子的 ${n} 张图片及原文。无法恢复。`
    : '将从磁盘删除这张图片。无法恢复。'
  if (!window.confirm(hint)) return
  deleting.value = true
  try {
    const r = it.post_key
      ? await photosApi.removePost(first.id)
      : await photosApi.remove(first.id)
    applyDeletedIds(it.photos.map((p) => p.id))
    message.success(r.deleted > 1 ? `已删除 ${r.deleted} 张` : '已删除')
    void refreshFilterOptions()
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    deleting.value = false
  }
}

async function deleteCurrentPost() {
  const photo = currentPhoto.value
  if (!photo || deleting.value) return
  const it =
    items.value.find((row) => row.photos.some((p) => p.id === photo.id)) ||
    ({
      kind: photo.post_key ? 'post' : 'photo',
      post_key: photo.post_key,
      photos: lightboxPhotos.value,
    } as PhotoFeedItem)
  await deletePostItem(it)
}

function stepLightbox(delta: number) {
  const list = lightboxPhotos.value
  if (list.length <= 1) return
  lightboxIndex.value = (lightboxIndex.value + delta + list.length) % list.length
  editing.value = false
}

function onKey(e: KeyboardEvent) {
  if (!lightboxOpen.value) {
    // 灯箱未开时 Esc 关帖子大卡片；灯箱开着时 Esc 先关灯箱（大卡片留在下层，符合层级）
    if (postDetailItem.value && e.key === 'Escape') closePostDetail()
    return
  }
  if (e.key === 'Escape') {
    if (editing.value) {
      cancelEdit()
      return
    }
    if (mobileInfoOpen.value) {
      mobileInfoOpen.value = false
      return
    }
    if (immersive.value) {
      immersive.value = false
      void exitFullscreen()
      return
    }
    closeLightbox()
  }
  if (editing.value) return
  if (e.key === 'ArrowLeft') stepLightbox(-1)
  if (e.key === 'ArrowRight') stepLightbox(1)
  if (e.key === 'f' || e.key === 'F') toggleImmersive()
  if (isVideoPhoto.value) return
  if (e.key === '+' || e.key === '=') zoomAt(centerX(), centerY(), zoomScale.value * 1.25)
  if (e.key === '-' || e.key === '_') zoomAt(centerX(), centerY(), zoomScale.value / 1.25)
  if (e.key === '0') resetZoom()
}

// 键盘缩放以舞台中心为不动点
function centerX(): number {
  const r = stageEl.value?.getBoundingClientRect()
  return r ? r.x + r.width / 2 : window.innerWidth / 2
}
function centerY(): number {
  const r = stageEl.value?.getBoundingClientRect()
  return r ? r.y + r.height / 2 : window.innerHeight / 2
}

function syncNarrow() {
  isNarrow.value = window.matchMedia('(max-width: 768px)').matches
}

// 排序选项
const SORT_OPTIONS: { key: string; label: string }[] = [
  { key: 'date_desc', label: '日期新到旧' },
  { key: 'date_asc', label: '日期旧到新' },
  { key: 'name_asc', label: '文件名 A-Z' },
  { key: 'name_desc', label: '文件名 Z-A' },
]
// 视图选项
const VIEW_OPTIONS: { key: PhotoViewMode; label: string }[] = [
  { key: 'post', label: '帖子' },
  { key: 'month', label: '按月分组' },
  { key: 'wall', label: '照片墙' },
]
const viewLabel = computed(
  () => VIEW_OPTIONS.find((o) => o.key === viewMode.value)?.label || '',
)
// 发布者 / 日期筛选是否生效（移动端图标高亮）
const authorActive = computed(() => !!selectedAuthor.value)
const dateActive = computed(() => selectedDate.value !== 'all')
const sortLabel = computed(
  () => SORT_OPTIONS.find((o) => o.key === sortKey.value)?.label || '',
)

// ===== 工具栏控件（2026-09-21 起统一为项目 HeroUI 口径）=====
// 桌面端 = SaSelect / SaDatePicker；移动端 = n-dropdown 图标菜单（浏览页 head-icon / 视频区 VideoFilterBar 同款）。
// 「全部」用哨兵值：naive-ui 下拉的 key 用空串容易踩坑（与浏览页同一处理）。
const ALL_SENTINEL = 'all'
const viewSelectOptions = VIEW_OPTIONS.map((o) => ({ label: o.label, value: o.key as string }))
const sortSelectOptions = SORT_OPTIONS.map((o) => ({ label: o.label, value: o.key }))
const authorSelectOptions = computed(() => [
  { label: '全部发布者', value: '' },
  ...authorOptions.value.map((a) => ({ label: `${a.author} · ${a.count}`, value: a.author })),
])
const ANALYZED_OPTIONS: { label: string; value: string }[] = [
  { label: '全部状态', value: '' },
  { label: '已分析', value: '1' },
  { label: '未分析', value: '0' },
]

function mark(active: boolean, label: string) {
  return active ? `✓ ${label}` : label
}
const viewDropdownOptions = computed<DropdownOption[]>(() =>
  VIEW_OPTIONS.map((o) => ({ label: mark(o.key === viewMode.value, o.label), key: o.key })),
)
const sortDropdownOptions = computed<DropdownOption[]>(() =>
  SORT_OPTIONS.map((o) => ({ label: mark(o.key === sortKey.value, o.label), key: o.key })),
)
const authorDropdownOptions = computed<DropdownOption[]>(() => [
  { label: mark(!selectedAuthor.value, '全部发布者'), key: ALL_SENTINEL },
  ...authorOptions.value.map((a) => ({
    label: mark(a.author === selectedAuthor.value, `${a.author} · ${a.count}`),
    key: a.author,
  })),
])
const dateDropdownOptions = computed<DropdownOption[]>(() => [
  { label: mark(selectedDate.value === 'all', '全部日期'), key: ALL_SENTINEL },
  ...timeline.value.map((d) => {
    const key = d.date || 'none'
    return { label: mark(key === selectedDate.value, `${formatPhotoDay(d.date)} · ${d.count}`), key }
  }),
])

function onViewSelect(key: string | number) {
  viewMode.value = key as PhotoViewMode
}
function onSortSelect(key: string | number) {
  sortKey.value = String(key)
}
function onAuthorSelect(key: string | number) {
  selectedAuthor.value = key === ALL_SENTINEL ? '' : String(key)
}
function onDateSelect(key: string | number) {
  selectedDate.value = String(key)
}

// 日期筛选：SaDatePicker 只认 YYYY-MM-DD / null（清空 = 全部日期）
const dateFilterValue = computed(() =>
  selectedDate.value === 'all' || selectedDate.value === 'none' ? null : selectedDate.value,
)
function onDateFilterChange(v: string | null) {
  selectedDate.value = v || 'all'
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
  syncNarrow()
  window.addEventListener('resize', syncNarrow)
  void refreshFilterOptions()
  void load(1)
})
onUnmounted(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('resize', syncNarrow)
  if (searchTimer) clearTimeout(searchTimer)
})

watch(
  () => [props.ownerType, props.ownerId, props.section, props.collectionId],
  () => {
    closeLightbox()
    filterAnalyzed.value = ''
    selectedDate.value = 'all'
    searchText.value = ''
    searchApplied.value = ''
    selectedAuthor.value = ''
    authorOptions.value = []
    void load(1)
    void refreshFilterOptions()
  },
)

watch(filterParams, () => {
  closeLightbox()
  void load(1)
})
</script>

<template>
  <div class="photo-gallery">
    <!-- 工具栏（口径对齐视频区 VideoFilterBar）：左「N 张」，右侧筛选控件。
         桌面端 = SaSelect / SaDatePicker / HeroUI 字段口径搜索框；
         移动端（≤768px）= 同一排：N 张 + 搜索 + 图标下拉（浏览页 head-icon 同款）。
         ⚠ 桌面控件靠 .photo-filter--desktop 在窄屏隐藏，图标组靠 .photo-tool-icons 在宽屏隐藏 -->
    <div class="photo-toolbar">
      <div class="photo-meta">{{ photoCount }} 张</div>
      <div class="photo-filters">
        <div class="photo-search">
          <input
            v-model="searchText"
            class="photo-search-input"
            type="text"
            placeholder="搜博文 / 标签 / 发布者 / 文件名"
            maxlength="100"
          />
          <button
            v-if="searchText"
            class="photo-search-clear"
            type="button"
            aria-label="清空搜索"
            @click="searchText = ''"
          >×</button>
        </div>
        <!-- 桌面端：视图 / 排序 / 发布者 / 分析状态（HeroUI 统一选择器） -->
        <SaSelect
          class="photo-filter--desktop pf-view"
          :model-value="viewMode"
          :options="viewSelectOptions"
          :clearable="false"
          placeholder="视图"
          @update:model-value="viewMode = $event as PhotoViewMode"
        />
        <SaSelect
          class="photo-filter--desktop pf-sort"
          :model-value="sortKey"
          :options="sortSelectOptions"
          :clearable="false"
          placeholder="排序"
          @update:model-value="sortKey = String($event)"
        />
        <SaSelect
          v-if="authorOptions.length"
          class="photo-filter--desktop pf-author"
          :model-value="selectedAuthor"
          :options="authorSelectOptions"
          :clearable="false"
          placeholder="发布者"
          @update:model-value="selectedAuthor = String($event ?? '')"
        />
        <SaDatePicker
          v-if="timeline.length"
          class="photo-filter--desktop pf-date"
          :model-value="dateFilterValue"
          placeholder="年 / 月 / 日"
          @update:model-value="onDateFilterChange"
        />
        <SaSelect
          v-if="!readonly"
          class="photo-filter--desktop pf-analyzed"
          :model-value="filterAnalyzed"
          :options="ANALYZED_OPTIONS"
          :clearable="false"
          placeholder="分析状态"
          @update:model-value="filterAnalyzed = String($event ?? '')"
        />
        <label
          v-if="canUpload"
          class="upload-btn photo-filter--desktop"
          :class="{ 'upload-btn--busy': uploading }"
        >
          {{ uploading ? '上传中…' : '上传' }}
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp,image/gif,image/bmp,video/mp4,video/quicktime,video/webm"
            :disabled="uploading"
            @change="onUpload"
          />
        </label>

        <!-- 移动端：图标下拉（浏览页 head-icon / 视频区 VideoFilterBar 同款） -->
        <div class="photo-tool-icons">
          <n-dropdown
            :options="viewDropdownOptions"
            trigger="click"
            placement="bottom-end"
            @select="onViewSelect"
          >
            <button
              class="photo-tool-icon"
              type="button"
              :title="`视图：${viewLabel}`"
              :aria-label="`视图：${viewLabel}`"
            >
              <ListOutlined :size="16" />
            </button>
          </n-dropdown>
          <n-dropdown
            :options="sortDropdownOptions"
            trigger="click"
            placement="bottom-end"
            @select="onSortSelect"
          >
            <button
              class="photo-tool-icon"
              type="button"
              :title="`排序：${sortLabel}`"
              :aria-label="`排序：${sortLabel}`"
            >
              <SortOutlined :size="16" />
            </button>
          </n-dropdown>
          <n-dropdown
            v-if="authorOptions.length"
            :options="authorDropdownOptions"
            trigger="click"
            placement="bottom-end"
            @select="onAuthorSelect"
          >
            <button
              class="photo-tool-icon"
              :class="{ 'photo-tool-icon--on': authorActive }"
              type="button"
              title="发布者筛选"
              aria-label="发布者筛选"
            >
              <PersonOutlined :size="16" />
            </button>
          </n-dropdown>
          <n-dropdown
            v-if="timeline.length"
            :options="dateDropdownOptions"
            trigger="click"
            placement="bottom-end"
            @select="onDateSelect"
          >
            <button
              class="photo-tool-icon"
              :class="{ 'photo-tool-icon--on': dateActive }"
              type="button"
              title="日期筛选"
              aria-label="日期筛选"
            >
              <CalendarOutlined :size="16" />
            </button>
          </n-dropdown>
        </div>
      </div>
    </div>

    <div v-if="loading" class="photo-empty">加载中…</div>
    <div v-else-if="errorText" class="photo-empty">{{ errorText }}</div>
    <div v-else-if="!items.length" class="photo-empty">
      <ImageOutlined :size="32" />
      <p>{{ emptyHint }}</p>
    </div>

    <template v-else>
      <div v-if="viewMode !== 'post'" class="wall-timeline" :class="{ 'wall-timeline--flat': viewMode === 'wall' }">
        <section v-for="group in wallGroups" :key="group.key" class="wall-day">
          <h3 v-if="group.title" class="wall-day-title">{{ group.title }}</h3>
          <div class="wall-grid">
            <button
              v-for="photo in group.photos"
              :key="photo.id"
              class="wall-cell"
              type="button"
              @click="openLightbox(photo, flatPhotos)"
            >
              <img
                class="wall-media"
                :src="thumbUrl(photo)"
                :alt="photo.file_name"
                loading="lazy"
                @error="onThumbError($event, photo)"
              />
              <span v-if="photo.media_kind === 'video'" class="media-play">
                <PlayArrowOutlined :size="18" />
              </span>
              <span v-if="isSaved(photo.id)" class="saved-dot" aria-label="已收藏">
                <BookmarkFilled :size="12" />
              </span>
              <span v-if="photo.analyzed_at" class="ai-dot" aria-label="已分析">
                <RobotOutlined :size="11" />
              </span>
            </button>
          </div>
        </section>
      </div>

      <div v-else class="post-list">
        <article
          v-for="(it, idx) in items"
          :key="(it.post_key || it.photos[0].id) + '-' + idx"
          class="post-card"
        >
          <!-- 顶端一行：左正文/作者/日期，右上角固定「查看帖子」图标入口 + 删除。
               2026-09-21：整卡不再可点（大卡片改由右上角图标弹出），
               因此 .post-actions 必须无条件渲染，不能跟着 .post-head 一起被 v-if 掉；
               同日把入口文字换成纯图标（张数保留在 title / aria-label） -->
          <div class="post-card-top">
            <div
              v-if="it.caption || it.author || it.published_at"
              class="post-head"
            >
              <p v-if="it.caption" class="post-caption">{{ it.caption }}</p>
              <div class="post-sub">
                <span v-if="it.author" class="post-author">{{ it.author }}</span>
                <span v-if="it.published_at" class="post-date">{{ formatDateTime(it.published_at) }}</span>
              </div>
            </div>
            <div class="post-actions">
              <button
                v-if="!isRemotePhoto(it.photos[0])"
                class="post-del"
                type="button"
                :disabled="deleting"
                @click.stop="deletePostItem(it)"
              >
                删除{{ it.post_key ? '帖子' : '' }}
              </button>
              <button
                class="post-open"
                type="button"
                :title="postOpenTitle(it)"
                :aria-label="postOpenTitle(it)"
                @click.stop="openPostDetail(it)"
              >
                <OpenInNewOutlined :size="16" />
              </button>
            </div>
          </div>
          <div class="post-grid" :class="mediaGridClass(it.photos.length)">
            <!-- 缩略图是热区：点它 → 直接开灯箱（上下文 = 本帖全部图片，可左右翻）。
                 进「帖子大卡片」的入口是右上角图标按钮，两条路互不干扰 -->
            <button
              v-for="(photo, pi) in it.photos.slice(0, POST_PREVIEW_LIMIT)"
              :key="photo.id"
              class="post-cell"
              type="button"
              :title="`查看第 ${pi + 1} 张`"
              @click.stop="openLightbox(photo, it.photos)"
            >
              <img
                class="post-media"
                :src="thumbUrl(photo)"
                :alt="photo.file_name"
                loading="lazy"
                @error="onThumbError($event, photo)"
              />
              <span v-if="photo.media_kind === 'video'" class="media-play">
                <PlayArrowOutlined :size="18" />
              </span>
              <span v-if="isSaved(photo.id)" class="saved-dot" aria-label="已收藏">
                <BookmarkFilled :size="12" />
              </span>
              <span v-if="photo.analyzed_at" class="ai-dot" aria-label="已分析">
                <RobotOutlined :size="11" />
              </span>
              <span
                v-if="pi === POST_PREVIEW_LIMIT - 1 && extraCount(it) > 0"
                class="post-more"
                :title="`展开全部 ${it.photos.length} 张`"
              >+{{ extraCount(it) }}</span>
            </button>
          </div>
        </article>
      </div>

      <SaPagination
        v-if="items.length"
        :total="total"
        :page="pageNum"
        :page-size="pageSize"
        :disabled="loading"
        :show-total="false"
        @update:page="onPageChange"
      />
    </template>

    <Teleport to="body">
      <div
        v-if="lightboxOpen && currentPhoto"
        class="lightbox"
        :class="{ 'lightbox--sheet': isNarrow && mobileInfoOpen, 'lightbox--immersive': immersive }"
        @click.self="closeLightbox"
      >
        <button
          class="lightbox-collect"
          type="button"
          :aria-label="isSaved(currentPhoto.id) ? '管理收藏' : '加入收藏'"
          @click.stop="pickerOpen = !pickerOpen"
        >
          <BookmarkFilled v-if="isSaved(currentPhoto.id)" :size="20" />
          <BookmarkOutlined v-else :size="20" />
        </button>
        <button
          v-if="!editing && !isRemotePhoto(currentPhoto)"
          class="lightbox-editbtn"
          type="button"
          aria-label="编辑描述与标签"
          @click.stop="startEdit"
        >
          ✎
        </button>
        <button
          v-if="!isRemotePhoto(currentPhoto)"
          class="lightbox-delete"
          type="button"
          :disabled="deleting"
          aria-label="删除这张图片"
          @click.stop="deleteCurrentPhoto"
        >
          <DeleteOutlined :size="18" />
        </button>
        <a
          class="lightbox-download"
          :href="fileUrl(currentPhoto)"
          :download="currentPhoto.file_name || ''"
          aria-label="下载原图"
          @click.stop
        >
          <DownloadOutlined :size="18" />
        </a>
        <button
          class="lightbox-fullbtn"
          type="button"
          :aria-label="immersive ? '退出沉浸模式' : '沉浸全屏看图'"
          @click.stop="toggleImmersive"
        >
          <FullscreenExitOutlined v-if="immersive" :size="18" />
          <FullscreenOutlined v-else :size="18" />
        </button>
        <button class="lightbox-close" type="button" aria-label="关闭" @click="closeLightbox">
          <CloseOutlined :size="22" />
        </button>
        <PhotoCollectPicker
          v-if="pickerOpen"
          class="lightbox-picker"
          :photo="currentPhoto"
          :current-collection-id="collectionId ?? null"
          @change="applyMembership"
          @removed-from-current="onRemovedFromCurrent"
        />
        <div class="lightbox-body">
          <div
            class="lightbox-main"
            @pointerdown="onLightboxPointerDown"
            @pointermove="onLightboxPointerMove"
            @pointerup="onLightboxPointerUp"
            @pointercancel="onLightboxPointerCancel"
          >
            <button
              v-if="lightboxCount > 1"
              class="lightbox-nav lightbox-nav--prev"
              type="button"
              @click.stop="stepLightbox(-1)"
            >
              ‹
            </button>
            <div ref="stageEl" class="lightbox-stage" @wheel.prevent="onWheel">
              <video
                v-if="currentPhoto.media_kind === 'video'"
                :key="'v-' + currentPhoto.id"
                class="lightbox-media"
                :src="fileUrl(currentPhoto)"
                controls
                autoplay
              />
              <img
                v-else
                :key="'i-' + currentPhoto.id"
                ref="mediaEl"
                class="lightbox-media"
                :src="displaySrc"
                :alt="currentPhoto.file_name"
                :style="zoomStyle"
                @load="onMediaLoad"
                @error="onMediaError"
              />
            </div>
            <button
              v-if="lightboxCount > 1"
              class="lightbox-nav lightbox-nav--next"
              type="button"
              @click.stop="stepLightbox(1)"
            >
              ›
            </button>
            <span v-if="zoomHint" class="zoom-chip">{{ zoomHint }}</span>
            <button
              v-if="isNarrow && !mobileInfoOpen"
              class="lightbox-info-btn"
              type="button"
              @click.stop="mobileInfoOpen = true"
            >
              <InfoOutlined :size="16" />
              描述与标签
            </button>
          </div>
          <aside
            v-if="(!isNarrow || mobileInfoOpen) && !immersive"
            class="lightbox-aside"
            @click.stop
            @pointerdown.stop
          >
            <div class="lightbox-aside-head">
              <div class="lightbox-aside-kicker">
                {{ lightboxCount > 1 ? `${lightboxIndex + 1} / ${lightboxCount}` : '详情' }}
                <template v-if="currentPhoto.published_at">
                  · {{ formatDateTime(currentPhoto.published_at) }}
                </template>
              </div>
              <button
                v-if="isNarrow"
                class="lightbox-aside-close"
                type="button"
                @click.stop="mobileInfoOpen = false"
              >
                收起
              </button>
            </div>
            <div class="lightbox-meta">
              <div v-if="currentPhoto.file_name" class="lightbox-filename">{{ currentPhoto.file_name }}</div>
              <div v-if="currentPhoto.author" class="lightbox-author">发布者：{{ currentPhoto.author }}</div>
              <template v-if="editing">
                <textarea
                  v-model="editCaption"
                  class="edit-caption"
                  rows="3"
                  placeholder="描述这张图片（可留空）"
                ></textarea>
                <div class="chip-editor">
                  <span v-for="t in editTags" :key="'et-' + t" class="analysis-chip chip-editable">
                    {{ t }}
                    <button type="button" class="chip-remove" aria-label="删除标签" @click.stop="removeEditTag(t)">×</button>
                  </span>
                  <input
                    v-model="editTagInput"
                    class="chip-input"
                    placeholder="输入标签，回车添加"
                    @keydown.enter.prevent="addEditTag"
                  />
                </div>
                <div class="edit-actions">
                  <button
                    class="edit-btn edit-btn--primary"
                    type="button"
                    :disabled="savingEdit"
                    @click.stop="saveEdit"
                  >{{ savingEdit ? '保存中…' : '保存' }}</button>
                  <button class="edit-btn" type="button" :disabled="savingEdit" @click.stop="cancelEdit">取消</button>
                </div>
              </template>
              <template v-else>
                <p v-if="currentAnalysis?.caption_zh">{{ currentAnalysis.caption_zh }}</p>
                <p v-else-if="currentPhoto.caption">{{ currentPhoto.caption }}</p>
                <p v-else class="lightbox-empty-desc">暂无描述</p>
                <div v-if="analysisChips(currentAnalysis).length" class="analysis-chips">
                  <span
                    v-for="(chip, ci) in analysisChips(currentAnalysis)"
                    :key="ci + '-' + chip"
                    class="analysis-chip"
                  >{{ chip }}</span>
                </div>
                <p v-else class="lightbox-empty-desc">暂无标签</p>
              </template>
              <div
                v-if="currentPhoto.media_kind !== 'video'"
                class="portrait-actions"
              >
                <button class="edit-btn" type="button" :disabled="cropSaving" @click.stop="openCrop('avatar')">
                  设为头像
                </button>
                <button class="edit-btn" type="button" :disabled="cropSaving" @click.stop="openCrop('banner')">
                  设为横幅
                </button>
              </div>
              <span v-if="!editing && currentAnalysis?.manually_edited" class="edited-hint">
                已手动编辑
              </span>
              <button
                v-if="!editing && currentPhoto.post_key && !isRemotePhoto(currentPhoto)"
                class="lightbox-del-post"
                type="button"
                :disabled="deleting"
                @click.stop="deleteCurrentPost"
              >
                删除整个帖子
              </button>
            </div>
          </aside>
        </div>
      </div>
    </Teleport>
    <!-- 帖子大卡片：点帖子卡片弹出，图片全部平铺（z-index 150：在灯箱 200 之下，
         所以在大卡片里点图仍能再叠一层灯箱，Esc 先关灯箱、再关大卡片） -->
    <Teleport to="body">
      <div
        v-if="postDetailItem"
        class="post-detail"
        @click.self="closePostDetail"
      >
        <div
          class="post-detail-panel"
          role="dialog"
          aria-modal="true"
          :aria-label="postDetailItem.caption ? '帖子详情' : `${postDetailPhotos.length} 张图片`"
        >
          <button
            class="post-detail-close"
            type="button"
            aria-label="关闭"
            @click="closePostDetail"
          >✕</button>
          <!-- 唯一滚条：正文与照片在同一个滚动容器里往下走，不再各滚各的 -->
          <div class="post-detail-scroll">
            <header class="post-detail-head">
              <div class="post-detail-head-main">
                <p v-if="postDetailItem.caption" class="post-detail-caption">{{ postDetailItem.caption }}</p>
                <div class="post-detail-sub">
                  <span v-if="postDetailItem.author" class="post-detail-author">{{ postDetailItem.author }}</span>
                  <span v-if="postDetailItem.published_at" class="post-detail-date">
                    {{ formatDateTime(postDetailItem.published_at) }}
                  </span>
                  <span class="post-detail-count">{{ postDetailPhotos.length }} 张</span>
                </div>
              </div>
            </header>
            <div class="post-detail-body">
              <div class="post-detail-grid" :class="detailGridClass(postDetailPhotos.length)">
                <button
                  v-for="photo in postDetailPhotos"
                  :key="photo.id"
                  class="post-detail-cell"
                  type="button"
                  :aria-label="`查看第 ${photo.position_in_post + 1} 张`"
                  @click="openDetailPhoto(photo)"
                >
                  <img
                    class="post-detail-media"
                    :src="thumbUrl(photo)"
                    :alt="photo.file_name"
                    loading="lazy"
                    @error="onThumbError($event, photo)"
                  />
                  <span v-if="photo.media_kind === 'video'" class="media-play">
                    <PlayArrowOutlined :size="18" />
                  </span>
                  <span v-if="isSaved(photo.id)" class="saved-dot" aria-label="已收藏">
                    <BookmarkFilled :size="12" />
                  </span>
                  <span v-if="photo.analyzed_at" class="ai-dot" aria-label="已分析">
                    <RobotOutlined :size="11" />
                  </span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
    <Teleport to="body">
      <CropDialog
        v-if="cropKind && currentPhoto && currentPhoto.media_kind !== 'video'"
        :src="fileUrl(currentPhoto)"
        :aspect="cropKind === 'avatar' ? 1 : 1.82"
        :title="cropKind === 'avatar' ? '裁切头像（1:1）' : '裁切手机横幅（1.82:1）'"
        @cancel="cropKind = null"
        @confirm="confirmCrop"
      />
    </Teleport>
  </div>
</template>

<style scoped>
.photo-gallery {
  min-height: 160px;
}
/* ===== 工具栏（口径对齐视频区 .video-toolbar：数量居左、控件居右、可换行） ===== */
.photo-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
  margin: 0 0 12px;
}
.photo-meta {
  margin: 0;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.photo-filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
/* 桌面端控件宽度（窄屏由 .photo-filter--desktop 整体隐藏，换图标组） */
.pf-view {
  width: 110px;
}
.pf-sort {
  width: 130px;
}
.pf-author {
  width: 150px;
}
.pf-date {
  width: 150px;
}
.pf-analyzed {
  width: 118px;
}
.photo-search {
  position: relative;
  display: inline-flex;
  align-items: center;
}
/* 搜索框 = HeroUI 字段口径（36px / 12px 圆角 / 13px / field-shadow），与 .sa-select 齐平 */
.photo-search-input {
  width: 200px;
  height: 36px;
  box-sizing: border-box;
  padding: 0 28px 0 12px;
  border: 1px solid var(--sa-border);
  border-radius: 12px;
  background: var(--sa-elevated);
  color: var(--sa-text-primary);
  font-family: inherit;
  font-size: 13px;
  outline: none;
  box-shadow:
    0 2px 4px rgba(0, 0, 0, 0.04),
    0 1px 2px rgba(0, 0, 0, 0.06),
    0 0 1px rgba(0, 0, 0, 0.06);
  transition: border-color 0.15s ease;
}
html[data-theme='dark'] .photo-search-input {
  box-shadow: none;
}
.photo-search-input::placeholder {
  color: var(--sa-text-tertiary);
}
.photo-search-input:focus {
  border-color: color-mix(in srgb, var(--sa-text-primary) 30%, transparent);
}
.photo-search-clear {
  position: absolute;
  right: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 0;
  border-radius: 9999px;
  background: var(--sa-hover);
  color: var(--sa-text-secondary);
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.wall-timeline {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.wall-day-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--sa-text-secondary);
}
/* ===== 移动端图标筛选组（宽屏整体不渲染；桌面用 SaSelect / SaDatePicker） ===== */
.photo-tool-icons {
  display: none;
}
.photo-tool-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  padding: 0;
  border: 1px solid var(--sa-border-subtle, var(--sa-border));
  border-radius: 9999px;
  background: var(--sa-elevated);
  color: var(--sa-text-secondary);
  cursor: pointer;
  transition: color 0.2s, border-color 0.2s, background 0.2s;
}
.photo-tool-icon:hover {
  color: var(--sa-text-primary);
  border-color: var(--sa-accent);
}
/* 筛选生效：图标点亮（与浏览页 .head-icon--on 同口径） */
.photo-tool-icon--on {
  color: var(--sa-accent);
  border-color: var(--sa-accent);
  background: color-mix(in srgb, var(--sa-accent) 12%, transparent);
}
.upload-btn {
  height: 36px;
  display: inline-flex;
  align-items: center;
  padding: 0 14px;
  border-radius: 12px;
  background: var(--sa-accent);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}
.upload-btn input {
  display: none;
}
.upload-btn--busy {
  opacity: 0.6;
  cursor: wait;
}
.photo-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 64px 0;
  color: var(--sa-text-tertiary);
  font-size: 14px;
}
.photo-empty p {
  margin: 0;
}

.wall-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}
.wall-cell,
.post-cell {
  position: relative;
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: var(--sa-subtle, #111);
  overflow: hidden;
  aspect-ratio: 1;
  border-radius: 8px;
}
.wall-cell {
  cursor: pointer;
}
/* 帖子缩略图同样是热区（点它开灯箱）→ 与 .wall-cell 一样给手型光标 */
.post-cell {
  cursor: pointer;
}
.wall-media,
.post-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  background: var(--sa-subtle, #1a1a1a);
}
.media-play {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 28px;
  height: 28px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.saved-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22px;
  height: 22px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}

.post-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
/* HeroUI v3 Card 规格：flex column + gap 12 + padding 16 + radius 24（rounded-3xl）
   + --shadow-surface 三层轻投影、无描边。
   2026-09-21：整卡不是热区（原本整卡可点进大卡片）→ 去掉 cursor:pointer。
   卡片内两个入口：点缩略图 → 灯箱；右上角图标按钮 → 帖子大卡片。hover 反馈保留 */
.post-card {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  border-radius: 24px;
  background: var(--sa-elevated);
  box-shadow: var(--sa-surface-shadow);
  transition: box-shadow 0.15s ease;
}
.post-card:hover {
  box-shadow: var(--sa-surface-shadow-hover);
}
/* 顶端一行：正文块占满左侧（窄池子里可换行），操作按钮固定右上角不参与收缩 */
.post-card-top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}
.post-head {
  min-width: 0;
  flex: 1;
}
/* 右上角操作组：删除在前（次级/破坏性）、查看在后（主入口）→ 查看贴住卡片右上角。
   ⚠ `margin-left:auto` 是必需的兜底：`.post-head` 被 v-if 掉时（远端来源且无正文/作者/日期的帖），
   `space-between` 对**唯一子元素**会把它推到**左端**，入口就跑到左上角去了 */
.post-actions {
  flex: none;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 1px;
}
.post-del {
  flex: none;
  margin-top: 0;
  border: 0;
  background: transparent;
  color: var(--sa-text-tertiary);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
  white-space: nowrap;
}
.post-del:hover:not(:disabled) {
  color: #c0392b;
}
.post-del:disabled {
  opacity: 0.5;
  cursor: wait;
}
.post-caption {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  color: var(--sa-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.post-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-top: 4px;
}
.post-author {
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-accent);
  word-break: break-word;
}
.post-date {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.post-grid {
  display: grid;
  gap: 4px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.post-grid--1 {
  grid-template-columns: minmax(0, 1fr);
}
.post-grid--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.post-grid--3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.post-more {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.48);
  color: #fff;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: 0.02em;
  /* 纯数字角标（旧版是「点它展开」的热区）→ 不吃事件，免得挡了上面的浮标 */
  pointer-events: none;
}
/* 右上角唯一入口（进帖子大卡片）：纯图标按钮。
   2026-09-21：文字「查看 N 张 ›」换成图标 —— 张数不再显示，语义挪到 title / aria-label。
   同尺寸同底色，与旁边「删除」文字按钮同一行不打架 */
.post-open {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.post-open:hover {
  background: var(--sa-hover);
  color: var(--sa-accent);
}

/* ===== 帖子大卡片（点帖子卡片弹出；图片全部平铺，不用「+N」藏图） ===== */
.post-detail {
  position: fixed;
  inset: 0;
  /* 低于灯箱(200)/裁切弹窗(220)，高于底部导航(100/101)与页头(50) */
  z-index: 150;
  background: rgba(8, 8, 12, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 20px;
}
.post-detail-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: min(980px, 100%);
  max-height: 100%;
  padding: 0;
  border-radius: 24px;
  background: var(--sa-elevated);
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.28);
  /* overflow:hidden 只为裁圆角；滚动交给里面的 .post-detail-scroll（唯一滚条） */
  overflow: hidden;
}
/* 唯一滚条：正文与照片在同一个滚动容器里（`.post-detail-caption` / `.post-detail-body`
   都不再各自 overflow:auto，旧版正文 28vh 内滚 + 图片区外滚的双滚条已移除） */
.post-detail-scroll {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  overflow-y: auto;
  overscroll-behavior: contain;
}
html[data-theme='dark'] .post-detail-panel {
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.6);
}
.post-detail-head {
  flex: none;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  /* 给浮在右上角的关闭按钮让位（按钮脱离滚动流，滚到哪都点得到） */
  padding-right: 34px;
}
.post-detail-head-main {
  min-width: 0;
  flex: 1;
}
/* 大卡片里正文不截断也不单独滚动（卡片上才是 6 行 clamp）；
   正文与照片同属 `.post-detail-scroll` 这一个滚条，正文再长也只是把图片往下推 */
.post-detail-caption {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  color: var(--sa-text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: none;
  overflow: visible;
}
.post-detail-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-top: 4px;
}
.post-detail-author {
  font-size: 14px;
  font-weight: 600;
  color: var(--sa-accent);
  word-break: break-word;
}
.post-detail-date,
.post-detail-count {
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.post-detail-close {
  /* 浮在面板右上角、不参与滚动：唯一滚条是 .post-detail-scroll，滚到任何位置都能关 */
  position: absolute;
  top: 14px;
  right: 14px;
  z-index: 2;
  flex: none;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 9999px;
  background: var(--sa-subtle);
  color: var(--sa-text-secondary);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
}
.post-detail-close:hover {
  background: var(--sa-hover);
  color: var(--sa-text-primary);
}
.post-detail-body {
  /* 不再自己滚：随 .post-detail-scroll 一起走 */
  flex: none;
}
.post-detail-grid {
  display: grid;
  gap: 6px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.post-detail-grid--1 {
  grid-template-columns: minmax(0, 1fr);
}
.post-detail-grid--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.post-detail-grid--3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.post-detail-grid--4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.post-detail-cell {
  position: relative;
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: var(--sa-subtle);
  overflow: hidden;
  cursor: zoom-in;
  aspect-ratio: 1;
  border-radius: 10px;
}
.post-detail-media {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  background: var(--sa-subtle);
}
/* 单图帖：整幅按原比例显示，不裁切 */
.post-detail-grid--1 .post-detail-cell {
  aspect-ratio: auto;
  background: transparent;
}
.post-detail-grid--1 .post-detail-media {
  height: auto;
  max-height: 62vh;
  object-fit: contain;
  background: transparent;
}

.lightbox {
  position: fixed;
  inset: 0;
  /* 高于底部导航栏（z-index 99/101），否则手机端贴底按钮会被盖住 */
  z-index: 200;
  background: rgba(0, 0, 0, 0.92);
  display: flex;
  touch-action: none;
  user-select: none;
}
.lightbox-body {
  display: flex;
  width: 100%;
  height: 100%;
  min-height: 0;
}
.lightbox-main {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 48px 56px 32px;
}
.lightbox-stage {
  position: relative;
  max-width: 100%;
  max-height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.lightbox-media {
  position: relative;
  max-width: 100%;
  max-height: calc(100vh - 96px);
  object-fit: contain;
  border-radius: 8px;
  touch-action: auto;
}
/* 下载 / 沉浸模式按钮（右上区，关闭键内侧） */
.lightbox-download,
.lightbox-fullbtn {
  position: absolute;
  top: 14px;
  border: 0;
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
  cursor: pointer;
  padding: 6px;
  border-radius: 9999px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.lightbox-download {
  right: 56px;
}
.lightbox-fullbtn {
  right: 96px;
}
.lightbox-download:hover,
.lightbox-fullbtn:hover {
  background: rgba(0, 0, 0, 0.55);
}
/* 缩放倍率提示（短暂显示后自动消失） */
.zoom-chip {
  position: absolute;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 12px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: rgba(255, 255, 255, 0.92);
  font-size: 12px;
  z-index: 3;
  pointer-events: none;
}
/* 沉浸模式：隐藏全部 UI，只留图片与倍率提示；退出靠点图 / Esc / F / ⛶ 键 */
.lightbox--immersive .lightbox-collect,
.lightbox--immersive .lightbox-editbtn,
.lightbox--immersive .lightbox-delete,
.lightbox--immersive .lightbox-download,
.lightbox--immersive .lightbox-close,
.lightbox--immersive .lightbox-nav,
.lightbox--immersive .lightbox-info-btn,
.lightbox--immersive .lightbox-aside {
  display: none;
}
.lightbox--immersive .lightbox-main {
  padding: 0;
}
.lightbox--immersive .lightbox-media {
  max-width: 100vw;
  max-height: 100vh;
  max-height: 100dvh;
  border-radius: 0;
}
.lightbox-aside {
  width: 320px;
  flex-shrink: 0;
  background: #141414;
  border-left: 1px solid rgba(255, 255, 255, 0.08);
  overflow-y: auto;
  padding: 56px 20px 24px;
}
.lightbox-meta {
  color: rgba(255, 255, 255, 0.86);
  font-size: 13px;
  text-align: left;
  max-width: none;
}
.lightbox-meta p {
  margin: 0 0 10px;
  white-space: pre-wrap;
  line-height: 1.55;
}
.lightbox-aside-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.lightbox-aside-kicker {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.5);
}
.lightbox-aside-close {
  flex-shrink: 0;
  border: 0;
  border-radius: 9999px;
  padding: 5px 12px;
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.lightbox-filename {
  margin-bottom: 12px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.62);
  word-break: break-all;
}
.lightbox-author {
  margin-bottom: 12px;
  font-size: 12px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.86);
  word-break: break-word;
}
.lightbox-empty-desc {
  color: rgba(255, 255, 255, 0.38) !important;
}
.lightbox-info-btn {
  display: none;
}
.lightbox-close,
.lightbox-collect,
.lightbox-editbtn,
.lightbox-delete {
  position: absolute;
  top: 14px;
  border: 0;
  background: rgba(0, 0, 0, 0.35);
  color: #fff;
  cursor: pointer;
  padding: 6px;
  border-radius: 9999px;
  z-index: 2;
}
.lightbox-close {
  right: 16px;
}
.lightbox-collect {
  left: 16px;
}
.lightbox-editbtn {
  left: 96px;
  font-size: 14px;
  line-height: 1;
  width: 32px;
  height: 32px;
}
.lightbox-delete {
  left: 136px;
}
.lightbox-delete:disabled {
  opacity: 0.5;
  cursor: wait;
}
.lightbox-del-post {
  display: inline-block;
  margin-top: 6px;
  border: 0;
  background: transparent;
  color: rgba(255, 180, 180, 0.9);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.lightbox-del-post:hover:not(:disabled) {
  color: #ff8a8a;
}
.lightbox-del-post:disabled {
  opacity: 0.5;
  cursor: wait;
}
.ai-dot {
  position: absolute;
  left: 8px;
  bottom: 8px;
  width: 22px;
  height: 22px;
  border-radius: 9999px;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.analysis-chips {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 6px;
  margin: 6px 0 8px;
}
.analysis-chip {
  padding: 2px 8px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.12);
  font-size: 11px;
}
.edited-hint {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.55);
}
.edit-caption {
  width: 100%;
  box-sizing: border-box;
  margin-bottom: 8px;
  padding: 8px 10px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
}
.chip-editor {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-start;
  align-items: center;
  gap: 6px;
  margin-bottom: 10px;
}
.chip-editable {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.chip-remove {
  border: 0;
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  padding: 0 2px;
  font-size: 13px;
  line-height: 1;
}
.chip-remove:hover {
  color: #fff;
}
.chip-input {
  width: 150px;
  padding: 4px 10px;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  font-size: 12px;
}
.edit-actions {
  display: flex;
  justify-content: flex-start;
  gap: 8px;
  margin-bottom: 6px;
}
.edit-btn {
  padding: 5px 16px;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 8px;
  background: transparent;
  color: #fff;
  font-size: 12px;
  cursor: pointer;
}
.edit-btn--primary {
  background: var(--sa-accent);
  border-color: var(--sa-accent);
}
.portrait-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0 4px;
}
.edit-btn:disabled {
  opacity: 0.55;
  cursor: wait;
}
.lightbox-picker {
  position: absolute;
  top: 52px;
  left: 16px;
  z-index: 3;
}
.lightbox-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  border: 0;
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  width: 44px;
  height: 44px;
  border-radius: 9999px;
  font-size: 28px;
  line-height: 1;
  cursor: pointer;
}
.lightbox-nav--prev {
  left: 16px;
}
.lightbox-nav--next {
  right: 16px;
}

@media (max-width: 768px) {
  /* 工具栏固定一行：数量「N 张」+ 搜索 + 图标组（同视频区 .video-toolbar） */
  .photo-toolbar {
    flex-wrap: nowrap;
  }
  .photo-meta {
    flex: none;
    white-space: nowrap;
  }
  .photo-filters {
    flex: 1;
    flex-wrap: nowrap;
    align-items: center;
  }
  .photo-search {
    flex: 1 1 auto;
    min-width: 0;
    order: 1;
  }
  .photo-search-input {
    width: 100%;
    min-width: 0;
  }
  /* 桌面控件（SaSelect / SaDatePicker / 上传）整体隐藏，换图标组 */
  .photo-filter--desktop {
    display: none;
  }
  .photo-tool-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: none;
    order: 2;
  }
  .wall-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 3px;
  }
  .wall-cell,
  .post-cell {
    border-radius: 4px;
  }
  .post-list {
    grid-template-columns: 1fr;
    gap: 12px;
  }
  .post-card {
    padding: 14px;
    border-radius: 20px;
  }
  .post-grid--1 {
    max-width: 72%;
  }
  /* 帖子大卡片手机端：底部抽屉（与灯箱信息栏同一套路），整幅铺满宽度 */
  .post-detail {
    align-items: flex-end;
    padding: 0;
  }
  .post-detail-panel {
    width: 100%;
    max-height: 92vh;
    border-radius: 20px 20px 0 0;
  }
  /* 内边距（含安全区）挪到唯一滚条上，面板本身不再有 padding */
  .post-detail-scroll {
    padding: 16px 12px calc(16px + env(safe-area-inset-bottom, 0px));
  }
  .post-detail-close {
    top: 12px;
    right: 12px;
  }
  /* 正文不再单独限高滚动（旧版 34vh 内滚）：整卡一口滚条，正文多长都往下推图 */
  .post-detail-grid--3,
  .post-detail-grid--4 {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .post-detail-grid--1 .post-detail-media {
    max-height: 52vh;
  }
  .lightbox-main {
    padding: 48px 8px 72px;
  }
  .lightbox-media {
    max-height: calc(100vh - 120px);
  }
  .lightbox-aside {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    width: auto;
    max-height: 52%;
    border-left: 0;
    border-top: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px 16px 0 0;
    padding: 20px 16px 24px;
    z-index: 4;
  }
  .lightbox-info-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    position: absolute;
    left: 50%;
    /* 沉浸模式隐藏后靠图片点按恢复；底部留出安全区避免被手势条遮挡 */
    bottom: calc(18px + env(safe-area-inset-bottom, 0px));
    transform: translateX(-50%);
    z-index: 3;
    border: 0;
    border-radius: 9999px;
    padding: 8px 14px;
    background: rgba(255, 255, 255, 0.16);
    color: #fff;
    font-size: 13px;
    cursor: pointer;
  }
  .lightbox-nav {
    width: 36px;
    height: 36px;
    font-size: 24px;
    background: rgba(255, 255, 255, 0.12);
  }
  .lightbox-nav--prev {
    left: 6px;
  }
  .lightbox-nav--next {
    right: 6px;
  }
}
</style>
