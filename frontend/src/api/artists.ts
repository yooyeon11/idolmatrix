import http from './index'
import type {
  Artist,
  ArtistBrief,
  ArtistFuzzyHit,
  EntityImageKind,
  EntityImageListResult,
  EntityImagePointerResult,
  FetchExternalPayload,
  FetchExternalResult,
  Pagination,
} from '@/types/models'

export interface ListParams {
  q?: string
  sort?: string
  page?: number
  page_size?: number
  only_with_videos?: boolean
  filter?: 'all' | 'linked' | 'orphan'
}

export const artistsApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Artist>>('/artists', { params }).then((r) => r.data)
  },
  brief(q?: string) {
    return http.get<ArtistBrief[]>('/artists/brief', { params: { q } }).then((r) => r.data)
  },
  fuzzy(q: string) {
    return http.get<ArtistFuzzyHit[]>('/artists/fuzzy', { params: { q } }).then((r) => r.data)
  },
  get(id: number) {
    return http.get<Artist>(`/artists/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<Artist>(`/artists/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  create(payload: Partial<Artist> & { allow_name_conflict?: boolean }) {
    return http.post<Artist>('/artists', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Artist>) {
    return http.patch<Artist>(`/artists/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/artists/${id}`).then((r) => r.data)
  },
  fetchExternal(id: number, payload: FetchExternalPayload) {
    return http
      .post<FetchExternalResult>(`/artists/${id}/fetch-external`, payload)
      .then((r) => r.data)
  },
  avatarUrl(id: number) {
    return `/api/artists/${id}/avatar`
  },
  bannerUrl(id: number) {
    return `/api/artists/${id}/banner`
  },
  uploadAvatar(id: number, file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<{ avatar_path: string | null }>(`/uploads/avatar/artist/${id}`, fd)
      .then((r) => r.data)
  },
  removeAvatar(id: number) {
    return http
      .delete<{ avatar_path: string | null }>(`/uploads/avatar/artist/${id}`)
      .then((r) => r.data)
  },
  uploadBanner(id: number, file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<{ banner_path: string | null }>(`/uploads/banner/artist/${id}`, fd)
      .then((r) => r.data)
  },
  removeBanner(id: number) {
    return http
      .delete<{ banner_path: string | null }>(`/uploads/banner/artist/${id}`)
      .then((r) => r.data)
  },
  listImages(id: number, kind?: EntityImageKind) {
    return http
      .get<EntityImageListResult>(`/uploads/images/artist/${id}`, { params: { kind } })
      .then((r) => r.data)
  },
  promoteImage(id: number, imageId: number) {
    return http
      .post<EntityImagePointerResult>(`/uploads/images/artist/${id}/${imageId}/promote`)
      .then((r) => r.data)
  },
  deleteImage(id: number, imageId: number) {
    return http
      .delete<EntityImagePointerResult>(`/uploads/images/artist/${id}/${imageId}`)
      .then((r) => r.data)
  },
  imageFileUrl(id: number, imageId: number) {
    return `/api/uploads/images/artist/${id}/${imageId}/file`
  },
}
