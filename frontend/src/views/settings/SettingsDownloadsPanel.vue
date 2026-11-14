<script setup lang="ts">
/**
 * 设置 → YouTube 下载
 *
 * 三块：
 * 1. yt-dlp 引擎卡：版本 / 落点路径 / 一键更新（容器内自更新，不需要重建镜像）
 * 2. 链接下载：粘贴 YouTube 链接（单视频 / 整个收藏夹列表），入队后串行下载
 * 3. 收藏夹订阅：定期检查列表新视频并自动入队
 *
 * 产物一律落到「待整理」的 incoming 目录；下载中的半成品写在 incoming/.downloading，
 * 扫描器会跳过隐藏目录，所以**下载完整才会出现在待整理里**。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useMessage, useDialog } from 'naive-ui'
import {
  downloadsApi,
  type DownloadCheckResult,
  type DownloadJob,
  type DownloadSubscription,
  type NetworkProbeResult,
  type YtdlpChannel,
  type YtdlpStatus,
} from '@/api/downloads'

const message = useMessage()
const dialog = useDialog()

const ytdlp = ref<YtdlpStatus | null>(null)
const updating = ref(false)
/** 更新通道：稳定版 / 每日构建（持久化在服务端 app_settings） */
const channel = ref<YtdlpChannel>('nightly')
const probing = ref(false)
const probe = ref<NetworkProbeResult | null>(null)
const jobs = ref<DownloadJob[]>([])
const subs = ref<DownloadSubscription[]>([])
const loading = ref(false)

const urlInput = ref('')
const mode = ref<'video' | 'playlist'>('video')
const submitting = ref(false)
/** 本次是否同时下载字幕（中文优先，单次可覆盖） */
const downloadSubtitles = ref(false)
const subsBusy = ref<number | null>(null)
async function onFetchSubs(j: { id: number }) {
  subsBusy.value = j.id
  try {
    await downloadsApi.fetchSubtitles(j.id)
    message.success('已开始补字幕，几秒后自动刷新')
    window.setTimeout(() => void loadJobs(), 6000)
  } catch (e) {
    message.error((e as Error).message)
  } finally {
    subsBusy.value = null
  }
}
const subInput = ref('')
const subDownloadExisting = ref(false)
const subSubmitting = ref(false)
const checkingId = ref<number | null>(null)

let timer: number | null = null

const activeJobs = computed(() =>
  jobs.value.filter((j) => ['queued', 'downloading', 'processing'].includes(j.status)),
)

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  downloading: '下载中',
  processing: '合并中',
  done: '已完成',
  failed: '失败',
  canceled: '已取消',
}

function statusLabel(s: string) {
  return STATUS_LABEL[s] || s
}

/** 已完成任务的落盘文件明细（视频 / 封面 / json / 字幕） */
function filesOf(j: { outputs?: string | null }) {
  if (!j.outputs) return [] as { kind: string; name: string; size: number; width?: number; height?: number }[]
  try {
    return JSON.parse(j.outputs) as { kind: string; name: string; size: number; width?: number; height?: number }[]
  } catch {
    return []
  }
}

const KIND_LABEL: Record<string, string> = { video: '视频', cover: '封面', json: 'JSON', subtitle: '字幕' }
function kindLabel(k: string) {
  return KIND_LABEL[k] || k
}

function formatBytes(n?: number | null) {
  if (!n || n <= 0) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(i === 0 ? 0 : 1)}${units[i]}`
}

function jobTitle(j: DownloadJob) {
  return j.title || j.video_id || j.url
}

async function loadYtdlp() {
  try {
    ytdlp.value = await downloadsApi.ytdlp()
    // 只有 nightly 一个通道，后端回什么都按 nightly 展示
    channel.value = 'nightly'
  } catch {
    ytdlp.value = null
  }
}

async function loadJobs() {
  try {
    jobs.value = await downloadsApi.jobs()
  } catch {
    /* 轮询失败不打扰用户 */
  }
}

async function loadSubs() {
  try {
    subs.value = await downloadsApi.subscriptions()
  } catch {
    subs.value = []
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([loadYtdlp(), loadJobs(), loadSubs()])
  } finally {
    loading.value = false
  }
}

/** 网络自检：逐个下载源试一下，回答「到底走没走代理」 */
async function onTestNetwork() {
  probing.value = true
  try {
    probe.value = await downloadsApi.testNetwork(channel.value)
    const ok = probe.value.results.filter((r) => r.ok).length
    if (!probe.value.proxy) {
      message.warning('当前没有配置代理（设置 → 站点获取代理），所有源都在直连测试')
    } else if (ok === 0) {
      message.error(`代理 ${probe.value.proxy} 下所有源都不通，看下方明细`)
    } else {
      message.success(`${ok}/${probe.value.results.length} 个源可用（代理 ${probe.value.proxy}）`)
    }
    await loadYtdlp()
  } catch (e) {
    message.error((e as Error).message || '自检失败')
  } finally {
    probing.value = false
  }
}

async function onUpdateYtdlp() {
  updating.value = true
  try {
    ytdlp.value = await downloadsApi.updateYtdlp(channel.value)
    message.success(`yt-dlp 已更新到 Nightly ${ytdlp.value.version || ''}`)
  } catch (e) {
    message.error((e as Error).message || '更新失败')
  } finally {
    updating.value = false
  }
}

/** 命中重复时的确认框：先摆证据，再让用户决定要不要硬下 */
function confirmDuplicate(check: DownloadCheckResult) {
  const lines = check.matches
    .slice(0, 5)
    .map((m) => `· ${m.label}：${m.name || m.file_name || '—'}${m.resolution ? `（${m.resolution}）` : ''}`)
  dialog.warning({
    title: '这条视频已在库中',
    content: [
      check.title ? `《${check.title}》` : '',
      check.message,
      '',
      ...lines,
      '',
      '确实需要重新下载（比如想要更高画质）？',
    ]
      .filter((x) => x !== null)
      .join('\n'),
    positiveText: '仍然下载',
    negativeText: '取消',
    onPositiveClick: async () => {
      submitting.value = true
      try {
        const job = await downloadsApi.createJob(check.url, mode.value, true, downloadSubtitles.value)
        message.success(`已强制加入队列：#${job.id}`)
        urlInput.value = ''
        await loadJobs()
      } catch (e) {
        message.error((e as Error).message || '创建任务失败')
      } finally {
        submitting.value = false
      }
    },
  })
}

async function onSubmitJob() {
  const url = urlInput.value.trim()
  if (!url) {
    message.warning('先粘贴 YouTube 链接')
    return
  }
  submitting.value = true
  try {
    // 先查重：命中就停下提醒，不建任务（业主口径：重复则先不下载）
    const check = await downloadsApi.check(url, mode.value)
    if (check.duplicate) {
      submitting.value = false
      confirmDuplicate(check)
      return
    }
    const job = await downloadsApi.createJob(url, mode.value, false, downloadSubtitles.value)
    message.success(
      job.status === 'done' ? '这条视频之前已经下载过' : `已加入队列：#${job.id} ${jobTitle(job)}`,
    )
    urlInput.value = ''
    await loadJobs()
  } catch (e) {
    message.error((e as Error).message || '创建任务失败')
  } finally {
    submitting.value = false
  }
}

async function onCancel(job: DownloadJob) {
  try {
    await downloadsApi.cancelJob(job.id)
    await loadJobs()
  } catch (e) {
    message.error((e as Error).message || '取消失败')
  }
}

async function onRetry(job: DownloadJob) {
  try {
    await downloadsApi.retryJob(job.id)
    message.success('已重新排队')
    await loadJobs()
  } catch (e) {
    message.error((e as Error).message || '重试失败')
  }
}

function onDelete(job: DownloadJob) {
  dialog.warning({
    title: '删除任务记录',
    content: `只删除这条任务记录，已下载到「待整理」的文件不会被删掉。确认删除 #${job.id}？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await downloadsApi.deleteJob(job.id)
        await loadJobs()
      } catch (e) {
        message.error((e as Error).message || '删除失败')
      }
    },
  })
}

async function onSubmitSub() {
  const url = subInput.value.trim()
  if (!url) {
    message.warning('先粘贴收藏夹 / 播放列表链接')
    return
  }
  subSubmitting.value = true
  try {
    await downloadsApi.createSubscription(url, subDownloadExisting.value)
    message.success(
      subDownloadExisting.value
        ? '订阅成功：下次检查会把列表里已有的视频也排进队列'
        : '订阅成功：只追新视频，列表里已有的不会下载',
    )
    subInput.value = ''
    subDownloadExisting.value = false
    await loadSubs()
  } catch (e) {
    message.error((e as Error).message || '订阅失败')
  } finally {
    subSubmitting.value = false
  }
}

async function onToggleSub(sub: DownloadSubscription) {
  try {
    await downloadsApi.updateSubscription(sub.id, { enabled: !sub.enabled })
    await loadSubs()
  } catch (e) {
    message.error((e as Error).message || '操作失败')
  }
}

async function onCheckSub(sub: DownloadSubscription) {
  checkingId.value = sub.id
  try {
    const r = await downloadsApi.checkSubscription(sub.id)
    if (r.error) {
      message.error(`检查失败：${r.error}`)
    } else {
      message.success(
        `列表 ${r.fetched} 条 · 新增 ${r.new} 条 · 入队 ${r.enqueued} 条 · 跳过 ${r.skipped} 条`,
      )
    }
    await Promise.all([loadSubs(), loadJobs()])
  } catch (e) {
    message.error((e as Error).message || '检查失败')
  } finally {
    checkingId.value = null
  }
}

function onDeleteSub(sub: DownloadSubscription) {
  dialog.warning({
    title: '取消订阅',
    content: `不再自动追 ${sub.name || sub.url} 的新视频（已下载的文件不受影响）。`,
    positiveText: '取消订阅',
    negativeText: '保留',
    onPositiveClick: async () => {
      try {
        await downloadsApi.deleteSubscription(sub.id)
        await loadSubs()
      } catch (e) {
        message.error((e as Error).message || '删除失败')
      }
    },
  })
}

onMounted(async () => {
  await refreshAll()
  // 有活跃任务时 2s 刷一次进度，空闲时 10s 兜底
  timer = window.setInterval(
    () => {
      void loadJobs()
      if (!activeJobs.value.length) void loadSubs()
    },
    activeJobs.value.length ? 2000 : 10000,
  )
})
onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})
</script>

<template>
  <section class="sa-section yt-panel">
    <div class="section-head">
      <h3 class="section-title">YouTube 下载</h3>
      <span class="section-tag">yt-dlp</span>
    </div>

    <!-- 引擎状态 -->
    <div class="yt-engine" :class="{ 'yt-engine--bad': !ytdlp?.available }">
      <div class="yt-engine-main">
        <div class="yt-engine-line">
          <span class="yt-dot" :class="{ 'yt-dot--ok': ytdlp?.available }" />
          <strong>{{ ytdlp?.available ? 'yt-dlp 已就绪' : 'yt-dlp 不可用' }}</strong>
          <code v-if="ytdlp?.version" class="yt-ver">{{ ytdlp.version }}</code>
        </div>
        <div v-if="ytdlp?.path" class="yt-engine-sub mono">{{ ytdlp.path }}</div>
        <div v-if="ytdlp?.archive_path" class="yt-engine-sub mono">
          下载存档 {{ ytdlp?.archive_count ?? 0 }} 条 · {{ ytdlp.archive_path }}
        </div>
        <div v-if="ytdlp?.error" class="yt-engine-sub yt-engine-sub--err">{{ ytdlp.error }}</div>
        <div class="yt-engine-sub">
          代理 <code class="mono">{{ ytdlp?.proxy || '直连（未配置代理）' }}</code>
          <span class="yt-proxy-from">来自「设置 → 站点获取代理」，下载与更新共用</span>
        </div>
        <div v-if="probe" class="yt-probe">
          <span
            v-for="r in probe.results"
            :key="r.source"
            class="yt-probe-item"
            :class="r.ok ? 'is-ok' : 'is-bad'"
            :title="r.error || r.final_host"
          >
            {{ r.source }} {{ r.ok ? `${r.ms}ms` : '失败' }}
          </span>
        </div>
        <div class="yt-engine-sub">
          当前通道 <code class="mono">{{ channel }}</code> · 升级只会替换
          <code class="mono">{{ ytdlp?.install_dir || '/data/tools' }}/yt-dlp</code>，
          不需要重建镜像。
        </div>
      </div>
      <div class="yt-engine-actions">
        <div class="yt-mode" title="只提供每日构建：跟进 YouTube 风控最快">
          <span class="yt-mode-btn is-on yt-mode-btn--static">Nightly · 每日构建</span>
        </div>
        <button class="yt-btn" type="button" :disabled="probing" @click="onTestNetwork">
          {{ probing ? '检测中…' : '检测网络' }}
        </button>
        <button class="yt-btn" type="button" :disabled="updating" @click="onUpdateYtdlp">
          {{ updating ? '更新中…' : '更新 yt-dlp' }}
        </button>
      </div>
    </div>

    <!-- 链接下载 -->
    <div class="yt-block">
      <div class="yt-block-head">
        <span>粘贴链接下载</span>
        <span class="yt-hint">命名模板、画质与音轨都按库内既有口径自动套用</span>
      </div>
      <div class="yt-form">
        <input
          v-model="urlInput"
          class="yt-input"
          type="text"
          placeholder="https://www.youtube.com/watch?v=… 或 收藏夹列表链接（含 list=…）"
          @keyup.enter="onSubmitJob"
        />
        <div class="yt-mode">
          <button
            type="button"
            class="yt-mode-btn"
            :class="{ 'is-on': mode === 'video' }"
            @click="mode = 'video'"
          >单个视频</button>
          <button
            type="button"
            class="yt-mode-btn"
            :class="{ 'is-on': mode === 'playlist' }"
            @click="mode = 'playlist'"
          >整个列表</button>
        </div>
        <label class="yt-subs">
          <input v-model="downloadSubtitles" type="checkbox" />
          <span>同时下载字幕（中文优先）</span>
        </label>
        <button class="yt-btn yt-btn--primary" type="button" :disabled="submitting" @click="onSubmitJob">
          {{ submitting ? '解析中…' : '开始下载' }}
        </button>
      </div>
      <p class="yt-note">
        画质 <code class="mono">bestvideo+bestaudio</code> · 合并为
        <code class="mono">mkv</code> · 落盘
        <code class="mono">incoming</code> · 命名
        <code class="mono">%(upload_date&gt;%y-%m-%d)s %(title)s[%(uploader)s] (%(id)s)</code>
      </p>
    </div>

    <!-- 任务列表 -->
    <div class="yt-block">
      <div class="yt-block-head">
        <span>下载任务（{{ jobs.length }}）</span>
        <span v-if="activeJobs.length" class="yt-hint">进行中 {{ activeJobs.length }}</span>
      </div>

      <div v-if="!jobs.length" class="yt-empty">还没有下载任务</div>
      <ul v-else class="yt-jobs">
        <li v-for="j in jobs" :key="j.id" class="yt-job" :class="`yt-job--${j.status}`">
          <div class="yt-job-top">
            <span class="yt-badge">{{ statusLabel(j.status) }}</span>
            <span class="yt-job-title" :title="j.url">{{ jobTitle(j) }}</span>
            <span class="yt-job-meta mono">
              {{ j.mode === 'playlist' ? '列表' : '单视频' }}
              <template v-if="j.status === 'done' && filesOf(j).length"> · {{ formatBytes(filesOf(j).reduce((s, f) => s + f.size, 0)) }}</template><template v-else-if="j.total_bytes"> · {{ formatBytes(j.total_bytes) }}</template>
            </span>
          </div>

          <div
            v-if="['queued', 'downloading', 'processing'].includes(j.status)"
            class="yt-progress"
          >
            <div class="yt-progress-bar" :style="{ width: `${Math.max(2, j.progress)}%` }" />
          </div>

          <ul v-if="j.status === 'done' && filesOf(j).length" class="yt-files">
            <li v-for="f in filesOf(j)" :key="f.name" class="yt-file">
              <span class="yt-file-kind">{{ kindLabel(f.kind) }}</span>
              <span class="yt-file-size mono">{{ formatBytes(f.size) }}</span>
              <span v-if="f.width && f.height" class="yt-file-dim mono">{{ f.width }}×{{ f.height }}</span>
              <span class="yt-file-name mono" :title="f.name">{{ f.name }}</span>
            </li>
          </ul>

          <div class="yt-job-bottom">
            <span v-if="j.status === 'downloading'" class="yt-job-stats mono">
              {{ j.progress.toFixed(1) }}%<template v-if="j.speed"> · {{ j.speed }}</template
              ><template v-if="j.eta"> · 剩余 {{ j.eta }}</template>
            </span>
            <span v-else-if="j.status === 'done'" class="yt-job-stats mono">
              {{ j.output_path || '已落盘' }}
            </span>
            <span v-else-if="j.status === 'failed'" class="yt-job-stats yt-job-stats--err">
              {{ j.error }}
            </span>
            <span v-else class="yt-job-stats" />

            <div class="yt-job-actions">
              <button
                v-if="['queued', 'downloading', 'processing'].includes(j.status)"
                class="yt-link"
                type="button"
                @click="onCancel(j)"
              >取消</button>
              <button
                v-if="['failed', 'canceled'].includes(j.status)"
                class="yt-link"
                type="button"
                @click="onRetry(j)"
              >重试</button>
              <button
                v-if="j.status === 'done'"
                class="yt-link"
                type="button"
                :disabled="subsBusy === j.id"
                @click="onFetchSubs(j)"
              >{{ subsBusy === j.id ? '下载中…' : '补中文字幕' }}</button>
              <button
                v-if="!['downloading', 'processing'].includes(j.status)"
                class="yt-link yt-link--del"
                type="button"
                @click="onDelete(j)"
              >删除</button>
            </div>
          </div>
        </li>
      </ul>
    </div>

    <!-- 收藏夹订阅 -->
    <div class="yt-block">
      <div class="yt-block-head">
        <span>收藏夹订阅（{{ subs.length }}）</span>
        <span class="yt-hint">定时检查列表里的新视频并自动入队</span>
      </div>

      <div class="yt-form">
        <input
          v-model="subInput"
          class="yt-input"
          type="text"
          placeholder="粘贴收藏夹 / 播放列表链接（https://www.youtube.com/playlist?list=…）"
          @keyup.enter="onSubmitSub"
        />
        <label class="yt-check">
          <input v-model="subDownloadExisting" type="checkbox" />
          连已有视频一起补下
        </label>
        <button class="yt-btn yt-btn--primary" type="button" :disabled="subSubmitting" @click="onSubmitSub">
          {{ subSubmitting ? '提交中…' : '订阅' }}
        </button>
      </div>

      <div v-if="!subs.length" class="yt-empty">还没有订阅</div>
      <ul v-else class="yt-subs">
        <li v-for="s in subs" :key="s.id" class="yt-sub" :class="{ 'is-off': !s.enabled }">
          <div class="yt-sub-main">
            <div class="yt-sub-title">
              {{ s.name || s.url }}
              <span v-if="!s.enabled" class="yt-badge yt-badge--muted">已暂停</span>
            </div>
            <div class="yt-sub-url mono">{{ s.url }}</div>
            <div class="yt-sub-meta">
              <span>累计入队 {{ s.total_enqueued }}</span>
              <span v-if="s.last_checked_at"> · 上次检查 {{ s.last_checked_at.replace('T', ' ').slice(0, 16) }}</span>
              <span v-if="s.last_error" class="yt-sub-err"> · {{ s.last_error }}</span>
            </div>
          </div>
          <div class="yt-sub-actions">
            <button class="yt-link" type="button" :disabled="checkingId === s.id" @click="onCheckSub(s)">
              {{ checkingId === s.id ? '检查中…' : '立即检查' }}
            </button>
            <button class="yt-link" type="button" @click="onToggleSub(s)">
              {{ s.enabled ? '暂停' : '启用' }}
            </button>
            <button class="yt-link yt-link--del" type="button" @click="onDeleteSub(s)">取消订阅</button>
          </div>
        </li>
      </ul>
    </div>

    <p v-if="loading" class="yt-loading">加载中…</p>
  </section>
</template>

<style scoped>
.yt-subs {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  color: var(--sa-text-secondary);
  cursor: pointer;
  white-space: nowrap;
}
.yt-subs input {
  width: 14px;
  height: 14px;
  accent-color: var(--sa-accent);
  cursor: pointer;
}
.yt-files {
  list-style: none;
  margin: 10px 0 4px;
  padding: 10px 0 0;
  border-top: 1px solid var(--sa-border-subtle);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.yt-file {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11.5px;
  color: var(--sa-text-secondary);
}
.yt-file-kind {
  flex: 0 0 34px;
  color: var(--sa-text-tertiary);
  font-size: 11px;
}
.yt-file-size {
  flex: 0 0 70px;
  color: var(--sa-text-primary);
  font-variant-numeric: tabular-nums;
}
.yt-file-dim {
  flex: 0 0 76px;
  color: var(--sa-text-tertiary);
  font-variant-numeric: tabular-nums;
}
.yt-file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--sa-text-tertiary);
  font-size: 10.5px;
}

.yt-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

/* ---- 引擎卡 ---- */
.yt-engine {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border: 1px solid var(--sa-border-subtle);
  border-left: 3px solid var(--sa-success, #34d399);
  background: var(--sa-elevated);
}
.yt-engine--bad {
  border-left-color: var(--sa-danger, #f87171);
}
.yt-engine-main {
  min-width: 0;
}
.yt-engine-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.yt-proxy-from {
  margin-left: 6px;
  opacity: 0.7;
}
.yt-probe {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.yt-probe-item {
  padding: 1px 8px;
  border: 1px solid var(--sa-border-subtle);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 10px;
}
.yt-probe-item.is-ok {
  border-color: var(--sa-success, #34d399);
  color: var(--sa-success, #34d399);
}
.yt-probe-item.is-bad {
  border-color: var(--sa-danger, #f87171);
  color: var(--sa-danger, #f87171);
}
.yt-engine-line {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.yt-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--sa-danger, #f87171);
}
.yt-dot--ok {
  background: var(--sa-success, #34d399);
}
.yt-ver {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--sa-text-tertiary);
}
.yt-engine-sub {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
  word-break: break-all;
}
.yt-engine-sub--err {
  color: var(--sa-danger, #f87171);
}

/* ---- 通用块 ---- */
.yt-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-elevated);
}
.yt-block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 12.5px;
  font-weight: 600;
}
.yt-hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--sa-text-tertiary);
}
.yt-note {
  margin: 0;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  line-height: 1.7;
}
.yt-note code,
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 0.95em;
}

/* ---- 表单 ---- */
.yt-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.yt-input {
  flex: 1;
  min-width: 220px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--sa-border-subtle);
  background: var(--sa-bg);
  color: var(--sa-text-primary);
  font: inherit;
  font-size: 12px;
  outline: none;
  transition: border-color 0.2s ease;
}
.yt-input:focus {
  border-color: var(--sa-accent);
}
.yt-mode {
  display: inline-flex;
  border: 1px solid var(--sa-border-subtle);
}
.yt-mode-btn {
  padding: 0 12px;
  height: 34px;
  border: none;
  background: none;
  color: var(--sa-text-tertiary);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
  transition: background-color 0.2s ease, color 0.2s ease;
}
.yt-mode-btn.is-on {
  background: var(--sa-text-primary);
  color: var(--sa-bg);
  font-weight: 600;
}
/* 只有一个通道时它是个状态标，不是可点按钮 */
.yt-mode-btn--static {
  cursor: default;
  user-select: none;
}
.yt-btn {
  height: 34px;
  padding: 0 16px;
  border: 1px solid var(--sa-border-subtle);
  background: none;
  color: var(--sa-text-primary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: border-color 0.2s ease, background-color 0.2s ease, opacity 0.2s ease;
}
.yt-btn:hover:not(:disabled) {
  border-color: var(--sa-text-primary);
}
.yt-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.yt-btn--primary {
  background: var(--sa-text-primary);
  border-color: var(--sa-text-primary);
  color: var(--sa-bg);
  font-weight: 600;
}
.yt-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--sa-text-secondary);
  white-space: nowrap;
}

/* ---- 任务 ---- */
.yt-empty {
  padding: 18px 0;
  text-align: center;
  font-size: 12px;
  color: var(--sa-text-tertiary);
}
.yt-jobs,
.yt-subs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.yt-job,
.yt-sub {
  padding: 10px 0;
  border-top: 1px solid var(--sa-border-subtle);
}
.yt-jobs > .yt-job:first-child,
.yt-subs > .yt-sub:first-child {
  border-top: none;
}
.yt-job-top {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.yt-badge {
  flex-shrink: 0;
  padding: 1px 7px;
  border: 1px solid var(--sa-border-subtle);
  font-size: 10px;
  color: var(--sa-text-tertiary);
}
.yt-job--downloading .yt-badge,
.yt-job--processing .yt-badge {
  border-color: var(--sa-accent);
  color: var(--sa-accent);
}
.yt-job--done .yt-badge {
  border-color: var(--sa-success, #34d399);
  color: var(--sa-success, #34d399);
}
.yt-job--failed .yt-badge {
  border-color: var(--sa-danger, #f87171);
  color: var(--sa-danger, #f87171);
}
.yt-job-title {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yt-job-meta {
  flex-shrink: 0;
  font-size: 10.5px;
  color: var(--sa-text-tertiary);
}
.yt-progress {
  margin: 8px 0 6px;
  height: 3px;
  background: var(--sa-subtle);
  overflow: hidden;
}
.yt-progress-bar {
  height: 100%;
  background: var(--sa-accent);
  transition: width 0.4s ease;
}
.yt-job-bottom {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-top: 4px;
}
.yt-job-stats {
  min-width: 0;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yt-job-stats--err {
  color: var(--sa-danger, #f87171);
  white-space: pre-wrap;
  max-height: 72px;
  overflow: auto;
}
.yt-job-actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}
.yt-link {
  padding: 0;
  border: none;
  background: none;
  font: inherit;
  font-size: 11px;
  color: var(--sa-text-tertiary);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}
.yt-link:hover:not(:disabled) {
  color: var(--sa-text-primary);
}
.yt-link:disabled {
  opacity: 0.5;
  cursor: default;
}
.yt-link--del:hover {
  color: var(--sa-danger, #f87171);
}

/* ---- 订阅 ---- */
.yt-sub {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
}
.yt-sub.is-off {
  opacity: 0.6;
}
.yt-sub-main {
  min-width: 0;
}
.yt-sub-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12.5px;
  font-weight: 600;
}
.yt-badge--muted {
  font-weight: 400;
}
.yt-sub-url {
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--sa-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.yt-sub-meta {
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--sa-text-tertiary);
}
.yt-sub-err {
  color: var(--sa-danger, #f87171);
}
.yt-sub-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.yt-loading {
  margin: 0;
  font-size: 11.5px;
  color: var(--sa-text-tertiary);
}

/* ==========================================================================
   与设置页统一（v3.9.994）：标题衬线、控件 32px、字段行 48px
   —— 原来这套 yt-* 是 34px 控件 + 无衬线粗标题，和其余分组不是一套语言。
   ========================================================================== */
.yt-panel {
  padding: 22px 24px 12px;
}
.section-head {
  margin-bottom: 4px;
  padding-bottom: 13px;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.section-title {
  font-family: 'Noto Serif SC', 'Songti SC', serif;
  font-size: 17.5px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--sa-text-primary);
}
.section-tag {
  font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace;
  font-size: 9.5px;
  font-weight: 500;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--sa-text-tertiary);
  border: 1px solid var(--sa-border-subtle);
  border-radius: 6px;
  padding: 2px 7px;
}
.yt-input {
  height: 32px;
  padding: 0 11px;
  border-radius: 8px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  font-size: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.85);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.yt-input:focus {
  border-color: var(--sa-accent);
  box-shadow: 0 0 0 3px var(--sa-accent-subtle);
  outline: none;
}
.yt-btn {
  height: 32px;
  padding: 0 13px;
  border-radius: 8px;
  border: 1px solid var(--sa-border);
  background: var(--sa-elevated);
  font-size: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03), inset 0 1px 0 rgba(255, 255, 255, 0.85);
  transition: border-color 0.2s ease, color 0.2s ease, transform 0.2s cubic-bezier(0.22, 1, 0.36, 1);
}
.yt-btn:hover {
  border-color: color-mix(in oklab, var(--sa-accent) 40%, var(--sa-border));
}
.yt-btn:active {
  transform: scale(0.975);
}
.yt-btn--primary {
  background: var(--sa-accent);
  border-color: transparent;
  color: #fff;
  box-shadow: 0 1px 2px rgba(4, 133, 247, 0.25), 0 6px 16px -8px rgba(4, 133, 247, 0.55);
}
.yt-mode-btn {
  height: 32px;
  font-size: 11.5px;
}
.yt-hint,
.yt-note {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--sa-text-tertiary);
}
.yt-block {
  padding: 14px 0;
  border-bottom: 1px solid var(--sa-border-subtle);
}
.yt-block:last-child {
  border-bottom: none;
}
.yt-block-head {
  font-family: 'Noto Serif SC', 'Songti SC', serif;
  font-size: 15px;
  font-weight: 500;
  letter-spacing: 0.02em;
  color: var(--sa-text-primary);
  margin-bottom: 8px;
}
.yt-check,
.yt-job-title {
  font-size: 12.5px;
}
.yt-engine {
  border-radius: 12px;
  margin-top: 14px;
}

.yt-subs {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--sa-text-secondary);
  cursor: pointer;
  user-select: none;
}
.yt-subs input {
  accent-color: var(--sa-accent);
}
</style>
