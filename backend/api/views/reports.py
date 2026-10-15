"""Report and reward workflow endpoints."""

import json
import os

import requests
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import DetectionRecord, Report, RewardRecord, SecurityTicket, UserProfile
from .common import (
    DETECTION_BASE_URL,
    _compact_detection_payload,
    _create_report_detection_record,
    _detection_error_status_code,
    _extract_detection_meta,
    _finalize_report_flow,
    _get_request_data,
    _grant_reward_once,
    _normalize_detection_items,
    _parse_float,
    _parse_int,
    _report_to_dict,
    _run_detection_request,
    _save_detection_record,
    _sync_report_evidence,
    _sync_report_status_from_ticket,
    _ticket_to_dict,
    _timeline_log,
    logger,
    require_admin_view,
    require_auth_view,
)

@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def reports(request):
    auth = getattr(request, "auth", None)
    if auth is None:
        report_list = Report.objects.all()
    else:
        user, profile = auth
        if profile and profile.role == UserProfile.ROLE_ADMIN:
            report_list = Report.objects.all()
        else:
            report_list = Report.objects.filter(created_by=user)
    return JsonResponse({
        "success": True,
        "reports": [_report_to_dict(report, request=request) for report in report_list],
    })


@require_auth_view(allow_dashboard=True)
@require_http_methods(["GET"])
def report_detail(request, report_id):
    report = get_object_or_404(Report, id=report_id)
    auth = getattr(request, "auth", None)
    if auth is not None:
        user, profile = auth
        if not (profile and profile.role == UserProfile.ROLE_ADMIN) and report.created_by_id != user.id:
            return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)
    return JsonResponse({
        "success": True,
        "report": _report_to_dict(report, request=request),
    })


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def report_upload(request):
    user, profile = getattr(request, "auth")

    payload = request.POST if request.POST else _get_request_data(request)
    image = request.FILES.get("file") or request.FILES.get("image")

    reporter_name = payload.get("reporterName") or (profile.full_name if profile else "") or user.username
    reporter_role = payload.get("reporterRole") or (profile.role if profile else "")
    reporter_contact = payload.get("reporterContact") or (profile.phone if profile else "") or ""
    department = payload.get("department") or (profile.department if profile else "") or ""

    report = Report(
        created_by=user,
        location=payload.get("location", ""),
        time=_parse_time(payload.get("time")),
        note=payload.get("note", ""),
        category=payload.get("category", "未分类"),
        priority=payload.get("priority", "P2"),
        reporter_name=reporter_name,
        reporter_role=reporter_role or "瀛︾敓",
        reporter_contact=reporter_contact,
        department=department,
        latitude=_parse_float(payload.get("latitude")),
        longitude=_parse_float(payload.get("longitude")),
    )

    if image is not None:
        report.image = image

    detections = []
    violations = []
    latency_ms = None
    process_images = None
    vis_image = None
    detection_error = None
    plate_text = None
    plate_confidence = None

    # 濡傛灉鍓嶇宸茬粡妫€娴嬭繃锛岃烦杩囨娴?
    skip_detection = payload.get("skipDetection") == "true"
    pre_violation = payload.get("violation") == "true"

    if skip_detection:
        if pre_violation:
            # 前端已经检测为违规，直接使用前端结果
            report.violation = True
            report.status = "CONFIRMED"
            report.reward_points = _parse_int(payload.get("rewardPoints")) or _priority_reward(report.priority)
            report.reward_message = payload.get("rewardMessage") or f"已确认违规，奖励 {report.reward_points} 积分"
        else:
            report.violation = False
            report.status = "SUBMITTED"
    elif image is not None:
        try:
            if hasattr(image, "seek"):
                image.seek(0)
            response = requests.post(
                f"{DETECTION_BASE_URL}/detect",
                params={"return_steps": 1},
                files={"file": image},
                timeout=30,
            )
            if response.status_code >= 200 and response.status_code < 300:
                data = response.json()
                detections = _normalize_detection_items(data.get("detections"))
                violations = _normalize_detection_items(data.get("violations"))
                latency_ms = data.get("latency_ms")
                process_images = data.get("process_images")
                vis_image = data.get("vis_jpeg_base64") or data.get("visImage")
                plate_text, plate_confidence = _extract_plate_meta({
                    "detections": detections,
                    "violations": violations,
                    "plateText": data.get("plateText") or data.get("plate_text"),
                    "plateConfidence": data.get("plateConfidence") or data.get("plate_conf"),
                })
            else:
                detection_error = response.text
        except Exception as e:
            detection_error = str(e)

    # 鍙湁闈炶烦杩囨娴嬫ā寮忔墠闇€瑕佹牴鎹娴嬬粨鏋滆缃姸鎬?
    if not skip_detection:
        report.violation = _is_violation(detections, violations)
        report.confidence = _confidence_from_detections(detections)

        if image is not None and hasattr(image, "seek"):
            image.seek(0)

        if report.violation:
            report.status = "CONFIRMED"
            report.reward_points = _priority_reward(report.priority)
            report.reward_message = f"宸茬‘璁よ繚瑙勶紝濂栧姳 {report.reward_points} 绉垎"
        else:
            report.status = "PENDING_REVIEW"

    report.save()
    _timeline_log(
        EventTimelineLog.STAGE_REPORT,
        "report_created",
        actor=user.username,
        report=report,
        payload={
            "location": report.location,
            "category": report.category,
            "priority": report.priority,
        },
    )

    _finalize_report_flow(
        report,
        user,
        profile,
        request=request,
        skip_detection=skip_detection,
        latency_ms=latency_ms,
        detection_error=detection_error,
        emit_received_when_no_violation=True,
    )

    return JsonResponse({
        "success": True,
        "reportId": str(report.id),
        "report": _report_to_dict(report, request=request),
        "detections": detections,
        "violations": violations,
        "latencyMs": latency_ms,
        "processImages": process_images,
        "visImage": vis_image,
        "plateText": plate_text or None,
        "plateConfidence": plate_confidence,
        "rewardPoints": report.reward_points,
        "rewardMessage": report.reward_message,
        "detectionError": detection_error,
    })


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST", "PATCH"])
def report_detect_result(request, report_id):
    user, profile = getattr(request, "auth")

    report = get_object_or_404(Report, id=report_id)
    if profile and profile.role != UserProfile.ROLE_ADMIN and report.created_by_id != user.id:
        return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)

    data = _get_request_data(request)
    detections = _normalize_detection_items(data.get("detections"))
    violations = _normalize_detection_items(data.get("violations"))
    detection_error = (data.get("detectionError") or "").strip()
    latency_ms = _parse_int(data.get("latencyMs"))
    violation = _coerce_bool(data.get("violation"))
    if violation is None:
        violation = _is_violation(detections, violations)
    confidence = _parse_int(data.get("confidence"))
    if confidence is None:
        confidence = _confidence_from_detections(detections)

    report.violation = bool(violation)
    report.confidence = confidence
    if report.violation:
        report.status = "CONFIRMED"
        report.reward_points = _parse_int(data.get("rewardPoints")) or report.reward_points or _priority_reward(report.priority)
        report.reward_message = (
            data.get("rewardMessage")
            or report.reward_message
            or f"已确认违规，奖励 {report.reward_points} 积分"
        )
    else:
        report.status = "PENDING_REVIEW"
        report.reward_points = None
        report.reward_message = ""

    report.save(update_fields=["violation", "confidence", "status", "reward_points", "reward_message", "updated_at"])

    _finalize_report_flow(
        report,
        user,
        profile,
        request=request,
        skip_detection=False,
        latency_ms=latency_ms,
        detection_error=detection_error or None,
        emit_received_when_no_violation=False,
    )
    compact_payload = _compact_detection_payload(
        {
            "detections": detections,
            "violations": violations,
            "latencyMs": latency_ms,
            "model": data.get("model") or data.get("modelName") or data.get("model_name"),
            "processImages": data.get("processImages") or data.get("process_images"),
            "vis_jpeg_base64": data.get("vis_jpeg_base64") or data.get("visImage"),
            "plateText": data.get("plateText") or data.get("plate_text"),
            "plateConfidence": data.get("plateConfidence") or data.get("plate_conf"),
        }
    )
    plate_text, plate_confidence = _extract_plate_meta(compact_payload)
    detection_record = _create_report_detection_record(
        report,
        user=report.created_by or user,
        result_payload=compact_payload,
        error_message=detection_error or "",
    )

    return JsonResponse({
        "success": True,
        "reportId": str(report.id),
        "report": _report_to_dict(report, request=request),
        "detections": detections,
        "violations": violations,
        "latencyMs": latency_ms,
        "processImages": compact_payload.get("process_images"),
        "visImage": compact_payload.get("vis_jpeg_base64"),
        "plateText": plate_text or None,
        "plateConfidence": plate_confidence,
        "detectionError": detection_error or None,
        "detectionRecordId": detection_record.id if detection_record else None,
    })


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def report_run_detect(request, report_id):
    user, profile = getattr(request, "auth")

    report = get_object_or_404(Report, id=report_id)
    if profile and profile.role != UserProfile.ROLE_ADMIN and report.created_by_id != user.id:
        return JsonResponse({"success": False, "error": "FORBIDDEN"}, status=403)
    if not report.image:
        return JsonResponse({"success": False, "error": "MISSING_IMAGE"}, status=400)

    file_name = os.path.basename(report.image.name or "report.jpg")
    file_ext = os.path.splitext(file_name)[1] or ".jpg"
    content_type = mimetypes.guess_type(file_name)[0] or "image/jpeg"
    with report.image.open("rb") as image_fp:
        file_bytes = image_fp.read()

    success, result_payload, error_message = _run_detection_request(
        file_bytes=file_bytes,
        file_name=file_name or f"report{file_ext}",
        content_type=content_type,
        detect_type=DetectionRecord.TYPE_IMAGE,
    )
    compact_payload = _compact_detection_payload(result_payload)
    detections = _normalize_detection_items(compact_payload.get("detections"))
    violations = _normalize_detection_items(compact_payload.get("violations"))
    plate_text, plate_confidence = _extract_plate_meta(compact_payload)
    latency_ms = _parse_int(
        compact_payload.get("latency_ms") or compact_payload.get("latencyMs")
    )
    violation = _is_violation(detections, violations)
    confidence = _confidence_from_detections(detections)

    report.violation = bool(violation)
    report.confidence = confidence
    if report.violation:
        report.status = "CONFIRMED"
        report.reward_points = report.reward_points or _priority_reward(report.priority)
        report.reward_message = report.reward_message or f"已确认违规，奖励 {report.reward_points} 积分"
    else:
        report.status = "PENDING_REVIEW"
        report.reward_points = None
        report.reward_message = ""
    report.save(update_fields=["violation", "confidence", "status", "reward_points", "reward_message", "updated_at"])

    _finalize_report_flow(
        report,
        user,
        profile,
        request=request,
        skip_detection=False,
        latency_ms=latency_ms,
        detection_error=None if success else (error_message or "DETECTION_FAILED"),
        emit_received_when_no_violation=False,
    )
    detection_record = _create_report_detection_record(
        report,
        user=report.created_by or user,
        result_payload=compact_payload,
        error_message="" if success else (error_message or "DETECTION_FAILED"),
    )

    response_payload = {
        "success": bool(success),
        "reportId": str(report.id),
        "report": _report_to_dict(report, request=request),
        "detections": detections,
        "violations": violations,
        "latencyMs": latency_ms,
        "processImages": compact_payload.get("process_images"),
        "visImage": compact_payload.get("vis_jpeg_base64"),
        "plateText": plate_text or None,
        "plateConfidence": plate_confidence,
        "rewardPoints": report.reward_points,
        "rewardMessage": report.reward_message or None,
        "detectionError": None if success else (error_message or "DETECTION_FAILED"),
        "detectionRecordId": detection_record.id if detection_record else None,
    }
    if not success:
        return JsonResponse(
            response_payload,
            status=_detection_error_status_code(error_message),
        )
    return JsonResponse(response_payload)


@csrf_exempt
@require_admin_view()
@require_http_methods(["POST", "PATCH"])
def report_review(request, report_id):
    user, profile = getattr(request, "auth")

    report = get_object_or_404(Report, id=report_id)
    data = _get_request_data(request)
    decision = (data.get("decision") or "").strip().lower()
    message = (data.get("message") or "").strip()
    if decision not in {"confirm", "reject"}:
        return JsonResponse({"success": False, "error": "INVALID_DECISION"}, status=400)

    actor = profile.full_name if profile and profile.full_name else user.username
    reporter = report.created_by or user
    reporter_profile = getattr(reporter, "profile", None)

    if decision == "confirm":
        report.violation = True
        report.status = "CONFIRMED"
        report.reward_points = _parse_int(data.get("rewardPoints")) or report.reward_points or _priority_reward(report.priority)
        report.reward_message = (
            data.get("rewardMessage")
            or report.reward_message
            or f"已人工确认违规，奖励 {report.reward_points} 积分"
        )
    else:
        report.violation = False
        report.status = "REJECTED"
        report.reward_points = None
        report.reward_message = ""
        report.handled_by = ""
        report.handled_at = None

    report.save(
        update_fields=[
            "violation",
            "status",
            "reward_points",
            "reward_message",
            "handled_by",
            "handled_at",
            "updated_at",
        ]
    )

    _timeline_log(
        EventTimelineLog.STAGE_DETECT,
        "manual_review_confirmed" if decision == "confirm" else "manual_review_rejected",
        actor=actor,
        report=report,
        payload={"decision": decision, "message": message or None},
    )
    _sync_report_evidence(report, request=request)

    if decision == "confirm":
        _grant_reward_once(report, reporter, reporter_profile)
        ticket = _ensure_alert_created(report, user, request=request)
        _push_governance_event(
            event_type="report_reviewed",
            title="举报已人工确认",
            message=message or report.note or report.category or "该举报已人工确认违规",
            level=RealtimeEvent.LEVEL_WARN,
            source="review",
            report=report,
            ticket=ticket,
            payload={"decision": decision, "reportId": str(report.id)},
        )
    else:
        _push_governance_event(
            event_type="report_reviewed",
            title="举报已人工驳回",
            message=message or report.note or report.category or "该举报经复核不构成违规",
            level=RealtimeEvent.LEVEL_INFO,
            source="review",
            report=report,
            payload={"decision": decision, "reportId": str(report.id)},
        )

    return JsonResponse(
        {
            "success": True,
            "decision": decision,
            "report": _report_to_dict(report, request=request),
        }
    )


@csrf_exempt
@require_admin_view()
@require_http_methods(["POST"])
def report_penalty(request, report_id):
    admin_user, admin_profile = getattr(request, "auth")

    report = get_object_or_404(Report, id=report_id)
    data = _get_request_data(request)
    target = (data.get("target") or "").strip()
    points = _parse_int(data.get("points"))
    message = (data.get("message") or "").strip()
    if not target or points is None or points >= 0 or not message:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    target_user = (
        User.objects.filter(username=target).first()
        or User.objects.filter(profile__student_id=target).first()
        or User.objects.filter(profile__staff_id=target).first()
    )
    if target_user is None:
        return JsonResponse({"success": False, "error": "TARGET_USER_NOT_FOUND"}, status=404)

    target_profile = getattr(target_user, "profile", None)
    if target_profile is None:
        return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)

    record = _create_points_record(
        user=target_user,
        profile=target_profile,
        points=points,
        message=message,
        status=RewardRecord.STATUS_REVOKED,
        report=report,
    )
    actor = admin_profile.full_name or admin_user.username
    _timeline_log(
        EventTimelineLog.STAGE_PROCESS,
        "report_penalty_applied",
        actor=actor,
        report=report,
        payload={
            "target": target_user.username,
            "points": points,
            "message": message,
            "recordId": record.id,
        },
    )
    _push_governance_event(
        event_type="report_penalty_applied",
        title="举报处罚已执行",
        message=f"{target_user.username} 积分变动 {points:+d}",
        level=RealtimeEvent.LEVEL_WARN,
        source="reward",
        report=report,
        payload={
            "recordId": record.id,
            "reportId": str(report.id),
            "target": target_user.username,
            "points": points,
            "message": message,
        },
    )
    return JsonResponse({
        "success": True,
        "reportId": str(report.id),
        "balance": target_profile.reward_points_total,
        "targetUser": {
            "username": target_user.username,
            "fullName": target_profile.full_name or "",
            "studentId": target_profile.student_id or "",
            "staffId": target_profile.staff_id or "",
        },
        "record": {
            "id": record.id,
            "reportId": str(report.id),
            "points": record.points,
            "message": record.message,
            "status": record.status,
            "kind": "penalty",
            "createdAt": record.created_at.isoformat(),
        },
    })
