import http from './index'
import type { Membership, MembershipWithArtist, MembershipWithGroup, Pagination } from '@/types/models'

export interface ListParams {
  group_id?: number
  artist_id?: number
  status?: string
  page?: number
  page_size?: number
}

export const membershipsApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Membership>>('/memberships', { params }).then((r) => r.data)
  },
  byGroup(groupId: number, status?: string) {
    return http
      .get<MembershipWithArtist[]>('/memberships/by_group/' + groupId, { params: { status } })
      .then((r) => r.data)
  },
  /** 批量：一次取多位艺人的在团关系（首页立绘墙用，避免 20 个请求） */
  byArtists(artistIds: number[], status?: string) {
    return http
      .get<MembershipWithGroup[]>('/memberships/by_artists', {
        params: { ids: artistIds.join(','), status },
      })
      .then((r) => r.data)
  },
  byArtist(artistId: number, status?: string) {
    return http
      .get<MembershipWithGroup[]>('/memberships/by_artist/' + artistId, { params: { status } })
      .then((r) => r.data)
  },
  create(payload: Partial<Membership>) {
    return http.post<Membership>('/memberships', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Membership>) {
    return http.patch<Membership>(`/memberships/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/memberships/${id}`).then((r) => r.data)
  },
}
