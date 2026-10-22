"""In-memory patrol task control helpers for the campus assistant."""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from typing import Any

from django.conf import settings as django_settings
from django.utils import timezone

from .patrol_ssh import run_remote_start_script

TASK_SCENES: dict[str, dict[str, str]] = {
    "ev-parking": {
        "key": "ev-parking",
        "title": "电动车违停巡检",
        "page": "patrol",
        "aliases": "电动车违停|违停巡检|电动车巡检|ev-parking|ev parking",
    },
    "road-defect": {
        "key": "road-defect",
        "title": "道路缺陷巡检",
        "page": "road-defect",
        "aliases": "道路缺陷|路面缺陷|道路巡检|road-defect|road defect",
    },
    "gauge": {
        "key": "gauge",
        "title": "仪表巡检",
        "page": "gauge",
        "aliases": "仪表巡检|仪表识别|gauge",
    },
}

_LOCK = threading.RLock()
_TASK_STATE: dict[str, Any] = {
    "scene": "",
    "status": "idle",
    "runId": "",
    "startedAt": "",
    "finishedAt": "",
    "progressLabel": "待命",
    "findingCount": 0,
    "latestFinding": "",
    "timelineNote": "当前无活跃任务",
    "findings": [],
    "message": "",
    "updatedAt": "",
}


def _now_text() -> str:
    return timezone.localtime().strftime("%Y-%m-%d %H:%M:%S")


def list_task_scenes() -> list[dict[str, str]]:
    return [
        {
            "key": item["key"],
            "title": item["title"],
            "page": item["page"],
        }
        for item in TASK_SCENES.values()
    ]


def resolve_task_scene(raw: str | None) -> dict[str, str] | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in TASK_SCENES:
        return TASK_SCENES[text]
    for scene in TASK_SCENES.values():
        if text == scene["title"].lower() or text == scene["page"].lower():
            return scene
        aliases = [part.strip().lower() for part in scene["aliases"].split("|") if part.strip()]
        for alias in aliases:
            if alias and alias in text:
                return scene
    return None


def get_task_state() -> dict[str, Any]:
    with _LOCK:
        state = dict(_TASK_STATE)
        state["findings"] = [dict(item) for item in _TASK_STATE.get("findings") or []]
        scene = TASK_SCENES.get(state.get("scene") or "")
        state["sceneTitle"] = scene["title"] if scene else ""
        state["page"] = scene["page"] if scene else ""
        state["scenes"] = list_task_scenes()
        return state


def _set_task_state(**kwargs: Any) -> dict[str, Any]:
    with _LOCK:
        for key, value in kwargs.items():
            if key == "findings" and isinstance(value, list):
                _TASK_STATE["findings"] = [dict(item) for item in value[:20]]
            else:
                _TASK_STATE[key] = value
        _TASK_STATE["updatedAt"] = timezone.now().isoformat()
        return get_task_state()


def update_task_status(
    *,
    scene: str | None = None,
    status: str | None = None,
    progress_label: str | None = None,
    finding_count: int | None = None,
    latest_finding: str | None = None,
    finding: dict[str, Any] | None = None,
    message: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if scene:
        resolved = resolve_task_scene(scene)
        if resolved:
            payload["scene"] = resolved["key"]
    if status:
        normalized = str(status).strip().lower()
        if normalized in {"idle", "running", "success", "failure"}:
            payload["status"] = normalized
            if normalized == "running":
                payload.setdefault("startedAt", _now_text())
                payload["finishedAt"] = ""
            if normalized in {"success", "failure"}:
                payload["finishedAt"] = _now_text()
    if progress_label is not None:
        payload["progressLabel"] = str(progress_label)
    if finding_count is not None:
        payload["findingCount"] = max(0, int(finding_count))
    if latest_finding is not None:
        payload["latestFinding"] = str(latest_finding)
    if message is not None:
        payload["message"] = str(message)
    if run_id is not None:
        payload["runId"] = str(run_id)

    with _LOCK:
        if finding:
            item = {
                "title": str(finding.get("title") or "关键发现"),
                "text": str(finding.get("text") or ""),
                "at": str(finding.get("at") or _now_text()),
            }
            findings = [item, *[dict(row) for row in _TASK_STATE.get("findings") or []]]
            payload["findings"] = findings[:20]
            payload["findingCount"] = max(int(_TASK_STATE.get("findingCount") or 0) + 1, len(findings))
            payload["latestFinding"] = f"{item['title']} · {item['text']}".strip(" ·")
        return _set_task_state(**payload)


def start_task(scene_key: str, *, dry_run: bool = False) -> dict[str, Any]:
    scene = resolve_task_scene(scene_key)
    if not scene:
        return {
            "success": False,
            "error": "UNKNOWN_SCENE",
            "message": "未知巡检任务，仅支持电动车违停巡检、道路缺陷巡检、仪表巡检。",
        }

    current = get_task_state()
    if current.get("status") == "running" and current.get("scene") and current.get("scene") != scene["key"]:
        return {
            "success": False,
            "error": "TASK_BUSY",
            "message": f"当前已有任务「{current.get('sceneTitle') or current.get('scene')}」执行中，请先等待完成。",
            "task": current,
        }

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    ssh_result: dict[str, Any] = {
        "success": True,
        "message": "框架模式：已记录任务启动，未实际触发 SSH。",
        "dryRun": True,
    }

    if not dry_run:
        cfg_path = getattr(django_settings, "PATROL_SSH_CONFIG_PATH", None)
        if cfg_path is None:
            return {
                "success": False,
                "error": "NOT_CONFIGURED",
                "message": "服务端未设置 PATROL_SSH_CONFIG_PATH",
            }
        ssh_result = run_remote_start_script(cfg_path)
        ssh_result = dict(ssh_result or {})
        if not ssh_result.get("success"):
            task = update_task_status(
                scene=scene["key"],
                status="failure",
                progress_label="启动失败",
                message=str(ssh_result.get("message") or ssh_result.get("error") or "启动失败"),
                run_id=run_id,
            )
            return {
                "success": False,
                "error": ssh_result.get("error") or "START_FAILED",
                "message": ssh_result.get("message") or "巡检任务启动失败",
                "task": task,
                "ssh": ssh_result,
            }

    task = update_task_status(
        scene=scene["key"],
        status="running",
        progress_label="执行中",
        finding_count=0,
        latest_finding="",
        message=str(ssh_result.get("message") or f"已启动{scene['title']}"),
        run_id=run_id,
    )
    with _LOCK:
        _TASK_STATE["findings"] = []
        _TASK_STATE["timelineNote"] = f"{scene['title']}执行中"
        task = get_task_state()

    return {
        "success": True,
        "message": f"已启动{scene['title']}",
        "task": task,
        "ssh": ssh_result,
        "scene": scene,
    }


def mark_task_finished(status: str = "success", *, message: str | None = None) -> dict[str, Any]:
    current = get_task_state()
    if not current.get("scene"):
        return {
            "success": False,
            "error": "NO_ACTIVE_TASK",
            "message": "当前没有可结束的巡检任务。",
            "task": current,
        }
    normalized = "success" if str(status).lower() == "success" else "failure"
    scene = TASK_SCENES.get(current["scene"])
    title = scene["title"] if scene else current["scene"]
    task = update_task_status(
        status=normalized,
        progress_label="已完成" if normalized == "success" else "异常结束",
        message=message or (f"{title}已完成" if normalized == "success" else f"{title}异常结束"),
    )
    with _LOCK:
        _TASK_STATE["timelineNote"] = task["message"]
        task = get_task_state()
    return {"success": True, "message": task["message"], "task": task}


_START_RE = re.compile(
    r"(启动|开始|执行|开启).{0,12}(电动车|违停|道路缺陷|路面缺陷|仪表|gauge|road|ev)",
    re.I,
)
_STATUS_RE = re.compile(r"(状态|进度|进行到哪|有没有在巡检|巡检情况|任务情况)", re.I)
_REPORT_RE = re.compile(r"(生成|出具|导出).{0,8}(巡检)?报告|(巡检)?报告.{0,6}(生成|出具|导出)", re.I)


def detect_assistant_intent(question: str) -> dict[str, Any] | None:
    text = str(question or "").strip()
    if not text:
        return None

    scene = resolve_task_scene(text)
    if _START_RE.search(text) or re.search(r"^(启动|开始).*(巡检)?$", text):
        if not scene:
            # bare “开始巡检” without scene
            if re.search(r"巡检", text) and not re.search(r"电动车|违停|道路|路面|仪表|gauge|road|ev", text, re.I):
                return {
                    "action": "clarify_start",
                    "message": "请指定要启动的巡检任务：电动车违停巡检、道路缺陷巡检或仪表巡检。",
                }
            return None
        return {"action": "start_task", "scene": scene["key"], "sceneTitle": scene["title"]}

    if _REPORT_RE.search(text):
        return {
            "action": "generate_report",
            "scene": scene["key"] if scene else "",
            "sceneTitle": scene["title"] if scene else "",
        }

    if _STATUS_RE.search(text) or re.search(r"(查(一下|询)?|检测).{0,8}(状态|进度)", text):
        return {
            "action": "check_status",
            "scene": scene["key"] if scene else "",
            "sceneTitle": scene["title"] if scene else "",
        }
    return None
