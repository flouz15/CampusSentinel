"""Robot ingestion and patrol endpoints."""

import time

from django.conf import settings as django_settings
from django.db.models import Q
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import EventTimelineLog, RealtimeEvent, RobotFrame, RobotRecognition, SecurityTicket
from ..patrol_ssh import run_remote_start_script
from .common import (
    _get_request_data,
    _parse_float,
    _parse_int,
    _parse_time,
    _push_governance_event,
    _report_to_alert,
    _report_to_dict,
    _ticket_to_dict,
    _timeline_log,
    logger,
    require_admin_view,
    require_auth_view,
)

def _robot_ingest_allowed(request):
    if ROBOT_INGEST_KEY:
        key = request.headers.get("X-Robot-Key") or request.META.get("HTTP_X_ROBOT_KEY")
        return key == ROBOT_INGEST_KEY
    return _is_local_request(request) or _dashboard_allowed(request)


def _get_or_create_robot(robot_id):
    rid = (robot_id or "").strip() or "RBT-01"
    device, _ = RobotDevice.objects.get_or_create(robot_id=rid, defaults={"name": rid})
    return device


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def robot_status(request):
    device = _get_or_create_robot(request.GET.get("robotId"))
    return JsonResponse({
        "success": True,
        "robotId": device.robot_id,
        "name": device.name,
        "online": device.online,
        "battery": device.battery,
        "firmware": device.firmware or "",
        "network": device.network or "",
        "lastHeartbeat": device.last_heartbeat.isoformat() if device.last_heartbeat else None,
        "updatedAt": device.updated_at.isoformat() if device.updated_at else None,
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def robot_camera_frame(request):
    device = _get_or_create_robot(request.GET.get("robotId"))
    frame = device.frames.order_by("-captured_at", "-id").first()
    return JsonResponse({
        "success": True,
        "robotId": device.robot_id,
        "frameUrl": frame.frame_url_or_path if frame else "",
        "capturedAt": frame.captured_at.isoformat() if frame and frame.captured_at else None,
        "source": frame.source_type if frame else "mock",
        "updatedAt": frame.created_at.isoformat() if frame else None,
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def robot_recognitions(request):
    page = _parse_int(request.GET.get("page")) or 1
    page_size = _parse_int(request.GET.get("pageSize")) or 10
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    device = _get_or_create_robot(request.GET.get("robotId"))
    queryset = RobotRecognition.objects.filter(robot=device).order_by("-captured_at", "-id")
    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    rows = list(queryset[start:end])
    external_ids = [f"robot-recognition:{row.id}" for row in rows]
    linked_tickets = {
        row.external_id: row
        for row in SecurityTicket.objects.select_related("report").filter(external_id__in=external_ids)
    }
    return JsonResponse({
        "success": True,
        "robotId": device.robot_id,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "items": [
            {
                "id": row.id,
                "label": row.label,
                "confidence": row.confidence,
                "imageUrl": row.image_url,
                "source": row.source,
                "capturedAt": row.captured_at.isoformat() if row.captured_at else None,
                "createdAt": row.created_at.isoformat() if row.created_at else None,
                "escalated": bool(linked_tickets.get(f"robot-recognition:{row.id}")),
                "reportId": str(linked_tickets[f"robot-recognition:{row.id}"].report_id) if linked_tickets.get(f"robot-recognition:{row.id}") and linked_tickets[f"robot-recognition:{row.id}"].report_id else None,
                "ticketId": linked_tickets[f"robot-recognition:{row.id}"].id if linked_tickets.get(f"robot-recognition:{row.id}") else None,
            }
            for row in rows
        ],
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def robot_stats(request):

    device = _get_or_create_robot(request.GET.get("robotId"))
    window_hours = 24
    window_start = timezone.now() - timezone.timedelta(hours=window_hours)

    recognition_qs = RobotRecognition.objects.filter(robot=device).filter(
        Q(captured_at__gte=window_start) | Q(captured_at__isnull=True, created_at__gte=window_start)
    )
    hit_count = recognition_qs.count()
    last_recognition = recognition_qs.order_by("-captured_at", "-id").first()

    anomaly_qs = RealtimeEvent.objects.filter(
        robot_id=device.robot_id,
        created_at__gte=window_start,
    ).filter(
        Q(source="robot") | Q(event_type__icontains="robot")
    ).filter(
        Q(level=RealtimeEvent.LEVEL_WARN) | Q(level=RealtimeEvent.LEVEL_CRITICAL)
    )
    anomaly_count = anomaly_qs.count()
    last_anomaly = anomaly_qs.order_by("-created_at", "-id").first()

    status_events = list(
        RealtimeEvent.objects.filter(
            robot_id=device.robot_id,
            event_type="robot_status_updated",
            created_at__gte=window_start,
        ).order_by("-created_at", "-id")
    )
    parsed_status_count = 0
    online_event_count = 0
    for row in status_events:
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        online_flag = payload.get("online")
        if online_flag is None:
            message = str(row.message or "")
            if "离线" in message:
                online_flag = False
            elif "在线" in message:
                online_flag = True
        if online_flag is None:
            continue
        parsed_status_count += 1
        if bool(online_flag):
            online_event_count += 1

    if parsed_status_count > 0:
        online_rate = round((online_event_count / parsed_status_count) * 100.0, 2)
        note = f"基于近 24 小时 {parsed_status_count} 条状态更新事件估算"
    else:
        online_rate = 100.0 if device.online else 0.0
        note = "近 24 小时无状态更新事件，已回退为当前在线状态"

    return JsonResponse({
        "success": True,
        "robotId": device.robot_id,
        "windowHours": window_hours,
        "hitCount24h": hit_count,
        "anomalyCount24h": anomaly_count,
        "onlineRate24h": online_rate,
        "statusUpdateCount24h": parsed_status_count,
        "lastRecognitionAt": last_recognition.captured_at.isoformat() if last_recognition and last_recognition.captured_at else (last_recognition.created_at.isoformat() if last_recognition else None),
        "lastAnomalyAt": last_anomaly.created_at.isoformat() if last_anomaly else None,
        "updatedAt": timezone.now().isoformat(),
        "note": note,
    })


@csrf_exempt
@require_auth_view(allow_dashboard=True)
@require_http_methods(["POST"])
def patrol_start_task_ssh(request):
    """
    安保巡检页「开始任务」：由服务端读取 config/patrol_ssh.json，SSH 到机器人执行 remote_command。
    私钥路径仅存在于服务端，勿把密钥写进可被前端加载的 dashboard.json。
    """
    cfg_path = getattr(django_settings, "PATROL_SSH_CONFIG_PATH", None)
    if cfg_path is None:
        return JsonResponse(
            {
                "success": False,
                "error": "NOT_CONFIGURED",
                "message": "服务端未设置 PATROL_SSH_CONFIG_PATH",
            },
            status=503,
        )
    logger.info("patrol_start_task_ssh: config_path=%s", cfg_path)
    result = run_remote_start_script(cfg_path)
    logger.info(
        "patrol_start_task_ssh: success=%s error=%s",
        result.get("success"),
        result.get("error"),
    )
    # 仅「未创建配置文件」时 503；其余错误仍返回 200 + JSON，便于前端统一解析
    st = 503 if result.get("error") == "CONFIG_MISSING" else 200
    return JsonResponse(result, status=st)


@csrf_exempt
@require_admin_view()
@require_http_methods(["POST"])
def robot_event_escalate(request, event_id):
    user, profile = getattr(request, "auth")

    event = get_object_or_404(RealtimeEvent, id=event_id)
    if (event.source or "").lower() != "robot" and "robot" not in (event.event_type or "").lower():
        return JsonResponse({"success": False, "error": "INVALID_EVENT_SOURCE"}, status=400)

    data = _get_request_data(request)
    message = (data.get("message") or "").strip()
    assignee = (data.get("assignee") or "").strip()
    department = (data.get("department") or "").strip()
    if not message:
        return JsonResponse({"success": False, "error": "MESSAGE_REQUIRED"}, status=400)

    if event.report_id and event.ticket_id:
        report = event.report
        ticket = event.ticket
        if assignee and ticket and ticket.assignee != assignee:
            ticket.assignee = assignee
            ticket.save(update_fields=["assignee", "updated_at"])
        _timeline_log(
            EventTimelineLog.STAGE_ALERT,
            "robot_event_escalate_reused",
            actor=profile.full_name or user.username,
            report=report,
            ticket=ticket,
            payload={"eventId": event.id, "robotId": event.robot_id},
        )
        return JsonResponse({
            "success": True,
            "reused": True,
            "report": _report_to_dict(report, request=request) if report else None,
            "alert": _report_to_alert(report, ticket=ticket, request=request) if report and ticket else None,
            "ticket": _ticket_to_dict(ticket, request=request) if ticket else None,
        })

    external_id = f"robot-event:{event.id}"
    existing = SecurityTicket.objects.select_related("report").filter(external_id=external_id).order_by("-id").first()
    if existing:
        report = existing.report
        if assignee and existing.assignee != assignee:
            existing.assignee = assignee
            existing.save(update_fields=["assignee", "updated_at"])
        _timeline_log(
            EventTimelineLog.STAGE_ALERT,
            "robot_event_escalate_reused",
            actor=profile.full_name or user.username,
            report=report,
            ticket=existing,
            payload={"eventId": event.id, "robotId": event.robot_id},
        )
        return JsonResponse({
            "success": True,
            "reused": True,
            "report": _report_to_dict(report, request=request) if report else None,
            "alert": _report_to_alert(report, ticket=existing, request=request) if report else None,
            "ticket": _ticket_to_dict(existing, request=request),
        })

    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    recognition = None
    recognition_id = _parse_int(payload.get("recognitionId"))
    if recognition_id:
        recognition = RobotRecognition.objects.select_related("robot").filter(id=recognition_id).first()

    confidence_pct = None
    raw_confidence = payload.get("confidence")
    if raw_confidence is None and recognition and recognition.confidence is not None:
        raw_confidence = recognition.confidence
    if raw_confidence is not None:
        try:
            confidence_pct = int(round(float(raw_confidence) * 100))
        except (TypeError, ValueError):
            confidence_pct = None

    label = (
        (payload.get("label") or "").strip()
        or (recognition.label if recognition else "")
        or (event.event_type or "robot-event")
    )[:64]
    priority = "P1" if event.level == RealtimeEvent.LEVEL_CRITICAL else "P2"
    evidence_urls = []
    if recognition and recognition.image_url:
        evidence_urls.append(recognition.image_url)
    image_url = (payload.get("imageUrl") or payload.get("frameUrl") or "").strip()
    if image_url and image_url not in evidence_urls:
        evidence_urls.append(image_url)

    note = message or event.message or f"机器人 {event.robot_id or 'RBT'} 异常事件"
    report = Report.objects.create(
        created_by=user,
        location=f"{event.robot_id or '机器人'} 巡检区域",
        time=event.created_at or timezone.now(),
        note=note,
        status="CONFIRMED",
        category=label,
        priority=priority,
        reporter_name=event.robot_id or "robot",
        reporter_role="robot",
        department=department or "机器人巡检",
        confidence=confidence_pct,
        evidence_urls=evidence_urls,
        violation=True,
    )

    actor = profile.full_name or user.username
    _timeline_log(
        EventTimelineLog.STAGE_REPORT,
        "robot_event_promoted",
        actor=actor,
        report=report,
        payload={
            "eventId": event.id,
            "robotId": event.robot_id,
            "eventType": event.event_type,
            "label": label,
        },
    )
    ticket = _ensure_alert_created(report, user, request=request)
    ticket.external_id = external_id
    if assignee:
        ticket.assignee = assignee
    if message:
        ticket.message = message
    ticket.save(update_fields=["external_id", "assignee", "message", "updated_at"])
    _sync_report_status_from_ticket(report, ticket)
    event.report = report
    event.ticket = ticket
    event.save(update_fields=["report", "ticket"])
    _timeline_log(
        EventTimelineLog.STAGE_ALERT,
        "robot_event_escalated",
        actor=actor,
        report=report,
        ticket=ticket,
        payload={
            "eventId": event.id,
            "robotId": event.robot_id,
            "eventType": event.event_type,
            "assignee": assignee or "",
        },
    )
    _push_governance_event(
        event_type="robot_event_escalated",
        title="机器人异常已转告警",
        message=note,
        level=RealtimeEvent.LEVEL_WARN if event.level != RealtimeEvent.LEVEL_CRITICAL else RealtimeEvent.LEVEL_CRITICAL,
        source="robot",
        report=report,
        ticket=ticket,
        robot_id=event.robot_id,
        payload={
            "eventId": event.id,
            "eventType": event.event_type,
            "label": label,
            "reportId": str(report.id),
            "ticketId": ticket.id,
        },
    )
    return JsonResponse({
        "success": True,
        "reused": False,
        "report": _report_to_dict(report, request=request),
        "alert": _report_to_alert(report, ticket=ticket, request=request),
        "ticket": _ticket_to_dict(ticket, request=request),
    })


@csrf_exempt
@require_admin_view()
@require_http_methods(["POST"])
def robot_recognition_escalate(request, recognition_id):
    user, profile = getattr(request, "auth")

    recognition = get_object_or_404(RobotRecognition.objects.select_related("robot"), id=recognition_id)
    data = _get_request_data(request)
    message = (data.get("message") or "").strip()
    assignee = (data.get("assignee") or "").strip()
    department = (data.get("department") or "").strip()
    if not message:
        return JsonResponse({"success": False, "error": "MESSAGE_REQUIRED"}, status=400)

    external_id = f"robot-recognition:{recognition.id}"
    existing = SecurityTicket.objects.select_related("report").filter(external_id=external_id).order_by("-id").first()
    if existing:
        report = existing.report
        if assignee and existing.assignee != assignee:
            existing.assignee = assignee
            existing.save(update_fields=["assignee", "updated_at"])
        _timeline_log(
            EventTimelineLog.STAGE_ALERT,
            "robot_recognition_escalate_reused",
            actor=profile.full_name or user.username,
            report=report,
            ticket=existing,
            payload={"recognitionId": recognition.id, "robotId": recognition.robot.robot_id},
        )
        return JsonResponse({
            "success": True,
            "reused": True,
            "report": _report_to_dict(report, request=request) if report else None,
            "alert": _report_to_alert(report, ticket=existing, request=request) if report else None,
            "ticket": _ticket_to_dict(existing, request=request),
        })

    confidence_pct = None
    if recognition.confidence is not None:
        try:
            confidence_pct = int(round(float(recognition.confidence) * 100))
        except (TypeError, ValueError):
            confidence_pct = None
    priority = "P1" if (recognition.confidence or 0) >= 0.9 else "P2"
    default_note = f"机器人 {recognition.robot.robot_id} 识别到 {recognition.label}"
    report = Report.objects.create(
        created_by=user,
        location=f"{recognition.robot.name or recognition.robot.robot_id} 巡检区域",
        time=recognition.captured_at or recognition.created_at or timezone.now(),
        note=message or default_note,
        status="CONFIRMED",
        category=(recognition.label or "robot-event")[:64],
        priority=priority,
        reporter_name=recognition.robot.robot_id,
        reporter_role="robot",
        department=department or "机器人巡检",
        confidence=confidence_pct,
        evidence_urls=[recognition.image_url] if recognition.image_url else [],
        violation=True,
    )

    actor = profile.full_name or user.username
    _timeline_log(
        EventTimelineLog.STAGE_REPORT,
        "robot_recognition_promoted",
        actor=actor,
        report=report,
        payload={
            "robotId": recognition.robot.robot_id,
            "recognitionId": recognition.id,
            "label": recognition.label,
            "confidence": recognition.confidence,
        },
    )
    ticket = _ensure_alert_created(report, user, request=request)
    ticket.external_id = external_id
    if assignee:
        ticket.assignee = assignee
    if message:
        ticket.message = message
    ticket.save(update_fields=["external_id", "assignee", "message", "updated_at"])
    _sync_report_status_from_ticket(report, ticket)
    _timeline_log(
        EventTimelineLog.STAGE_ALERT,
        "robot_recognition_escalated",
        actor=actor,
        report=report,
        ticket=ticket,
        payload={
            "robotId": recognition.robot.robot_id,
            "recognitionId": recognition.id,
            "label": recognition.label,
            "assignee": assignee or "",
        },
    )
    _push_governance_event(
        event_type="robot_alert_escalated",
        title="机器人事件已转告警",
        message=message or default_note,
        level=RealtimeEvent.LEVEL_WARN,
        source="robot",
        report=report,
        ticket=ticket,
        robot_id=recognition.robot.robot_id,
        payload={
            "recognitionId": recognition.id,
            "label": recognition.label,
            "confidence": recognition.confidence,
            "reportId": str(report.id),
            "ticketId": ticket.id,
        },
    )
    return JsonResponse({
        "success": True,
        "reused": False,
        "report": _report_to_dict(report, request=request),
        "alert": _report_to_alert(report, ticket=ticket, request=request),
        "ticket": _ticket_to_dict(ticket, request=request),
    })


@csrf_exempt
@require_http_methods(["POST"])
def robot_ingest_status(request):
    if not _robot_ingest_allowed(request):
        return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)
    data = _get_request_data(request)
    device = _get_or_create_robot(data.get("robotId"))
    device.name = (data.get("name") or device.name or device.robot_id).strip()
    online_raw = data.get("online")
    device.online = bool(online_raw) if online_raw is not None else device.online
    battery = _parse_float(data.get("battery"))
    if battery is not None:
        device.battery = battery
    firmware = data.get("firmware")
    if firmware is not None:
        device.firmware = str(firmware).strip()
    network = data.get("network")
    if network is not None:
        device.network = str(network).strip()
    heartbeat = _parse_time(data.get("lastHeartbeat"))
    device.last_heartbeat = heartbeat
    device.save()

    _push_governance_event(
        event_type="robot_status_updated",
        title="机器人状态更新",
        message=f"{device.robot_id} · {'在线' if device.online else '离线'}",
        level=RealtimeEvent.LEVEL_INFO if device.online else RealtimeEvent.LEVEL_WARN,
        source="robot",
        robot_id=device.robot_id,
        payload={"battery": device.battery, "network": device.network, "online": device.online},
    )
    return JsonResponse({"success": True, "robotId": device.robot_id})


@csrf_exempt
@require_http_methods(["POST"])
def robot_ingest_frame(request):
    if not _robot_ingest_allowed(request):
        return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)
    data = _get_request_data(request)
    device = _get_or_create_robot(data.get("robotId"))
    frame_url = (data.get("frameUrl") or data.get("imageUrl") or "").strip()
    if not frame_url:
        return JsonResponse({"success": False, "error": "MISSING_FRAME_URL"}, status=400)
    source_type = (data.get("sourceType") or RobotFrame.SOURCE_HTTP).strip().lower()
    if source_type not in {x[0] for x in RobotFrame.SOURCE_CHOICES}:
        source_type = RobotFrame.SOURCE_HTTP
    captured_at = _parse_time(data.get("capturedAt"))
    row = RobotFrame.objects.create(
        robot=device,
        frame_url_or_path=frame_url,
        source_type=source_type,
        captured_at=captured_at,
    )
    return JsonResponse({"success": True, "frameId": row.id})


@csrf_exempt
@require_http_methods(["POST"])
def robot_ingest_recognitions(request):
    if not _robot_ingest_allowed(request):
        return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)
    data = _get_request_data(request)
    device = _get_or_create_robot(data.get("robotId"))
    items = data.get("items")
    if not isinstance(items, list):
        return JsonResponse({"success": False, "error": "MISSING_ITEMS"}, status=400)

    created = 0
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        row = RobotRecognition.objects.create(
            robot=device,
            label=(item.get("label") or "unknown")[:128],
            confidence=_parse_float(item.get("confidence")),
            image_url=(item.get("imageUrl") or "").strip(),
            source=(item.get("source") or "robot-camera")[:64],
            captured_at=_parse_time(item.get("capturedAt")),
        )
        created += 1
        _push_governance_event(
            event_type="robot_recognition",
            title="机器人识别事件",
            message=f"{device.robot_id} 检测到 {row.label}",
            level=RealtimeEvent.LEVEL_INFO,
            source="robot",
            robot_id=device.robot_id,
            payload={"recognitionId": row.id, "confidence": row.confidence, "label": row.label},
        )
    return JsonResponse({"success": True, "created": created})
