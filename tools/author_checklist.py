"""Build the per-candidate author-home acceptance checklist from decisions."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    job_dir = Path(sys.argv[1])
    decisions_dir = job_dir / "author_decisions"
    rows = []
    for path in sorted(decisions_dir.glob("*.decision.json")):
        decision = json.loads(path.read_text(encoding="utf-8"))
        evidence_id = int(decision.get("evidence_id") or 0)
        accepted = bool(decision.get("accepted"))
        rows.append(
            (
                evidence_id,
                "接受" if accepted else "拒绝",
                decision.get("rejection_code") or "—",
                decision.get("page_type") or "unknown",
                decision.get("overlay_state") or "unknown",
                decision.get("candidate_url") or "",
            )
        )
    lines = [
        "# 个人主页截图逐张验收清单",
        "",
        f"- 任务：`{job_dir.name}`",
        f"- 候选主页：{len(rows)}",
        f"- 接受：{sum(1 for row in rows if row[1] == '接受')}",
        f"- 拒绝：{sum(1 for row in rows if row[1] == '拒绝')}",
        "",
        "| 证据编号 | 结论 | 拒绝码 | 页面类型 | 遮挡状态 | 候选 URL |",
        "|---:|---|---|---|---|---|",
    ]
    for evidence_id, verdict, code, page_type, overlay, url in rows:
        lines.append(
            f"| {evidence_id:03d} | {verdict} | {code} | {page_type} | {overlay} | {url} |"
        )
    destination = job_dir / "author_evidence_checklist.md"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"checklist={destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
