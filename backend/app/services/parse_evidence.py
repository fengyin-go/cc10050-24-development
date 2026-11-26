"""证据交付单领域服务：把解析质量审计选定窗口的站点样本证据打包交付给指标复核。

数据链路位置：审计交付层。解析质量审计（parse_audit）选定站点 + 模板版本窗口后
在此申请证据导出，交付单固定窗口快照（站点、模板版本、样本明细），下载时按落库
明细逐条生成证据行。采集量指标（collect_metrics）只用交付单实际下载文件对应的
证据复核，不从指标表按最新版本重取，避免旧窗口被新数据盖过。

关键约束：
* 同一窗口内的站点与模板版本在申请时绑定，包内总量必须等于实际样本明细数。
* 补报按样本指纹幂等：同一指纹重复补报不会进第二条明细，也不会重复加命中数。
* 下载会冻结一个交付批次（download_batch），之后所有复核都引用该批次里的
  样本明细快照，迟到样本只影响下一次下载，不会改写已交付文件。
"""
from __future__ import annotations

import csv
import hashlib
import io
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ParseEvidenceConflict(Exception):
    """窗口申请量与实际样本明细冲突；必须显式确认后才能继续交付。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip() or 0)
    except ValueError:
        return 0


def _fingerprint(site: str, version: str, index: int) -> str:
    """证据样本指纹：站点 + 模板版本 + 样本序号，同一窗口内稳定可复算。"""
    raw = f"{site}|{version}|{index}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _rate(hit: int, total: int) -> str:
    if total <= 0:
        return "0.00%"
    return f"{(hit / total):.2%}"


class ParseEvidenceService(BaseModuleService):
    MODULE = "parse_evidence"
    ENTITY = "证据交付单"
    LIST_FIELDS = [
        "申请单号", "站点名称", "模板名称", "模板版本", "窗口标识",
        "申请样本数", "交付样本数", "命中样本数", "失配样本数", "指纹摘要",
    ]
    REQUIRED_FIELDS = ["申请单号", "站点名称", "模板名称", "模板版本", "申请样本数"]
    STATUS_ORDER = ["待下载", "可复核", "已复核", "已驳回"]
    ACTION_RULES = {
        "确认导出": "可复核",
        "重新申请": "待下载",
        "关联指标复核": "已复核",
        "驳回证据": "已驳回",
    }
    NEGATIVE_ACTIONS = ["驳回证据"]

    # ---- 查询辅助 ----

    def list_entries(self, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """列表只回交付摘要；明细范围走详情与下载包，避免把成百条证据塞进列表。"""
        rows, total = super().list_entries(**kwargs)
        return [self._summary(row) for row in rows], total

    def summary_of(self, entry: dict[str, Any]) -> dict[str, Any]:
        """对外暴露的交付摘要：不含明细正文。"""
        return self._summary(entry)

    def _summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        self._hydrate(entry)
        self._sync_totals(entry)
        counts = self._recount(entry)
        view = {key: value for key, value in entry.items()
                if key not in ("明细", "补报记录", "下载记录")}
        view["明细范围"] = self._scope(entry, counts)
        view["命中率"] = _rate(counts["hit"], counts["total"])
        return view

    def _scope(self, entry: dict[str, Any], counts: dict[str, int]) -> str:
        """证据包的明细范围说明：哪些序号命中、哪些失配，补报落在哪一段。"""
        lines = self._delivery_lines(entry)
        if not lines:
            return "暂无样本明细"
        hit_seq = [line.get("序号") for line in lines if line.get("结果") == "命中"]
        miss_seq = [line.get("序号") for line in lines if line.get("结果") == "失配"]
        added = [line.get("序号") for line in lines if line.get("来源") != "申请受理"]
        parts = [
            f"明细 {counts['total']} 条",
            f"命中 {counts['hit']} 条(序号 {self._ranges(hit_seq)})",
            f"失配 {counts['miss']} 条(序号 {self._ranges(miss_seq)})",
        ]
        if added:
            parts.append(f"补报 {len(added)} 条(序号 {self._ranges(added)})")
        return "；".join(parts)

    @staticmethod
    def _ranges(seq: list[Any]) -> str:
        """把序号列表压缩成区间写法，如 1-12、21-24。"""
        nums = sorted({_int(item) for item in seq})
        if not nums:
            return "无"
        spans: list[str] = []
        start = prev = nums[0]
        for num in nums[1:]:
            if num == prev + 1:
                prev = num
                continue
            spans.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = num
        spans.append(f"{start}" if start == prev else f"{start}-{prev}")
        return "、".join(spans)

    @staticmethod
    def _lines(entry: dict[str, Any]) -> list[dict[str, Any]]:
        # 返回原始列表引用：补报要就地追加，包内总量才能与明细同步。
        return entry.setdefault("明细", [])

    @staticmethod
    def _bound_match(entry: dict[str, Any]) -> bool:
        """窗口内站点与模板版本是否已绑定。"""
        return bool(entry.get("站点名称") and entry.get("模板版本") and entry.get("模板名称"))

    @staticmethod
    def _template_version(template_name: str) -> str:
        for tpl in store.rows("page_parser"):
            if tpl.get("模板名称") == template_name:
                return str(tpl.get("解析版本", "") or "")
        return ""

    def _hydrate(self, entry: dict[str, Any]) -> None:
        """种子交付单只带申请口径时，按绑定窗口补齐受理明细。

        仅在明细为空时补一次，已落库的明细（含补报）不会被覆盖。
        """
        if self._lines(entry):
            return
        requested = _int(entry.get("申请样本数"))
        if requested <= 0:
            return
        site = str(entry.get("站点名称", ""))
        version = str(entry.get("模板版本", ""))
        hit = _int(entry.get("命中样本数")) if entry.get("命中样本数") not in (None, "") else round(requested * 0.6)
        lines = self._lines(entry)
        for index in range(1, requested + 1):
            lines.append({
                "序号": index,
                "样本指纹": _fingerprint(site, version, index),
                "来源": "申请受理",
                "结果": "命中" if index <= hit else "失配",
                "补报时间": "",
            })
        self._sync_totals(entry)

    def _delivery_lines(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        """本次交付实际进包的明细；拒收的补报留档但不进包。"""
        lines = self._lines(entry)
        return [line for line in lines if not line.get("拒收")]

    def _recount(self, entry: dict[str, Any]) -> dict[str, int]:
        """按进包明细重算命中/失配/总量，明细数是唯一口径。"""
        lines = self._delivery_lines(entry)
        hit = sum(1 for line in lines if line.get("结果") == "命中")
        total = len(lines)
        return {"total": total, "hit": hit, "miss": total - hit}

    def _sync_totals(self, entry: dict[str, Any]) -> dict[str, int]:
        counts = self._recount(entry)
        lines = self._delivery_lines(entry)
        entry["交付样本数"] = str(counts["total"]) if counts["total"] else ""
        entry["命中样本数"] = str(counts["hit"]) if counts["total"] else ""
        entry["失配样本数"] = str(counts["miss"]) if counts["total"] else ""
        digest = hashlib.sha1(
            "|".join(sorted(str(line.get("样本指纹", "")) for line in lines)).encode("utf-8")
        ).hexdigest()[:12]
        entry["指纹摘要"] = digest if counts["total"] else ""
        entry["命中率"] = _rate(counts["hit"], counts["total"])
        return counts

    # ---- 申请 ----

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """通用登记入口即证据包申请：必须绑站点 + 模板版本 + 窗口。"""
        try:
            result = self.apply_package(
                applicant=str(values.get("申请人") or "数据工程组"),
                site=str(values.get("站点名称") or ""),
                template=str(values.get("模板名称") or ""),
                version=str(values.get("模板版本") or ""),
                window=str(values.get("窗口标识") or ""),
                requested=_int(values.get("申请样本数")),
                hit_count=_int(values.get("命中样本数"))
                if values.get("命中样本数") not in (None, "") else None,
                serial=str(values.get("申请单号") or ""),
            )
        except ValueError as exc:
            return None, [f"证据包申请失败：{exc}"]
        if not result.get("ok"):
            return None, [result.get("message", "证据包申请失败")]
        return result["entry"], []

    def apply_package(
        self,
        *,
        applicant: str,
        site: str,
        template: str,
        version: str,
        window: str,
        requested: int,
        hit_count: int | None = None,
        serial: str = "",
    ) -> dict[str, Any]:
        """申请证据包：绑定窗口内的站点与模板版本，按序号落明细。

        `hit_count` 为该窗口解析时实际命中的样本数；不传则按演示口径取 60%
        （与审计批次 A20 的 12/20 一致）。真实部署应逐样本传入解析结果。
        明细比申请数少说明样本尚未到齐，先挂"迟到待并入"；明细比申请数多说明
        窗口口径过期，必须重新申请，不能悄悄放大分母。
        """
        if requested <= 0:
            return {"ok": False, "message": "申请样本数必须为正整数"}
        for need, label in ((applicant, "申请人"), (site, "站点名称"),
                            (template, "模板名称"), (version, "模板版本"), (window, "窗口标识")):
            if not str(need or "").strip():
                raise ValueError(f"缺少{label}")
        if hit_count is None:
            hit_count = round(requested * 0.6)
        if not 0 <= hit_count <= requested:
            return {"ok": False, "message": "命中数必须介于 0 与申请样本数之间"}
        # 先校验模板版本与模板当前登记的解析版本一致；窗口一旦绑定不再随版本漂移。
        current_version = self._template_version(template)
        # 同一窗口只能绑定一个站点 + 模板版本；窗口号复用但换了口径必须显式另开单。
        sibling = next(
            (r for r in store.rows(self.MODULE) if r.get("窗口标识") == window), None
        )
        if sibling is not None and (
            sibling.get("站点名称") != site or sibling.get("模板版本") != version
        ):
            raise ValueError(
                f"窗口 {window} 已绑定 {sibling.get('站点名称')}/{sibling.get('模板版本')}，"
                f"不能改为 {site}/{version}"
            )
        rows = store.rows(self.MODULE)
        eid = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        lines: list[dict[str, Any]] = []
        for index in range(1, requested + 1):
            lines.append({
                "序号": index,
                "样本指纹": _fingerprint(site, version, index),
                "来源": "申请受理",
                "结果": "命中" if index <= hit_count else "失配",
                "补报时间": "",
            })
        entry: dict[str, Any] = {
            "id": eid,
            "status": "待下载",
            "pending": True,
            "abnormal": False,
            "申请单号": serial.strip() or f"EVD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{eid:03d}",
            "站点名称": site,
            "模板名称": template,
            "模板版本": version,
            "窗口标识": window,
            "申请样本数": str(requested),
            "交付样本数": "",
            "命中样本数": "",
            "失配样本数": "",
            "指纹摘要": "",
            "申请人": applicant,
            "模板登记版本": current_version,
            "明细": lines,
            "补报记录": [],
            "下载批次": "",
            "下载时间": "",
            "申请时间": _now(),
        }
        # 先按绑定版本补齐受理明细；窗口分母只认明细行数。
        counts = self._sync_totals(entry)
        rows.append(entry)
        return {
            "ok": True,
            "message": "证据包已申请",
            "entry": entry,
            "requested": requested,
            "detail_total": counts["total"],
            "version_drift": bool(current_version and version != current_version),
        }

    # ---- 补报 ----

    def supplement(
        self,
        entry_id: int,
        *,
        fingerprint: str = "",
        result: str = "命中",
        source: str = "补报",
        allow_after_download: bool = False,
    ) -> dict[str, Any]:
        """补报样本明细：按指纹幂等，重复指纹不加第二条明细。

        下载前补报并入原单并重算分母；下载后到达的补报默认拒绝，
        避免改写已冻结的交付文件（确需并入须显式 allow_after_download）。
        指纹与窗口绑定：不带指纹视为迟到样本，按绑定站点 + 版本取续号指纹，
        补报结果必须落在绑定窗口内，越界视为另一窗口，不改原单与已交付结果。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        if not self._bound_match(entry):
            return {"ok": False, "message": "窗口未绑定站点与模板版本，不能补报"}
        self._hydrate(entry)
        lines = self._lines(entry)
        if not fingerprint:
            fingerprint = _fingerprint(
                str(entry.get("站点名称", "")), str(entry.get("模板版本", "")),
                max((_int(line.get("序号")) for line in lines), default=0) + 1,
            )
        # 幂等优先：同一指纹重复投递只回显现状，不因办结/下载状态而报错。
        if any(line.get("样本指纹") == fingerprint for line in lines):
            return {
                "ok": True,
                "message": "同一指纹已存在，补报未新增明细",
                "deduped": True,
                "entry": entry,
                "counts": self._recount(entry),
            }
        if entry.get("status") in ("已复核", "已驳回"):
            return {"ok": False, "message": "交付单已办结，新指纹须重新申请窗口"}
        if entry.get("下载批次") and not allow_after_download:
            return {
                "ok": False,
                "message": "交付文件已下载，补报须重新申请或显式开新批次",
                "download_batch": entry.get("下载批次"),
            }
        if result not in ("命中", "失配"):
            return {"ok": False, "message": "补报结果只能是命中或失配"}
        if _int(entry.get("拒绝补报")):
            return {"ok": False, "message": "当班人已选择不并入补报，须重新申请窗口"}
        next_index = max((_int(line.get("序号")) for line in lines), default=0) + 1
        lines.append({
            "序号": next_index,
            "样本指纹": fingerprint,
            "来源": source,
            "结果": result,
            "补报时间": _now(),
        })
        entry["补报记录"] = list(entry.get("补报记录") or []) + [{
            "样本指纹": fingerprint,
            "结果": result,
            "补报时间": _now(),
            "下载批次": entry.get("下载批次", ""),
        }]
        counts = self._sync_totals(entry)
        entry["abnormal"] = False
        return {
            "ok": True,
            "message": "补报已并入交付单明细",
            "deduped": False,
            "entry": entry,
            "counts": counts,
            "denominator": counts["total"],
        }

    # ---- 交付前核对 ----

    def reconcile(self, entry_id: int) -> dict[str, Any]:
        """交付前核对：申请分母、实际明细分母与包内总量是否一致。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        self._hydrate(entry)
        counts = self._recount(entry)
        requested = _int(entry.get("申请样本数"))
        conflicts: list[str] = []
        if counts["total"] != requested:
            conflicts.append(
                f"申请受理 {requested} 条，实际明细 {counts['total']} 条，分母冲突"
            )
        supplemented = counts["total"] - requested
        rejected = [
            line for line in self._lines(entry) if line.get("拒收")
        ]
        return {
            "ok": not conflicts,
            "message": "分母一致，可交付" if not conflicts else "；".join(conflicts),
            "requested": requested,
            "detail_total": counts["total"],
            "supplemented": supplemented,
            "hit": counts["hit"],
            "miss": counts["miss"],
            "rate": _rate(counts["hit"], counts["total"]),
            "conflict": bool(conflicts),
            "conflicts": conflicts,
            "rejected_supplements": len(rejected),
            "version_bound": str(entry.get("模板版本", "")),
            "site_bound": str(entry.get("站点名称", "")),
        }

    def confirm_export(self, entry_id: int, *, accept_supplemented: bool = True) -> dict[str, Any]:
        """当班人确认继续导出：把迟到补报纳入本次交付分母并冻结明细快照。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        self._hydrate(entry)
        if entry.get("status") in ("已复核", "已驳回"):
            return {"ok": False, "message": "交付单已办结，不能再次确认导出"}
        check = self.reconcile(entry_id)
        counts = self._recount(entry)
        supplement_lines = [
            line for line in self._lines(entry) if line.get("来源") != "申请受理"
        ]
        if supplement_lines and not accept_supplemented:
            # 当班人选择按原申请分母交付：补报明细留档但显式标为拒收，不进包。
            for line in supplement_lines:
                line["拒收"] = True
                line["拒收原因"] = "当班人选择按原申请分母交付"
            counts = self._recount(entry)
            entry["拒绝补报"] = 1
            entry["拒收补报数"] = str(len(supplement_lines))
            entry["原申请分母"] = str(_int(entry.get("申请样本数")))
            entry["验收分母"] = str(counts["total"])
            entry["补报增量"] = "0"
            entry["status"] = "可复核"
            entry["pending"] = True
            entry["确认导出时间"] = _now()
            self._sync_totals(entry)
            return {
                "ok": True,
                "message": (
                    f"已按原申请分母 {counts['total']} 交付，"
                    f"{len(supplement_lines)} 条补报拒收并留档"
                ),
                "entry": entry,
                "acceptance_denominator": counts["total"],
                "original_denominator": _int(entry.get("申请样本数")),
                "rejected_supplements": len(supplement_lines),
                "hit": counts["hit"],
                "miss": counts["miss"],
                "rate": _rate(counts["hit"], counts["total"]),
            }
        if check["conflict"] and counts["total"] > check["requested"] \
                and not accept_supplemented:
            # 有冲突但没人明确确认时，不能悄悄把补报算进分母。
            raise ParseEvidenceConflict(
                f"原申请分母 {check['requested']} 与补报后分母 {counts['total']} 冲突，"
                "请确认并入补报或按原分母交付"
            )
        entry["拒绝补报"] = 0
        # 确认并入补报时，之前被拒收的明细重新计入交付范围。
        for line in self._lines(entry):
            line.pop("拒收", None)
            line.pop("拒收原因", None)
        counts = self._recount(entry)
        entry["拒收补报数"] = "0"
        entry["验收分母"] = str(counts["total"])
        entry["原申请分母"] = str(check["requested"])
        entry["补报增量"] = str(max(counts["total"] - check["requested"], 0))
        entry["status"] = "可复核"
        entry["pending"] = True
        entry["确认导出时间"] = _now()
        return {
            "ok": True,
            "message": "已确认继续导出，补报并入交付分母",
            "entry": entry,
            "acceptance_denominator": counts["total"],
            "original_denominator": check["requested"],
            "hit": counts["hit"],
            "miss": counts["miss"],
            "rate": _rate(counts["hit"], counts["total"]),
        }

    # ---- 下载 ----

    def build_package(self, entry_id: int) -> dict[str, Any]:
        """生成实际下载文件的内容与冻结快照；每次下载产生一个交付批次。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            raise LookupError(f"{self.ENTITY} {entry_id} 不存在")
        self._hydrate(entry)
        counts = self._recount(entry)
        batch = f"{entry.get('申请单号', 'EVD')}-B{len(entry.get('下载记录') or []) + 1:02d}"
        snapshot = [dict(line) for line in self._delivery_lines(entry)]
        entry["下载批次"] = batch
        entry["下载时间"] = _now()
        entry["下载记录"] = list(entry.get("下载记录") or []) + [{
            "交付批次": batch,
            "下载时间": entry["下载时间"],
            "版本": entry.get("模板版本", ""),
            "站点名称": entry.get("站点名称", ""),
            "样本数": counts["total"],
            "命中数": counts["hit"],
            "失配数": counts["miss"],
            "指纹摘要": entry.get("指纹摘要", ""),
            "明细快照": snapshot,
        }]
        if entry.get("status") == "待下载":
            entry["status"] = "可复核"
            entry["pending"] = True
        return {
            "entry": entry,
            "batch": batch,
            "lines": snapshot,
            "counts": counts,
            "rate": _rate(counts["hit"], counts["total"]),
        }

    def render_csv(self, entry_id: int) -> tuple[str, str]:
        """渲染证据包 CSV；返回 (文件名, 内容)，总量行必须等于明细行数。"""
        package = self.build_package(entry_id)
        entry = package["entry"]
        counts = package["counts"]
        version = entry.get("模板版本", "")
        output = io.StringIO()
        output.write("﻿")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([
            "# 证据交付单", entry.get("申请单号", ""),
            "站点", entry.get("站点名称", ""),
            "模板", entry.get("模板名称", ""),
            "版本", version,
            "窗口", entry.get("窗口标识", ""),
            "交付批次", package["batch"],
            "原申请分母", entry.get("申请样本数", ""),
            "验收分母", counts["total"],
            "命中", counts["hit"],
            "失配", counts["miss"],
            "命中率", package["rate"],
        ])
        writer.writerow(["序号", "样本指纹", "来源", "结果", "补报时间"])
        for line in package["lines"]:
            writer.writerow([
                line.get("序号", ""), line.get("样本指纹", ""),
                line.get("来源", ""), line.get("结果", ""), line.get("补报时间", ""),
            ])
        writer.writerow(["# 合计", counts["total"], "命中", counts["hit"], "失配", counts["miss"]])
        assert len(package["lines"]) == counts["total"], "包内总量必须等于明细行数"
        filename = f"{entry.get('申请单号', 'evidence')}-{package['batch']}.csv"
        return filename, output.getvalue()

    # ---- 下载文件后再复核（验收端） ----

    def parse_uploaded_rows(self, text: str) -> list[dict[str, str]]:
        """解析验收端上传的证据文件，只认文件里的明细行。"""
        rows: list[dict[str, str]] = []
        reader = csv.reader(io.StringIO(text.lstrip("﻿")))
        header_seen = False
        for row in reader:
            if not row:
                continue
            if row[0].startswith("# 合计") or row[0].startswith("# 证据交付单"):
                continue
            if not header_seen:
                header_seen = True
                continue
            rows.append({
                "序号": row[0],
                "样本指纹": row[1] if len(row) > 1 else "",
                "来源": row[2] if len(row) > 2 else "",
                "结果": row[3] if len(row) > 3 else "",
                "补报时间": row[4] if len(row) > 4 else "",
            })
        return rows

    def verify_download(
        self, entry_id: int, *, batch: str = "", rows: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """只用实际下载文件对应的证据复核，禁止回指标表按最新版本重取。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"{self.ENTITY} {entry_id} 不存在"}
        self._hydrate(entry)
        downloads = list(entry.get("下载记录") or [])
        if not downloads:
            return {"ok": False, "message": "未找到交付文件，不能复核"}
        record = None
        if batch:
            record = next((d for d in downloads if d.get("交付批次") == batch), None)
        if record is None:
            record = downloads[-1]
        snapshot: list[dict[str, Any]] = list(record.get("明细快照") or [])
        if rows is None:
            rows = snapshot
        # 指纹与版本一致性校验：文件里的样本必须全部来自冻结快照。
        snap_fp = {str(line.get("样本指纹", "")) for line in snapshot}
        file_fp = {str(row.get("样本指纹", "")) for row in rows}
        unknown = sorted(file_fp - snap_fp)
        missing = sorted(snap_fp - file_fp)
        hit = sum(1 for row in rows if row.get("结果") == "命中")
        total = len(rows)
        # 文件必须与冻结快照逐条对齐：多一条、少一条、改一条结果都不能放过。
        aligned = not unknown and not missing
        snapshot_hit = sum(1 for line in snapshot if line.get("结果") == "命中")
        if not aligned:
            message = (
                f"下载文件与冻结快照不一致：多 {len(unknown)} 条、缺 {len(missing)} 条"
            )
        elif hit != snapshot_hit:
            aligned = False
            message = f"下载文件命中 {hit} 条，与快照命中 {snapshot_hit} 条不一致"
        else:
            message = "下载文件与冻结快照一致"
        return {
            "ok": aligned,
            "message": message,
            "entry": entry,
            "batch": record.get("交付批次", ""),
            "version": record.get("版本") or entry.get("模板版本", ""),
            "site": record.get("站点名称") or entry.get("站点名称", ""),
            "download_total": _int(record.get("样本数")),
            "snapshot_count": len(snapshot),
            "file_total": total,
            "hit": hit,
            "miss": total - hit,
            "rate": _rate(hit, total),
            "download_hit": _int(record.get("命中数")),
            "download_miss": _int(record.get("失配数")),
            "unknown_fingerprints": unknown,
            "missing_fingerprints": missing,
            "recomputable": aligned and len(snapshot) == _int(record.get("样本数")) == total,
        }

    def link_metric_review(self, entry_id: int, *, batch: str = "",
                           rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
        """把下载文件对应的证据交给采集量指标复核，并回写关联记录。

        未指定批次时优先复用已登记的复核证据批次，避免一次新下载意外顶掉旧结论。
        """
        if not batch:
            batch = str(entry.get("复核证据批次", "") or "") if (
                entry := store.find(self.MODULE, entry_id)) else ""
        verified = self.verify_download(entry_id, batch=batch, rows=rows)
        if not verified.get("entry"):
            return {"ok": False, "message": verified.get("message", "复核失败")}
        entry = verified["entry"]
        if not verified.get("ok"):
            return {"ok": False, "message": verified["message"], "verify": verified}
        review = store.rows("collect_metrics")
        # 同一交付单由同一指标记录承接；换批次时更新到新批次，但需显式传入批次。
        snapshot = next(
            (m for m in review if str(m.get("证据单号", "")) == str(entry.get("申请单号", ""))
             and str(m.get("证据批次", "")) == verified["batch"]), None,
        )
        if snapshot is None:
            snapshot = next(
                (m for m in review if str(m.get("证据单号", "")) == str(entry.get("申请单号", ""))),
                None,
            )
        payload = {
            "站点名称": entry.get("站点名称"),
            "证据批次": verified["batch"],
            "证据单号": entry.get("申请单号"),
            "证据版本": verified["version"],
            "下载样本数": str(verified["download_total"]),
            "文件样本数": str(verified["file_total"]),
            "命中数": str(verified["hit"]),
            "失配数": str(verified["miss"]),
            "命中率": verified["rate"],
            "关联审计": entry.get("窗口标识"),
        }
        if snapshot is None:
            mid = max((int(m.get("id", 0)) for m in review), default=0) + 1
            snapshot = {
                "id": mid,
                "status": "正常",
                "pending": True,
                "abnormal": False,
                "统计时间": _now(),
                **payload,
            }
            review.append(snapshot)
        else:
            snapshot.update(payload)
        entry["指标复核记录"] = snapshot.get("id")
        entry["复核证据批次"] = verified["batch"]
        entry["status"] = "已复核"
        entry["pending"] = False
        return {
            "ok": True,
            "message": "证据已交采集量指标复核",
            "metric_id": snapshot.get("id"),
            "verify": verified,
        }

    # ---- 基础动作覆盖 ----

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "确认导出":
            result = self.confirm_export(entry_id)
            return (result.get("entry"), result["message"]) if result.get("ok") else (None, result["message"])
        if action == "关联指标复核":
            result = self.link_metric_review(entry_id)
            return (result.get("verify", {}).get("entry"), result["message"]) \
                if result.get("ok") else (None, result["message"])
        return super().run_action(entry_id, action)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        self._hydrate(entry)
        self._sync_totals(entry)
        view = dict(entry)
        view["明细范围"] = self._scope(entry, self._recount(entry))
        view["分母核对"] = self.reconcile(entry_id)
        view["下载记录"] = [
            {key: value for key, value in record.items() if key != "明细快照"}
            for record in entry.get("下载记录") or []
        ]
        return view
