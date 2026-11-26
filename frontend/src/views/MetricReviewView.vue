<template>
  <section class="page" data-module="collect_metrics_review">
    <header class="page-head">
      <div>
        <h2>采集量指标 · 证据复核</h2>
        <p class="page-desc">只按证据交付单实际下载文件复核：读取包内样本明细、关联历史发布量对账，并写出说明差额的复核报告。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="metric-refresh" @click="loadMetrics">刷新指标</button>
      </div>
    </header>

    <form class="review-form" @submit.prevent="runReview">
      <label>
        <span>证据交付单 ID</span>
        <input v-model="form.evidenceId" data-testid="review-evidence-id" placeholder="从证据交付单页带入" />
      </label>
      <label>
        <span>交付批次</span>
        <input v-model="form.batch" data-testid="review-batch" placeholder="下载后冻结的批次" />
      </label>
      <label>
        <span>指标记录</span>
        <select v-model="form.metricId" data-testid="review-metric">
          <option value="">按站点自动匹配</option>
          <option v-for="metric in metrics" :key="metric.id" :value="String(metric.id)">
            #{{ metric.id }} {{ metric['站点名称'] }}{{ metric['证据批次'] ? ` · ${metric['证据批次']}` : '' }}
          </option>
        </select>
      </label>
      <button class="btn primary" type="submit" data-testid="run-review" :disabled="busy">
        {{ busy ? '复核中…' : '按下载文件复核' }}
      </button>
    </form>

    <p class="file-hint" v-if="fileText" data-testid="file-hint">
      已读取实际下载文件（{{ fileMeta?.filename }} · 批次 {{ fileMeta?.batch }}），复核只用该文件的样本明细。
    </p>
    <p class="file-hint warn" v-else data-testid="file-missing">
      还没有实际下载文件：请先在证据交付单页下载证据包，验收端不会从指标表按最新版本重取。
    </p>

    <p v-if="message" :class="report?.ok === false ? 'error-text' : 'ok-text'" data-testid="review-message">
      {{ message }}
    </p>

    <section v-if="report && report.ok" class="report-panel" data-testid="review-report">
      <h3>差额复核报告 #{{ report['report_id'] }}</h3>
      <div class="detail-grid">
        <div>
          <span class="stat-label">文件样本数（抓取量）</span>
          <strong>{{ report['measured'] }}</strong>
        </div>
        <div>
          <span class="stat-label">历史发布量</span>
          <strong>{{ report['published'] }}</strong>
        </div>
        <div>
          <span class="stat-label">差额</span>
          <strong>{{ report['diff'] }}</strong>
        </div>
        <div>
          <span class="stat-label">命中 / 失配</span>
          <strong>{{ report['hit'] }} / {{ report['miss'] }}</strong>
        </div>
        <div>
          <span class="stat-label">命中率</span>
          <strong data-testid="report-hit-rate">{{ report['rate'] }}</strong>
        </div>
        <div>
          <span class="stat-label">证据批次 / 版本</span>
          <strong>{{ report['batch'] }} / {{ report['version'] }}</strong>
        </div>
      </div>
      <p class="report-note" data-testid="report-note">{{ report.message }}</p>
    </section>

    <table class="data-table">
      <thead>
        <tr>
          <th>指标记录</th>
          <th>站点名称</th>
          <th>抓取量</th>
          <th>解析量</th>
          <th>产出量</th>
          <th>证据批次</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="metric in metrics" :key="metric.id" :data-testid="`metric-${metric.id}`">
          <td>#{{ metric.id }}</td>
          <td>{{ metric['站点名称'] }}</td>
          <td>{{ metric['抓取量'] }}</td>
          <td>{{ metric['解析量'] }}</td>
          <td>{{ metric['产出量'] }}</td>
          <td>{{ metric['证据批次'] || '—' }}</td>
          <td>{{ metric.status }}</td>
        </tr>
        <tr v-if="!metrics.length">
          <td colspan="7" class="empty-state">暂无指标记录</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { listMetrics, reviewMetricWithEvidence } from '@/api/evidence-service'
import type { EntryRow, EvidenceReviewResult } from '@/data/types'

const route = useRoute()
const metrics = ref<EntryRow[]>([])
const form = reactive({ evidenceId: '', batch: '', metricId: '' })
const busy = ref(false)
const message = ref('')
const report = ref<(EvidenceReviewResult & { report_id?: number }) | null>(null)
const fileText = ref('')
const fileMeta = ref<{ filename: string; batch: string } | null>(null)

async function loadMetrics() {
  const payload = await listMetrics()
  metrics.value = payload.items
}

function hydrateFromRoute() {
  const evidence = String(route.query.evidence ?? '')
  const batch = String(route.query.batch ?? '')
  if (!evidence) return
  form.evidenceId = evidence
  const raw = sessionStorage.getItem(`evidence-file:${evidence}`)
  if (!raw) return
  try {
    const parsed = JSON.parse(raw) as { filename: string; batch: string; text: string }
    fileText.value = parsed.text
    fileMeta.value = { filename: parsed.filename, batch: parsed.batch }
    form.batch = batch || parsed.batch
  } catch {
    fileText.value = ''
  }
}

function pickMetric(): EntryRow | undefined {
  if (form.metricId) return metrics.value.find((m) => String(m.id) === form.metricId)
  const meta = fileText.value
  if (!meta) return undefined
  return metrics.value.find((m) => m['证据批次'] === form.batch)
    ?? metrics.value.find((m) => m['证据批次'])
}

async function runReview() {
  busy.value = true
  message.value = ''
  report.value = null
  try {
    if (!fileText.value) throw new Error('缺少实际下载文件，不能复核')
    const metric = pickMetric()
    if (!metric) throw new Error('没有匹配的指标记录，请在证据页先关联指标复核')
    const result = await reviewMetricWithEvidence(Number(metric.id), {
      evidenceId: Number(form.evidenceId),
      batch: form.batch || fileMeta.value?.batch || '',
      text: fileText.value,
    })
    report.value = result
    message.value = result.message
    // 复核用的批次与版本留在页面上，避免被后续下载悄悄改口径。
    if (result.batch) form.batch = result.batch
    await loadMetrics()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '指标复核失败'
    report.value = { ok: false, message: message.value }
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  hydrateFromRoute()
  await loadMetrics()
  const pinned = metrics.value.find((m) => m['证据批次'] && !form.metricId)
  if (pinned) form.metricId = String(pinned.id)
})
</script>

<style scoped>
.review-form { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 12px; }
.review-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.review-form input, .review-form select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; }
.file-hint { font-size: 12px; color: var(--muted); background: #f8fafc; border-radius: 6px; padding: 8px 10px; }
.file-hint.warn { background: #fef3c7; color: #92400e; }
.ok-text { color: #166534; font-size: 13px; }
.report-panel { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.report-panel h3 { margin: 0 0 10px; font-size: 15px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 10px; }
.detail-grid strong { display: block; font-size: 16px; }
.report-note { font-size: 12px; background: #ecfdf3; color: #166534; border-radius: 6px; padding: 8px 10px; margin: 0; }
</style>
