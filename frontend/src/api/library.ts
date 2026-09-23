import http from './index'
import type {
  BuildTitle,
  DbRelations,
  IncomingInfo,
  IngestRequest,
  IngestResult,
  LibraryCleanupResult,
  MatchHints,
  PathPreview,
  ScanSyncStats,
  ScannedFileItem,
  SystemPaths,
} from '@/types/models'

export const libraryApi = {
  dbRelations(kind: 'songs' | 'albums' | 'artists' | 'groups' | 'companies', id: number) {
    return http
      .get<DbRelations>(`/library/db-relations/${kind}/${id}`)
      .then((r) => r.data)
  },
  scan() {
    return http.get<ScannedFileItem[]>('/library/scan').then((r) => r.data)
  },
  matchHints(filePath: string) {
    return http
      .get<MatchHints>('/library/match-hints', {
        params: { file_path: filePath },
      })
      .then((r) => r.data)
  },
  scanSync() {
    return http.post<ScanSyncStats>('/library/scan/sync').then((r) => r.data)
  },
  info(filePath: string) {
    return http.post<IncomingInfo>('/library/info', { file_path: filePath }).then((r) => r.data)
  },
  localCoverUrl(filePath: string) {
    return `/api/library/local-cover?file_path=${encodeURIComponent(filePath)}`
  },
  ingest(payload: IngestRequest) {
    // 大文件入库（跨盘/网络拷贝 + 缩略图）可能远超全局 30s，放宽到 10 分钟
    return http
      .post<IngestResult>('/library/ingest', payload, { timeout: 600_000 })
      .then((r) => r.data)
  },
  previewPath(payload: {
    file_name: string
    group_name?: string | null
    title?: string | null
    video_type?: string | null
    is_short?: boolean
    is_solo?: boolean
    duration?: number | null
    performance_date?: string | null
    song_ids?: number[]
    draft_song_names?: string[]
    draft_artist_names?: string[]
    draft_group_names?: string[]
    draft_subject_artist_name?: string | null
    artist_ids?: number[]
    group_ids?: number[]
    video_types?: string[]
  }) {
    return http.post<PathPreview>('/library/preview-path', payload).then((r) => r.data)
  },
  buildTitle(payload: {
    performance_date?: string | null
    published_date?: string | null
    song_ids?: number[]
    artist_ids?: number[]
    group_ids?: number[]
    subject_artist_id?: number | null
    event_name?: string | null
    video_types?: string[]
  }) {
    return http.post<BuildTitle>('/library/build-title', payload).then((r) => r.data)
  },
  directories() {
    return http.get<string[]>('/library/directories').then((r) => r.data)
  },
  paths() {
    return http.get<SystemPaths>('/library/paths').then((r) => r.data)
  },
  missing() {
    return http.get<LibraryCleanupResult>('/library/missing').then((r) => r.data)
  },
  cleanupMissing(force = false, purgeFiles = false) {
    return http
      .post<LibraryCleanupResult>('/library/cleanup-missing', null, {
        params: { force, purge_files: purgeFiles },
        timeout: 60_000,
      })
      .then((r) => r.data)
  },
}
