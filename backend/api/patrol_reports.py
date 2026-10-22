"""Filesystem-backed patrol inspection reports under repo/report."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings as django_settings
from django.utils import timezone

from .patrol_task_control import TASK_SCENES, get_task_state, resolve_task_scene


def report_root() -> Path:
    configured = getattr(django_settings, "PATROL_REPORT_ROOT", None)
    if configured:
        return Path(configured).resolve()
    return (Path(django_settings.BASE_DIR).resolve().parent / "report").resolve()


def ensure_report_root() -> Path:
    root = report_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_slug(value: str) -> str:
    text = re.sub(r"[^0-9A-Za-z一-鿿_-]+", "-", str(value or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text or "report"


def public_report_url(filename: str) -> str:
    name = Path(filename).name
    return f"/report/{name}"


def _sample_payload(scene_key: str) -> dict[str, Any]:
    scene = TASK_SCENES[scene_key]
    stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")
    common = {
        "scene": scene_key,
        "sceneTitle": scene["title"],
        "status": "success",
        "generatedAt": stamp,
        "source": "sample",
        "runId": f"SAMPLE-{scene_key.upper()}",
    }
    if scene_key == "ev-parking":
        return {
            **common,
            "title": "电动车违停巡检示例报告",
            "summary": "示例报告：本轮电动车违停巡检覆盖教学楼与宿舍区主干道，共回传 3 处疑似违停点，建议安保复核后闭环。",
            "metrics": [
                {"label": "巡检场景", "value": scene["title"]},
                {"label": "发现数量", "value": "3"},
                {"label": "执行时段", "value": "08:10 - 08:42"},
                {"label": "闭环建议", "value": "现场复核"},
            ],
            "timeline": [
                {"title": "任务启动", "text": "08:10 启动电动车违停巡检"},
                {"title": "现场执行", "text": "08:18 进入宿舍区主干道"},
                {"title": "结果回传", "text": "08:35 累计回传 3 条违停抓拍"},
                {"title": "任务完成", "text": "08:42 任务正常结束"},
            ],
            "findings": [
                {"title": "违停 #1", "text": "图书馆东侧非机动车道，疑似电动车占道"},
                {"title": "违停 #2", "text": "2 号宿舍楼下消防通道口，车牌 OCR 待复核"},
                {"title": "违停 #3", "text": "食堂西侧人行道，建议安保现场确认"},
            ],
        }
    if scene_key == "road-defect":
        return {
            **common,
            "title": "道路缺陷巡检示例报告",
            "summary": "示例报告：本轮道路缺陷巡检覆盖校园主干环路，识别到裂缝与坑槽共 2 类问题，建议纳入设施维修计划。",
            "metrics": [
                {"label": "巡检场景", "value": scene["title"]},
                {"label": "发现数量", "value": "4"},
                {"label": "执行时段", "value": "09:05 - 09:40"},
                {"label": "风险等级", "value": "中"},
            ],
            "timeline": [
                {"title": "任务启动", "text": "09:05 启动道路缺陷巡检"},
                {"title": "现场执行", "text": "09:16 完成南门至实验楼路段"},
                {"title": "结果回传", "text": "09:33 回传裂缝/坑槽检测结果"},
                {"title": "任务完成", "text": "09:40 任务正常结束"},
            ],
            "findings": [
                {"title": "缺陷 #1", "text": "南门内侧慢车道纵向裂缝，建议跟踪"},
                {"title": "缺陷 #2", "text": "实验楼路口坑槽，建议优先修补"},
                {"title": "缺陷 #3", "text": "体育馆旁人行道网裂，建议纳入周检修"},
                {"title": "缺陷 #4", "text": "停车场入口轻微破损，暂可观察"},
            ],
        }
    return {
        **common,
        "title": "仪表巡检示例报告",
        "summary": "示例报告：本轮仪表巡检完成机房与配电间关键表计读取，读数整体正常，1 处需人工复核。",
        "metrics": [
            {"label": "巡检场景", "value": scene["title"]},
            {"label": "读数条数", "value": "5"},
            {"label": "执行时段", "value": "10:20 - 10:48"},
            {"label": "异常项", "value": "1"},
        ],
        "timeline": [
            {"title": "任务启动", "text": "10:20 启动仪表巡检"},
            {"title": "现场执行", "text": "10:28 完成配电间第一组表计"},
            {"title": "结果回传", "text": "10:41 回传 5 条仪表识别结果"},
            {"title": "任务完成", "text": "10:48 任务正常结束"},
        ],
        "findings": [
            {"title": "仪表 #A1", "text": "电压表读数 220.4V，状态正常"},
            {"title": "仪表 #A2", "text": "电流表读数 12.8A，状态正常"},
            {"title": "仪表 #B1", "text": "温度表读数偏高，建议人工复核"},
            {"title": "仪表 #B2", "text": "压力表读数 0.42MPa，状态正常"},
            {"title": "仪表 #C1", "text": "液位计读数稳定，状态正常"},
        ],
    }


def render_report_html(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics") or []
    timeline = payload.get("timeline") or []
    findings = payload.get("findings") or []
    metric_html = "".join(
        f"<div class='metric'><span>{item.get('label', '')}</span><strong>{item.get('value', '')}</strong></div>"
        for item in metrics
    )
    timeline_html = "".join(
        f"<li><strong>{item.get('title', '')}</strong><span>{item.get('text', '')}</span></li>"
        for item in timeline
    )
    findings_html = "".join(
        f"<li><strong>{item.get('title', '')}</strong><span>{item.get('text', '')}</span></li>"
        for item in findings
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{payload.get('title') or '巡检报告'}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #07111f;
      --card: rgba(12, 28, 48, 0.92);
      --line: rgba(120, 170, 220, 0.28);
      --text: #e8f3ff;
      --muted: #9bb4cc;
      --accent: #3dbbff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top, #123255 0%, var(--bg) 55%);
      color: var(--text);
      padding: 32px 18px 48px;
    }}
    .wrap {{
      max-width: 920px;
      margin: 0 auto;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 28px;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 8px 0 6px;
      font-size: 28px;
    }}
    .meta, .summary, li span {{
      color: var(--muted);
      line-height: 1.6;
    }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 22px 0;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: rgba(255,255,255,0.03);
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .metric strong {{
      font-size: 18px;
    }}
    h2 {{
      margin: 28px 0 12px;
      font-size: 18px;
    }}
    ul {{
      list-style: none;
      padding: 0;
      margin: 0;
      display: grid;
      gap: 10px;
    }}
    li {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.025);
      display: grid;
      gap: 4px;
    }}
    .footer {{
      margin-top: 28px;
      font-size: 12px;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="eyebrow">Campus Patrol Report</div>
    <h1>{payload.get('title') or '巡检报告'}</h1>
    <div class="meta">场景：{payload.get('sceneTitle') or '--'} · 任务号：{payload.get('runId') or '--'} · 生成时间：{payload.get('generatedAt') or '--'}</div>
    <p class="summary">{payload.get('summary') or ''}</p>
    <div class="metrics">{metric_html}</div>
    <h2>执行时间线</h2>
    <ul>{timeline_html or '<li><strong>暂无时间线</strong><span>本轮未记录执行节点。</span></li>'}</ul>
    <h2>关键发现</h2>
    <ul>{findings_html or '<li><strong>暂无关键发现</strong><span>本轮未回传检测结果。</span></li>'}</ul>
    <div class="footer">文件由校园智能巡检助手自动生成，可从首页智能巡检助手下方的「巡检报告」入口打开报告记录页。</div>
  </div>
</body>
</html>
"""


def write_report_files(payload: dict[str, Any], *, filename_stem: str | None = None) -> dict[str, Any]:
    root = ensure_report_root()
    scene = str(payload.get("scene") or "patrol")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = _safe_slug(filename_stem or f"{scene}-{stamp}")
    html_name = f"{stem}.html"
    json_name = f"{stem}.json"
    html_path = root / html_name
    json_path = root / json_name

    payload = dict(payload)
    payload.setdefault("generatedAt", timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"))
    payload["filename"] = html_name
    payload["jsonFilename"] = json_name
    payload["url"] = public_report_url(html_name)

    html_path.write_text(render_report_html(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "success": True,
        "report": payload,
        "path": str(html_path),
        "jsonPath": str(json_path),
        "url": payload["url"],
    }


def build_report_from_task(task: dict[str, Any] | None = None, *, source: str = "assistant") -> dict[str, Any]:
    state = task or get_task_state()
    scene_key = str(state.get("scene") or "").strip()
    scene = TASK_SCENES.get(scene_key) or resolve_task_scene(scene_key)
    if not scene:
        raise ValueError("NO_TASK_SCENE")

    status = str(state.get("status") or "idle")
    status_text = {
        "success": "已完成",
        "failure": "异常结束",
        "running": "执行中",
        "idle": "待命",
    }.get(status, status)
    findings = state.get("findings") or []
    duration = " -- "
    if state.get("startedAt") and state.get("finishedAt"):
        duration = f"{state['startedAt']} 至 {state['finishedAt']}"
    elif state.get("startedAt"):
        duration = str(state["startedAt"])

    payload = {
        "scene": scene["key"],
        "sceneTitle": scene["title"],
        "title": f"{scene['title']}任务报告",
        "summary": (
            f"{scene['title']}已结束。本轮状态为「{status_text}」，"
            f"共回传 {int(state.get('findingCount') or 0)} 条关键发现。"
            + (f"最近一条：{state.get('latestFinding')}。" if state.get("latestFinding") else "")
        ),
        "status": status,
        "runId": state.get("runId") or "",
        "generatedAt": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "metrics": [
            {"label": "任务场景", "value": scene["title"]},
            {"label": "发现数量", "value": str(int(state.get("findingCount") or 0))},
            {"label": "执行时段", "value": duration},
            {"label": "当前状态", "value": status_text},
        ],
        "timeline": [
            {"title": "任务启动", "text": f"启动于 {state.get('startedAt')}" if state.get("startedAt") else "已启动"},
            {"title": "现场执行", "text": state.get("progressLabel") or "已执行"},
            {
                "title": "结果回传",
                "text": (
                    f"累计回传 {int(state.get('findingCount') or 0)} 条，最近：{state.get('latestFinding') or '--'}"
                    if int(state.get("findingCount") or 0)
                    else "本轮无新增发现"
                ),
            },
            {
                "title": "任务完成",
                "text": f"完成于 {state.get('finishedAt')}" if state.get("finishedAt") else "已结束",
            },
        ],
        "findings": [
            {
                "title": item.get("title") or "关键发现",
                "text": item.get("text") or "",
            }
            for item in findings[:12]
        ]
        or [{"title": "暂无关键发现", "text": "本轮任务未回传检测结果。"}],
    }
    return payload


def generate_task_report(*, source: str = "assistant", allow_running: bool = False) -> dict[str, Any]:
    state = get_task_state()
    if not state.get("scene"):
        return {
            "success": False,
            "error": "NO_ACTIVE_TASK",
            "message": "当前没有可生成报告的巡检任务，请先完成一轮巡检。",
            "task": state,
        }
    if state.get("status") == "running" and not allow_running:
        return {
            "success": False,
            "error": "TASK_RUNNING",
            "message": f"{state.get('sceneTitle') or '巡检任务'}仍在执行中，完成后可再生成报告。",
            "task": state,
        }
    if state.get("status") not in {"success", "failure"} and not allow_running:
        return {
            "success": False,
            "error": "TASK_NOT_FINISHED",
            "message": "当前任务尚未结束，暂不能生成巡检报告。",
            "task": state,
        }

    payload = build_report_from_task(state, source=source)
    result = write_report_files(payload)
    result["task"] = state
    result["message"] = f"已生成{payload['sceneTitle']}报告"
    return result


def list_reports(limit: int = 50) -> list[dict[str, Any]]:
    root = ensure_report_root()
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.lower() == "index.html":
            continue
        meta: dict[str, Any] = {
            "filename": path.name,
            "title": path.stem,
            "url": public_report_url(path.name),
            "updatedAt": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        json_path = path.with_suffix(".json")
        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                meta.update(
                    {
                        "title": data.get("title") or meta["title"],
                        "scene": data.get("scene") or "",
                        "sceneTitle": data.get("sceneTitle") or "",
                        "runId": data.get("runId") or "",
                        "status": data.get("status") or "",
                        "generatedAt": data.get("generatedAt") or meta["updatedAt"],
                        "source": data.get("source") or "",
                    }
                )
            except Exception:
                pass
        items.append(meta)
        if len(items) >= max(1, min(limit, 200)):
            break
    return items


def ensure_sample_reports() -> list[dict[str, Any]]:
    ensure_report_root()
    created: list[dict[str, Any]] = []
    for scene_key, stem in (
        ("ev-parking", "sample-ev-parking"),
        ("road-defect", "sample-road-defect"),
        ("gauge", "sample-gauge"),
    ):
        html_path = report_root() / f"{stem}.html"
        if html_path.exists():
            created.append(
                {
                    "filename": html_path.name,
                    "url": public_report_url(html_path.name),
                    "scene": scene_key,
                    "existed": True,
                }
            )
            continue
        result = write_report_files(_sample_payload(scene_key), filename_stem=stem)
        created.append(
            {
                "filename": result["report"]["filename"],
                "url": result["url"],
                "scene": scene_key,
                "existed": False,
            }
        )
    return created
