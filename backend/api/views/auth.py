"""Authentication and profile endpoints."""

import random

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..models import ApiToken, EmailVerification, UserProfile
from .common import (
    EMAIL_SENDER,
    _get_request_data,
    _get_token,
    _profile_to_dict,
    require_admin_view,
    require_auth_view,
)

def _normalize_role(value):
    role = (value or "").strip().lower()
    if role in ("student", "admin"):
        return role
    return None


def _resolve_login_user(account):
    identifier = (account or "").strip()
    if not identifier:
        return None

    return (
        User.objects.filter(username=identifier).first()
        or User.objects.filter(profile__phone=identifier).first()
        or User.objects.filter(profile__student_id=identifier).first()
        or User.objects.filter(profile__staff_id=identifier).first()
    )


def _role_matches_profile(profile, role):
    return role is None or profile.role == role


def _user_to_dict(user, profile):
    return {
        "id": user.id,
        "username": user.username,
        "profile": _profile_to_dict(profile),
    }


def _generate_code():
    return f"{random.randint(0, 999999):06d}"


def _email_backend_ready():
    return bool(os.environ.get("EMAIL_HOST"))


def _verify_email_code(email, code, purpose):
    record = (
        EmailVerification.objects.filter(
            email=email,
            purpose=purpose,
            used=False,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    if record is None:
        return False
    if record.code != code:
        return False
    record.used = True
    record.save(update_fields=["used"])
    return True


def _build_auth_response(user, profile):
    token = ApiToken.objects.create(user=user)
    return JsonResponse({
        "success": True,
        "token": token.key,
        "user": _user_to_dict(user, profile),
    })


@csrf_exempt
@require_http_methods(["POST"])
def auth_register(request):
    data = _get_request_data(request)
    username = (data.get("username") or data.get("account") or "").strip()
    password = (data.get("password") or "").strip()
    role = _normalize_role(data.get("role"))
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not username or not password or not role or not email:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    if User.objects.filter(username=username).exists():
        return JsonResponse({"success": False, "error": "ACCOUNT_EXISTS"}, status=409)

    if not code:
        return JsonResponse({"success": False, "error": "CODE_REQUIRED"}, status=400)
    if not _verify_email_code(email, code, EmailVerification.PURPOSE_REGISTER):
        return JsonResponse({"success": False, "error": "CODE_INVALID"}, status=400)

    user = User.objects.create_user(
        username=username,
        password=password,
        email=email,
    )

    profile = UserProfile.objects.create(
        user=user,
        role=role,
        full_name=(data.get("fullName") or data.get("name") or "").strip(),
        phone=(data.get("phone") or "").strip(),
        student_id=(data.get("studentId") or "").strip(),
        staff_id=(data.get("staffId") or "").strip(),
        department=(data.get("department") or "").strip(),
    )

    token = ApiToken.objects.create(user=user)

    return JsonResponse({
        "success": True,
        "token": token.key,
        "user": _user_to_dict(user, profile),
    })


@csrf_exempt
@require_http_methods(["POST"])
def auth_login(request):
    data = _get_request_data(request)
    account = (data.get("username") or data.get("account") or "").strip()
    password = (data.get("password") or "").strip()
    role = _normalize_role(data.get("role"))

    if not account or not password:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    login_user = _resolve_login_user(account)
    if login_user is None:
        return JsonResponse({"success": False, "error": "ACCOUNT_NOT_FOUND"}, status=404)

    user = authenticate(request, username=login_user.username, password=password)
    if user is None:
        return JsonResponse({"success": False, "error": "INVALID_PASSWORD"}, status=401)

    profile = getattr(user, "profile", None)
    if profile is None:
        return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)

    if not _role_matches_profile(profile, role):
        return JsonResponse({"success": False, "error": "ROLE_MISMATCH"}, status=403)

    return _build_auth_response(user, profile)


@csrf_exempt
@require_http_methods(["POST"])
def auth_login_code(request):
    data = _get_request_data(request)
    account = (data.get("username") or data.get("account") or "").strip()
    code = (data.get("code") or "").strip()
    role = _normalize_role(data.get("role"))

    if not account or not code:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    user = _resolve_login_user(account)
    if user is None:
        return JsonResponse({"success": False, "error": "ACCOUNT_NOT_FOUND"}, status=404)

    profile = getattr(user, "profile", None)
    if profile is None:
        return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)
    if not _role_matches_profile(profile, role):
        return JsonResponse({"success": False, "error": "ROLE_MISMATCH"}, status=403)

    email = (user.email or "").strip()
    if not email:
        return JsonResponse({"success": False, "error": "EMAIL_NOT_BOUND"}, status=400)
    if not _verify_email_code(email, code, EmailVerification.PURPOSE_LOGIN):
        return JsonResponse({"success": False, "error": "CODE_INVALID"}, status=400)

    return _build_auth_response(user, profile)


@require_auth_view()
@require_http_methods(["GET"])
def auth_me(request):
    user, profile = getattr(request, "auth")
    return JsonResponse({
        "success": True,
        "user": _user_to_dict(user, profile),
    })


@require_admin_view()
@require_http_methods(["GET"])
def admin_user_search(request):
    _user, _profile = getattr(request, "auth")

    keyword = (request.GET.get("q") or request.GET.get("keyword") or "").strip()
    role = _normalize_role(request.GET.get("role"))
    department = (request.GET.get("department") or "").strip()
    if not keyword:
        return JsonResponse({"success": True, "users": []})

    queryset = User.objects.select_related("profile").filter(
        Q(username__icontains=keyword)
        | Q(profile__full_name__icontains=keyword)
        | Q(profile__student_id__icontains=keyword)
        | Q(profile__staff_id__icontains=keyword)
        | Q(profile__department__icontains=keyword)
    )
    if role:
        queryset = queryset.filter(profile__role=role)
    if department:
        queryset = queryset.filter(profile__department__icontains=department)
    queryset = queryset.order_by("username")[:12]

    items = []
    for row in queryset:
        profile = getattr(row, "profile", None)
        if profile is None:
            continue
        items.append({
            "id": row.id,
            "username": row.username,
            "fullName": profile.full_name or "",
            "studentId": profile.student_id or "",
            "staffId": profile.staff_id or "",
            "department": profile.department or "",
            "role": profile.role,
            "rewardPointsTotal": profile.reward_points_total,
        })
    return JsonResponse({"success": True, "users": items})


@csrf_exempt
@require_auth_view()
@require_http_methods(["PATCH", "POST"])
def auth_update(request):
    user, profile = getattr(request, "auth")
    data = _get_request_data(request)

    if profile is None:
        return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)

    profile.full_name = (data.get("fullName") or profile.full_name or "").strip()
    profile.phone = (data.get("phone") or profile.phone or "").strip()
    profile.student_id = (data.get("studentId") or profile.student_id or "").strip()
    profile.staff_id = (data.get("staffId") or profile.staff_id or "").strip()
    profile.department = (data.get("department") or profile.department or "").strip()
    profile.save()

    return JsonResponse({
        "success": True,
        "user": _user_to_dict(user, profile),
    })


@csrf_exempt
@require_http_methods(["POST"])
def auth_logout(request):
    token = _get_token(request)
    if token:
        ApiToken.objects.filter(key=token).delete()
    return JsonResponse({"success": True})


@csrf_exempt
@require_http_methods(["POST"])
def email_send(request):
    data = _get_request_data(request)
    email = (data.get("email") or "").strip()
    purpose = (data.get("purpose") or EmailVerification.PURPOSE_REGISTER).strip()
    account = (data.get("account") or data.get("username") or "").strip()
    role = _normalize_role(data.get("role"))
    if purpose not in dict(EmailVerification.PURPOSE_CHOICES):
        purpose = EmailVerification.PURPOSE_REGISTER

    if purpose == EmailVerification.PURPOSE_LOGIN:
        if not account:
            return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)
        login_user = _resolve_login_user(account)
        if login_user is None:
            return JsonResponse({"success": False, "error": "ACCOUNT_NOT_FOUND"}, status=404)
        profile = getattr(login_user, "profile", None)
        if profile is None:
            return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)
        if not _role_matches_profile(profile, role):
            return JsonResponse({"success": False, "error": "ROLE_MISMATCH"}, status=403)
        email = (login_user.email or "").strip()
        if not email:
            return JsonResponse({"success": False, "error": "EMAIL_NOT_BOUND"}, status=400)
    elif purpose == EmailVerification.PURPOSE_RESET_PASSWORD:
        if not account:
            return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)
        reset_user = _resolve_login_user(account)
        if reset_user is None:
            return JsonResponse({"success": False, "error": "ACCOUNT_NOT_FOUND"}, status=404)
        registered_email = (reset_user.email or "").strip().lower()
        if not registered_email or registered_email != email.lower():
            return JsonResponse(
                {"success": False, "error": "ACCOUNT_EMAIL_MISMATCH"},
                status=400,
            )
    elif not email:
        return JsonResponse({"success": False, "error": "INVALID_EMAIL"}, status=400)

    code = _generate_code()
    expires_at = timezone.now() + timezone.timedelta(minutes=5)
    EmailVerification.objects.create(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=expires_at,
    )

    if _email_backend_ready():
        try:
            send_mail(
                subject="验证码",
                message=f"您的验证码是：{code}，5分钟内有效。",
                from_email=EMAIL_SENDER or None,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception as exc:
            return JsonResponse(
                {"success": False, "error": "EMAIL_SEND_FAILED", "detail": str(exc)},
                status=500,
            )
        return JsonResponse({"success": True})

    # 寮€鍙戞ā寮忥細鏈厤缃偖绠辨椂鐩存帴杩斿洖楠岃瘉鐮?
    return JsonResponse({"success": True, "devCode": code})


@csrf_exempt
@require_http_methods(["POST"])
def auth_password_reset(request):
    data = _get_request_data(request)
    account = (data.get("account") or data.get("username") or "").strip()
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    new_password = (
        data.get("newPassword")
        or data.get("password")
        or ""
    ).strip()

    if not account or not email or not code or not new_password:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)

    user = _resolve_login_user(account)
    if user is None:
        return JsonResponse({"success": False, "error": "ACCOUNT_NOT_FOUND"}, status=404)

    registered_email = (user.email or "").strip().lower()
    if not registered_email or registered_email != email.lower():
        return JsonResponse(
            {"success": False, "error": "ACCOUNT_EMAIL_MISMATCH"},
            status=400,
        )

    if not _verify_email_code(email, code, EmailVerification.PURPOSE_RESET_PASSWORD):
        return JsonResponse({"success": False, "error": "CODE_INVALID"}, status=400)

    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        detail = exc.messages[0] if exc.messages else "PASSWORD_INVALID"
        return JsonResponse(
            {"success": False, "error": "PASSWORD_INVALID", "detail": detail},
            status=400,
        )

    user.set_password(new_password)
    user.save(update_fields=["password"])
    ApiToken.objects.filter(user=user).delete()
    return JsonResponse({"success": True})


@require_auth_view()
@require_http_methods(["GET"])
def reward_list(request):
    user, profile = getattr(request, "auth")
    queryset = RewardRecord.objects.order_by("-created_at")
    report_id = (request.GET.get("reportId") or request.GET.get("report_id") or "").strip()
    if profile and profile.role == UserProfile.ROLE_ADMIN:
        if report_id:
            queryset = queryset.filter(report_id=report_id)
    else:
        queryset = queryset.filter(user=user)
        if report_id:
            queryset = queryset.filter(report_id=report_id)
    rewards = queryset
    data = []
    for record in rewards:
        kind = "reward" if record.points > 0 else "penalty"
        if record.points < 0 and "兑换" in (record.message or ""):
            kind = "redeem"
        data.append({
            "id": record.id,
            "reportId": str(record.report_id) if record.report_id else None,
            "points": record.points,
            "message": record.message,
            "status": record.status,
            "kind": kind,
            "createdAt": record.created_at.isoformat(),
        })
    return JsonResponse({
        "success": True,
        "balance": profile.reward_points_total if profile else 0,
        "rewards": data,
    })


@require_auth_view()
@require_http_methods(["GET"])
def reward_catalog(request):
    _user, profile = getattr(request, "auth")
    return JsonResponse({
        "success": True,
        "catalog": _reward_catalog_payload(profile),
    })


@csrf_exempt
@require_auth_view()
@require_http_methods(["POST"])
def reward_redeem(request):
    user, profile = getattr(request, "auth")
    if profile is None:
        return JsonResponse({"success": False, "error": "PROFILE_MISSING"}, status=400)

    data = _get_request_data(request)
    item_id = (data.get("itemId") or "").strip()
    quantity = _parse_int(data.get("quantity")) or 1
    if quantity < 1 or quantity > 20:
        return JsonResponse({"success": False, "error": "INVALID_QUANTITY"}, status=400)

    item = _find_reward_catalog_item(item_id)
    if item is None:
        return JsonResponse({"success": False, "error": "INVALID_ITEM"}, status=404)

    total_cost = item["pointsCost"] * quantity
    balance = profile.reward_points_total or 0
    if balance < total_cost:
        return JsonResponse({
            "success": False,
            "error": "INSUFFICIENT_POINTS",
            "balance": balance,
            "requiredPoints": total_cost,
        }, status=400)

    record = _create_points_record(
        user=user,
        profile=profile,
        points=-total_cost,
        message=f"兑换：{item['title']} x{quantity}",
    )
    _push_governance_event(
        event_type="reward_redeemed",
        title="积分兑换完成",
        message=f"{user.username} 已兑换 {item['title']} x{quantity}",
        level=RealtimeEvent.LEVEL_INFO,
        source="reward",
        payload={"userId": user.id, "itemId": item_id, "quantity": quantity, "points": -total_cost},
    )
    return JsonResponse({
        "success": True,
        "balance": profile.reward_points_total,
        "record": {
            "id": record.id,
            "reportId": None,
            "points": record.points,
            "message": record.message,
            "status": record.status,
            "kind": "redeem",
            "createdAt": record.created_at.isoformat(),
        },
    })


@csrf_exempt
@require_admin_view()
@require_http_methods(["POST"])
def reward_adjust(request):
    admin_user, admin_profile = getattr(request, "auth")
    data = _get_request_data(request)

    target = (data.get("target") or "").strip()
    points = _parse_int(data.get("points"))
    message = (data.get("message") or "").strip()
    report_id = (data.get("reportId") or "").strip()
    if not target or points is None or not message:
        return JsonResponse({"success": False, "error": "INVALID_INPUT"}, status=400)
    if points == 0:
        return JsonResponse({"success": False, "error": "INVALID_POINTS"}, status=400)

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

    report = None
    if report_id:
        report = Report.objects.filter(id=report_id).first()

    record = _create_points_record(
        user=target_user,
        profile=target_profile,
        points=points,
        message=message,
        status=RewardRecord.STATUS_GRANTED if points > 0 else RewardRecord.STATUS_REVOKED,
        report=report,
    )
    _timeline_log(
        EventTimelineLog.STAGE_PROCESS,
        "reward_adjusted",
        actor=admin_profile.full_name or admin_user.username,
        report=report,
        payload={"target": target_user.username, "points": points, "message": message},
    )
    _push_governance_event(
        event_type="reward_adjusted",
        title="积分账户已调整",
        message=f"{target_user.username} 积分变动 {points:+d}",
        level=RealtimeEvent.LEVEL_WARN if points < 0 else RealtimeEvent.LEVEL_INFO,
        source="reward",
        report=report,
        payload={"target": target_user.username, "points": points, "recordId": record.id},
    )
    return JsonResponse({
        "success": True,
        "balance": target_profile.reward_points_total,
        "targetUser": {
            "username": target_user.username,
            "fullName": target_profile.full_name or "",
            "studentId": target_profile.student_id or "",
            "staffId": target_profile.staff_id or "",
        },
        "record": {
            "id": record.id,
            "reportId": str(report.id) if report else None,
            "points": record.points,
            "message": record.message,
            "status": record.status,
            "kind": "reward" if record.points > 0 else "penalty",
            "createdAt": record.created_at.isoformat(),
        },
    })
