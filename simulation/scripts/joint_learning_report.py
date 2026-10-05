"""Offline presentation of verified simulation experiment evidence only."""
from __future__ import annotations

from collections import defaultdict
from html import escape
import json
import statistics


def _groups(rows: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["candidate_id"]].append(row)
    output = []
    for candidate, values in sorted(grouped.items()):
        valid = [row for row in values if row.get("summary", {}).get("success") is True and not row.get("summary", {}).get("truncated", False)]
        scores = [row.get("score", row.get("summary", {}).get("score")) for row in valid]
        scores = [value for value in scores if type(value) in (int, float)]
        output.append({"candidate": candidate, "episodes": len(values),
                       "completed": len(valid), "failed": len(values) - len(valid),
                       "score": round(statistics.mean(scores), 3) if scores else None,
                       "fill": _mean(valid, "demand_fill_rate", 100),
                       "stockout": _mean(valid, "stockout_minutes"),
                       "inspection": _mean(valid, "inspection_completion_rate", 100)})
    return output


def _mean(values: list[dict], key: str, multiplier: float = 1) -> float | None:
    samples = [row.get("summary", {}).get(key) for row in values]
    samples = [value for value in samples if type(value) in (int, float)]
    return round(statistics.mean(samples) * multiplier, 2) if samples else None


def _text(value: object) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    return "<table><thead><tr>" + "".join(f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{escape(_text(v))}</td>" for v in row) + "</tr>" for row in rows) + "</tbody></table>"


def _validate(payload: dict) -> tuple[dict, dict]:
    if not isinstance(payload, dict) or payload.get("state", {}).get("environment") != "SIMULATION":
        raise ValueError("only SIMULATION evidence can be presented")
    json.dumps(payload, allow_nan=False)
    return payload["state"], payload.get("last_completed") or {}


def render_report(payload: dict) -> str:
    state, last = _validate(payload)
    labels = {"READY": "已准备", "RUNNING": "本批运行中", "WAITING_NEXT_RUN": "已保存进度，等待下次继续",
              "BATCH_COMPLETE": "本批完成，等待下一批", "PAUSED": "已暂停", "FAILED": "出现失败，查看原因", "DISK_LIMIT": "已达到磁盘上限"}
    status = "已暂停" if payload.get("control", {}).get("paused") else labels.get(state["status"], state["status"])
    parts = []
    for title, key in (("训练：用来选择候选", "train"), ("验证：决定是否保留候选", "validation_results"), ("保留测试：只报告，不参与选择", "test")):
        groups = _groups(last.get(key, []))
        parts.append(f"<section><h2>{title}</h2>" + (_table(["策略", "完整／总局数", "失败或截断", "球需求满足率 %", "缺球分钟", "巡查完成率 %", "平均评分"], [[row["candidate"], f"{row['completed']} / {row['episodes']}", row["failed"], row["fill"], row["stockout"], row["inspection"], row["score"]] for row in groups]) if groups else "<p>本阶段尚无完整结果。</p>") + "<p class='note'>均值仅使用完整成功的模拟日；失败与截断单独列出，并会阻止候选通过验证。</p></section>")
    verdict = last.get("validation", {})
    details = _table(["项目", "记录"], [["验证决定", verdict], ["训练选择", last.get("selection")],
                    ["当前模拟策略", state["incumbent"]], ["当前进度", state.get("active_progress")],
                    ["失败原因", state.get("last_error")], ["假设配置", payload.get("config")]])
    rows = []
    for row in last.get("test", []):
        summary = row.get("summary", {})
        regime_labels = {"dry": "晴天", "morning_rain": "上午降雨", "afternoon_rain": "下午降雨", "rain_then_surge": "雨停后客流激增", "surprise_surge": "突发客流"}
        rows.append([regime_labels.get(row.get("regime"), row.get("regime")), row.get("candidate_id"),
                     round(summary["demand_fill_rate"] * 100, 2) if type(summary.get("demand_fill_rate")) in (int, float) else None,
                     summary.get("stockout_minutes"), summary.get("staff_jobs_completed"), summary.get("staff_jobs_overdue"),
                     summary.get("hard_failures"), "通过" if summary.get("success") else "失败／不完整"])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>NXTektal 联动模拟学习报告</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f0;color:#183b30;font:15px/1.65 -apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif}}header{{background:#193d32;color:#fff;padding:32px max(24px,calc((100vw - 1120px)/2))}}header p{{opacity:.8}}h1{{font-size:29px;margin:8px 0}}h2{{font-size:19px}}main{{max-width:1168px;margin:auto;padding:24px}}section,.card{{background:white;border:1px solid #dbe3d7;border-radius:12px;padding:20px;margin-bottom:20px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}.card b{{display:block;font-size:30px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e3e8de;vertical-align:top;overflow-wrap:anywhere}}th{{background:#edf2e8}}.scroll{{overflow:auto}}.note{{color:#53694e;font-size:13px}}details{{margin:20px 0}}summary{{cursor:pointer}}@media(max-width:650px){{.stats{{grid-template-columns:1fr}}main{{padding:12px}}}}
</style></head><body><header><small>SIMULATION · 离线参数选择</small><h1>练习场与全场巡查，联动压力测试</h1><p>{escape(status)} · 更新时间 UTC {escape(state['updated_at_utc'])}</p></header><main>
<div class="stats"><div class="card"><span>已完成批次</span><b>{state['completed_batches']}</b></div><div class="card"><span>已完成策略 × 模拟日</span><b>{state['total_completed_episodes']}</b></div><div class="card"><span>当前模拟策略</span><b style="font-size:18px">{escape(state['incumbent']['candidate_id'])}</b></div></div>
<p class="note">同一天气与逐分钟球需求用于配对比较。人力在机器人救援和球场巡查之间共享；球量始终守恒。这里的“学习”是模拟参数选择，没有训练大语言模型，也没有向真实机器人下指令。</p>
{''.join(parts)}
<section><h2>本批保留测试：逐场景结果</h2><p class="note">保留失败、缺球、待巡查与超期信息；评分及天气/客流关系均采用明示假设，不能当作现场准确率或成本节省证明。</p><div class="scroll">{_table(['情景','策略','需求满足率 %','缺球分钟','完成巡查','超期巡查','设备故障','完整性'],rows) if rows else '<p>等待本批完成。</p>'}</div></section>
<details><summary>查看参数、选择理由、进度及失败记录</summary><section>{details}</section></details>
<p class="note">这是保存的报告快照，请重新打开或刷新文件查看新结果。每次后台唤醒只运行有时间与磁盘上限的一批；暂停开关在 control.json。各模拟日独立重置，尚未模拟库存跨日结转。完整逐局记录有保留上限，批次摘要保留。</p>
</main></body></html>'''


def render_markdown(payload: dict) -> str:
    state, last = _validate(payload)
    def safe(value):
        return escape(_text(value)).replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
    lines = ["# NXTektal 联动模拟学习报告", "", "SIMULATION：天气、客流和物理参数为假设；学习仅用于离线模拟策略选择。", "",
             f"状态：{safe(state['status'])}；暂停：{safe(payload.get('control',{}).get('paused'))}",
             f"已完成批次：{state['completed_batches']}；策略 × 模拟日：{state['total_completed_episodes']}",
             f"当前模拟策略：{safe(state['incumbent']['candidate_id'])}", f"更新时间 UTC：{safe(state['updated_at_utc'])}", ""]
    for title, key in (("训练", "train"), ("验证", "validation_results"), ("保留测试（不参与选择）", "test")):
        lines.extend([f"## {title}", "", "| 策略 | 完整／总局数 | 失败或截断 | 完整局平均评分 |", "| --- | --- | --- | --- |"])
        lines.extend(f"| {safe(row['candidate'])} | {row['completed']} / {row['episodes']} | {row['failed']} | {safe(row['score'])} |" for row in _groups(last.get(key, [])))
        lines.append("")
    lines.extend(["验证决定：" + safe(last.get("validation")), "", "失败原因：" + safe(state.get("last_error")), "",
                  "各模拟日独立，使用相同外生需求进行配对比较；人力在机器人救援和球场巡查间共享。任务完成不证明真实问题已解决。没有现场设备控制、真实图像识别或 LLM 训练。", ""])
    return "\n".join(lines)
