import { createRouter, createWebHistory } from 'vue-router'

import EvidenceView from '@/views/EvidenceView.vue'
import MetricReviewView from '@/views/MetricReviewView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径要先于 /:moduleKey 注册，否则会被动态段吞掉。
    { path: '/parse_evidence', name: 'parse_evidence', component: EvidenceView },
    { path: '/collect_metrics/review', name: 'metric_review', component: MetricReviewView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
