"""Detection upload and lookup endpoints."""

import mimetypes
import re
from urllib.parse import urlparse

import requests
from django.db.models import Q
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import DetectionRecord, UserProfile
from .common import (
    DETECTION_BASE_URL,
    VIDEO_DETECT_EXECUTOR,
    _create_pending_detection_record,
    _detection_record_to_dict,
    _extract_plate_meta,
    _detection_media_candidates,
    _is_allowed_detection_media_target,
    _normalize_detection_media_target,
    _parse_int,
    _process_video_detection_record,
    _save_detection_record,
    logger,
    require_auth_view,
)

@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def detection_image_upload(request):
    auth = getattr(request, "auth", None)
    user = auth[0] if auth else None
    uploaded_file = request.FILES.get("file") or request.FILES.get("image")
    if uploaded_file is None:
        return JsonResponse({"success": False, "error": "MISSING_FILE"}, status=400)

    model_name_hint = (request.POST.get("modelName") or "").strip()
    record = _save_detection_record(
        uploaded_file=uploaded_file,
        detect_type=DetectionRecord.TYPE_IMAGE,
        user=user,
        model_name_hint=model_name_hint,
    )
    _push_governance_event(
        event_type="detection_image_finished",
        title="单图检测完成",
        message=f"{record.original_name or 'image'} · {record.label or '未识别'}",
        level=RealtimeEvent.LEVEL_INFO if record.status == DetectionRecord.STATUS_SUCCESS else RealtimeEvent.LEVEL_WARN,
        source="detection",
        detection=record,
        payload=_detection_record_to_dict(record, request=request),
    )
    response_payload = {
        "success": record.status == DetectionRecord.STATUS_SUCCESS,
        "record": _detection_record_to_dict(record, request=request),
        "detectionError": record.error_message or None,
    }
    if record.status == DetectionRecord.STATUS_FAILED:
        return JsonResponse(
            response_payload,
            status=_detection_error_status_code(record.error_message),
        )
    return JsonResponse(response_payload)


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def detection_batch_upload(request):
    auth = getattr(request, "auth", None)
    user = auth[0] if auth else None
    files = request.FILES.getlist("files")
    if not files:
        single = request.FILES.get("file")
        if single is not None:
            files = [single]
    if not files:
        return JsonResponse({"success": False, "error": "MISSING_FILES"}, status=400)

    if len(files) > 50:
        return JsonResponse({"success": False, "error": "TOO_MANY_FILES"}, status=400)

    model_name_hint = (request.POST.get("modelName") or "").strip()
    batch_id = uuid.uuid4().hex[:12]
    records = []
    success_count = 0
    failed_count = 0
    for uploaded_file in files:
        record = _save_detection_record(
            uploaded_file=uploaded_file,
            detect_type=DetectionRecord.TYPE_BATCH,
            user=user,
            batch_id=batch_id,
            model_name_hint=model_name_hint,
        )
        records.append(record)
        if record.status == DetectionRecord.STATUS_SUCCESS:
            success_count += 1
        else:
            failed_count += 1
        _push_governance_event(
            event_type="detection_batch_item_finished",
            title="批量检测结果更新",
            message=f"{record.original_name or 'file'} · {record.label or record.status}",
            level=RealtimeEvent.LEVEL_INFO if record.status == DetectionRecord.STATUS_SUCCESS else RealtimeEvent.LEVEL_WARN,
            source="detection",
            detection=record,
            payload={"batchId": batch_id, "recordId": record.id, "status": record.status},
        )

    failed_items = [
        {
            "id": item.id,
            "fileName": item.original_name,
            "errorMessage": item.error_message or "DETECTION_FAILED",
        }
        for item in records
        if item.status == DetectionRecord.STATUS_FAILED
    ]
    first_error = failed_items[0]["errorMessage"] if failed_items else None
    response_payload = {
        "success": failed_count == 0,
        "partialSuccess": success_count > 0 and failed_count > 0,
        "batchId": batch_id,
        "count": len(records),
        "successCount": success_count,
        "failedCount": failed_count,
        "detectionError": first_error,
        "failedItems": failed_items,
        "records": [_detection_record_to_dict(item, request=request) for item in records],
    }
    if failed_count == len(records):
        return JsonResponse(
            response_payload,
            status=_detection_error_status_code(first_error),
        )
    return JsonResponse(response_payload)


@require_auth_view()
@require_http_methods(["GET"])
def detection_video_health(request):
    results = []
    available = False
    url = f"{DETECTION_BASE_URL}/health"
    try:
        resp = requests.get(url, timeout=5)
        ok = resp.ok
        results.append({
            "endpoint": "health",
            "url": url,
            "statusCode": resp.status_code,
            "available": ok,
            "error": None,
        })
        available = ok
    except Exception as exc:
        results.append({
            "endpoint": "health",
            "url": url,
            "statusCode": None,
            "available": False,
            "error": str(exc),
        })
    return JsonResponse({
        "success": True,
        "available": available,
        "base": DETECTION_BASE_URL,
        "targets": results,
    })


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def detection_video_upload(request):
    auth = getattr(request, "auth", None)
    user = auth[0] if auth else None
    uploaded_file = request.FILES.get("file") or request.FILES.get("video")
    if uploaded_file is None:
        return JsonResponse({"success": False, "error": "MISSING_FILE"}, status=400)

    model_name_hint = (request.POST.get("modelName") or "").strip()
    frame_stride = _parse_int(request.POST.get("frameStride")) or 3
    ocr_every_n = _parse_int(request.POST.get("ocrEveryN")) or 8
    return_vis = 1 if _coerce_bool(request.POST.get("returnVis")) else 0
    ocr_on_violation_only = (
        1 if _coerce_bool(request.POST.get("ocrOnViolationOnly")) is not False else 0
    )
    query_params = {
        "frame_stride": frame_stride,
        "return_vis": return_vis,
        "ocr_every_n": ocr_every_n,
        "ocr_on_violation_only": ocr_on_violation_only,
    }
    job_meta = {
        "submittedAt": timezone.now().isoformat(),
        "frameStride": frame_stride,
        "ocrEveryN": ocr_every_n,
        "returnVis": return_vis,
        "ocrOnViolationOnly": ocr_on_violation_only,
    }
    record = _create_pending_detection_record(
        uploaded_file=uploaded_file,
        detect_type=DetectionRecord.TYPE_VIDEO,
        user=user,
        model_name_hint=model_name_hint,
        job_meta=job_meta,
    )

    transaction.on_commit(
        lambda: VIDEO_DETECT_EXECUTOR.submit(
            _process_video_detection_record,
            record.id,
            query_params=query_params,
            model_name_hint=model_name_hint,
        )
    )

    response_payload = {
        "success": True,
        "queued": True,
        "record": _detection_record_to_dict(record, request=request),
        "detectionError": None,
    }
    return JsonResponse(response_payload, status=202)


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def detection_records(request):
    auth = getattr(request, "auth", None)
    user, profile = auth if auth is not None else (None, None)
    detect_type = (request.GET.get("type") or "").strip().lower()
    status = (request.GET.get("status") or "").strip().lower()
    keyword = (request.GET.get("keyword") or "").strip()
    report_id = (request.GET.get("reportId") or request.GET.get("report_id") or "").strip()
    record_id = _parse_int(request.GET.get("recordId") or request.GET.get("record_id"))
    page = _parse_int(request.GET.get("page")) or 1
    page_size = _parse_int(request.GET.get("pageSize")) or 10
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    queryset = DetectionRecord.objects.order_by("-created_at", "-id")
    if auth is not None and not (profile and profile.role == UserProfile.ROLE_ADMIN):
        queryset = queryset.filter(created_by=user)
    valid_types = {DetectionRecord.TYPE_IMAGE, DetectionRecord.TYPE_BATCH, DetectionRecord.TYPE_VIDEO}
    valid_status = {DetectionRecord.STATUS_PENDING, DetectionRecord.STATUS_SUCCESS, DetectionRecord.STATUS_FAILED}
    if detect_type in valid_types:
        queryset = queryset.filter(detect_type=detect_type)
    if status in valid_status:
        queryset = queryset.filter(status=status)
    if record_id:
        queryset = queryset.filter(id=record_id)
    if report_id:
        queryset = queryset.filter(batch_id=report_id)
    if keyword:
        queryset = queryset.filter(
            Q(original_name__icontains=keyword)
            | Q(label__icontains=keyword)
            | Q(model_name__icontains=keyword)
            | Q(result_payload__icontains=keyword)
        )

    total = queryset.count()
    start = (page - 1) * page_size
    end = start + page_size
    rows = list(queryset[start:end])

    return JsonResponse({
        "success": True,
        "records": [_detection_record_to_dict(item, request=request) for item in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def detection_ev_parking_stats(request):
    """近 N 天内与电动车/违停相关的检测成功记录次数，按识别标签聚合（供大屏展示）。"""
    from datetime import timedelta

    auth = getattr(request, "auth", None)
    user, profile = auth if auth is not None else (None, None)
    days = _parse_int(request.GET.get("days")) or 30
    days = max(1, min(days, 365))
    since = timezone.now() - timedelta(days=days)

    queryset = DetectionRecord.objects.filter(
        created_at__gte=since,
        status=DetectionRecord.STATUS_SUCCESS,
    ).filter(
        Q(label__icontains="违停")
        | Q(label__icontains="电动车")
        | Q(label__icontains="电瓶车")
        | Q(label__icontains="非机动车")
        | Q(original_name__icontains="违停"),
    )
    if auth is not None and not (profile and profile.role == UserProfile.ROLE_ADMIN):
        queryset = queryset.filter(created_by=user)

    total = queryset.count()
    agg = (
        queryset.values("label")
        .annotate(count=Count("id"))
        .order_by("-count")[:12]
    )
    by_label = [
        {"label": (row["label"] or "").strip() or "未标注标签", "count": row["count"]}
        for row in agg
    ]
    return JsonResponse({"success": True, "days": days, "total": total, "byLabel": by_label})


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def detection_media_proxy(request):
    raw_target = request.GET.get("url") or request.GET.get("path") or ""
    target_url = _normalize_detection_media_target(raw_target)
    if not target_url or not _is_allowed_detection_media_target(target_url):
        return JsonResponse({"success": False, "error": "INVALID_MEDIA_TARGET"}, status=400)

    headers = {}
    range_header = request.META.get("HTTP_RANGE") or ""
    if range_header:
        headers["Range"] = range_header

    upstream = None
    last_error = ""
    last_status = 0
    for candidate in _detection_media_candidates(target_url):
        try:
            response = requests.get(candidate, stream=True, timeout=30, headers=headers)
        except Exception as exc:
            last_error = str(exc)
            continue
        if response.status_code >= 400:
            last_status = response.status_code
            response.close()
            continue
        upstream = response
        target_url = candidate
        break

    if upstream is None:
        if last_status:
            return JsonResponse({"success": False, "error": f"UPSTREAM_{last_status}"}, status=last_status)
        return JsonResponse({"success": False, "error": f"MEDIA_PROXY_FAILED: {last_error or 'unreachable'}"}, status=502)

    content_type = upstream.headers.get("Content-Type") or mimetypes.guess_type(urlparse(target_url).path)[0] or "application/octet-stream"

    def stream_chunks():
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    response = StreamingHttpResponse(stream_chunks(), status=upstream.status_code, content_type=content_type)
    for header_name in ("Content-Length", "Content-Range", "Accept-Ranges", "Cache-Control", "ETag", "Last-Modified"):
        header_value = upstream.headers.get(header_name)
        if header_value:
            response[header_name] = header_value
    return response


def _normalize_plate_lookup(value):
    text = str(value or "").strip().upper()
    if not text:
        return ""
    return re.sub(r"[\s\-·]", "", text)


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def detection_plate_summary(request):
    auth = getattr(request, "auth", None)
    user, profile = auth if auth is not None else (None, None)
    plate_text = (request.GET.get("plateText") or request.GET.get("plate") or request.GET.get("keyword") or "").strip()
    normalized_target = _normalize_plate_lookup(plate_text)
    if not normalized_target:
        return JsonResponse({"success": False, "error": "MISSING_PLATE_TEXT"}, status=400)

    matched = []
    queryset = DetectionRecord.objects.all()
    if auth is not None and not (profile and profile.role == UserProfile.ROLE_ADMIN):
        queryset = queryset.filter(created_by=user)
    if plate_text:
        queryset = queryset.filter(result_payload__icontains=plate_text)
    for row in queryset.order_by("-created_at", "-id")[:1000]:
        payload = row.result_payload or {}
        current_plate, current_confidence = _extract_plate_meta(payload if isinstance(payload, dict) else {})
        normalized_current = _normalize_plate_lookup(current_plate)
        if not normalized_current or normalized_current != normalized_target:
            continue
        matched.append((row, current_plate, current_confidence))

    latest_row = matched[0][0] if matched else None
    latest_plate = matched[0][1] if matched else ""
    latest_confidence = matched[0][2] if matched else None
    latest_report_id = None
    if latest_row:
        row_payload = latest_row.result_payload if isinstance(latest_row.result_payload, dict) else {}
        latest_report_id = row_payload.get("reportId") or row_payload.get("report_id") or latest_row.batch_id or None

    return JsonResponse({
        "success": True,
        "plateText": latest_plate or plate_text,
        "normalizedPlate": normalized_target,
        "totalCount": len(matched),
        "lastSeenAt": latest_row.created_at.isoformat() if latest_row else None,
        "latestRecordId": latest_row.id if latest_row else None,
        "latestReportId": str(latest_report_id) if latest_report_id else None,
        "latestLabel": latest_row.label if latest_row else "",
        "latestConfidence": latest_row.confidence if latest_row else None,
        "plateConfidence": latest_confidence,
    })
