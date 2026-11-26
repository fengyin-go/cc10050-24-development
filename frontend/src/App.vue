<template>
  <div class="app-shell">
    <aside class="app-side">
      <h1 class="app-title">商品价格监控采集平台</h1>
      <nav class="nav-list">
        <RouterLink v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          {{ item.label }}
        </RouterLink>
      </nav>
    </aside>
    <main class="app-main">
      <header class="app-head">
        <span class="head-desc">面向多区域站点的商品价格抓取、规格归一、比价计算与变价告警的采集治理平台</span>
        <span class="head-user">当前值班：数据工程组</span>
      </header>
      <RouterView />
    </main>
  </div>
</template>

<script setup lang="ts">
const navItems = [{ label: "运营概览", path: "/" }, { label: "采集站点目录", path: "/shop_site" }, { label: "价格爬取任务", path: "/price_crawler" }, { label: "页面解析模板", path: "/page_parser" }, { label: "规格归一器", path: "/spec_normalizer" }, { label: "货币换算器", path: "/currency_conv" }, { label: "价格基线库", path: "/price_baseline" }, { label: "比价计算器", path: "/price_compare" }, { label: "变价告警规则", path: "/price_alert" }, { label: "商品登记中心", path: "/goods_registry" }, { label: "规格匹配器", path: "/sku_match" }, { label: "页面样本库", path: "/sample_store" }, { label: "解析质量审计", path: "/parse_audit" }, { label: "证据交付单", path: "/parse_evidence" }, { label: "采集调度计划", path: "/crawl_schedule" }, { label: "抓取频控", path: "/rate_limit" }, { label: "代理线路池", path: "/proxy_route" }, { label: "失败隔离区", path: "/failure_quarantine" }, { label: "比价导出通道", path: "/price_export" }, { label: "变价历史流水", path: "/change_history" }, { label: "人工复核队列", path: "/match_review" }, { label: "采集量指标", path: "/collect_metrics" }, { label: "指标证据复核", path: "/collect_metrics/review" }]
</script>
