import http from './index'
import type { Company, CompanyRelations, Pagination } from '@/types/models'

export interface ListParams {
  q?: string
  sort?: string
  page?: number
  page_size?: number
}

export const companiesApi = {
  list(params: ListParams = {}) {
    return http.get<Pagination<Company>>('/companies', { params }).then((r) => r.data)
  },
  brief(q?: string) {
    return http.get<Company[]>('/companies/brief', { params: { q } }).then((r) => r.data)
  },
  get(id: number) {
    return http.get<Company>(`/companies/${id}`).then((r) => r.data)
  },
  getByUid(uid: string) {
    return http.get<Company>(`/companies/by-uid/${encodeURIComponent(uid)}`).then((r) => r.data)
  },
  relations(id: number) {
    return http.get<CompanyRelations>(`/companies/${id}/relations`).then((r) => r.data)
  },
  create(payload: Partial<Company>) {
    return http.post<Company>('/companies', payload).then((r) => r.data)
  },
  update(id: number, payload: Partial<Company>) {
    return http.patch<Company>(`/companies/${id}`, payload).then((r) => r.data)
  },
  remove(id: number) {
    return http.delete(`/companies/${id}`).then((r) => r.data)
  },
}
