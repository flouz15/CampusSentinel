"""Dashboard, alert, ticket, and contact endpoints."""

import requests
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import ContactMessage, EventTimelineLog, GovernanceAuditRecord, RealtimeEvent, Report, SecurityTicket
from .common import (
    ALERT_API_BASE_URL,
    SECURITY_NOTIFY_BASE_URL,
    _alert_status_to_ticket_status,
    _category_for_alert,
    _get_request_data,
    _parse_float,
    _parse_int,
    _priority_from_severity,
    _priority_reward,
    _push_governance_event,
    _report_to_alert,
    _reporter_payload,
    _severity_from_priority,
    _sync_report_status_from_ticket,
    _ticket_status_to_alert_status,
    _ticket_to_dict,
    _timeline_log,
    require_admin_view,
    require_auth_view,
)

@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def security_notify(request):
    auth = getattr(request, "auth", None)

    data = _get_request_data(request)
    report_id = data.get("reportId") or data.get("report_id")
    channel = (data.get("channel") or SecurityTicket.CHANNEL_WORK_ORDER).strip().lower()
    message = (data.get("message") or data.get("description") or "").strip()
    assignee = (data.get("assignee") or "").strip()
    external_id = (data.get("externalId") or "").strip()

    report = None
    if report_id:
        try:
            report = Report.objects.get(id=report_id)
        except Report.DoesNotExist:
            report = None

    ticket = SecurityTicket.objects.create(
        report=report,
        channel=channel if channel in {c[0] for c in SecurityTicket.CHANNEL_CHOICES} else SecurityTicket.CHANNEL_WORK_ORDER,
        status=SecurityTicket.STATUS_OPEN,
        message=message,
        assignee=assignee,
        external_id=external_id,
    )
    _timeline_log(
        EventTimelineLog.STAGE_DISPATCH,
        "security_notify_created",
        actor=(auth[0].username if auth else "dashboard"),
        report=report,
        ticket=ticket,
        payload={"channel": ticket.channel, "assignee": ticket.assignee},
    )
    _push_governance_event(
        event_type="dispatch_created",
        title="新增调度工单",
        message=ticket.message or "已创建调度工单",
        level=RealtimeEvent.LEVEL_WARN,
        source="dispatch",
        report=report,
        ticket=ticket,
        payload=_ticket_to_dict(ticket, request=request),
    )

    return JsonResponse({
        "success": True,
        "ticket": {
            "id": ticket.id,
            "reportId": str(ticket.report_id) if ticket.report_id else None,
            "status": ticket.status,
            "channel": ticket.channel,
            "message": ticket.message,
            "assignee": ticket.assignee,
            "externalId": ticket.external_id,
            "createdAt": ticket.created_at.isoformat(),
        },
    })


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def dashboard_metrics(request):
    auth = getattr(request, "auth", None)
    if auth:
        _user, profile = auth
        if profile and profile.role != UserProfile.ROLE_ADMIN:
            return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)

    tickets = SecurityTicket.objects.select_related("report").all()
    reports = Report.objects.all()
    audits = GovernanceAuditRecord.objects.all()

    total_tickets = tickets.count()
    resolved_tickets = tickets.filter(status=SecurityTicket.STATUS_DONE).count()
    closure_rate = round((resolved_tickets / total_tickets) * 100, 1) if total_tickets else None

    elapsed_minutes = []
    for ticket in tickets:
        if ticket.created_at and ticket.resolved_at:
            elapsed_minutes.append((ticket.resolved_at - ticket.created_at).total_seconds() / 60)
    avg_handle_minutes = round(sum(elapsed_minutes) / len(elapsed_minutes), 1) if elapsed_minutes else None

    hotspot_map = {}
    for report in reports:
        key = (report.location or report.department or '未知区域').strip() or '未知区域'
        hotspot_map[key] = hotspot_map.get(key, 0) + 1
    hotspot_sorted = sorted(hotspot_map.items(), key=lambda item: item[1], reverse=True)
    hotspot_topn = []
    if hotspot_sorted:
        top_base = max(hotspot_sorted[0][1], 1)
        for index, (name, count) in enumerate(hotspot_sorted[:5]):
            level = 'high' if index == 0 else 'medium' if index < 3 else 'low'
            hotspot_topn.append({
                'name': name,
                'count': count,
                'ratio': round((count / top_base) * 100, 1),
                'level': level,
            })

    reviewed_reports = reports.filter(status__in=['CONFIRMED', 'REJECTED'])
    reviewed_count = reviewed_reports.count()
    rejected_count = reviewed_reports.filter(status='REJECTED').count()

    false_positive_audits = audits.filter(review_type=GovernanceAuditRecord.REVIEW_FALSE_POSITIVE).count()
    miss_audits = audits.filter(review_type=GovernanceAuditRecord.REVIEW_MISS).count()
    confirm_audits = audits.filter(review_type=GovernanceAuditRecord.REVIEW_CONFIRM).count()
    audit_total = false_positive_audits + miss_audits + confirm_audits

    false_positive_rate = round((false_positive_audits / audit_total) * 100, 1) if audit_total else (round((rejected_count / reviewed_count) * 100, 1) if reviewed_count else None)
    miss_rate = round((miss_audits / audit_total) * 100, 1) if audit_total else None

    night_reports = []
    for report in reports:
        dt = report.created_at or report.time
        if not dt:
            continue
        dt = timezone.localtime(dt)
        if dt.hour >= 20 or dt.hour < 6:
            night_reports.append(report)
    night_coverage_rate = round((len(night_reports) / reports.count()) * 100, 1) if reports.count() else None

    recurrence_rows = []
    recurrence_base = sum(count for _, count in hotspot_sorted if count > 1)
    if recurrence_base:
        for index, (name, count) in enumerate([(n, c) for n, c in hotspot_sorted if c > 1][:5]):
            level = 'high' if index == 0 else 'medium' if index < 3 else 'low'
            recurrence_rows.append({
                'name': name,
                'count': count,
                'ratio': round((count / recurrence_base) * 100, 1),
                'level': level,
            })
    recurrence_rate = round((sum(count for _, count in hotspot_sorted if count > 1) / max(sum(count for _, count in hotspot_sorted), 1)) * 100, 1) if hotspot_sorted else None

    return JsonResponse({
        'success': True,
        'metrics': {
            'closureRate': closure_rate,
            'avgHandleMinutes': avg_handle_minutes,
            'nightCoverageRate': night_coverage_rate,
            'recurrenceRate': recurrence_rate,
            'falsePositiveRate': false_positive_rate,
            'missRate': miss_rate,
            'hotspotCount': len(hotspot_topn),
            'hotspotTopN': hotspot_topn,
            'recurrenceTopN': recurrence_rows,
        },
        'definitions': {
            'closureRate': '已解决工单数 / 工单总数',
            'avgHandleMinutes': '工单 created_at 到 resolved_at 的平均分钟数',
            'nightCoverageRate': '20:00-06:00 夜间时段报告占比（当前代理口径）',
            'recurrenceRate': '同区域重复事件占比（当前代理口径）',
            'falsePositiveRate': '优先采用 GovernanceAuditRecord.false_positive；无审计数据时回退到人工驳回举报率',
            'missRate': '采用 GovernanceAuditRecord.miss 的占比统计',
        },
        'updatedAt': timezone.now().isoformat(),
    })


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def governance_audit_list(request):
    report_id = (request.GET.get('reportId') or request.GET.get('report_id') or '').strip()
    detection_record_id = _parse_int(request.GET.get('detectionRecordId') or request.GET.get('detection_record_id'))

    queryset = GovernanceAuditRecord.objects.select_related('report', 'detection_record').all()
    if report_id:
        queryset = queryset.filter(report_id=report_id)
    if detection_record_id:
        queryset = queryset.filter(detection_record_id=detection_record_id)

    rows = list(queryset[:50])
    return JsonResponse({
        'success': True,
        'audits': [
            {
                'id': row.id,
                'reviewType': row.review_type,
                'reportId': str(row.report_id) if row.report_id else None,
                'detectionRecordId': row.detection_record_id,
                'reviewer': row.reviewer,
                'note': row.note,
                'createdAt': row.created_at.isoformat(),
            }
            for row in rows
        ]
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def governance_audit_create(request):
    auth = getattr(request, "auth", None)
    reviewer = auth[0].username if auth else 'dashboard'
    data = _get_request_data(request)
    review_type = (data.get('reviewType') or data.get('review_type') or '').strip().lower()
    if review_type not in {
        GovernanceAuditRecord.REVIEW_FALSE_POSITIVE,
        GovernanceAuditRecord.REVIEW_MISS,
        GovernanceAuditRecord.REVIEW_CONFIRM,
    }:
        return JsonResponse({'success': False, 'error': 'INVALID_REVIEW_TYPE'}, status=400)

    report = None
    detection_record = None
    report_id = (data.get('reportId') or data.get('report_id') or '').strip()
    detection_record_id = _parse_int(data.get('detectionRecordId') or data.get('detection_record_id'))
    if report_id:
        report = Report.objects.filter(id=report_id).first()
    if detection_record_id:
        detection_record = DetectionRecord.objects.filter(id=detection_record_id).first()

    row = GovernanceAuditRecord.objects.create(
        report=report,
        detection_record=detection_record,
        review_type=review_type,
        reviewer=(data.get('reviewer') or reviewer or '').strip(),
        note=(data.get('note') or '').strip(),
    )
    return JsonResponse({
        'success': True,
        'audit': {
            'id': row.id,
            'reviewType': row.review_type,
            'reportId': str(row.report_id) if row.report_id else None,
            'detectionRecordId': row.detection_record_id,
            'reviewer': row.reviewer,
            'note': row.note,
            'createdAt': row.created_at.isoformat(),
        }
    })


# 鍐呭瓨瀛樺偍鍛婅锛堢畝鍗曞疄鐜帮紝鐢熶骇鐜搴斾娇鐢ㄦ暟鎹簱锛?
@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def dashboard_summary(request):
    auth = getattr(request, "auth", None)
    if auth:
        _user, profile = auth
        if profile and profile.role != UserProfile.ROLE_ADMIN:
            return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)

    now = timezone.localtime()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tickets = SecurityTicket.objects.all()
    pending_count = tickets.filter(status=SecurityTicket.STATUS_OPEN).count()
    processing_count = tickets.filter(status=SecurityTicket.STATUS_ASSIGNED).count()
    resolved_count = tickets.filter(status=SecurityTicket.STATUS_DONE).count()
    active_count = pending_count + processing_count

    return JsonResponse({
        "success": True,
        "todayReports": Report.objects.filter(created_at__gte=start_of_day).count(),
        "pendingAlerts": pending_count,
        "processingAlerts": processing_count,
        "resolvedAlerts": resolved_count,
        "activeTickets": active_count,
        "onlineRate": 98.0,
        "realtimeEventsToday": RealtimeEvent.objects.filter(created_at__gte=start_of_day).count(),
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["POST"])
def alert_receive(request):
    """兼容旧 POST /api/alert 入口，改为数据库落库"""
    data = _get_request_data(request)
    report_id = data.get("reportId")
    report = None
    if report_id:
        try:
            report = Report.objects.get(id=report_id)
        except Report.DoesNotExist:
            report = None

    if report is None:
        report = Report.objects.create(
            location=(data.get("title") or "").strip()[:255],
            time=_parse_time(data.get("timestamp")),
            note=(data.get("description") or "").strip(),
            status="CONFIRMED",
            category=(data.get("category") or "legacy"),
            priority=_priority_from_severity(data.get("severity")),
            reporter_name=(data.get("reporter") or "legacy"),
            reporter_role="legacy",
            latitude=_parse_float(data.get("latitude")),
            longitude=_parse_float(data.get("longitude")),
            violation=True,
            reward_points=_parse_int(data.get("rewardPoints")),
            reward_message=(data.get("rewardMessage") or "").strip(),
        )
        _timeline_log(
            EventTimelineLog.STAGE_REPORT,
            "legacy_alert_report_created",
            actor="legacy",
            report=report,
            payload={"source": "legacy_api"},
        )

    ticket, _ = SecurityTicket.objects.get_or_create(
        report=report,
        defaults={
            "channel": SecurityTicket.CHANNEL_OTHER,
            "status": _alert_status_to_ticket_status(data.get("status")),
            "message": (data.get("description") or report.note or "").strip(),
            "assignee": "",
            "legacy_source": True,
        },
    )
    if ticket.legacy_source is False:
        ticket.legacy_source = True
    status_value = _alert_status_to_ticket_status(data.get("status"))
    ticket.status = status_value
    if status_value == SecurityTicket.STATUS_DONE:
        ticket.resolved_at = timezone.now()
    ticket.message = (data.get("description") or ticket.message or "").strip()
    ticket.save(update_fields=["legacy_source", "status", "resolved_at", "message", "updated_at"])
    _sync_report_status_from_ticket(report, ticket)
    _timeline_log(
        EventTimelineLog.STAGE_ALERT,
        "legacy_alert_upserted",
        actor="legacy",
        report=report,
        ticket=ticket,
        payload={"status": ticket.status, "legacySource": True},
    )
    _push_governance_event(
        event_type="legacy_alert_received",
        title="兼容告警已入库",
        message=ticket.message or "收到旧链路告警并已落库",
        level=RealtimeEvent.LEVEL_WARN,
        source="legacy",
        report=report,
        ticket=ticket,
        payload=_report_to_alert(report, ticket=ticket, request=request),
    )

    return JsonResponse({"success": True, "alert": _report_to_alert(report, ticket=ticket, request=request)})


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def alert_list(request):

    since_id = _parse_int(request.GET.get("since")) or _parse_int(request.GET.get("since_id"))
    limit = _parse_int(request.GET.get("limit")) or 50
    status = (request.GET.get("status") or "").strip().lower()
    department = (request.GET.get("department") or "").strip()
    report_id = (request.GET.get("reportId") or request.GET.get("report_id") or "").strip()

    tickets = SecurityTicket.objects.select_related("report").order_by("-id")
    if status:
        tickets = tickets.filter(status=_alert_status_to_ticket_status(status))
    if department:
        tickets = tickets.filter(report__department__icontains=department)
    if report_id:
        tickets = tickets.filter(report_id=report_id)
    if since_id:
        tickets = tickets.filter(id__gt=since_id)

    ticket_items = list(tickets[:limit])
    alerts = []
    for ticket in ticket_items:
        if ticket.report is None:
            continue
        alerts.append(_report_to_alert(ticket.report, ticket=ticket, request=request))

    latest_ticket = SecurityTicket.objects.order_by("-id").first()
    return JsonResponse({
        "success": True,
        "alerts": alerts,
        "count": len(alerts),
        "latestId": latest_ticket.id if latest_ticket else 0,
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["PATCH", "POST"])
def alert_update_status(request, report_id):
    auth = getattr(request, "auth", None)

    report = get_object_or_404(Report, id=report_id)
    payload = _get_request_data(request)
    status_value = _alert_status_to_ticket_status(payload.get("status"))
    message = (payload.get("message") or "").strip()
    assignee = (payload.get("assignee") or "").strip()

    ticket, _ = SecurityTicket.objects.get_or_create(
        report=report,
        defaults={
            "status": status_value,
            "channel": SecurityTicket.CHANNEL_WORK_ORDER,
            "message": message or report.note or "",
            "assignee": assignee,
        },
    )
    ticket.status = status_value
    if message:
        ticket.message = message
    if assignee:
        ticket.assignee = assignee
    ticket.resolved_at = timezone.now() if status_value == SecurityTicket.STATUS_DONE else None
    ticket.save(update_fields=["status", "message", "assignee", "resolved_at", "updated_at"])
    _sync_report_status_from_ticket(report, ticket)
    stage = EventTimelineLog.STAGE_RESOLVED if status_value == SecurityTicket.STATUS_DONE else EventTimelineLog.STAGE_PROCESS
    _timeline_log(
        stage,
        "alert_status_updated",
        actor=(auth[0].username if auth else "dashboard"),
        report=report,
        ticket=ticket,
        payload={"status": ticket.status, "assignee": ticket.assignee, "message": ticket.message},
    )
    _push_governance_event(
        event_type="alert_status_updated",
        title="告警状态已更新",
        message=f"告警 {report.id} 更新为 { _ticket_status_to_alert_status(ticket.status) }",
        level=RealtimeEvent.LEVEL_INFO if status_value != SecurityTicket.STATUS_DONE else RealtimeEvent.LEVEL_WARN,
        source="alert",
        report=report,
        ticket=ticket,
        payload=_report_to_alert(report, ticket=ticket, request=request),
    )

    return JsonResponse({"success": True, "alert": _report_to_alert(report, ticket=ticket, request=request)})


@require_admin_view(allow_dashboard=True)
@require_http_methods(["GET"])
def ticket_list(request):

    status = (request.GET.get("status") or "").strip().lower()
    assignee = (request.GET.get("assignee") or "").strip()
    department = (request.GET.get("department") or "").strip()
    report_id = (request.GET.get("reportId") or request.GET.get("report_id") or "").strip()
    page = _parse_int(request.GET.get("page")) or 1
    page_size = _parse_int(request.GET.get("pageSize")) or 20
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    if page_size > 100:
        page_size = 100

    queryset = SecurityTicket.objects.select_related("report").order_by("-updated_at", "-id")
    if status:
        queryset = queryset.filter(status=_alert_status_to_ticket_status(status))
    if assignee:
        queryset = queryset.filter(assignee__icontains=assignee)
    if department:
        queryset = queryset.filter(report__department__icontains=department)
    if report_id:
        queryset = queryset.filter(report_id=report_id)

    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    items = [_ticket_to_dict(item, request=request) for item in queryset[start:end]]

    return JsonResponse({
        "success": True,
        "tickets": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


@csrf_exempt
@require_admin_view(allow_dashboard=True)
@require_http_methods(["PATCH", "POST"])
def ticket_update(request, ticket_id):
    auth = getattr(request, "auth", None)

    ticket = get_object_or_404(SecurityTicket.objects.select_related("report"), id=ticket_id)
    data = _get_request_data(request)
    status_raw = data.get("status")
    if status_raw:
        ticket.status = _alert_status_to_ticket_status(status_raw)
    assignee = data.get("assignee")
    if assignee is not None:
        ticket.assignee = (assignee or "").strip()
    message = data.get("message")
    if message is not None:
        ticket.message = (message or "").strip()
    ticket.resolved_at = timezone.now() if ticket.status == SecurityTicket.STATUS_DONE else None
    ticket.save(update_fields=["status", "assignee", "message", "resolved_at", "updated_at"])

    if ticket.report_id:
        _sync_report_status_from_ticket(ticket.report, ticket)
    stage = EventTimelineLog.STAGE_RESOLVED if ticket.status == SecurityTicket.STATUS_DONE else EventTimelineLog.STAGE_PROCESS
    _timeline_log(
        stage,
        "ticket_updated",
        actor=(auth[0].username if auth else "dashboard"),
        report=ticket.report,
        ticket=ticket,
        payload={"status": ticket.status, "assignee": ticket.assignee, "message": ticket.message},
    )
    _push_governance_event(
        event_type="ticket_updated",
        title="工单状态已更新",
        message=f"工单#{ticket.id} 当前状态：{_ticket_status_to_alert_status(ticket.status)}",
        level=RealtimeEvent.LEVEL_INFO,
        source="ticket",
        report=ticket.report,
        ticket=ticket,
        payload=_ticket_to_dict(ticket, request=request),
    )

    return JsonResponse({"success": True, "ticket": _ticket_to_dict(ticket, request=request)})


@csrf_exempt
@require_http_methods(["POST"])
def contact_create(request):
    data = _get_request_data(request)
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    topic = (data.get("topic") or "").strip()
    message = (data.get("message") or "").strip()
    if not name or not email or not message:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    record = ContactMessage.objects.create(
        name=name,
        email=email,
        phone=phone,
        topic=topic,
        message=message,
    )
    return JsonResponse({
        "success": True,
        "contactId": record.id,
        "createdAt": record.created_at.isoformat(),
    })
