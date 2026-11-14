import http from './index'

/** 下载任务状态（与后端 download_jobs.status 一致） */
export type DownloadStatus =
  | 'queued'
  | 'downloading'
  | 'processing'
  | 'done'
  | 'failed'
  | 'canceled'

export interface DownloadJob {
  /** 落盘明细 JSON 字符串：[{kind,name,size,width,height}] */
  outputs?: string | null
  /** 本次是否下载字幕 */
  download_subtitles?: boolean

  id: number
  url: string
  video_id?: string | null
  mode: 'video' | 'playlist' | string
  title?: string | null
  uploader?: string | null
  duration?: number | null
  status: DownloadStatus | string
  progress: number
  downloaded_bytes?: number | null
  total_bytes?: number | null
  speed?: string | null
  eta?: string | null
  error?: string | null
  output_path?: string | null
  subscription_id?: number | null
  created_at?: string | null
  started_at?: string | null
  finished_at?: string | null
}

export interface DownloadSubscription {
  id: number
  url: string
  name?: string | null
  enabled: boolean
  download_existing: boolean
  initialized: boolean
  last_checked_at?: string | null
  last_error?: string | null
  total_enqueued: number
  created_at?: string | null
}

/** 只保留 nightly（稳定版已下线，2026-10-31） */
export type YtdlpChannel = 'nightly'

export interface YtdlpStatus {
  available: boolean
  version: string
  path: string
  install_dir: string
  updatable: boolean
  /** 当前更新通道：恒为 nightly（每日构建） */
  channel?: YtdlpChannel
  /** 生效的下载代理（已脱敏），来自「设置 → 站点获取代理」 */
  proxy?: string
  proxy_source?: string
  /** 本地下载存档路径（incoming/download-archive.txt） */
  archive_path?: string
  /** 存档里已记录的 video id 数量 */
  archive_count?: number
  error?: string | null
}

export interface NetworkProbeItem {
  source: string
  ok: boolean
  ms: number
  bytes: number
  final_host: string
  error: string
}

export interface NetworkProbeResult {
  channel: string
  proxy: string
  proxy_source: string
  results: NetworkProbeItem[]
}

/** 下载前预检结果（含重复命中明细） */
export interface DuplicateMatch {
  where: string
  label: string
  id?: number | null
  uid?: string | null
  name?: string | null
  file_name?: string | null
  resolution: string
  file_size: string
  duration?: number | null
}

export interface DownloadCheckResult {
  url: string
  video_id?: string | null
  title?: string | null
  uploader?: string | null
  duration?: number | null
  duplicate: boolean
  message: string
  matches: DuplicateMatch[]
}

export interface SubscriptionCheckResult {
  subscription_id: number
  fetched: number
  new: number
  enqueued: number
  skipped: number
  error: string
}

export const downloadsApi = {
  jobs(status?: string, limit = 60) {
    return http
      .get<DownloadJob[]>('/downloads/jobs', { params: { status, limit } })
      .then((r) => r.data)
  },
  /** 下载前预检：解析链接 + 查重，不建任务 */
  check(url: string, mode: 'video' | 'playlist' = 'video') {
    return http
      .post<DownloadCheckResult>('/downloads/check', { url, mode })
      .then((r) => r.data)
  },
  createJob(
    url: string,
    mode: 'video' | 'playlist' = 'video',
    force = false,
    downloadSubtitles = false,
  ) {
    return http
      .post<DownloadJob>('/downloads/jobs', {
        url,
        mode,
        force,
        download_subtitles: downloadSubtitles,
      })
      .then((r) => r.data)
  },
  fetchSubtitles(id: number) {
    return http.post<{ started: boolean }>(`/downloads/jobs/${id}/subtitles`).then((r) => r.data)
  },

  cancelJob(id: number) {
    return http.post<DownloadJob>(`/downloads/jobs/${id}/cancel`).then((r) => r.data)
  },
  retryJob(id: number) {
    return http.post<DownloadJob>(`/downloads/jobs/${id}/retry`).then((r) => r.data)
  },
  deleteJob(id: number) {
    return http.delete<void>(`/downloads/jobs/${id}`).then((r) => r.data)
  },

  subscriptions() {
    return http.get<DownloadSubscription[]>('/downloads/subscriptions').then((r) => r.data)
  },
  createSubscription(url: string, downloadExisting = false) {
    return http
      .post<DownloadSubscription>('/downloads/subscriptions', {
        url,
        download_existing: downloadExisting,
      })
      .then((r) => r.data)
  },
  updateSubscription(id: number, patch: { enabled?: boolean; name?: string }) {
    return http
      .patch<DownloadSubscription>(`/downloads/subscriptions/${id}`, patch)
      .then((r) => r.data)
  },
  checkSubscription(id: number) {
    return http
      .post<SubscriptionCheckResult>(`/downloads/subscriptions/${id}/check`)
      .then((r) => r.data)
  },
  deleteSubscription(id: number) {
    return http.delete<void>(`/downloads/subscriptions/${id}`).then((r) => r.data)
  },

  ytdlp() {
    return http.get<YtdlpStatus>('/downloads/ytdlp').then((r) => r.data)
  },
  testNetwork(channel?: YtdlpChannel) {
    return http
      .post<NetworkProbeResult>('/downloads/ytdlp/test-network', channel ? { channel } : {})
      .then((r) => r.data)
  },
  updateYtdlp(channel?: YtdlpChannel) {
    return http
      .post<YtdlpStatus>('/downloads/ytdlp/update', channel ? { channel } : {})
      .then((r) => r.data)
  },
}
