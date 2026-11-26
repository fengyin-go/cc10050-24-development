/** 证据交付与指标复核的前端数据层：与后端 /api/parse_evidence 与 /api/collect_metrics 对齐。 */
import { request } from './client'
import type {
  EvidenceDetail,
  EvidenceReviewResult,
  EvidenceVerify,
  PageResult,
} from '@/data/types'

async function json<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok || payload === null) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : fallback
    throw new Error(detail)
  }
  return payload as T
}

export async function applyEvidencePackage(values: Record<string, string>): Promise<EvidenceDetail> {
  const response = await request('/api/parse_evidence', {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  const result = await json<{ ok: boolean; message: string; entry: EvidenceDetail }>(
    response, '证据包申请失败')
  if (!result.ok) throw new Error(result.message)
  return result.entry
}

export async function listEvidence(): Promise<PageResult> {
  const response = await request('/api/parse_evidence?page=1&size=200')
  return json<PageResult>(response, '证据交付单列表读取失败')
}

export async function getEvidence(id: number): Promise<EvidenceDetail> {
  const response = await request(`/api/parse_evidence/${id}`)
  return json<EvidenceDetail>(response, '证据交付单读取失败')
}

export async function reconcileEvidence(id: number): Promise<EvidenceDetail['分母核对']> {  const response = await request(`/api/parse_evidence/${id}/reconcile`)
  return json<EvidenceDetail['分母核对']>(response, '分母核对失败')
}

export async function supplementEvidence(
  id: number,
  values: Record<string, unknown>,
): Promise<{ ok: boolean; message: string; deduped?: boolean; summary?: EvidenceDetail }> {
  const response = await request(`/api/parse_evidence/${id}/supplement`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return json(response, '补报失败')
}

export async function confirmEvidence(
  id: number,
  rejectSupplement = false,
): Promise<{ ok: boolean; message: string; summary?: EvidenceDetail }> {
  const response = await request(`/api/parse_evidence/${id}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ values: { 拒绝补报: rejectSupplement } }),
  })
  return json(response, '确认导出失败')
}

/** 下载证据包：返回实际保存到本地的文件名与内容，交给验收端复核。 */
export async function downloadEvidencePackage(
  id: number,
): Promise<{ filename: string; text: string }> {
  const response = await request(`/api/parse_evidence/${id}/package`)
  if (!response.ok) throw new Error('证据包下载失败')
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match ? decodeURIComponent(match[1]) : `parse_evidence_${id}.csv`
  const text = await response.text()
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
  return { filename, text }
}

export async function verifyEvidenceFile(
  id: number,
  text: string,
  batch = '',
): Promise<EvidenceVerify> {
  const response = await request(`/api/parse_evidence/${id}/verify`, {
    method: 'POST',
    body: JSON.stringify({ values: { 文件内容: text, 交付批次: batch } }),
  })
  return json<EvidenceVerify>(response, '证据文件复核失败')
}

export async function linkMetricToReview(
  id: number,
  text: string,
  batch = '',
): Promise<EvidenceReviewResult & { metric_id?: number }> {
  const response = await request(`/api/parse_evidence/${id}/link`, {
    method: 'POST',
    body: JSON.stringify({ values: { 文件内容: text, 交付批次: batch } }),
  })
  const result = await json<EvidenceReviewResult & { metric_id?: number }>(
    response, '关联指标复核失败')
  if (!result.ok) throw new Error(result.message)
  return result
}

/** 指标页办理复核：证据交付单 + 下载批次 + 实际文件内容。 */
export async function reviewMetricWithEvidence(
  metricId: number,
  payload: { evidenceId: number; batch: string; text: string },
): Promise<EvidenceReviewResult> {
  const response = await request(`/api/collect_metrics/${metricId}/evidence-review`, {
    method: 'POST',
    body: JSON.stringify({
      values: {
        证据交付单: payload.evidenceId,
        证据批次: payload.batch,
        文件内容: payload.text,
      },
    }),
  })
  const result = await json<EvidenceReviewResult>(response, '指标复核失败')
  if (!result.ok) throw new Error(result.message)
  return result
}

export async function requestAuditEvidence(
  auditId: number,
): Promise<{ ok: boolean; message: string; evidence_id?: number; reused?: boolean }> {
  const response = await request(`/api/parse_audit/${auditId}/request-evidence`, {
    method: 'POST',
  })
  return json(response, '证据导出申请失败')
}

export async function listMetrics(): Promise<PageResult> {
  const response = await request('/api/collect_metrics?page=1&size=200')
  return json<PageResult>(response, '指标列表读取失败')
}
