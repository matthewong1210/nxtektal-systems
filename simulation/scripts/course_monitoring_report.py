"""Offline, read-only presentation of course-monitoring simulation projections.

The caller owns all observations, case transitions and task evidence. This
module accepts plain serialized projections, imports no domain implementation,
and never writes evidence or converts a report interaction into an action.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
from collections import Counter
from typing import Any

SCHEMA = "nxt-course-monitoring-report/v0"
COVERAGE_LABELS = {
    "UNOBSERVED": "未观察",
    "OBSERVED_CLEAR": "本次未见异常",
    "CANDIDATE": "疑似异常",
    "UNUSABLE": "图像不可用",
    "STALE": "证据已过期",
}
STATUS_LABELS = {
    **COVERAGE_LABELS,
    "CONFIRMED": "人工已确认",
    "ASSIGNED": "已派工",
    "IN_PROGRESS": "处理中",
    "AWAITING_VERIFICATION": "等待复查",
    "VERIFIED": "复查通过",
    "REOPENED": "重新打开",
    "DISMISSED": "人工已排除",
    "COMPLETED": "作业已完成",
    "PENDING": "等待处理",
    "CANCELLED": "已取消",
    "INSPECT": "人工巡检",
    "RAKE_BUNKER": "整理沙坑",
    "REPAIR_DIVOT": "修复打痕",
    "FAIRWAY": "球道",
    "BUNKER": "沙坑",
    "GREEN": "果岭",
    "HUMAN": "脚本人员",
    "SIMULATED_ROBOT": "模拟机器人",
    "VERIFICATION_FAILED": "复查未通过",
    "DIVOT": "疑似打痕",
    "BUNKER_SURFACE": "疑似沙面异常",
    "STANDING_WATER": "疑似积水",
}
BASE_DISCLAIMERS = (
    "合成巡检场景 · SIMULATION：没有真实图像、真实视觉识别或现场机器人执行。",
    "地图仅为观察点与球车位置示意，不是实测地图、行驶路线或导航指令。",
    "覆盖统计的分母是预设观察点数量，不是球场面积；未观察、不可用或过期均不代表正常。",
    "本次未见异常仅适用于该观察点、本次检查类别与拍摄时刻，不证明整洞或整个区域没有问题。",
    "本报告只读。播放、筛选与时间轴只改变显示，不确认问题、不派工，也不向后端写入。",
    "确认、派工和复查由合成场景中的脚本角色产生，不是现场人员或真实设备的操作记录。",
)


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _value(record: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return default


def _text(value: Any) -> str:
    if value is None or value == "":
        return "未提供"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return str(value)


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _markdown(value: Any) -> str:
    # Both raw HTML and Markdown links/images must stay inert in a report cell.
    text = _escape(value).replace("\\", "\\\\")
    for character in ("|", "`", "*", "_", "[", "]", "#", "!"):
        text = text.replace(character, "\\" + character)
    return " ".join(text.splitlines())


def _label(value: Any) -> str:
    text = _text(value)
    label = STATUS_LABELS.get(text)
    return f"{label} · {text}" if label else text


def _validate(report: dict[str, Any]) -> None:
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise ValueError(f"Expected report schema {SCHEMA}")
    if report.get("environment") != "SIMULATION":
        raise ValueError("The offline monitoring report supports SIMULATION only")
    for key in ("checkpoints", "frames"):
        if not isinstance(report.get(key, []), list):
            raise ValueError(f"{key} must be a list")
    # Reject unsupported objects and non-finite numbers before embedding data.
    json.dumps(report, ensure_ascii=False, allow_nan=False)


def _frame(report: dict[str, Any]) -> dict[str, Any]:
    frames = _records(report.get("frames"))
    return frames[-1] if frames else {}


def _coverage(report: dict[str, Any], frame: dict[str, Any]) -> list[dict[str, Any]]:
    supplied = {str(row.get("checkpoint_id")): row for row in _records(frame.get("coverage"))}
    rows = []
    for checkpoint in _records(report.get("checkpoints")):
        evidence = supplied.get(str(checkpoint.get("checkpoint_id")), {})
        # Missing evidence remains explicit; do not derive coverage from carts.
        status = evidence.get("status", "UNOBSERVED")
        if status not in COVERAGE_LABELS:
            status = "UNOBSERVED"
        rows.append({**checkpoint, "status": status, "last_observed_at_utc": evidence.get("last_observed_at_utc")})
    return rows


def _final_records(report: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = _records(report[key]) if key in report else _records(_frame(report).get(key))
    cases = _records(report.get("cases", _frame(report).get("cases")))
    by_case = {str(row.get("case_id")): row for row in cases}
    by_checkpoint = {str(row.get("checkpoint_id")): row for row in _records(report.get("checkpoints"))}
    result = []
    for row in rows:
        case = by_case.get(str(row.get("case_id")), {})
        checkpoint_id = row.get("checkpoint_id", case.get("checkpoint_id"))
        checkpoint = by_checkpoint.get(str(checkpoint_id), {})
        result.append({**row, "checkpoint_id": checkpoint_id,
                       "hole_number": row.get("hole_number", case.get("hole_number", checkpoint.get("hole_number")))})
    return result


def _case_cells(case: dict[str, Any]) -> list[Any]:
    return [
        _value(case, "case_id", "finding_id"),
        _value(case, "hole_number"),
        _value(case, "checkpoint_id", "feature_id"),
        _label(_value(case, "condition_kind", "kind", "category", "issue_type", "defect_type")),
        _label(_value(case, "status", "state")),
        _value(case, "confirmed_by", "reviewed_by"),
        _value(case, "latest_task_id", "task_id", "assigned_task_id"),
        _value(case, "verified_at_utc", "reviewed_at_utc", "closed_at_utc"),
        _value(case, "verification_reason", "review_reason", "detail", "summary", "note"),
    ]


def _task_cells(task: dict[str, Any]) -> list[Any]:
    resource = task.get("resource") if isinstance(task.get("resource"), dict) else {}
    return [
        task.get("task_id"),
        _value(task, "case_id", "finding_id"),
        task.get("hole_number"),
        _label(_value(task, "task_kind", "kind", "task_type", "action")),
        _label(_value(task, "status", "state")),
        _value(task, "assigned_to", "resource_id", "assignee_id", default=resource.get("id")),
        _label(_value(task, "resource_kind", "assignee_kind", default=resource.get("kind"))),
        _value(task, "assigned_at_utc", "created_at_utc"),
        task.get("completed_at_utc"),
        _value(task, "verified_at_utc", "reviewed_at_utc"),
    ]


CASE_HEADERS = ["问题 ID", "球洞", "观察点", "问题类别", "状态", "确认人", "关联任务", "复查时间 UTC", "说明"]
TASK_HEADERS = ["任务 ID", "问题 ID", "球洞", "工作内容", "状态", "负责人 / 资源", "资源类型", "派工时间 UTC", "完成时间 UTC", "复查时间 UTC"]
COVERAGE_HEADERS = ["观察点", "球洞", "区域", "观察状态", "最近观察时间 UTC"]


def _table(headers: list[str], rows: list[list[Any]], empty: str, identifier: str = "") -> str:
    cells = "".join("<tr>" + "".join(f"<td>{_escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    if not cells:
        cells = f'<tr><td colspan="{len(headers)}" class="empty">{_escape(empty)}</td></tr>'
    return ('<div class="table-wrap"><table><thead><tr>' +
            "".join(f"<th scope=\"col\">{_escape(header)}</th>" for header in headers) +
            f'</tr></thead><tbody id="{identifier}">{cells}</tbody></table></div>')


def _md_table(headers: list[str], rows: list[list[Any]], empty: str) -> str:
    if not rows:
        return empty + "\n"
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" +
            "\n".join("| " + " | ".join(_markdown(cell) for cell in row) + " |" for row in rows) + "\n")


def render_markdown(report: dict[str, Any]) -> str:
    """Render an inert, deterministic final-snapshot report; never mutate input."""
    _validate(report)
    coverage = _coverage(report, _frame(report))
    counts = Counter(row["status"] for row in coverage)
    holes = sorted({row.get("hole_number") for row in coverage if isinstance(row.get("hole_number"), int)})
    disclaimers = list(BASE_DISCLAIMERS) + [str(value) for value in report.get("disclaimers", [])]
    output = [
        "# NXTektal 球场巡检报告\n",
        "\n".join(f"> {_markdown(item)}" for item in disclaimers) + "\n",
        f"场地：{_markdown(report.get('site_id'))} · 部署：{_markdown(report.get('deployment_id'))}\n",
        f"地图版本：{_markdown(report.get('map_revision'))}\n",
        f"生成时间 UTC：{_markdown(report.get('generated_at_utc'))} · 场景时长：{_markdown(report.get('duration_minutes'))} 分钟\n",
        f"场景总计：{_markdown(report.get('summary', {}).get('observations'))} 条合成观测；{_markdown(report.get('summary', {}).get('issues'))} 条问题；{_markdown(report.get('summary', {}).get('tasks'))} 条任务。\n",
        "## 最终观察点覆盖\n",
        f"共有 {len(holes)} 个球洞、{len(coverage)} 个预设观察点。分母是观察点，不是球场面积。\n",
        "；".join(f"{label} {counts.get(status, 0)}" for status, label in COVERAGE_LABELS.items()) + "。\n",
        _md_table(COVERAGE_HEADERS, [[row.get("checkpoint_id"), row.get("hole_number"), _label(row.get("kind")),
            _label(row.get("status")), row.get("last_observed_at_utc")] for row in coverage], "没有观察点资料；无法判断覆盖情况。"),
        "## 问题 → 确认 → 派工 → 复查\n",
        "以下是输入记录的最终状态；没有问题记录不等于球场没有问题。\n",
        _md_table(CASE_HEADERS, [_case_cells(case) for case in _final_records(report, "cases")], "没有问题证据。"),
        "## 维护任务\n",
        "作业完成与复查通过分别展示；任务记录不是物理执行凭据。\n",
        _md_table(TASK_HEADERS, [_task_cells(task) for task in _final_records(report, "tasks")], "没有维护任务记录。"),
    ]
    if not _records(report.get("frames")):
        output.append("没有回放帧；无法显示巡检过程或最近观察时间。\n")
    return "\n".join(output)


_STYLE = r"""
:root{--paper:#f3f4ef;--card:#fff;--ink:#1d3028;--muted:#627169;--line:#dce3dc;--green:#246949;--gold:#b87b19;--red:#a94436;--purple:#786381}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}button,select,input{font:inherit}button,select{border:1px solid var(--line);background:#fff;border-radius:7px;padding:8px 12px;color:var(--ink)}button{cursor:pointer}button:disabled{cursor:default;opacity:.5}:focus-visible{outline:3px solid #6ba8d0;outline-offset:3px}header.hero{background:#172e24;color:#eef5ee;padding:35px max(24px,calc((100vw - 1320px)/2)) 30px}.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#c1d2b9}.hero h1{margin:7px 0;font-size:clamp(25px,3vw,38px);font-weight:650;letter-spacing:-.04em}.hero p{color:#c5d2c9;max-width:950px;margin:6px 0}.meta{font-size:12px;display:flex;flex-wrap:wrap;gap:6px 22px;overflow-wrap:anywhere}.badge{display:inline-block;border:1px solid currentColor;border-radius:4px;padding:1px 7px;font-size:10px;letter-spacing:.05em;color:#f0d999;vertical-align:middle;margin-left:10px}main{max-width:1368px;padding:24px;margin:auto}.notice{background:#fff9e9;border:1px solid #e7dcb9;border-radius:9px;padding:12px 16px;color:#6c5a2d;margin-bottom:20px}.notice ul{margin:0;padding-left:18px}.notice li+li{margin-top:3px}.stats{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:18px 0}.stat{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:14px 16px}.stat strong{display:block;font-size:30px;font-weight:650;line-height:1.25}.stat span{display:block;font-size:12px;color:var(--muted)}.stat small{font-size:11px;color:var(--muted)}.section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin:18px 0;min-width:0}.section h2{font-size:18px;margin:0 0 4px;letter-spacing:-.02em}.section p.help{color:var(--muted);font-size:12px;margin:0 0 14px}.toolbar{display:flex;align-items:center;flex-wrap:wrap;gap:10px 18px;margin:16px 0}.toolbar label{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}#play{background:var(--green);color:#fff;border-color:var(--green);min-width:84px}#timeline{flex:1;min-width:180px;accent-color:var(--green)}#frame-time{font-variant-numeric:tabular-nums;font-size:12px;color:var(--muted)}.map-shell{border:1px solid var(--line);border-radius:8px;background:#f6f8f3;overflow:hidden}.map-shell svg{display:block;width:100%;height:auto;min-height:240px}.legend{display:flex;gap:12px 18px;flex-wrap:wrap;margin:12px 0;font-size:11px;color:var(--muted)}.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}.map-note{font-size:11px;fill:#66776a}.hole-label{font-size:12px;fill:#647567;font-weight:600}.checkpoint-label{font-size:10px;fill:#344c3c}.cart-label{font-size:10px;fill:#254d8a;font-weight:650}.map-point{stroke:#fff;stroke-width:2}.table-wrap{overflow:auto;max-height:530px;border:1px solid var(--line);border-radius:7px}table{width:100%;border-collapse:collapse;font-size:12px;text-align:left}th{position:sticky;top:0;background:#f5f7f3;color:#56685a;font-weight:600;white-space:nowrap;z-index:1}td,th{padding:10px 12px;border-bottom:1px solid #e7ece6;vertical-align:top}td{overflow-wrap:anywhere;min-width:80px;max-width:310px}tbody tr:last-child td{border-bottom:0}.empty{padding:26px;color:var(--muted)}.count-line{font-size:12px;color:var(--muted);margin:8px 0}.details-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.details-grid .section{margin:0}footer{color:var(--muted);font-size:11px;padding:8px 0 22px}.readonly{display:inline-flex;align-items:center;border-radius:5px;background:#ecf2ec;padding:3px 8px;font-size:11px;color:var(--green)}noscript p{background:#fff9e9;padding:12px;border:1px solid #e7dcb9}.no-map{padding:24px;color:var(--muted)}@media(max-width:800px){main{padding:14px}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.section{padding:14px}.details-grid{grid-template-columns:1fr}.hero{padding:26px 18px!important}.toolbar label{flex:1}.toolbar select{width:100%}#timeline{flex-basis:100%}}@media print{header.hero{background:#fff;color:#172e24;padding:15px 0}.hero p,.eyebrow{color:#333}main{padding:0}.toolbar{display:none}.table-wrap{max-height:none;overflow:visible}.section{break-inside:avoid}.notice{background:#fff}.stats{grid-template-columns:repeat(5,1fr)}}
"""

_SCRIPT = r"""
'use strict';
(() => {
  const report = JSON.parse(document.getElementById('report-data').textContent);
  const labels = JSON.parse(document.getElementById('label-data').textContent);
  const frames = Array.isArray(report.frames) ? report.frames : [];
  const checkpoints = Array.isArray(report.checkpoints) ? report.checkpoints : [];
  const statuses = ['UNOBSERVED','OBSERVED_CLEAR','CANDIDATE','UNUSABLE','STALE'];
  const colors = {UNOBSERVED:'#8c9790',OBSERVED_CLEAR:'#337c59',CANDIDATE:'#c68926',UNUSABLE:'#a95045',STALE:'#867090'};
  const byId = new Map(checkpoints.map(point => [String(point.checkpoint_id),point]));
  const el = id => document.getElementById(id);
  const text = value => value === null || value === undefined || value === '' ? '未提供' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  const value = (row,...keys) => { for(const key of keys) if(row[key] !== null && row[key] !== undefined && row[key] !== '') return row[key]; return null; };
  const label = item => labels[text(item)] ? labels[text(item)]+' · '+text(item) : text(item);
  const records = data => Array.isArray(data) ? data.filter(row => row && typeof row === 'object') : data && typeof data === 'object' ? Object.values(data).filter(row => row && typeof row === 'object') : [];
  let index = Math.max(0,frames.length-1), timer = null;
  const table = (id,rows,columns,empty) => {
    const body = el(id); body.replaceChildren();
    if(!rows.length){ const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=columns;td.className='empty';td.textContent=empty;tr.append(td);body.append(tr);return; }
    for(const row of rows){ const tr=document.createElement('tr');for(const item of row){const td=document.createElement('td');td.textContent=text(item);tr.append(td);}body.append(tr); }
  };
  const ns='http://www.w3.org/2000/svg';
  const svgNode = (tag,attributes={},content=null) => {const node=document.createElementNS(ns,tag);for(const [key,item] of Object.entries(attributes))node.setAttribute(key,String(item));if(content!==null)node.textContent=String(content);return node;};
  const coordinates = checkpoints.concat(frames.flatMap(frame=>records(frame.carts))).filter(row=>Number.isFinite(row.x_m)&&Number.isFinite(row.y_m));
  const xs=coordinates.map(row=>row.x_m),ys=coordinates.map(row=>row.y_m);
  const minX=xs.length?Math.min(...xs):0,maxX=xs.length?Math.max(...xs):1,minY=ys.length?Math.min(...ys):0,maxY=ys.length?Math.max(...ys):1;
  const scale=Math.min(870/Math.max(1,maxX-minX),390/Math.max(1,maxY-minY));
  const x=v=>500+(v-(minX+maxX)/2)*scale,y=v=>260-(v-(minY+maxY)/2)*scale;
  function renderMap(frame,coverage,visible){
    const svg=el('course-map');svg.replaceChildren();
    svg.append(svgNode('title',{},'球场观察点示意：仅显示提供的坐标和该帧覆盖状态'));
    for(let i=80;i<960;i+=80)svg.append(svgNode('line',{x1:i,x2:i,y1:35,y2:490,stroke:'#e6ebe3','stroke-width':1}));
    for(let j=50;j<500;j+=55)svg.append(svgNode('line',{x1:40,x2:960,y1:j,y2:j,stroke:'#e6ebe3','stroke-width':1}));
    if(!checkpoints.length){svg.append(svgNode('text',{x:50,y:70,class:'map-note'},'没有观察点坐标；无法绘制覆盖示意。'));return;}
    const holeGroups=new Map();
    for(const point of visible){if(!Number.isFinite(point.x_m)||!Number.isFinite(point.y_m))continue;
      const status=coverage.get(String(point.checkpoint_id))?.status??'UNOBSERVED';
      const group=svgNode('g',{'data-checkpoint-id':point.checkpoint_id});
      group.append(svgNode('title',{},`第 ${text(point.hole_number)} 洞 · ${text(point.checkpoint_id)} · ${label(point.kind)} · ${label(status)}`));
      const px=x(point.x_m),py=y(point.y_m),fill=colors[status]??colors.UNOBSERVED;
      const marker=point.kind==='BUNKER'?svgNode('rect',{x:px-5,y:py-5,width:10,height:10,rx:2,fill,class:'map-point'}):point.kind==='GREEN'?svgNode('path',{d:`M ${px} ${py-7} L ${px+7} ${py+5} L ${px-7} ${py+5} Z`,fill,class:'map-point'}):svgNode('circle',{cx:px,cy:py,r:6,fill,class:'map-point'});
      group.append(marker);group.append(svgNode('text',{x:px+9,y:py+3,class:'checkpoint-label'},({FAIRWAY:'球道',BUNKER:'沙坑',GREEN:'果岭'}[point.kind]??'点位')));svg.append(group);
      const key=String(point.hole_number);if(!holeGroups.has(key))holeGroups.set(key,[]);holeGroups.get(key).push(point);
    }
    for(const [hole,points] of holeGroups){const px=points.reduce((a,p)=>a+x(p.x_m),0)/points.length,py=Math.min(...points.map(p=>y(p.y_m)))-16;svg.append(svgNode('text',{x:px,y:py,class:'hole-label','text-anchor':'middle'},`H${hole.padStart(2,'0')}`));}
    for(const cart of records(frame.carts)){if(!Number.isFinite(cart.x_m)||!Number.isFinite(cart.y_m))continue;const px=x(cart.x_m),py=y(cart.y_m),g=svgNode('g');g.append(svgNode('path',{d:`M ${px} ${py-8} L ${px+8} ${py} L ${px} ${py+8} L ${px-8} ${py} Z`,fill:'#416fb0',stroke:'#fff','stroke-width':2}));g.append(svgNode('text',{x:px+11,y:py-5,class:'cart-label'},text(cart.cart_id)));svg.append(g);}
    svg.append(svgNode('text',{x:24,y:514,class:'map-note'},'示意坐标（米） · 点 = 预设观察点 · 蓝色菱形 = 当前帧合成球车位置 · 无路线推断'));
  }
  const holeOf=row=>value(row,'hole_number')??byId.get(String(row.checkpoint_id))?.hole_number;
  function render(){
    const frame=frames[index]??{cases:report.cases,tasks:report.tasks},hole=el('hole-filter').value,statusFilter=el('status-filter').value;
    const coverage=new Map(records(frame.coverage).map(row=>[String(row.checkpoint_id),row]));
    const rows=checkpoints.map(point=>{const sample=coverage.get(String(point.checkpoint_id))??{};return {...point,status:statuses.includes(sample.status)?sample.status:'UNOBSERVED',last_observed_at_utc:sample.last_observed_at_utc??null};});
    const visible=rows.filter(row=>(!hole||String(row.hole_number)===hole)&&(!statusFilter||row.status===statusFilter));
    const counts=Object.fromEntries(statuses.map(status=>[status,rows.filter(row=>row.status===status).length]));
    for(const status of statuses)el('count-'+status).textContent=String(counts[status]);
    el('frame-time').textContent=frames.length?`第 ${text(frame.minute)} 分钟 · ${text(frame.at_utc)} (UTC)`:'没有回放帧';
    el('timeline').value=String(index);el('timeline').setAttribute('aria-valuetext',el('frame-time').textContent);
    el('coverage-count').textContent=`显示 ${visible.length} / ${checkpoints.length} 个预设观察点。统计不是球场面积覆盖率；筛选仅改变显示。`;
    renderMap(frame,coverage,visible);
    table('coverage-body',visible.map(row=>[row.checkpoint_id,row.hole_number,label(row.kind),label(row.status),row.last_observed_at_utc]),5,'没有符合筛选条件的观察点。');
    const cases=records(frame.cases).filter(row=>!hole||String(holeOf(row))===hole);
    const casesById=new Map(records(frame.cases).map(row=>[String(row.case_id),row]));
    const tasks=records(frame.tasks).map(row=>({...row,hole_number:holeOf(row)??holeOf(casesById.get(String(row.case_id))??{})})).filter(row=>!hole||String(holeOf(row))===hole);
    el('case-count').textContent=`当前帧：${cases.length} 条问题记录。覆盖状态筛选只作用于地图和覆盖表。`;
    el('task-count').textContent=`当前帧：${tasks.length} 条任务记录。`;
    table('case-body',cases.map(row=>[value(row,'case_id','finding_id'),holeOf(row),value(row,'checkpoint_id','feature_id'),label(value(row,'condition_kind','kind','category','issue_type','defect_type')),label(value(row,'status','state')),value(row,'confirmed_by','reviewed_by'),value(row,'latest_task_id','task_id','assigned_task_id'),value(row,'verified_at_utc','reviewed_at_utc','closed_at_utc'),value(row,'verification_reason','review_reason','detail','summary','note')]),9,'该帧没有问题证据；不能据此认定球场正常。');
    table('task-body',tasks.map(row=>[row.task_id,value(row,'case_id','finding_id'),holeOf(row),label(value(row,'task_kind','kind','task_type','action')),label(value(row,'status','state')),value(row,'assigned_to','resource_id','assignee_id')??row.resource?.id,label(value(row,'resource_kind','assignee_kind')??row.resource?.kind),value(row,'assigned_at_utc','created_at_utc'),row.completed_at_utc,value(row,'verified_at_utc','reviewed_at_utc')]),10,'该帧没有维护任务记录。');
  }
  function stop(){if(timer!==null)clearInterval(timer);timer=null;el('play').textContent='播放回放';el('play').setAttribute('aria-pressed','false');}
  el('play').addEventListener('click',()=>{if(timer!==null){stop();return;}if(index>=frames.length-1)index=0;render();el('play').textContent='暂停';el('play').setAttribute('aria-pressed','true');timer=setInterval(()=>{if(index>=frames.length-1){stop();return;}index+=1;render();},700);});
  el('timeline').addEventListener('input',()=>{stop();index=Number(el('timeline').value);render();});
  for(const id of ['hole-filter','status-filter'])el(id).addEventListener('change',render);
  window.addEventListener('pagehide',stop);
  render();
})();
"""


def _json_for_html(value: Any) -> str:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def render_report(report: dict[str, Any]) -> str:
    """Return a standalone HTML replay; no files, network or domain mutations."""
    _validate(report)
    frame = _frame(report)
    coverage = _coverage(report, frame)
    counts = Counter(row["status"] for row in coverage)
    holes = sorted({row.get("hole_number") for row in coverage if isinstance(row.get("hole_number"), int)})
    frames = _records(report.get("frames"))
    disclaimers = list(BASE_DISCLAIMERS) + [str(value) for value in report.get("disclaimers", [])]
    digest = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode("ascii")
    csp = f"default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    stats = "".join(f'<div class="stat"><strong id="count-{status}">{counts.get(status, 0)}</strong><span>{label}</span><small>{status}</small></div>' for status, label in COVERAGE_LABELS.items())
    colors = {"UNOBSERVED": "#8c9790", "OBSERVED_CLEAR": "#337c59", "CANDIDATE": "#c68926", "UNUSABLE": "#a95045", "STALE": "#867090"}
    legend = "".join(f'<span><i style="background:{colors[status]}"></i>{label}</span>' for status, label in COVERAGE_LABELS.items())
    hole_options = '<option value="">全部球洞</option>' + "".join(f'<option value="{hole}">第 {hole:02d} 洞</option>' for hole in holes)
    status_options = '<option value="">全部覆盖状态</option>' + "".join(f'<option value="{status}">{label}</option>' for status, label in COVERAGE_LABELS.items())
    coverage_table = _table(COVERAGE_HEADERS, [[row.get("checkpoint_id"), row.get("hole_number"), _label(row.get("kind")), _label(row["status"]), row.get("last_observed_at_utc")] for row in coverage], "没有观察点资料；无法判断覆盖情况。", "coverage-body")
    case_table = _table(CASE_HEADERS, [_case_cells(row) for row in _final_records(report, "cases")], "没有问题证据；不能据此认定球场正常。", "case-body")
    task_table = _table(TASK_HEADERS, [_task_cells(row) for row in _final_records(report, "tasks")], "没有维护任务记录。", "task-body")
    disabled = " disabled" if len(frames) < 2 else ""
    timestamp = f"第 {_escape(frame.get('minute'))} 分钟 · {_escape(frame.get('at_utc'))} (UTC)" if frames else "没有回放帧"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{html.escape(csp, quote=True)}"><title>NXTektal 球场巡检报告 · SIMULATION</title><style>{_STYLE}</style></head>
<body><header class="hero"><div class="eyebrow">NXTektal · Course monitoring <span class="badge">SIMULATION</span></div><h1>球场巡检与维护记录</h1><p>看清观察范围，追踪问题从发现到复查的过程。</p><div class="meta"><span>场地 {_escape(report.get('site_id'))}</span><span>部署 {_escape(report.get('deployment_id'))}</span><span>地图版本 {_escape(report.get('map_revision'))}</span></div><div class="meta"><span>生成时间 UTC：{_escape(report.get('generated_at_utc'))}</span><span>场景时长：{_escape(report.get('duration_minutes'))} 分钟</span></div></header>
<main><div class="notice"><ul>{''.join(f'<li>{_escape(item)}</li>' for item in disclaimers)}</ul></div>
<noscript><p>JavaScript 未启用：下方表格展示最终快照；时间轴和地图回放不可用。</p></noscript>
<div class="count-line">场景总计：{_escape(report.get("summary", {}).get("observations"))} 条合成观测 · {_escape(report.get("summary", {}).get("issues"))} 条问题 · {_escape(report.get("summary", {}).get("tasks"))} 条任务 · {len(frames)} 帧记录。</div>
<div class="count-line">{len(holes)} 个球洞 · {len(coverage)} 个预设观察点。分母是观察点，不是球场面积。</div><div class="stats">{stats}</div>
<section class="section" aria-label="观察点地图与回放"><h2>观察点地图与回放</h2><p class="help">点位状态来自输入证据。球车位置与观察覆盖分别展示；不以球车经过推定草皮已检查。</p><div class="toolbar"><button id="play" type="button" aria-pressed="false"{disabled}>播放回放</button><label for="timeline">时间轴</label><input id="timeline" type="range" min="0" max="{max(0, len(frames)-1)}" value="{max(0, len(frames)-1)}" step="1"{disabled}><output id="frame-time" for="timeline">{timestamp}</output></div>
<div class="toolbar"><label for="hole-filter">球洞<select id="hole-filter">{hole_options}</select></label><label for="status-filter">覆盖状态<select id="status-filter">{status_options}</select></label><span class="readonly">只读回放 · 不改变业务状态</span></div>
<div class="map-shell"><svg id="course-map" viewBox="0 0 1000 530" role="img" aria-label="球场观察点与合成球车位置示意"><text x="35" y="65" class="map-note">地图需要 JavaScript；全部观察点状态可在下表查阅。</text></svg></div><div class="legend">{legend}<span>○ 球道 · □ 沙坑 · △ 果岭 · ◆ 球车</span></div><p id="coverage-count" class="count-line">{len(coverage)} 个预设观察点。不是球场面积覆盖率。</p>{coverage_table}</section>
<section class="section" aria-label="问题处理记录"><h2>问题 → 确认 → 派工 → 复查</h2><p class="help">展示合成证据中的脚本角色确认与处理状态；没有真实图片，也不提供模拟确认按钮。</p><p id="case-count" class="count-line">最终快照；播放时展示所选帧记录。</p>{case_table}</section>
<section class="section" aria-label="维护任务记录"><h2>维护任务与复查</h2><p class="help">作业完成不等于复查通过。HUMAN 和 SIMULATED_ROBOT 为场景中的资源类型，不代表现场设备执行。</p><p id="task-count" class="count-line">最终快照；播放时展示所选帧记录。</p>{task_table}</section>
<footer>报告格式：{SCHEMA} · 时间按输入 UTC 显示 · 离线单文件 · 浏览器操作仅改变当前视图</footer></main>
<script id="report-data" type="application/json">{_json_for_html(report)}</script><script id="label-data" type="application/json">{_json_for_html(STATUS_LABELS)}</script><script>{_SCRIPT}</script></body></html>'''
