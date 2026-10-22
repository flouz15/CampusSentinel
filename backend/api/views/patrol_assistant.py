"""Assistant-facing patrol task control and report endpoints."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..patrol_reports import (
    ensure_sample_reports,
    generate_task_report,
    list_reports,
    report_root,
)
from ..patrol_task_control import (
    detect_assistant_intent,
    get_task_state,
    mark_task_finished,
    start_task,
    update_task_status,
)
from .common import _get_request_data, _parse_int, require_auth_view


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def patrol_task_status(request):
    ensure_sample_reports()
    task = get_task_state()
    return JsonResponse(
        {
            "success": True,
            "task": task,
            "reports": list_reports(limit=12),
            "reportRoot": str(report_root()),
        }
    )


@csrf_exempt
@require_auth_view(allow_dashboard=True)
@require_http_methods(["POST"])
def patrol_task_start(request):
    payload = _get_request_data(request)
    scene = str(payload.get("scene") or payload.get("task") or "").strip()
    dry_run = bool(payload.get("dryRun"))
    # Framework default: avoid accidental SSH while scaffolding the assistant flow.
    if "dryRun" not in payload:
        dry_run = True
    result = start_task(scene, dry_run=dry_run)
    status = 200 if result.get("success") else 400
    if result.get("error") in {"NOT_CONFIGURED", "CONFIG_MISSING"}:
        status = 503
    return JsonResponse(result, status=status)


@csrf_exempt
@require_auth_view(allow_dashboard=True)
@require_http_methods(["POST"])
def patrol_task_finish(request):
    payload = _get_request_data(request)
    status_value = str(payload.get("status") or "success").strip().lower()
    message = str(payload.get("message") or "").strip() or None
    result = mark_task_finished(status_value, message=message)
    return JsonResponse(result, status=200 if result.get("success") else 400)


@csrf_exempt
@require_auth_view(allow_dashboard=True)
@require_http_methods(["POST"])
def patrol_task_sync(request):
    """Optional sync endpoint so frontend ROS state can refresh assistant task memory."""
    payload = _get_request_data(request)
    task = update_task_status(
        scene=payload.get("scene"),
        status=payload.get("status"),
        progress_label=payload.get("progressLabel"),
        finding_count=_parse_int(payload.get("findingCount")),
        latest_finding=payload.get("latestFinding"),
        finding=payload.get("finding") if isinstance(payload.get("finding"), dict) else None,
        message=payload.get("message"),
        run_id=payload.get("runId"),
    )
    return JsonResponse({"success": True, "task": task})


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def patrol_report_list(request):
    ensure_sample_reports()
    limit = _parse_int(request.GET.get("limit")) or 50
    return JsonResponse(
        {
            "success": True,
            "items": list_reports(limit=limit),
            "reportRoot": str(report_root()),
        }
    )


@csrf_exempt
@require_auth_view(allow_dashboard=True)
@require_http_methods(["POST"])
def patrol_report_generate(request):
    payload = _get_request_data(request)
    allow_running = bool(payload.get("allowRunning"))
    source = str(payload.get("source") or "assistant").strip() or "assistant"
    result = generate_task_report(source=source, allow_running=allow_running)
    return JsonResponse(result, status=200 if result.get("success") else 400)


def format_task_status_answer(task: dict | None = None) -> str:
    state = task or get_task_state()
    scene_title = state.get("sceneTitle") or "未指定场景"
    status = state.get("status") or "idle"
    if status == "idle" or not state.get("scene"):
        return "当前没有正在执行的巡检任务。你可以直接让我启动电动车违停巡检、道路缺陷巡检或仪表巡检。"
    status_text = {
        "running": "执行中",
        "success": "已完成",
        "failure": "异常结束",
    }.get(status, status)
    parts = [
        f"当前巡检任务：{scene_title}",
        f"状态：{status_text}",
    ]
    if state.get("runId"):
        parts.append(f"任务号：{state['runId']}")
    if state.get("progressLabel"):
        parts.append(f"进度：{state['progressLabel']}")
    parts.append(f"关键发现：{int(state.get('findingCount') or 0)} 条")
    if state.get("latestFinding"):
        parts.append(f"最近发现：{state['latestFinding']}")
    if status == "success":
        parts.append("任务已完成，可让我生成巡检报告，或点击首页智能巡检助手下方的「巡检报告」打开报告页。")
    elif status == "failure":
        parts.append("任务异常结束，如需可让我基于当前结果生成报告。")
    return "；".join(parts) + "。"


def handle_assistant_task_command(question: str) -> dict | None:
    intent = detect_assistant_intent(question)
    if not intent:
        return None

    action = intent.get("action")
    if action == "clarify_start":
        return {
            "handled": True,
            "answer": intent.get("message")
            or "请指定要启动的巡检任务：电动车违停巡检、道路缺陷巡检或仪表巡检。",
            "action": action,
            "task": get_task_state(),
        }

    if action == "start_task":
        result = start_task(intent["scene"], dry_run=True)
        if not result.get("success"):
            return {
                "handled": True,
                "answer": result.get("message") or "巡检任务启动失败。",
                "action": action,
                "task": result.get("task") or get_task_state(),
                "result": result,
            }
        task = result["task"]
        answer = (
            f"已启动{intent.get('sceneTitle') or intent['scene']}。"
            f"任务号 {task.get('runId') or '--'}，当前状态：执行中。"
            "你可随时问我任务状态；完成后可让我生成巡检报告。"
        )
        return {
            "handled": True,
            "answer": answer,
            "action": action,
            "task": task,
            "result": result,
        }

    if action == "check_status":
        task = get_task_state()
        return {
            "handled": True,
            "answer": format_task_status_answer(task),
            "action": action,
            "task": task,
        }

    if action == "generate_report":
        result = generate_task_report(source="assistant")
        if not result.get("success"):
            return {
                "handled": True,
                "answer": result.get("message") or "暂时无法生成巡检报告。",
                "action": action,
                "task": result.get("task") or get_task_state(),
                "result": result,
            }
        report = result.get("report") or {}
        answer = (
            f"已生成{report.get('sceneTitle') or '巡检'}报告："
            f"{report.get('title') or report.get('filename')}。"
            f"可点击首页智能巡检助手下方的「巡检报告」打开，或直接访问 {report.get('url')}。"
        )
        return {
            "handled": True,
            "answer": answer,
            "action": action,
            "task": result.get("task") or get_task_state(),
            "result": result,
            "report": report,
        }

    return None
