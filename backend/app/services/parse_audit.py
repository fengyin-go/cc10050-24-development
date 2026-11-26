"""解析质量审计领域服务：审计解析模板的字段命中率与失败分布，负责命中率劣化告警与沉淀。

数据链路位置：质量审计层，消费页面解析模板上报的命中数据与失败隔离区的
失败原因分布，产出的审计结论供模板版本升级决策。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ParseAuditService(BaseModuleService):
    MODULE = "parse_audit"
    ENTITY = "审计明细"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["审计时间", "解析模板", "样本总数", "字段命中率", "失败原因分布"]
    REQUIRED_FIELDS = ["审计时间", "解析模板", "样本总数"]
    STATUS_ORDER = ["正常", "命中率下降", "解析异常"]
    ACTION_RULES = {"查看详情": "命中率下降", "忽略告警": "解析异常"}
    NEGATIVE_ACTIONS = ["忽略告警"]

    # ---- 业务扩展：命中率审计与劣化告警 ----

    def record_hit_rate(self, template: str, total: int, hit: int) -> dict[str, Any]:
        """记录一次模板命中率审计明细。"""
        if total <= 0:
            return {"ok": False, "message": "样本总数必须为正数"}
        rate = hit / total
        rows = store.rows(self.MODULE)
        aid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        status = "正常" if rate >= 0.9 else ("命中率下降" if rate >= 0.6 else "解析异常")
        rows.append({
            "id": aid,
            "审计时间": datetime.now(timezone.utc).isoformat(),
            "解析模板": template,
            "样本总数": str(total),
            "字段命中率": f"{rate:.2%}",
            "失败原因分布": "" if rate >= 0.9 else "选择器失配/字段缺失",
            "status": status,
            "pending": status != "正常",
            "abnormal": status == "解析异常",
        })
        return {"ok": True, "message": "审计明细已记录", "audit_id": aid, "rate": f"{rate:.2%}"}

    def detect_degradation(self, entry_id: int) -> dict[str, Any]:
        """对比历史审计检测模板命中率劣化趋势。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        template = entry.get("解析模板", "")
        history = [
            r for r in store.rows(self.MODULE)
            if r.get("解析模板") == template and r.get("id") != entry.get("id")
        ]
        current = float(str(entry.get("字段命中率", "0%")).rstrip("%") or 0)
        worst = max(
            (float(str(r.get("字段命中率", "0%")).rstrip("%") or 0) for r in history),
            default=current,
        )
        degraded = worst - current >= 0.15
        if degraded:
            entry["status"] = "命中率下降"
            entry["pending"] = True
        return {
            "ok": True,
            "message": "劣化检测完成",
            "degraded": degraded,
            "current": f"{current:.2%}",
            "best_before": f"{worst:.2%}",
        }

    def request_evidence(self, entry_id: int) -> dict[str, Any]:
        """把本审计窗口的站点样本交给证据交付单申请导出。

        站点与模板版本在申请时一次性绑定，后续补报只按指纹并入原单。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        from app.services import service_for  # 延迟导入，避免模块初始化循环

        evidence = service_for("parse_evidence")
        if evidence is None:
            return {"ok": False, "message": "证据交付模块未注册"}
        template = str(entry.get("解析模板", "") or "")
        site = str(entry.get("站点名称", "") or "")
        window = str(entry.get("窗口标识", "") or f"AUDIT-{entry_id}")
        if not site:
            # 审计批次未单独记站点时，用模板登记的适用站点兜底。
            site = next(
                (str(t.get("适用站点", "")) for t in store.rows("page_parser")
                 if t.get("模板名称") == template), "",
            )
        version = next(
            (str(t.get("解析版本", "")) for t in store.rows("page_parser")
             if t.get("模板名称") == template), "",
        )
        if not site or not version:
            return {"ok": False, "message": "审计批次缺少站点或模板版本，无法绑定证据窗口"}
        # 同一窗口已有交付单时直接复用，重复点击不会开出第二张单。
        existing = next(
            (e for e in store.rows("parse_evidence") if e.get("窗口标识") == window), None
        )
        if existing is not None:
            entry["证据交付单"] = existing.get("id")
            return {
                "ok": True,
                "message": f"窗口 {window} 已有证据交付单，直接复用",
                "evidence_id": existing.get("id"),
                "reused": True,
                "site": existing.get("站点名称"),
                "version": existing.get("模板版本"),
                "requested": int(str(existing.get("申请样本数", "0") or 0)),
            }
        rate_text = str(entry.get("字段命中率", "0%")).rstrip("%")
        try:
            rate = float(rate_text) / 100
        except ValueError:
            rate = 0.0
        total = int(str(entry.get("样本总数", "0") or 0))
        result = evidence.apply_package(
            applicant="解析质量审计",
            site=site,
            template=template,
            version=version,
            window=window,
            requested=total,
            hit_count=round(total * rate),
        )
        if not result.get("ok"):
            return result
        entry["证据交付单"] = result["entry"].get("id")
        return {
            "ok": True,
            "message": f"已按审计窗口 {window} 申请证据导出",
            "evidence_id": result["entry"].get("id"),
            "site": site,
            "version": version,
            "requested": total,
        }

    def summarize_failures(self, entry_id: int) -> dict[str, Any]:
        """汇总失败隔离区中该模板的失败原因分布。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        reasons: dict[str, int] = {}
        for q in store.rows("failure_quarantine"):
            if q.get("失败阶段") == "解析":
                reason = str(q.get("失败原因", "") or "未知")
                reasons[reason] = reasons.get(reason, 0) + 1
        top = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:3]
        entry["失败原因分布"] = ";".join(f"{k}:{v}" for k, v in top)
        return {"ok": True, "message": "失败分布已汇总", "distribution": top}

    def recommend_template_upgrade(self, entry_id: int) -> dict[str, Any]:
        """命中率持续下降时建议模板升级版本。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        if entry.get("status") == "正常":
            return {"ok": True, "message": "模板健康，无需升级", "upgrade": False}
        template = next(
            (t for t in store.rows("page_parser")
             if t.get("模板名称") == entry.get("解析模板", "")), None
        )
        if template is None:
            return {"ok": False, "message": "关联模板不存在"}
        return {
            "ok": True,
            "message": "建议升级模板解析版本",
            "upgrade": True,
            "template": template.get("模板名称", ""),
            "current_version": template.get("解析版本", ""),
        }

    def publish_metrics(self, entry_id: int) -> dict[str, Any]:
        """把审计结论发布到采集量指标作为解析量口径。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "审计明细不存在"}
        metrics = store.rows("collect_metrics")
        row = next((m for m in metrics if m.get("站点名称") == entry.get("解析模板", "")), None)
        if row is None:
            mid = max((int(m.get("id", 0)) for m in metrics), default=0) + 1
            metrics.append({
                "id": mid,
                "统计时间": datetime.now(timezone.utc).isoformat(),
                "站点名称": entry.get("解析模板", ""),
                "抓取量": entry.get("样本总数", "0"),
                "解析量": entry.get("样本总数", "0"),
                "产出量": "0",
                "status": entry.get("status", "正常"),
                "pending": True,
                "abnormal": entry.get("status") == "解析异常",
            })
        else:
            row["解析量"] = entry.get("样本总数", "0")
            row["统计时间"] = datetime.now(timezone.utc).isoformat()
        return {"ok": True, "message": "审计结论已发布指标"}

    def audit_health(self, days: int = 1) -> dict[str, Any]:
        """输出审计批次健康度统计。"""
        rows = store.rows(self.MODULE)
        abnormal = sum(1 for r in rows if r.get("status") == "解析异常")
        degraded = sum(1 for r in rows if r.get("status") == "命中率下降")
        return {
            "ok": True,
            "message": "审计健康度统计完成",
            "total": len(rows),
            "abnormal": abnormal,
            "degraded": degraded,
        }
