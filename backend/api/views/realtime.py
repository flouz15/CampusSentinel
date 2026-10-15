"""Realtime event endpoints."""

import json
import time

from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from ..models import EventTimelineLog, RealtimeEvent, Report
from .common import REALTIME_POLL_INTERVAL_SECONDS, require_auth_view

def _event_source_url(event):
    if event.report_id:
        return f"/frontend/index.html#alert={event.report_id}"
    if event.ticket_id:
        return f"/frontend/index.html#ticket={event.ticket_id}"
    if event.detection_id:
        return f"/frontend/index.html#report={event.detection_id}"
    if event.robot_id:
        return f"/frontend/index.html#robot={event.robot_id}"
    return "/frontend/index.html"


def _realtime_event_to_dict(event):
    return {
        "id": event.id,
        "eventType": event.event_type,
        "level": event.level,
        "title": event.title,
        "message": event.message,
        "source": event.source,
        "reportId": str(event.report_id) if event.report_id else None,
        "ticketId": event.ticket_id,
        "detectionId": event.detection_id,
        "robotId": event.robot_id or None,
        "payload": event.payload_json or {},
        "sourceUrl": _event_source_url(event),
        "createdAt": event.created_at.isoformat(),
    }


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def report_timeline(request, report_id):
    logs = EventTimelineLog.objects.filter(report_id=report_id).order_by("id")
    return JsonResponse({
        "success": True,
        "timeline": [
            {
                "id": row.id,
                "reportId": str(row.report_id) if row.report_id else None,
                "ticketId": row.ticket_id,
                "stage": row.stage,
                "action": row.action,
                "actor": row.actor,
                "payload": row.payload_json or {},
                "createdAt": row.created_at.isoformat(),
            }
            for row in logs
        ],
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def realtime_poll(request):

    since_id = _parse_int(request.GET.get("sinceId")) or 0
    limit = _parse_int(request.GET.get("limit")) or 50
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    rows = list(RealtimeEvent.objects.filter(id__gt=since_id).order_by("id")[:limit])
    latest = rows[-1].id if rows else since_id
    return JsonResponse({
        "success": True,
        "events": [_realtime_event_to_dict(item) for item in rows],
        "latestId": latest,
        "pollIntervalSec": REALTIME_POLL_INTERVAL_SECONDS,
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def realtime_stream(request):
    since_id = _parse_int(request.GET.get("sinceId")) or _parse_int(request.headers.get("Last-Event-ID")) or 0

    def event_iter(start_id):
        last_id = start_id
        heartbeat_at = time.time()
        while True:
            rows = list(RealtimeEvent.objects.filter(id__gt=last_id).order_by("id")[:100])
            if rows:
                for row in rows:
                    payload = _realtime_event_to_dict(row)
                    yield f"id: {row.id}\n"
                    yield "event: update\n"
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_id = row.id
                heartbeat_at = time.time()
            else:
                now = time.time()
                if now - heartbeat_at > 15:
                    yield "event: ping\n"
                    yield f"data: {json.dumps({'ts': timezone.now().isoformat()}, ensure_ascii=False)}\n\n"
                    heartbeat_at = now
                time.sleep(1.2)

    response = StreamingHttpResponse(event_iter(since_id), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
