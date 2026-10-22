"""Shared helpers for the campus dashboard API."""

import json
import logging
import mimetypes
import os
import random
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import wraps
from urllib.parse import urlparse

import requests
from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import (
    ApiToken,
    DetectionRecord,
    EventTimelineLog,
    RealtimeEvent,
    Report,
    RewardRecord,
    SecurityTicket,
    UserProfile,
)

# =========================
# 后端运行参数（统一从 backend/.env 读取）
# =========================

# 高德 Web 服务 Key：仅供后端代理地理编码、天气、POI、路径规划等接口使用
AMAP_WEB_SERVICE_KEY = os.environ.get("AMAP_WEB_SERVICE_KEY", "").strip()
# 高德 Web 服务 API 根地址
AMAP_API_BASE = "https://restapi.amap.com/v3"

# 检测服务地址：图片/视频检测、媒体代理等会转发到该地址
DETECTION_BASE_URL = os.environ.get("DETECTION_BASE_URL", "http://127.0.0.1:8001")
# 外部告警源地址：如需反向同步告警状态可配置
ALERT_API_BASE_URL = os.environ.get("ALERT_API_BASE_URL", "http://localhost:8080")
# 大屏请求后端时使用的密钥；与 config/dashboard.json 的 dashboardApiKey 保持一致
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
# 安保通知服务地址；留空则跳过外部通知推送
SECURITY_NOTIFY_BASE_URL = os.environ.get("SECURITY_NOTIFY_BASE_URL", "")
# 邮件发件人显示地址；未配置时回退到 EMAIL_HOST_USER
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")

# ---------- 智能巡检助手 · DeepSeek ----------
# 助手优先调用 DeepSeek 兼容模式接口；失败时再回退到 LightRAG / 本地 Ollama / 规则检索
# 兼容模式接口地址（OpenAI Chat Completions 风格）
DEEPSEEK_COMPAT_BASE_URL = os.environ.get(
    "DEEPSEEK_COMPAT_BASE_URL",
    "https://api.deepseek.com/v1",
).rstrip("/")
# DeepSeek API Key（sk-...）
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
# 首页智能巡检助手默认模型（deepseek-flash，默认关闭思考/推理）
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"
try:
    # DeepSeek 请求超时时间（秒）
    DEEPSEEK_TIMEOUT_SECONDS = int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "60"))
except (TypeError, ValueError):
    DEEPSEEK_TIMEOUT_SECONDS = 60

# ---------- 智能巡检助手 · 本地 Ollama 回退 ----------
# 本地 Ollama 服务地址；DeepSeek 不可用时尝试调用
ASSISTANT_OLLAMA_BASE = os.environ.get("ASSISTANT_OLLAMA_BASE", "http://127.0.0.1:11434")
# 本地 Ollama 模型名
ASSISTANT_OLLAMA_MODEL = os.environ.get("ASSISTANT_OLLAMA_MODEL", "qwen2.5:7b-instruct")

# ---------- 智能巡检助手 · LightRAG 回退 ----------
# LightRAG 服务地址；可用于知识库问答
LIGHTRAG_BASE_URL = os.environ.get("LIGHTRAG_BASE_URL", "http://127.0.0.1:9621")
# LightRAG API Key；服务未开启鉴权时可留空
LIGHTRAG_API_KEY = os.environ.get("LIGHTRAG_API_KEY", "")
try:
    # LightRAG 请求超时时间（秒）
    LIGHTRAG_TIMEOUT_SECONDS = int(os.environ.get("LIGHTRAG_TIMEOUT_SECONDS", "120"))
except (TypeError, ValueError):
    LIGHTRAG_TIMEOUT_SECONDS = 120

# 助手会话记忆轮数（一问一答计 1 轮）
ASSISTANT_MEMORY_LIMIT = 8
# 实时事件前端轮询建议间隔（秒）
REALTIME_POLL_INTERVAL_SECONDS = 5
# 机器人状态上报密钥；留空时仅接受本机或已通过大屏密钥校验的请求
ROBOT_INGEST_KEY = os.environ.get("ROBOT_INGEST_KEY", "")

logger = logging.getLogger(__name__)
VIDEO_DETECT_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="video-detect")

REWARD_CATALOG = [
    {
        "id": "coffee_coupon",
        "title": "校园咖啡券",
        "description": "可在线下合作咖啡点兑换一杯标准饮品。",
        "pointsCost": 80,
        "category": "校园权益",
    },
    {
        "id": "print_credit",
        "title": "打印额度",
        "description": "兑换 20 页黑白打印额度，用于学习资料打印。",
        "pointsCost": 50,
        "category": "学习服务",
    },
    {
        "id": "library_pass",
        "title": "图书借阅券",
        "description": "可用于热门图书优先借阅或延长借阅时长。",
        "pointsCost": 100,
        "category": "学习服务",
    },
    {
        "id": "canteen_discount",
        "title": "食堂优惠券",
        "description": "可在指定食堂窗口抵扣 10 元餐费。",
        "pointsCost": 120,
        "category": "校园权益",
    },
]


def _parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value):
    if not value:
        return timezone.now()
    dt = parse_datetime(value)
    if dt is None:
        return timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _timeline_log(stage, action, actor="", report=None, ticket=None, payload=None):
    EventTimelineLog.objects.create(
        report=report,
        ticket=ticket,
        stage=stage if stage in {x[0] for x in EventTimelineLog.STAGE_CHOICES} else EventTimelineLog.STAGE_REPORT,
        action=(action or "unknown")[:64],
        actor=(actor or "")[:64],
        payload_json=payload or {},
    )


def _create_realtime_event(
    *,
    event_type,
    title,
    message="",
    level=RealtimeEvent.LEVEL_INFO,
    source="system",
    report=None,
    ticket=None,
    detection=None,
    robot_id="",
    payload=None,
):
    row = RealtimeEvent.objects.create(
        event_type=(event_type or "system.event")[:64],
        level=level if level in {RealtimeEvent.LEVEL_INFO, RealtimeEvent.LEVEL_WARN, RealtimeEvent.LEVEL_CRITICAL} else RealtimeEvent.LEVEL_INFO,
        title=(title or "系统通知")[:120],
        message=message or "",
        source=(source or "system")[:32],
        report=report,
        ticket=ticket,
        detection=detection,
        robot_id=(robot_id or "")[:64],
        payload_json=payload or {},
    )
    return row


def _push_governance_event(
    *,
    event_type,
    title,
    message,
    level="info",
    source="system",
    report=None,
    ticket=None,
    detection=None,
    robot_id="",
    payload=None,
):
    event = _create_realtime_event(
        event_type=event_type,
        title=title,
        message=message,
        level=level,
        source=source,
        report=report,
        ticket=ticket,
        detection=detection,
        robot_id=robot_id,
        payload=payload,
    )
    return event


def _get_request_data(request):
    if request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except Exception:
            pass
    if hasattr(request, "POST"):
        return request.POST
    return {}


def _get_token(request):
    auth_header = request.headers.get("Authorization") or request.META.get("HTTP_AUTHORIZATION")
    if auth_header:
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    token = request.headers.get("X-Token") or request.META.get("HTTP_X_TOKEN")
    if token:
        return token.strip()
    return request.GET.get("token")


def _is_local_request(request):
    addr = request.META.get("REMOTE_ADDR")
    return addr in ("127.0.0.1", "::1")


def _dashboard_allowed(request):
    if DASHBOARD_API_KEY:
        key = request.headers.get("X-Dashboard-Key") or request.META.get("HTTP_X_DASHBOARD_KEY")
        if key == DASHBOARD_API_KEY:
            return True
        q = (request.GET.get("dashboard_key") or request.GET.get("key") or "").strip()
        if q == DASHBOARD_API_KEY:
            return True
        return False
    return _is_local_request(request)


def _get_auth_user(request):
    token = _get_token(request)
    if not token:
        return None, None
    try:
        token_obj = ApiToken.objects.select_related("user", "user__profile").get(key=token)
    except ApiToken.DoesNotExist:
        return None, None
    token_obj.last_used_at = timezone.now()
    token_obj.save(update_fields=["last_used_at"])
    user = token_obj.user
    profile = getattr(user, "profile", None)
    return user, profile


def _require_auth(request):
    user, profile = _get_auth_user(request)
    if user is None:
        return None, JsonResponse(
            {"success": False, "error": "AUTH_REQUIRED"},
            status=401,
        )
    return (user, profile), None


def _require_admin(request):
    auth, error = _require_auth(request)
    if error:
        return None, error
    user, profile = auth
    if profile is None or profile.role != UserProfile.ROLE_ADMIN:
        return auth, JsonResponse(
            {"success": False, "error": "FORBIDDEN"},
            status=403,
        )
    return (user, profile), None


def require_auth_view(*, allow_dashboard=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            auth, error = _require_auth(request)
            if error:
                if allow_dashboard and _dashboard_allowed(request):
                    setattr(request, "auth", None)
                    return view_func(request, *args, **kwargs)
                return error
            setattr(request, "auth", auth)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def require_admin_view(*, allow_dashboard=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            auth, error = _require_admin(request)
            if error:
                if allow_dashboard and _dashboard_allowed(request) and auth is None:
                    setattr(request, "auth", None)
                    return view_func(request, *args, **kwargs)
                return error
            setattr(request, "auth", auth)
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


def _profile_to_dict(profile):
    if profile is None:
        return None
    return {
        "role": profile.role,
        "fullName": profile.full_name or "",
        "phone": profile.phone or "",
        "studentId": profile.student_id or "",
        "staffId": profile.staff_id or "",
        "department": profile.department or "",
        "rewardPointsTotal": profile.reward_points_total,
    }


def _viewer_is_admin(request):
    if request is None:
        return False
    cached = getattr(request, "_viewer_is_admin", None)
    if cached is not None:
        return cached
    try:
        _user, profile = _get_auth_user(request)
        value = bool(profile and profile.role == UserProfile.ROLE_ADMIN)
    except Exception:
        value = False
    setattr(request, "_viewer_is_admin", value)
    return value


def _reporter_payload(report, request=None):
    if _viewer_is_admin(request):
        return {
            "reporterName": report.reporter_name,
            "reporterContact": report.reporter_contact or None,
            "department": report.department or None,
            "reporterInfoProtected": False,
            "reporterPrivacyHint": None,
        }
    return {
        "reporterName": "已加密",
        "reporterContact": None,
        "department": None,
        "reporterInfoProtected": True,
        "reporterPrivacyHint": "举报人姓名、学号/工号、手机号等信息已加密，仅管理员可见",
    }


def _report_to_dict(report, request=None):
    image_url = None
    if report.image:
        try:
            image_url = request.build_absolute_uri(report.image.url) if request else report.image.url
        except Exception:
            image_url = report.image.url
    evidence = list(report.evidence_urls or [])
    if image_url and image_url not in evidence:
        evidence.append(image_url)
    reporter_payload = _reporter_payload(report, request=request)
    return {
        "id": str(report.id),
        "location": report.location or "",
        "time": report.time.isoformat(),
        "note": report.note or "",
        "status": report.status,
        "imageUrl": image_url,
        "category": report.category,
        "priority": report.priority,
        "reporterName": reporter_payload["reporterName"],
        "reporterRole": report.reporter_role,
        "reporterContact": reporter_payload["reporterContact"],
        "department": reporter_payload["department"],
        "reporterInfoProtected": reporter_payload["reporterInfoProtected"],
        "reporterPrivacyHint": reporter_payload["reporterPrivacyHint"],
        "latitude": report.latitude,
        "longitude": report.longitude,
        "confidence": report.confidence,
        "evidenceUrls": evidence,
        "handledBy": report.handled_by or None,
        "handledAt": report.handled_at.isoformat() if report.handled_at else None,
        "violation": report.violation,
        "rewardPoints": report.reward_points,
        "rewardMessage": report.reward_message or None,
    }


def _confidence_from_detections(detections):
    max_conf = None
    for item in detections or []:
        conf = item.get("conf")
        if conf is None:
            conf = item.get("confidence")
        if conf is None:
            conf = item.get("score")
        try:
            conf_value = float(conf)
        except (TypeError, ValueError):
            continue
        if conf_value > 1:
            conf_value = conf_value / 100.0
        if max_conf is None or conf_value > max_conf:
            max_conf = conf_value
    if max_conf is None:
        return None
    return int(round(max_conf * 100))


def _is_violation(detections, violations):
    for item in detections or []:
        if item.get("violation") is True:
            return True
        if isinstance(item.get("violation"), (int, float)) and item.get("violation") != 0:
            return True
    if violations:
        return True
    return False


def _priority_reward(priority):
    value = (priority or "").upper()
    if value == "P1":
        return 30
    if value == "P2":
        return 20
    if value == "P3":
        return 10
    return 10


def _severity_from_priority(priority):
    value = (priority or "").upper()
    if value == "P1":
        return "high"
    if value == "P3":
        return "low"
    return "medium"


def _category_for_alert(category):
    text = (category or "").strip()
    if not text:
        return None
    if "杩濆仠" in text:
        return "parking"
    if "鍚哥儫" in text:
        return "smoking"
    if "鍨冨溇" in text:
        return "trash"
    if "鍣煶" in text:
        return "noise"
    if "娑堥槻" in text or "澶寸洈" in text or "杩濊" in text:
        return "safety"
    return "other"


def _ticket_status_to_alert_status(ticket_status):
    if ticket_status == SecurityTicket.STATUS_ASSIGNED:
        return "processing"
    if ticket_status == SecurityTicket.STATUS_DONE:
        return "resolved"
    return "pending"


def _alert_status_to_ticket_status(alert_status):
    value = (alert_status or "").strip().lower()
    if value == "processing":
        return SecurityTicket.STATUS_ASSIGNED
    if value == "resolved":
        return SecurityTicket.STATUS_DONE
    return SecurityTicket.STATUS_OPEN


def _priority_from_severity(severity):
    value = (severity or "").strip().lower()
    if value in ("critical", "high"):
        return "P1"
    if value == "medium":
        return "P2"
    return "P3"


def _report_to_alert(report, ticket=None, request=None):
    ticket = ticket or report.security_tickets.order_by("-created_at").first()
    alert_id = ticket.id if ticket else None
    created_at = ticket.created_at if ticket else report.created_at
    updated_at = ticket.updated_at if ticket else report.updated_at
    status = _ticket_status_to_alert_status(ticket.status) if ticket else "pending"
    image_url = None
    if report.image:
        try:
            image_url = request.build_absolute_uri(report.image.url) if request else report.image.url
        except Exception:
            image_url = report.image.url
    reporter_payload = _reporter_payload(report, request=request)

    return {
        "id": alert_id,
        "reportId": str(report.id),
        "type": "alert",
        "title": f"{report.location} · {report.category}" if report.location else report.category,
        "description": report.note or "",
        "latitude": report.latitude,
        "longitude": report.longitude,
        "severity": _severity_from_priority(report.priority),
        "category": _category_for_alert(report.category),
        "reporter": reporter_payload["reporterName"],
        "department": reporter_payload["department"] or "",
        "reporterInfoProtected": reporter_payload["reporterInfoProtected"],
        "reporterPrivacyHint": reporter_payload["reporterPrivacyHint"],
        "imageUrl": image_url,
        "rewardPoints": report.reward_points,
        "rewardMessage": report.reward_message or None,
        "timestamp": report.time.isoformat(),
        "status": status,
        "createdAt": created_at.isoformat() if created_at else None,
        "updatedAt": updated_at.isoformat() if updated_at else None,
        "legacySource": bool(ticket.legacy_source) if ticket else False,
    }


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return None


def _coerce_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_bbox_xyxy(value):
    if isinstance(value, list) and len(value) >= 4:
        left = _coerce_float(value[0])
        top = _coerce_float(value[1])
        right = _coerce_float(value[2])
        bottom = _coerce_float(value[3])
        if None not in (left, top, right, bottom):
            return [left, top, right, bottom]
        return None

    if not isinstance(value, dict):
        return None

    key_groups = (
        ("x1", "y1", "x2", "y2"),
        ("left", "top", "right", "bottom"),
        ("xmin", "ymin", "xmax", "ymax"),
    )
    for left_key, top_key, right_key, bottom_key in key_groups:
        left = _coerce_float(value.get(left_key))
        top = _coerce_float(value.get(top_key))
        right = _coerce_float(value.get(right_key))
        bottom = _coerce_float(value.get(bottom_key))
        if None not in (left, top, right, bottom):
            return [left, top, right, bottom]
    return None


def _standardize_detection_item(item):
    if not isinstance(item, dict):
        return None

    normalized = dict(item)
    label = (
        normalized.get("cls")
        or normalized.get("label")
        or normalized.get("class")
        or normalized.get("name")
    )
    if label not in (None, ""):
        normalized["cls"] = str(label)

    conf = normalized.get("conf")
    if conf is None:
        conf = normalized.get("confidence")
    if conf is None:
        conf = normalized.get("score")
    conf_value = _coerce_float(conf)
    if conf_value is not None:
        normalized["conf"] = conf_value

    bbox = _normalize_bbox_xyxy(
        normalized.get("bbox_xyxy")
        or normalized.get("bbox")
        or normalized.get("box")
        or normalized.get("xyxy")
    )
    if bbox is not None:
        normalized["bbox_xyxy"] = bbox

    strip_bbox = _normalize_bbox_xyxy(
        normalized.get("strip_xyxy")
        or normalized.get("strip_bbox")
        or normalized.get("stripBox")
        or normalized.get("strip")
    )
    if strip_bbox is not None:
        normalized["strip_xyxy"] = strip_bbox

    plate_conf = normalized.get("plate_conf")
    if plate_conf is None:
        plate_conf = normalized.get("plateConfidence")
    plate_conf_value = _coerce_float(plate_conf)
    if plate_conf_value is not None:
        normalized["plate_conf"] = plate_conf_value

    plate_text = normalized.get("plate_text")
    if plate_text in (None, ""):
        plate_text = normalized.get("plateText")
    if plate_text not in (None, ""):
        normalized["plate_text"] = str(plate_text).strip()

    if normalized.get("process_images") is None and normalized.get("processImages") is not None:
        normalized["process_images"] = normalized.get("processImages")

    violation = _coerce_bool(normalized.get("violation"))
    if violation is not None:
        normalized["violation"] = violation

    return normalized


def _compact_process_images(value):
    if not isinstance(value, dict):
        return None
    compact = {}
    for key, entry in value.items():
        if entry in (None, ""):
            continue
        compact[str(key)] = str(entry)
    return compact or None


def _normalize_detection_items(value):
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        normalized = _standardize_detection_item(item)
        if normalized is not None:
            result.append(normalized)
    return result


def _compact_detection_payload(payload):
    if not isinstance(payload, dict):
        return {}

    compact = {}
    for key in ("latency_ms", "latencyMs", "model", "model_name", "modelName"):
        if key in payload:
            compact[key] = payload.get(key)

    vis_image = payload.get("vis_jpeg_base64") or payload.get("visImage")
    if vis_image not in (None, ""):
        compact["vis_jpeg_base64"] = str(vis_image)

    process_images = _compact_process_images(
        payload.get("process_images") if "process_images" in payload else payload.get("processImages")
    )
    if process_images:
        compact["process_images"] = process_images

    detections = []
    for raw in payload.get("detections", []) or []:
        item = _standardize_detection_item(raw)
        if item is None:
            continue
        item.pop("process_images", None)
        detections.append(item)
    compact["detections"] = detections

    violations = []
    for raw in payload.get("violations", []) or []:
        item = _standardize_detection_item(raw)
        if item is None:
            continue
        item.pop("process_images", None)
        violations.append(item)
    compact["violations"] = violations
    return compact


def _sync_report_evidence(report, request=None):
    evidence = list(report.evidence_urls or [])
    if report.image:
        try:
            image_url = request.build_absolute_uri(report.image.url) if request else report.image.url
        except Exception:
            image_url = report.image.url
        if image_url not in evidence:
            evidence.append(image_url)
    if evidence != list(report.evidence_urls or []):
        report.evidence_urls = evidence
        report.save(update_fields=["evidence_urls"])


def _grant_reward_once(report, user, profile):
    if not report.violation or not report.reward_points or profile is None:
        return
    exists = RewardRecord.objects.filter(
        user=user,
        report=report,
        status=RewardRecord.STATUS_GRANTED,
    ).exists()
    if exists:
        return
    RewardRecord.objects.create(
        user=user,
        report=report,
        points=report.reward_points,
        message=report.reward_message or "",
        status=RewardRecord.STATUS_GRANTED,
    )
    profile.reward_points_total = (profile.reward_points_total or 0) + report.reward_points
    profile.save(update_fields=["reward_points_total"])


def _reward_catalog_payload(profile):
    balance = profile.reward_points_total if profile else 0
    items = []
    for item in REWARD_CATALOG:
        items.append({
            "id": item["id"],
            "title": item["title"],
            "description": item["description"],
            "pointsCost": item["pointsCost"],
            "category": item["category"],
            "canRedeem": balance >= item["pointsCost"],
        })
    return {
        "balance": balance,
        "items": items,
    }


def _find_reward_catalog_item(item_id):
    target = (item_id or "").strip()
    for item in REWARD_CATALOG:
        if item["id"] == target:
            return item
    return None


def _create_points_record(*, user, profile, points, message, status=RewardRecord.STATUS_GRANTED, report=None):
    record = RewardRecord.objects.create(
        user=user,
        report=report,
        points=points,
        message=(message or "").strip(),
        status=status,
    )
    profile.reward_points_total = (profile.reward_points_total or 0) + points
    profile.save(update_fields=["reward_points_total"])
    return record


def _ensure_alert_created(report, user, request=None):
    ticket, created = SecurityTicket.objects.get_or_create(
        report=report,
        defaults={
            "channel": SecurityTicket.CHANNEL_WORK_ORDER,
            "status": SecurityTicket.STATUS_OPEN,
            "message": report.note or "检测到疑似违规行为",
        },
    )
    if not ticket.message:
        ticket.message = report.note or "检测到疑似违规行为"
        ticket.save(update_fields=["message", "updated_at"])

    if created:
        alert_payload = _report_to_alert(report, ticket=ticket, request=request)
        _timeline_log(
            EventTimelineLog.STAGE_ALERT,
            "alert_created",
            actor=user.username,
            report=report,
            ticket=ticket,
            payload={
                "alertId": ticket.id,
                "status": ticket.status,
            },
        )
        _push_governance_event(
            event_type="alert_created",
            title="新增校园告警",
            message=alert_payload.get("title") or "检测到疑似违规行为",
            level=RealtimeEvent.LEVEL_WARN,
            source="report",
            report=report,
            ticket=ticket,
            payload=alert_payload,
        )

        if SECURITY_NOTIFY_BASE_URL:
            try:
                requests.post(
                    f"{SECURITY_NOTIFY_BASE_URL}/notify",
                    json={
                        "reportId": str(report.id),
                        "location": report.location,
                        "latitude": report.latitude,
                        "longitude": report.longitude,
                        "category": report.category,
                        "priority": report.priority,
                        "description": alert_payload.get("description") or "",
                    },
                    timeout=5,
                )
            except Exception:
                pass
    return ticket


def _finalize_report_flow(
    report,
    user,
    profile,
    request=None,
    *,
    skip_detection=False,
    latency_ms=None,
    detection_error=None,
    emit_received_when_no_violation=True,
):
    _timeline_log(
        EventTimelineLog.STAGE_DETECT,
        "detection_finished" if not detection_error else "detection_failed",
        actor=user.username,
        report=report,
        payload={
            "skipDetection": bool(skip_detection),
            "violation": bool(report.violation),
            "latencyMs": latency_ms,
            "error": detection_error or None,
        },
    )

    _sync_report_evidence(report, request=request)

    if report.violation:
        _grant_reward_once(report, user, profile)
        _ensure_alert_created(report, user, request=request)
        return

    if emit_received_when_no_violation:
        _push_governance_event(
            event_type="report_received",
            title="收到新上报",
            message=report.note or report.category or "有新的校园事件上报",
            level=RealtimeEvent.LEVEL_INFO,
            source="report",
            report=report,
            payload={"reportId": str(report.id), "violation": False},
        )
    elif detection_error:
        _push_governance_event(
            event_type="report_detection_failed",
            title="检测未完成，待人工复核",
            message=report.note or report.category or "检测服务暂不可用",
            level=RealtimeEvent.LEVEL_WARN,
            source="report",
            report=report,
            payload={"reportId": str(report.id), "error": detection_error},
        )
    else:
        _push_governance_event(
            event_type="report_detection_completed",
            title="举报检测完成",
            message=report.note or report.category or "该上报已完成自动检测",
            level=RealtimeEvent.LEVEL_INFO,
            source="report",
            report=report,
            payload={"reportId": str(report.id), "violation": False},
        )


def _ticket_to_dict(ticket, request=None):
    report = ticket.report
    elapsed_hours = None
    if ticket.created_at:
        elapsed = timezone.now() - ticket.created_at
        elapsed_hours = round(elapsed.total_seconds() / 3600.0, 2)
    sla_hours = 4 if (report and (report.priority or "").upper() == "P1") else 12
    overdue = bool(elapsed_hours is not None and elapsed_hours > sla_hours and ticket.status != SecurityTicket.STATUS_DONE)

    data = {
        "id": ticket.id,
        "reportId": str(ticket.report_id) if ticket.report_id else None,
        "status": _ticket_status_to_alert_status(ticket.status),
        "ticketStatus": ticket.status,
        "channel": ticket.channel,
        "message": ticket.message,
        "assignee": ticket.assignee,
        "externalId": ticket.external_id,
        "legacySource": ticket.legacy_source,
        "createdAt": ticket.created_at.isoformat(),
        "updatedAt": ticket.updated_at.isoformat() if ticket.updated_at else ticket.created_at.isoformat(),
        "resolvedAt": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "elapsedHours": elapsed_hours,
        "slaHours": sla_hours,
        "overdue": overdue,
        "department": (report.department if report else "") or "",
    }
    if report:
        data["alert"] = _report_to_alert(report, ticket=ticket, request=request)
    return data


def _sync_report_status_from_ticket(report, ticket):
    if ticket.status == SecurityTicket.STATUS_DONE:
        report.status = "RESOLVED"
        report.handled_by = ticket.assignee or report.handled_by
        report.handled_at = ticket.resolved_at or timezone.now()
        report.save(update_fields=["status", "handled_by", "handled_at", "updated_at"])
        return
    if ticket.status == SecurityTicket.STATUS_ASSIGNED:
        report.status = "PROCESSING"
        report.handled_by = ticket.assignee or report.handled_by
        report.save(update_fields=["status", "handled_by", "updated_at"])
        return
    report.status = "CONFIRMED" if report.violation else "PENDING_REVIEW"
    report.save(update_fields=["status", "updated_at"])


def _extract_detection_meta(payload):
    if not isinstance(payload, dict):
        return "", None

    candidates = []
    detections = payload.get("detections", []) or []
    if isinstance(detections, list):
        candidates.extend([item for item in detections if isinstance(item, dict)])

    frames = payload.get("frames", []) or []
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            frame_detections = frame.get("detections", []) or []
            if isinstance(frame_detections, list):
                candidates.extend([item for item in frame_detections if isinstance(item, dict)])

    label = ""
    best_conf = None
    for item in candidates:
        if not label:
            label = str(item.get("cls") or item.get("label") or item.get("class") or item.get("name") or "")
        raw_conf = item.get("fusion_score")
        if raw_conf is None:
            raw_conf = item.get("conf")
        if raw_conf is None:
            raw_conf = item.get("confidence")
        if raw_conf is None:
            raw_conf = item.get("score")
        if raw_conf is None:
            raw_conf = item.get("plate_conf")
        if raw_conf is None:
            raw_conf = item.get("plateConfidence")
        try:
            conf = float(raw_conf)
        except (TypeError, ValueError):
            continue
        if conf > 1:
            conf = conf / 100.0
        if best_conf is None or conf > best_conf:
            best_conf = conf
    return label, best_conf


def _extract_plate_meta(payload):
    if not isinstance(payload, dict):
        return "", None
    top_level_text = payload.get("plateText")
    if top_level_text in (None, ""):
        top_level_text = payload.get("plate_text")
    top_level_text = str(top_level_text).strip() if top_level_text not in (None, "") else ""
    top_level_conf = payload.get("plateConfidence")
    if top_level_conf is None:
        top_level_conf = payload.get("plate_conf")
    top_level_conf = _coerce_float(top_level_conf)
    if top_level_conf is not None and top_level_conf > 1:
        top_level_conf = top_level_conf / 100.0

    detections = payload.get("detections", []) or []
    if not isinstance(detections, list):
        return top_level_text, top_level_conf

    best_text = top_level_text
    best_conf = top_level_conf
    for item in detections:
        if not isinstance(item, dict):
            continue
        plate_text = str(item.get("plate_text") or item.get("plateText") or "").strip()
        if not plate_text:
            continue
        raw_conf = item.get("plate_conf")
        if raw_conf is None:
            raw_conf = item.get("plateConfidence")
        conf = _coerce_float(raw_conf)
        if conf is not None and conf > 1:
            conf = conf / 100.0
        if best_conf is None or (conf is not None and conf > best_conf):
            best_text = plate_text
            best_conf = conf
        elif not best_text:
            best_text = plate_text
    return best_text, best_conf


def _run_detection_request(file_bytes, file_name, content_type, detect_type, query_params=None):
    endpoint_map = {
        DetectionRecord.TYPE_IMAGE: ["detect"],
        DetectionRecord.TYPE_BATCH: ["detect"],
        DetectionRecord.TYPE_VIDEO: ["detect_video"],
    }
    endpoints = endpoint_map.get(detect_type, ["detect"])
    last_error = "DETECTION_UNAVAILABLE"
    parsed_data = {}
    request_id = uuid.uuid4().hex[:12]

    retryable_status_codes = {502, 503, 504}
    file_size = len(file_bytes or b"")
    for endpoint in endpoints:
        for attempt in range(2):
            started_at = time.time()
            target_url = f"{DETECTION_BASE_URL}/{endpoint}"
            logger.warning(
                "[detection] request_start id=%s type=%s endpoint=%s attempt=%s file=%s size=%s query=%s",
                request_id,
                detect_type,
                target_url,
                attempt + 1,
                file_name,
                file_size,
                query_params or {},
            )
            try:
                response = requests.post(
                    target_url,
                    params=query_params or None,
                    files={
                        "file": (
                            file_name,
                            file_bytes,
                            content_type or "application/octet-stream",
                        )
                    },
                    timeout=1800,
                )
                elapsed_ms = int((time.time() - started_at) * 1000)
                logger.warning(
                    "[detection] request_done id=%s type=%s endpoint=%s attempt=%s status=%s elapsedMs=%s",
                    request_id,
                    detect_type,
                    target_url,
                    attempt + 1,
                    response.status_code,
                    elapsed_ms,
                )
                if 200 <= response.status_code < 300:
                    try:
                        parsed_data = response.json()
                    except Exception:
                        parsed_data = {"raw": response.text}
                    return True, parsed_data, ""
                last_error = response.text[:500] if response.text else f"HTTP_{response.status_code}"
                logger.warning(
                    "[detection] request_fail id=%s type=%s endpoint=%s attempt=%s status=%s error=%s",
                    request_id,
                    detect_type,
                    target_url,
                    attempt + 1,
                    response.status_code,
                    last_error,
                )
                if response.status_code in retryable_status_codes and attempt == 0:
                    time.sleep(0.6)
                    continue
            except Exception as exc:
                elapsed_ms = int((time.time() - started_at) * 1000)
                last_error = str(exc)
                logger.exception(
                    "[detection] request_exception id=%s type=%s endpoint=%s attempt=%s elapsedMs=%s file=%s size=%s",
                    request_id,
                    detect_type,
                    target_url,
                    attempt + 1,
                    elapsed_ms,
                    file_name,
                    file_size,
                )
                if attempt == 0:
                    time.sleep(0.6)
                    continue
    return False, parsed_data, last_error


def _detection_error_status_code(error_message):
    match = re.search(r"HTTP_(\d{3})", str(error_message or ""))
    if match:
        code = int(match.group(1))
        if 400 <= code <= 599:
            return code
    return 503


def _finalize_detection_record(record, *, success, result_payload=None, error_message="", model_name_hint=""):
    payload = result_payload if isinstance(result_payload, dict) else {}
    label, confidence = _extract_detection_meta(payload)
    plate_text, plate_confidence = _extract_plate_meta(payload)
    result_model = ""
    if isinstance(payload, dict):
        payload = dict(payload)
        raw_output_video_url = payload.get("output_video_url") or payload.get("outputVideoUrl") or ""
        if raw_output_video_url:
            if str(raw_output_video_url).startswith("/"):
                payload["output_video_url"] = f"{DETECTION_BASE_URL.rstrip('/')}{raw_output_video_url}"
            elif not re.match(r"^https?://", str(raw_output_video_url), re.I):
                payload["output_video_url"] = f"{DETECTION_BASE_URL.rstrip('/')}/{str(raw_output_video_url).lstrip('/')}"
        if plate_text:
            payload.setdefault("plateText", plate_text)
        if plate_confidence is not None:
            payload.setdefault("plateConfidence", plate_confidence)
        result_model = (
            payload.get("model")
            or payload.get("modelName")
            or payload.get("model_name")
            or ""
        )

    record.status = DetectionRecord.STATUS_SUCCESS if success else DetectionRecord.STATUS_FAILED
    record.model_name = (result_model or model_name_hint or record.model_name or "").strip()
    record.label = label[:128] if label else ""
    record.confidence = confidence
    record.result_payload = payload if isinstance(payload, dict) else {"raw": str(result_payload)}
    record.error_message = (error_message or "")[:1000] if not success else ""
    record.save(update_fields=["status", "model_name", "label", "confidence", "result_payload", "error_message", "updated_at"])
    return record


def _normalize_detection_media_target(raw_value):
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if value.startswith("/"):
        return f"{DETECTION_BASE_URL.rstrip('/')}{value}"
    if re.match(r"^https?://", value, re.I):
        try:
            parsed = urlparse(value)
            if (parsed.path or "").startswith("/detect_outputs/"):
                suffix = parsed.path or ""
                if parsed.query:
                    suffix = f"{suffix}?{parsed.query}"
                return f"{DETECTION_BASE_URL.rstrip('/')}{suffix}"
        except Exception:
            return value
        return value
    return f"{DETECTION_BASE_URL.rstrip('/')}/{value.lstrip('/')}"


def _is_allowed_detection_media_target(target_url):
    try:
        parsed_target = urlparse(str(target_url or ""))
        parsed_base = urlparse(DETECTION_BASE_URL)
    except Exception:
        return False
    if parsed_target.scheme not in {"http", "https"}:
        return False
    if parsed_target.netloc != parsed_base.netloc:
        return False
    path = parsed_target.path or ""
    return path.startswith("/detect_outputs/")


def _detection_media_candidates(target_url):
    candidates = []
    parsed = urlparse(str(target_url or ""))
    path = parsed.path or ""
    suffix = path
    if parsed.query:
        suffix = f"{suffix}?{parsed.query}"
    for base in (
        DETECTION_BASE_URL,
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ):
        candidate = _normalize_detection_media_target(f"{str(base).rstrip('/')}{suffix}")
        if candidate and candidate not in candidates and _is_allowed_detection_media_target(candidate):
            candidates.append(candidate)
    return candidates



def _create_pending_detection_record(uploaded_file, detect_type, user=None, batch_id="", model_name_hint="", job_meta=None):
    file_name = os.path.basename(uploaded_file.name or "upload.bin")
    _file_root, file_ext = os.path.splitext(file_name)
    file_ext = (file_ext or "").lower()
    file_bytes = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    storage_name = f"{uuid.uuid4().hex}{file_ext}"
    payload = {}
    if isinstance(job_meta, dict):
        payload["job"] = job_meta

    return DetectionRecord.objects.create(
        created_by=user,
        detect_type=detect_type,
        status=DetectionRecord.STATUS_PENDING,
        batch_id=batch_id or "",
        source_file=ContentFile(file_bytes, name=storage_name),
        original_name=file_name,
        model_name=(model_name_hint or "").strip(),
        label="",
        confidence=None,
        result_payload=payload,
        error_message="",
        file_size=len(file_bytes),
        file_ext=file_ext.lstrip("."),
    )



def _process_video_detection_record(record_id, *, query_params=None, model_name_hint=""):
    try:
        logger.warning("[video-async] start record_id=%s query=%s", record_id, query_params or {})
        record = DetectionRecord.objects.filter(id=record_id, detect_type=DetectionRecord.TYPE_VIDEO).first()
        if not record or not record.source_file:
            logger.warning("[video-async] missing record or source_file record_id=%s", record_id)
            return
        with record.source_file.open("rb") as fp:
            file_bytes = fp.read()
        success, result_payload, error_message = _run_detection_request(
            file_bytes=file_bytes,
            file_name=record.original_name or f"video-{record.id}.mp4",
            content_type=mimetypes.guess_type(record.original_name or "")[0] or "application/octet-stream",
            detect_type=DetectionRecord.TYPE_VIDEO,
            query_params=query_params,
        )
        logger.warning(
            "[video-async] detection_done record_id=%s success=%s error=%s payload_keys=%s",
            record_id,
            success,
            (error_message or "")[:200],
            sorted(list(result_payload.keys())) if isinstance(result_payload, dict) else type(result_payload).__name__,
        )
        record = _finalize_detection_record(
            record,
            success=success,
            result_payload=result_payload,
            error_message=error_message,
            model_name_hint=model_name_hint,
        )
        payload = record.result_payload if isinstance(record.result_payload, dict) else {}
        logger.warning(
            "[video-async] saved record_id=%s status=%s output_video_url=%s result_keys=%s",
            record_id,
            record.status,
            payload.get("output_video_url") or payload.get("outputVideoUrl") or "",
            sorted(list(payload.keys())) if isinstance(payload, dict) else [],
        )
        _push_governance_event(
            event_type="detection_video_finished",
            title="视频检测完成",
            message=f"{record.original_name or 'video'} · {record.label or record.status}",
            level=RealtimeEvent.LEVEL_INFO if record.status == DetectionRecord.STATUS_SUCCESS else RealtimeEvent.LEVEL_WARN,
            source="detection",
            detection=record,
            payload=_detection_record_to_dict(record),
        )
    except Exception as exc:
        logger.exception("[video-async] process failed record_id=%s", record_id)
        record = DetectionRecord.objects.filter(id=record_id, detect_type=DetectionRecord.TYPE_VIDEO).first()
        if not record:
            return
        record.status = DetectionRecord.STATUS_FAILED
        record.error_message = str(exc)[:1000]
        record.save(update_fields=["status", "error_message", "updated_at"])
        logger.warning("[video-async] marked failed record_id=%s error=%s", record_id, record.error_message)
        _push_governance_event(
            event_type="detection_video_finished",
            title="视频检测失败",
            message=record.error_message or "video detection failed",
            level=RealtimeEvent.LEVEL_WARN,
            source="detection",
            detection=record,
            payload=_detection_record_to_dict(record),
        )



def _save_detection_record(uploaded_file, detect_type, user=None, batch_id="", model_name_hint="", query_params=None):
    file_name = os.path.basename(uploaded_file.name or "upload.bin")
    file_root, file_ext = os.path.splitext(file_name)
    file_ext = (file_ext or "").lower()
    file_bytes = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)

    success, result_payload, error_message = _run_detection_request(
        file_bytes=file_bytes,
        file_name=file_name,
        content_type=getattr(uploaded_file, "content_type", None),
        detect_type=detect_type,
        query_params=query_params,
    )

    storage_name = f"{uuid.uuid4().hex}{file_ext}"
    record = DetectionRecord.objects.create(
        created_by=user,
        detect_type=detect_type,
        status=DetectionRecord.STATUS_PENDING,
        batch_id=batch_id or "",
        source_file=ContentFile(file_bytes, name=storage_name),
        original_name=file_name,
        model_name=(model_name_hint or "").strip(),
        label="",
        confidence=None,
        result_payload={},
        error_message="",
        file_size=len(file_bytes),
        file_ext=file_ext.lstrip("."),
    )
    return _finalize_detection_record(
        record,
        success=success,
        result_payload=result_payload,
        error_message=error_message,
        model_name_hint=model_name_hint,
    )


def _create_report_detection_record(report, *, user=None, result_payload=None, error_message=""):
    if report is None or not report.image:
        return None

    file_name = os.path.basename(report.image.name or "report.jpg")
    _file_root, file_ext = os.path.splitext(file_name)
    file_ext = (file_ext or ".jpg").lower()

    with report.image.open("rb") as image_fp:
        file_bytes = image_fp.read()

    payload = result_payload if isinstance(result_payload, dict) else {}
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("reportId", str(report.id))

    label, confidence = _extract_detection_meta(payload)
    plate_text, plate_confidence = _extract_plate_meta(payload)
    if isinstance(payload, dict):
        if plate_text:
            payload.setdefault("plateText", plate_text)
        if plate_confidence is not None:
            payload.setdefault("plateConfidence", plate_confidence)
    result_model = ""
    if isinstance(payload, dict):
        result_model = (
            payload.get("model")
            or payload.get("modelName")
            or payload.get("model_name")
            or ""
        )

    storage_name = f"{uuid.uuid4().hex}{file_ext}"
    return DetectionRecord.objects.create(
        created_by=user,
        detect_type=DetectionRecord.TYPE_IMAGE,
        status=DetectionRecord.STATUS_SUCCESS if not error_message else DetectionRecord.STATUS_FAILED,
        batch_id=str(report.id),
        source_file=ContentFile(file_bytes, name=storage_name),
        original_name=file_name,
        model_name=(result_model or "").strip(),
        label=label[:128] if label else "",
        confidence=confidence,
        result_payload=payload if isinstance(payload, dict) else {"reportId": str(report.id)},
        error_message=error_message[:1000] if error_message else "",
        file_size=len(file_bytes),
        file_ext=file_ext,
    )


def _detection_record_to_dict(record, request=None):
    file_url = None
    if record.source_file:
        try:
            file_url = request.build_absolute_uri(record.source_file.url) if request else record.source_file.url
        except Exception:
            file_url = record.source_file.url
    payload = record.result_payload or {}
    report_id = None
    if isinstance(payload, dict):
        raw_report_id = payload.get("reportId") or payload.get("report_id")
        report_id = str(raw_report_id) if raw_report_id else None
    if report_id is None and record.batch_id:
        if re.fullmatch(r"[0-9a-fA-F-]{32,36}", record.batch_id or ""):
            report_id = record.batch_id
    plate_text = None
    plate_confidence = None
    if isinstance(payload, dict):
        plate_text, plate_confidence = _extract_plate_meta(payload)
        if not plate_text:
            raw_plate_text = payload.get("plateText") or payload.get("plate_text")
            plate_text = str(raw_plate_text).strip() if raw_plate_text not in (None, "") else None
        raw_plate_conf = payload.get("plateConfidence") if payload.get("plateConfidence") is not None else payload.get("plate_conf")
        if plate_confidence is None:
            plate_confidence = _coerce_float(raw_plate_conf)
    return {
        "id": record.id,
        "detectType": record.detect_type,
        "status": record.status,
        "batchId": record.batch_id or None,
        "reportId": report_id,
        "fileUrl": file_url,
        "originalName": record.original_name,
        "modelName": record.model_name,
        "label": record.label,
        "confidence": record.confidence,
        "plateText": plate_text or None,
        "plateConfidence": plate_confidence,
        "fileSize": record.file_size,
        "fileExt": record.file_ext,
        "errorMessage": record.error_message or None,
        "resultPayload": record.result_payload or {},
        "createdBy": record.created_by.username if record.created_by else "anonymous",
        "createdAt": record.created_at.isoformat(),
        "updatedAt": record.updated_at.isoformat(),
    }
