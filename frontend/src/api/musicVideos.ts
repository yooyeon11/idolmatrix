import http from './index'
import type { AxiosRequestConfig } from 'axios'
import type {
  FolderBrowseResult,
  MusicVideo,
  MusicVideoBrief,
  MessageResponse,
  Pagination,
  ResolutionFacetResponse,
  VideoPathSuggestion,
  VideoRelocateResult,
} from '@/types/models'

export interface CoverFrameItem {
  at: number
  name: string
}

export interface CoverFramesResponse {
  items: CoverFrameItem[]
}

export interface ListParams {
  q?: string
  video_type?: string
  video_types?: string
  song_id?: number
  subject_artist_id?: number
  artist_id?: number
  group_id?: number
  ingestion_status?: string
  uploader?: string
  is_short?: boolean
  /** 分辨率档位，逗号分隔多选：8k/4k/2k/1080p/720p/480p/360p/240p/sd/unknown */
  resolution?: string
  sort?: string
  page?: number
  page_size?: number
}

export async function listAllVideos(params: ListParams = {}, config: AxiosRequestConfig = {}) {
  const pageSize = 100
  const items: MusicVideo[] = []
  let page = 1
  let total = 0
  for (;;) {
    const res = await musicVideosApi.list(
      { ...params, page, page_size: pageSize },
      config,
    )
    total = res.total
    items.push(...res.items)
    if (items.length >= total || res.items.length === 0) break
    page += 1
    if (page > 200) break
  }
  return { items, total }
}

export const musicVideosApi = {
  list(params: ListParams = {}, config: AxiosRequestConfig = {}) {
    return http.get<Pagination<MusicVideo>>('/music-videos', { params, ...config }).then((r) => r.data)
  },
  listAll: listAllVideos,
  folderBrowse(params: {
    prefix?: string
    q?: string
    video_type?: string
    video_types?: string
    is_short?: boolean
    resolution?: string
    sort?: string
    page?: number
    page_size?: number
  } = {}) {
    return http
      .get<FolderBrowseResult>('/music-videos/folder-browse', { params })
      .then((r) => r.data)
  },
  /**
   * 分辨率档位计数：浏览页「分辨率」菜单只列有条数的档位（空档位不展示）。
   * 归档口径与统计页、列表筛选同一套（按短边），所以菜单条数 = 选中后的实际条数。
   */
  resolutionFacets(
    params: {
      q?: string
      video_type?: string
      video_types?: string
      is_short?: boolean
      ingestion_status?: string
    } = {},
  ) {
    return http
      .get<ResolutionFacetResponse>('/music-videos/resolution-facets', { params })
      .then((r) => r.data)
  },
  brief(params: { video_type?: string; ingestion_status?: string; limit?: number } = {}) {
    return http.get<MusicVideoBrief[]>('/music-videos/brief', { params }).then((r) => r.data)
  },
  get(id: number) {
    return http.get<MusicVideo>(`/music-videos/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<MusicVideo>(`/music-videos/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  create(payload: Partial<MusicVideo>) {
    return http.post<MusicVideo>('/music-videos', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<MusicVideo>) {
    return http.patch<MusicVideo>(`/music-videos/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/music-videos/${id}`).then((r) => r.data)
  },
  /**
   * 封面 URL。`w` 传展示宽度档位（见 utils/imageSizes）——
   * 封面原图是视频原始分辨率或 sidecar 原图，列表位只需几百像素宽，
   * 不带 `w` 会白传几十倍体积；服务端只缩不放，源图小则自动回退原图。
   */
  thumbnailUrl(id: number, bust?: string | number | null, w?: number) {
    const params = new URLSearchParams({ r: '2' })
    if (bust != null && bust !== '') params.set('v', String(bust))
    if (w) params.set('w', String(w))
    return `/api/music-videos/${id}/thumbnail?${params.toString()}`
  },
  // ===== 手动选帧封面 =====
  coverFrameUrl(id: number, name: string) {
    return `/api/music-videos/${id}/cover-frames/${encodeURIComponent(name)}`
  },
  generateCoverFrames(id: number, exclude: number[] = [], count = 8) {
    return http
      .post<CoverFramesResponse>(`/music-videos/${id}/cover-frames`, { count, exclude })
      .then((r) => r.data)
  },
  applyCoverFrame(id: number, atSeconds: number) {
    return http
      .post<MessageResponse>(`/music-videos/${id}/cover-frames/apply`, {
        at_seconds: atSeconds,
      })
      .then((r) => r.data)
  },
  clearManualCover(id: number) {
    return http.delete<MessageResponse>(`/music-videos/${id}/cover-manual`).then((r) => r.data)
  },
  openInSystemPlayer(id: number) {
    return http
      .post<MessageResponse>(`/music-videos/${id}/open-in-system-player`)
      .then((r) => r.data)
  },
  pathSuggestion(id: number) {
    return http.get<VideoPathSuggestion>(`/music-videos/${id}/path-suggestion`).then((r) => r.data)
  },
  relocate(id: number, destinationRel?: string | null) {
    return http
      .post<VideoRelocateResult>(`/music-videos/${id}/relocate`, {
        destination_rel: destinationRel || null,
      })
      .then((r) => r.data)
  },
}
