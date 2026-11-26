"""采集量指标领域服务：统计各站点采集-解析-产出的量级健康度，负责波动检测与断采告警。

数据链路位置：观测层，汇聚爬取任务、调度计划、样本库与隔离区上报的量级数据，
产出的健康度结论供采集治理决策与告警联动。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


def _to_int(value: Any) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0


class CollectMetricsService(BaseModuleService):
    MODULE = "collect_metrics"
    ENTITY = "指标记录"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["统计时间", "站点名称", "抓取量", "解析量", "产出量"]
    REQUIRED_FIELDS = ["统计时间", "站点名称", "抓取量"]
    STATUS_ORDER = ["正常", "量级波动", "断采"]
    ACTION_RULES = {"查看趋势": "量级波动", "忽略波动": "断采"}
    NEGATIVE_ACTIONS = ["忽略波动"]

    # ---- 业务扩展：量级统计与健康度判定 ----

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """列表额外带出关联证据批次与最近一次差额结论，供指标页跳转复核。"""
        rows, total = super().list_entries(**kwargs)
        reports = store.rows("metric_reconcile_report")
        for row in rows:
            batch = str(row.get("证据批次", "") or "")
            report = next((r for r in reports if r.get("交付批次") == batch), None)
            row["关联复核报告"] = (
                f"#{report['id']} 差额 {report.get('差额', '0')}" if report else ""
            )
            row["证据批次"] = batch
        return rows, total

    def record_stats(self, site: str, crawled: int, parsed: int, output: int) -> dict[str, Any]:
        """记录一次站点量级统计。"""
        if crawled < 0 or parsed < 0 or output < 0:
            return {"ok": False, "message": "量级不能为负数"}
        rows = store.rows(self.MODULE)
        row = next((r for r in rows if r.get("站点名称") == site), None)
        now = datetime.now(timezone.utc).isoformat()
        if row is None:
            mid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            row = {
                "id": mid,
                "统计时间": now,
                "站点名称": site,
                "抓取量": str(crawled),
                "解析量": str(parsed),
                "产出量": str(output),
                "status": "正常",
                "pending": True,
                "abnormal": False,
            }
            rows.append(row)
        else:
            row["抓取量"] = str(int(row.get("抓取量", "0") or 0) + crawled)
            row["解析量"] = str(int(row.get("解析量", "0") or 0) + parsed)
            row["产出量"] = str(int(row.get("产出量", "0") or 0) + output)
            row["统计时间"] = now
        return {"ok": True, "message": "量级已记录", "site": site}

    def detect_anomaly(self, entry_id: int) -> dict[str, Any]:
        """检测站点量级异常：断采与波动判定。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        crawled = int(str(entry.get("抓取量", "0") or 0))
        parsed = int(str(entry.get("解析量", "0") or 0))
        output = int(str(entry.get("产出量", "0") or 0))
        if crawled == 0:
            entry["status"] = "断采"
            entry["abnormal"] = True
            return {"ok": True, "message": "检测到断采", "anomaly": "断采"}
        if parsed < crawled * 0.5:
            entry["status"] = "量级波动"
            entry["pending"] = True
            return {"ok": True, "message": "解析量占比过低，量级波动", "anomaly": "量级波动"}
        entry["status"] = "正常"
        entry["abnormal"] = False
        entry["pending"] = False
        return {"ok": True, "message": "量级健康", "anomaly": ""}

    def cross_check_site(self, entry_id: int) -> dict[str, Any]:
        """与站点目录交叉核对指标站点是否仍有效。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "指标记录不存在"}
        site = entry.get("站点名称", "")
        site_row = next(
            (s for s in store.rows("shop_site") if s.get("站点域名") == site), None
        )
        if site_row is None:
            return {"ok": False, "message": f"站点 {site} 不在站点目录"}
        if site_row.get("status") == "已封禁":
            entry["status"] = "断采"
            entry["abnormal"] = True
            return {"ok": True, "message": "站点已封禁，指标标记断采"}
        return {"ok": True, "message": "站点有效", "site_status": site_row.get("status")}

    def aggregate_health(self) -> dict[str, Any]:
        """汇总全部站点的量级健康度。"""
        rows = store.rows(self.MODULE)
        normal = sum(1 for r in rows if r.get("status") == "正常")
        broken = sum(1 for r in rows if r.get("status") == "断采")
        total_crawled = sum(int(str(r.get("抓取量", "0") or 0)) for r in rows)
        total_output = sum(int(str(r.get("产出量", "0") or 0)) for r in rows)
        return {
            "ok": True,
            "message": "健康度汇总完成",
            "sites": len(rows),
            "normal": normal,
            "broken": broken,
            "total_crawled": total_crawled,
            "total_output": total_output,
            "yield_rate": f"{(total_output / total_crawled):.0%}" if total_crawled else "0%",
        }

    def notify_quarantine_pressure(self, entry_id: int) -> dict[str, Any]:
        """隔离区压力大时把压力传导到指标异常标记。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "指标记录不存在"}
        pending_quarantine = sum(
            1 for q in store.rows("failure_quarantine")
            if q.get("status") in ("待处理", "重试中")
        )
        output = int(str(entry.get("产出量", "0") or 0))
        if pending_quarantine > max(output * 0.2, 5):
            entry["status"] = "量级波动"
            entry["abnormal"] = True
            return {
                "ok": True,
                "message": "隔离区压力已传导",
                "pending_quarantine": pending_quarantine,
            }
        return {"ok": True, "message": "隔离区压力正常", "pending_quarantine": pending_quarantine}

    def apply_evidence_review(self, metric_id: int) -> dict[str, Any]:
        """按证据交付单实际下载文件复核本条指标，并写一条差额复核报告。

        口径固定为下载文件对应的交付批次（冻结快照），不从指标表按最新版本
        重取，也不允许用后续补报改写已交付结果。抓取量取读取交付批次的样本数。
        """
        entry = store.find(self.MODULE, metric_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {metric_id} 不存在"}
        batch = str(entry.get("证据批次", "") or "")
        if not batch:
            return {"ok": False, "message": "该指标记录未关联证据交付批次"}
        package = next(
            (p for p in store.rows("parse_evidence")
             if any(d.get("交付批次") == batch for d in p.get("下载记录") or [])),
            None,
        )
        if package is None:
            return {"ok": False, "message": f"找不到交付批次 {batch} 的交付单"}
        record = next(d for d in package["下载记录"] if d.get("交付批次") == batch)
        snapshot = list(record.get("明细快照") or [])
        # 只用下载文件对应的证据；总量必须等于明细数，否则拒绝复核。
        if len(snapshot) != _to_int(record.get("样本数")):
            return {"ok": False, "message": "交付明细与包内总量不一致，不能复核"}
        hit = sum(1 for line in snapshot if line.get("结果") == "命中")
        total = len(snapshot)
        miss = total - hit
        published = self._published_crawled(str(entry.get("站点名称", "")))
        measured = total
        diff = measured - published
        entry["抓取量"] = str(measured)
        entry["解析量"] = str(hit)
        entry["产出量"] = str(hit)
        entry["统计时间"] = record.get("下载时间", datetime.now(timezone.utc).isoformat())
        entry["证据版本"] = record.get("版本", package.get("模板版本", ""))
        entry["命中率"] = f"{(hit / total):.2%}" if total else "0.00%"
        if diff:
            entry["status"] = "量级波动"
            entry["pending"] = True
        else:
            entry["status"] = "正常"
            entry["pending"] = False
        entry["abnormal"] = False
        report = self._write_diff_report(
            metric_id=metric_id,
            site=str(entry.get("站点名称", "")),
            batch=batch,
            measured=measured,
            published=published,
            hit=hit,
            miss=miss,
            total=total,
            version=str(entry.get("证据版本", "")),
        )
        return {
            "ok": True,
            "message": report["说明"],
            "report_id": report["id"],
            "measured": measured,
            "published": published,
            "diff": diff,
            "hit": hit,
            "miss": miss,
            "rate": entry["命中率"],
            "batch": batch,
            "version": entry.get("证据版本", ""),
        }

    def _published_crawled(self, site: str) -> int:
        """取该站点最近一次历史发布量作为对账基线。"""
        history = [
            h for h in store.rows("metric_publish_history")
            if h.get("站点名称") == site
        ]
        if not history:
            return 0
        latest = sorted(history, key=lambda h: str(h.get("发布时间", "")))[-1]
        return _to_int(latest.get("发布抓取量"))

    def _write_diff_report(self, *, metric_id: int, site: str, batch: str,
                           measured: int, published: int, hit: int, miss: int,
                           total: int, version: str) -> dict[str, Any]:
        """写一条说明差额的复核报告，同一交付批次重复复核只更新不重写。"""
        reports = store.rows("metric_reconcile_report")
        diff = measured - published
        if total <= 0:
            note = f"交付批次 {batch} 无有效明细，无法对账"
        elif diff == 0:
            note = (
                f"交付批次 {batch}（版本 {version}）文件样本 {total} 条，"
                f"与历史发布量 {published} 条一致，差额 0"
            )
        else:
            direction = "多出" if diff > 0 else "少了"
            note = (
                f"交付批次 {batch}（版本 {version}）文件样本 {total} 条，"
                f"历史发布量 {published} 条，{direction} {abs(diff)} 条；"
                f"命中和 {hit}、失配 {miss}，命中率 {(hit / total):.2%}"
            )
        existing = next((r for r in reports if r.get("交付批次") == batch), None)
        payload = {
            "站点名称": site,
            "交付批次": batch,
            "交付单号": next(
                (p.get("申请单号", "") for p in store.rows("parse_evidence")
                 if any(d.get("交付批次") == batch for d in p.get("下载记录") or [])),
                "",
            ),
            "模板版本": version,
            "文件样本数": str(total),
            "抓取量": str(measured),
            "历史发布量": str(published),
            "差额": str(diff),
            "命中数": str(hit),
            "失配数": str(miss),
            "命中率": f"{(hit / total):.2%}" if total else "0.00%",
            "说明": note,
            "指标记录": str(metric_id),
            "复核时间": datetime.now(timezone.utc).isoformat(),
        }
        if existing is None:
            rid = max((int(r.get("id", 0)) for r in reports), default=0) + 1
            existing = {
                "id": rid,
                "status": "正常" if diff == 0 else "量级波动",
                "pending": diff != 0,
                "abnormal": False,
                **payload,
            }
            reports.append(existing)
        else:
            existing.update(payload)
            existing["status"] = "正常" if diff == 0 else "量级波动"
            existing["pending"] = diff != 0
        return {**existing, "id": existing["id"], "说明": note}

    def explain_diff(self, report_id: int) -> dict[str, Any]:
        """输出指定复核报告的差额说明，供审计端核对。"""
        report = store.find("metric_reconcile_report", report_id)
        if report is None:
            return {"ok": False, "message": "复核报告不存在"}
        return {
            "ok": True,
            "message": report.get("说明", ""),
            "batch": report.get("交付批次", ""),
            "diff": report.get("差额", "0"),
            "version": report.get("模板版本", ""),
        }

    def audit_health(self) -> dict[str, Any]:
        """输出采集量指标的健康度统计。"""
        rows = store.rows(self.MODULE)
        return {
            "ok": True,
            "message": "采集量健康度统计完成",
            "total": len(rows),
            "normal": sum(1 for r in rows if r.get("status") == "正常"),
            "broken": sum(1 for r in rows if r.get("status") == "断采"),
        }

    def trend_report(self, site: str) -> dict[str, Any]:
        """输出站点量级趋势报告供告警规则参考。"""
        rows = [r for r in store.rows(self.MODULE) if r.get("站点名称") == site]
        if not rows:
            return {"ok": False, "message": f"站点 {site} 没有指标记录"}
        latest = rows[-1]
        return {
            "ok": True,
            "message": "趋势报告已生成",
            "site": site,
            "crawled": latest.get("抓取量", ""),
            "parsed": latest.get("解析量", ""),
            "output": latest.get("产出量", ""),
            "status": latest.get("status", ""),
        }
