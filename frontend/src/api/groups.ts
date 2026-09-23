import http from './index'
import type {
  EntityImageKind,
  EntityImageListResult,
  EntityImagePointerResult,
  FetchExternalPayload,
  FetchExternalResult,
  Group,
  GroupBrief,
  GroupFuzzyHit,
  MembershipWithArtist,
  Pagination,
} from '@/types/models'

export interface ListParams {
  q?: string
  group_type?: string
  sort?: string
  page?: number
  page_size?: number
  filter?: 'all' | 'linked' | 'orphan'
}

export const groupsApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Group>>('/groups', { params }).then((r) => r.data)
  },
  brief(q?: string) {
    return http.get<GroupBrief[]>('/groups/brief', { params: { q } }).then((r) => r.data)
  },
  fuzzy(q: string) {
    return http.get<GroupFuzzyHit[]>('/groups/fuzzy', { params: { q } }).then((r) => r.data)
  },
  relatedArtists(groupIds: number[]) {
    const ids = groupIds.filter((n) => Number.isFinite(n) && n > 0).join(',')
    if (!ids) return Promise.resolve({ artist_ids: [] as number[] })
    return http
      .get<{ artist_ids: number[] }>('/groups/related-artists', { params: { ids } })
      .then((r) => r.data)
  },
  get(id: number) {
    return http.get<Group>(`/groups/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<Group>(`/groups/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  create(payload: Partial<Group>) {
    return http.post<Group>('/groups', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Group>) {
    return http.patch<Group>(`/groups/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/groups/${id}`).then((r) => r.data)
  },
  members(groupId: number, status?: string) {
    return http
      .get<MembershipWithArtist[]>(`/memberships/by_group/${groupId}`, {
        params: { status },
      })
      .then((r) => r.data)
  },
  fetchExternal(id: number, payload: FetchExternalPayload) {
    return http
      .post<FetchExternalResult>(`/groups/${id}/fetch-external`, payload)
      .then((r) => r.data)
  },
  avatarUrl(id: number) {
    return `/api/groups/${id}/avatar`
  },
  bannerUrl(id: number) {
    return `/api/groups/${id}/banner`
  },
  uploadAvatar(id: number, file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<{ avatar_path: string | null }>(`/uploads/avatar/group/${id}`, fd)
      .then((r) => r.data)
  },
  removeAvatar(id: number) {
    return http
      .delete<{ avatar_path: string | null }>(`/uploads/avatar/group/${id}`)
      .then((r) => r.data)
  },
  uploadBanner(id: number, file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<{ banner_path: string | null }>(`/uploads/banner/group/${id}`, fd)
      .then((r) => r.data)
  },
  removeBanner(id: number) {
    return http
      .delete<{ banner_path: string | null }>(`/uploads/banner/group/${id}`)
      .then((r) => r.data)
  },
  listImages(id: number, kind?: EntityImageKind) {
    return http
      .get<EntityImageListResult>(`/uploads/images/group/${id}`, { params: { kind } })
      .then((r) => r.data)
  },
  promoteImage(id: number, imageId: number) {
    return http
      .post<EntityImagePointerResult>(`/uploads/images/group/${id}/${imageId}/promote`)
      .then((r) => r.data)
  },
  deleteImage(id: number, imageId: number) {
    return http
      .delete<EntityImagePointerResult>(`/uploads/images/group/${id}/${imageId}`)
      .then((r) => r.data)
  },
  imageFileUrl(id: number, imageId: number) {
    return `/api/uploads/images/group/${id}/${imageId}/file`
  },
}
