"""Dispatch, scenario, and model endpoints."""

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import (
    DispatchOrder,
    ModelFeedback,
    ModelMetricSnapshot,
    Report,
    RobotDevice,
    ScenarioRun,
    ScenarioTemplate,
    SecurityTicket,
)
from .common import (
    _get_request_data,
    _parse_int,
    _parse_time,
    _push_governance_event,
    _timeline_log,
    require_admin_view,
    require_auth_view,
)

def _dispatch_to_dict(row):
    return {
        "id": row.id,
        "reportId": str(row.report_id) if row.report_id else None,
        "ticketId": row.ticket_id,
        "dept": row.dept,
        "assignee": row.assignee,
        "priority": row.priority,
        "status": row.status,
        "expectedAt": row.expected_at.isoformat() if row.expected_at else None,
        "arrivedAt": row.arrived_at.isoformat() if row.arrived_at else None,
        "finishedAt": row.finished_at.isoformat() if row.finished_at else None,
        "note": row.note,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def twin_overview(request):

    now = timezone.localtime()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tickets = SecurityTicket.objects.all()
    pending_count = tickets.filter(status=SecurityTicket.STATUS_OPEN).count()
    processing_count = tickets.filter(status=SecurityTicket.STATUS_ASSIGNED).count()
    resolved_count = tickets.filter(status=SecurityTicket.STATUS_DONE).count()
    today_reports = Report.objects.filter(created_at__gte=start_of_day).count()

    recent_reports = list(Report.objects.order_by("-created_at")[:500])
    area_counter = {}
    for row in recent_reports:
        key = (row.location or "未知区域").strip()[:64]
        area_counter[key] = area_counter.get(key, 0) + 1
    heat_top = [
        {"name": name, "count": count}
        for name, count in sorted(area_counter.items(), key=lambda x: x[1], reverse=True)[:8]
    ]

    # 12 points trend for latest 60 min.
    trend = [0] * 12
    ts_now = time.time()
    for t in tickets.order_by("-updated_at")[:400]:
        ts = (t.updated_at or t.created_at)
        if not ts:
            continue
        diff = int((ts_now - ts.timestamp()) / (5 * 60))
        idx = 11 - diff
        if 0 <= idx < 12:
            trend[idx] += 1
    trend_labels = []
    for i in range(11, -1, -1):
        d = timezone.localtime(timezone.now() - timezone.timedelta(minutes=5 * i))
        trend_labels.append(d.strftime("%H:%M"))

    robots_total = RobotDevice.objects.count()
    robots_online = RobotDevice.objects.filter(online=True).count()
    online_rate = 98.0
    if robots_total > 0:
        online_rate = round((robots_online / robots_total) * 100.0, 2)

    dispatch_status = {
        "pending": DispatchOrder.objects.filter(status=DispatchOrder.STATUS_PENDING).count(),
        "processing": DispatchOrder.objects.filter(status=DispatchOrder.STATUS_PROCESSING).count(),
        "done": DispatchOrder.objects.filter(status=DispatchOrder.STATUS_DONE).count(),
    }

    assignee_load = {}
    for d in DispatchOrder.objects.order_by("-updated_at")[:200]:
        key = (d.assignee or "未分配").strip()[:64]
        assignee_load[key] = assignee_load.get(key, 0) + 1
    dispatch_load = [{"assignee": k, "count": v} for k, v in sorted(assignee_load.items(), key=lambda x: x[1], reverse=True)[:10]]

    return JsonResponse({
        "success": True,
        "kpi": {
            "todayReports": today_reports,
            "pendingAlerts": pending_count,
            "processingAlerts": processing_count,
            "resolvedAlerts": resolved_count,
            "activeTickets": pending_count + processing_count,
        },
        "heatTopAreas": heat_top,
        "alertsTrend": {
            "labels": trend_labels,
            "values": trend,
        },
        "deviceStatus": {
            "total": robots_total,
            "online": robots_online,
            "offline": max(0, robots_total - robots_online),
        },
        "dispatchLoad": {
            "status": dispatch_status,
            "assignees": dispatch_load,
        },
        "onlineRate": online_rate,
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def dispatch_create(request):
    auth = getattr(request, "auth", None)
    data = _get_request_data(request)
    report_id = data.get("reportId")
    ticket_id = _parse_int(data.get("ticketId"))
    report = None
    ticket = None
    if report_id:
        report = Report.objects.filter(id=report_id).first()
    if ticket_id:
        ticket = SecurityTicket.objects.filter(id=ticket_id).first()
    if report is None and ticket and ticket.report_id:
        report = ticket.report

    row = DispatchOrder.objects.create(
        report=report,
        ticket=ticket,
        dept=(data.get("dept") or "").strip(),
        assignee=(data.get("assignee") or "").strip(),
        priority=(data.get("priority") or "P2").strip().upper()[:16],
        status=DispatchOrder.STATUS_PENDING,
        expected_at=_parse_time(data.get("expectedAt")) if data.get("expectedAt") else None,
        note=(data.get("note") or "").strip(),
    )
    _timeline_log(
        EventTimelineLog.STAGE_DISPATCH,
        "dispatch_created",
        actor=(auth[0].username if auth else "dashboard"),
        report=report,
        ticket=ticket,
        payload=_dispatch_to_dict(row),
    )
    _push_governance_event(
        event_type="dispatch_created",
        title="调度指令已创建",
        message=f"{row.dept or '值守中心'} -> {row.assignee or '待分配'}",
        level=RealtimeEvent.LEVEL_WARN,
        source="dispatch",
        report=report,
        ticket=ticket,
        payload=_dispatch_to_dict(row),
    )
    return JsonResponse({"success": True, "dispatch": _dispatch_to_dict(row)})


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["PATCH", "POST"])
def dispatch_update(request, dispatch_id):
    auth = getattr(request, "auth", None)
    row = get_object_or_404(DispatchOrder, id=dispatch_id)
    data = _get_request_data(request)

    status = (data.get("status") or "").strip().lower()
    valid_status = {x[0] for x in DispatchOrder.STATUS_CHOICES}
    if status in valid_status:
        row.status = status
    if data.get("arrivedAt"):
        row.arrived_at = _parse_time(data.get("arrivedAt"))
    if data.get("finishedAt"):
        row.finished_at = _parse_time(data.get("finishedAt"))
    if data.get("note") is not None:
        row.note = (data.get("note") or "").strip()
    if data.get("assignee") is not None:
        row.assignee = (data.get("assignee") or "").strip()
    row.save()

    _timeline_log(
        EventTimelineLog.STAGE_DISPATCH,
        "dispatch_updated",
        actor=(auth[0].username if auth else "dashboard"),
        report=row.report,
        ticket=row.ticket,
        payload=_dispatch_to_dict(row),
    )
    _push_governance_event(
        event_type="dispatch_updated",
        title="调度状态更新",
        message=f"调度#{row.id} -> {row.status}",
        level=RealtimeEvent.LEVEL_INFO,
        source="dispatch",
        report=row.report,
        ticket=row.ticket,
        payload=_dispatch_to_dict(row),
    )
    return JsonResponse({"success": True, "dispatch": _dispatch_to_dict(row)})


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def dispatch_list(request):
    status = (request.GET.get("status") or "").strip().lower()
    dept = (request.GET.get("dept") or "").strip()
    assignee = (request.GET.get("assignee") or "").strip()
    page = _parse_int(request.GET.get("page")) or 1
    page_size = _parse_int(request.GET.get("pageSize")) or 20
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    if page_size > 100:
        page_size = 100
    qs = DispatchOrder.objects.select_related("report", "ticket").order_by("-updated_at", "-id")
    if status:
        qs = qs.filter(status=status)
    if dept:
        qs = qs.filter(dept__icontains=dept)
    if assignee:
        qs = qs.filter(assignee__icontains=assignee)
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    rows = list(qs[start:end])
    return JsonResponse({
        "success": True,
        "dispatches": [_dispatch_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


@csrf_exempt
@require_http_methods(["GET", "POST"])
def dispatch_entry(request):
    if request.method == "GET":
        return dispatch_list(request)
    return dispatch_create(request)


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def scenario_create(request):
    data = _get_request_data(request)
    name = (data.get("name") or "").strip()
    scene_type = (data.get("sceneType") or "generic").strip()
    script = data.get("script")
    if not name:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)
    if not isinstance(script, dict):
        script = {}
    row = ScenarioTemplate.objects.create(
        name=name[:64],
        scene_type=scene_type[:32],
        script=script,
        enabled=True,
    )
    return JsonResponse({
        "success": True,
        "scenario": {
            "id": row.id,
            "name": row.name,
            "sceneType": row.scene_type,
            "script": row.script,
            "enabled": row.enabled,
            "createdAt": row.created_at.isoformat(),
        },
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def scenario_run(request, scenario_id):
    tpl = get_object_or_404(ScenarioTemplate, id=scenario_id)
    data = _get_request_data(request)
    mode = (data.get("mode") or ScenarioRun.MODE_SIMULATION).strip().lower()
    if mode not in {ScenarioRun.MODE_SIMULATION, ScenarioRun.MODE_MIXED}:
        mode = ScenarioRun.MODE_SIMULATION
    run = ScenarioRun.objects.create(
        template=tpl,
        mode=mode,
        status=ScenarioRun.STATUS_RUNNING,
        start_at=timezone.now(),
        summary={
            "injectedEvents": 0,
            "handledCount": 0,
            "avgResponseSec": 0,
        },
    )

    events = [
        {"title": "演练事件：违规停车", "message": "教学区北门发现违规停车风险", "level": RealtimeEvent.LEVEL_WARN},
        {"title": "演练事件：夜间聚集", "message": "宿舍区疑似夜间聚集风险", "level": RealtimeEvent.LEVEL_WARN},
        {"title": "演练事件：危险行为", "message": "操场边出现疑似危险行为", "level": RealtimeEvent.LEVEL_CRITICAL},
    ]
    script_events = tpl.script.get("events") if isinstance(tpl.script, dict) else None
    if isinstance(script_events, list) and script_events:
        events = []
        for item in script_events[:10]:
            if not isinstance(item, dict):
                continue
            events.append(
                {
                    "title": str(item.get("title") or "演练事件")[:120],
                    "message": str(item.get("message") or ""),
                    "level": str(item.get("level") or RealtimeEvent.LEVEL_WARN),
                }
            )
    injected = 0
    for e in events:
        _push_governance_event(
            event_type="scenario_event",
            title=e["title"],
            message=e["message"],
            level=e["level"],
            source="scenario",
            payload={"runId": run.id, "templateId": tpl.id, "mode": mode},
        )
        injected += 1
    run.summary = {
        "injectedEvents": injected,
        "handledCount": max(1, injected - 1),
        "avgResponseSec": 18,
    }
    run.status = ScenarioRun.STATUS_DONE
    run.end_at = timezone.now()
    run.save(update_fields=["summary", "status", "end_at"])
    return JsonResponse({"success": True, "runId": run.id})


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def scenario_run_detail(request, run_id):
    run = get_object_or_404(ScenarioRun.objects.select_related("template"), id=run_id)
    return JsonResponse(
        {
            "success": True,
            "run": {
                "id": run.id,
                "templateId": run.template_id,
                "templateName": run.template.name if run.template else None,
                "mode": run.mode,
                "status": run.status,
                "startAt": run.start_at.isoformat() if run.start_at else None,
                "endAt": run.end_at.isoformat() if run.end_at else None,
                "summary": run.summary or {},
            },
        }
    )


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def ai_metrics(request):
    model_name = (request.GET.get("modelName") or "").strip()
    from_dt = _parse_time(request.GET.get("from")) if request.GET.get("from") else None
    to_dt = _parse_time(request.GET.get("to")) if request.GET.get("to") else None
    qs = ModelMetricSnapshot.objects.all().order_by("-created_at", "-id")
    if model_name:
        qs = qs.filter(model_name__icontains=model_name)
    if from_dt:
        qs = qs.filter(created_at__gte=from_dt)
    if to_dt:
        qs = qs.filter(created_at__lte=to_dt)
    rows = list(qs[:120])
    return JsonResponse(
        {
            "success": True,
            "metrics": [
                {
                    "id": r.id,
                    "modelName": r.model_name,
                    "dataScope": r.data_scope,
                    "accuracy": r.accuracy,
                    "precision": r.precision,
                    "recall": r.recall,
                    "f1": r.f1,
                    "falseAlarmRate": r.false_alarm_rate,
                    "avgLatencyMs": r.avg_latency_ms,
                    "sampleCount": r.sample_count,
                    "createdAt": r.created_at.isoformat(),
                }
                for r in rows
            ],
        }
    )


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def ai_feedback(request):
    user, profile = getattr(request, "auth")
    data = _get_request_data(request)
    detection_id = _parse_int(data.get("detectionRecordId"))
    corrected_label = (data.get("correctedLabel") or "").strip()
    comment = (data.get("comment") or "").strip()
    if not detection_id or not corrected_label:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)
    record = get_object_or_404(DetectionRecord, id=detection_id)
    reviewer = (
        profile.full_name if profile and profile.full_name else user.username
    ) if user else "anonymous"
    row = ModelFeedback.objects.create(
        detection_record=record,
        corrected_label=corrected_label[:128],
        reviewer=reviewer[:64],
        comment=comment,
    )

    snapshot = ModelMetricSnapshot.objects.order_by("-created_at", "-id").first()
    if snapshot:
        snapshot.sample_count = (snapshot.sample_count or 0) + 1
        snapshot.save(update_fields=["sample_count"])

    _push_governance_event(
        event_type="ai_feedback",
        title="模型纠错反馈已提交",
        message=f"检测#{record.id} 已纠错为 {corrected_label}",
        level=RealtimeEvent.LEVEL_INFO,
        source="ai",
        detection=record,
        payload={"feedbackId": row.id, "correctedLabel": corrected_label},
    )
    return JsonResponse(
        {
            "success": True,
            "feedback": {
                "id": row.id,
                "detectionRecordId": row.detection_record_id,
                "correctedLabel": row.corrected_label,
                "reviewer": row.reviewer,
                "comment": row.comment,
                "createdAt": row.created_at.isoformat(),
            },
        }
    )


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def patrol_camera_mjpeg(request):
    """将 .env 中 PATROL_RAW_CAMERA_RTSP_URL 转为 MJPEG，供巡检页 <img> 显示（浏览器无法直接播 RTSP）。"""
    from .patrol_rtsp_reader import configured_rtsp_url, ensure_rtsp_reader, get_latest_jpeg

    url = configured_rtsp_url()
    if not url:
        return JsonResponse({"success": False, "error": "PATROL_RTSP_NOT_CONFIGURED"}, status=503)

    ensure_rtsp_reader(url)

    boundary = b"frame"

    def gen():
        while True:
            chunk = get_latest_jpeg()
            if chunk:
                yield b"--" + boundary + b"\r\nContent-Type: image/jpeg\r\n\r\n" + chunk + b"\r\n"
            time.sleep(1 / 25.0)

    response = StreamingHttpResponse(gen(), content_type="multipart/x-mixed-replace; boundary=frame")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response["Pragma"] = "no-cache"
    return response
