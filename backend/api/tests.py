from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import ApiToken, DetectionRecord, Report, SecurityTicket, UserProfile


class PermissionMiddlewareTests(TestCase):
    def setUp(self):
        self._old_dashboard_key = views.DASHBOARD_API_KEY
        views.DASHBOARD_API_KEY = "test-dashboard-key"

        self.student_user = User.objects.create_user(username="student", password="pass12345")
        self.admin_user = User.objects.create_user(username="admin", password="pass12345")

        self.student_profile = UserProfile.objects.create(user=self.student_user, role=UserProfile.ROLE_STUDENT)
        self.admin_profile = UserProfile.objects.create(user=self.admin_user, role=UserProfile.ROLE_ADMIN)

        self.student_token = ApiToken.objects.create(user=self.student_user)
        self.admin_token = ApiToken.objects.create(user=self.admin_user)

    def tearDown(self):
        views.DASHBOARD_API_KEY = self._old_dashboard_key

    def _headers(self, token=None, dashboard=False):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        if dashboard:
            headers["HTTP_X_DASHBOARD_KEY"] = "test-dashboard-key"
        return headers

    def test_auth_required_for_report_upload(self):
        resp = self.client.post(reverse("report_upload"), data={"location": "A 区", "time": timezone.now().isoformat()})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json().get("error"), "AUTH_REQUIRED")

    def test_dashboard_key_does_not_bypass_non_dashboard_endpoint(self):
        resp = self.client.post(
            reverse("report_upload"),
            data={"location": "A 区", "time": timezone.now().isoformat()},
            **self._headers(dashboard=True),
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json().get("error"), "AUTH_REQUIRED")

    def test_dashboard_key_can_access_dashboard_allowed_endpoint(self):
        resp = self.client.get(reverse("alert_list"), **self._headers(dashboard=True))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

    def test_student_forbidden_on_admin_endpoint(self):
        resp = self.client.get(reverse("ticket_list"), **self._headers(token=self.student_token.key))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "FORBIDDEN")

    def test_admin_can_access_admin_endpoint(self):
        resp = self.client.get(reverse("ticket_list"), **self._headers(token=self.admin_token.key))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

    def test_student_cannot_access_other_user_report_detail(self):
        report = Report.objects.create(
            created_by=self.admin_user,
            location="教学楼",
            time=timezone.now(),
            note="test",
            status="SUBMITTED",
            category="test",
            priority="P2",
            reporter_name="admin",
            reporter_role="admin",
        )

        resp = self.client.get(
            reverse("report_detail", kwargs={"report_id": report.id}),
            **self._headers(token=self.student_token.key),
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "FORBIDDEN")

    def test_admin_can_access_any_report_detail(self):
        report = Report.objects.create(
            created_by=self.student_user,
            location="操场",
            time=timezone.now(),
            note="test",
            status="SUBMITTED",
            category="test",
            priority="P2",
            reporter_name="student",
            reporter_role="student",
        )

        resp = self.client.get(
            reverse("report_detail", kwargs={"report_id": report.id}),
            **self._headers(token=self.admin_token.key),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

    def test_dashboard_key_can_access_detection_records(self):
        report = Report.objects.create(
            created_by=self.student_user,
            location="图书馆",
            time=timezone.now(),
            note="test",
            status="SUBMITTED",
            category="test",
            priority="P2",
            reporter_name="student",
            reporter_role="student",
        )
        DetectionRecord.objects.create(
            created_by=self.student_user,
            detect_type=DetectionRecord.TYPE_IMAGE,
            status=DetectionRecord.STATUS_SUCCESS,
            batch_id=str(report.id),
            source_file=SimpleUploadedFile("detect.jpg", b"test-image-bytes", content_type="image/jpeg"),
            original_name="detect.jpg",
            model_name="unit-test",
            label="违规停车",
            confidence=0.91,
            result_payload={"plateText": "陕A12345", "reportId": str(report.id)},
            file_size=16,
            file_ext=".jpg",
        )

        resp = self.client.get(
            reverse("detection_records"),
            {"reportId": str(report.id), "page": 1, "pageSize": 1},
            **self._headers(dashboard=True),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        self.assertEqual(resp.json().get("total"), 1)

    def test_dashboard_key_can_access_detection_plate_summary(self):
        report = Report.objects.create(
            created_by=self.student_user,
            location="北门",
            time=timezone.now(),
            note="test",
            status="SUBMITTED",
            category="test",
            priority="P2",
            reporter_name="student",
            reporter_role="student",
        )
        DetectionRecord.objects.create(
            created_by=self.student_user,
            detect_type=DetectionRecord.TYPE_IMAGE,
            status=DetectionRecord.STATUS_SUCCESS,
            batch_id=str(report.id),
            source_file=SimpleUploadedFile("plate.jpg", b"test-plate-bytes", content_type="image/jpeg"),
            original_name="plate.jpg",
            model_name="unit-test",
            label="车辆识别",
            confidence=0.95,
            result_payload={"plateText": "陕A12345", "reportId": str(report.id)},
            file_size=16,
            file_ext=".jpg",
        )

        resp = self.client.get(
            reverse("detection_plate_summary"),
            {"plateText": "陕A12345"},
            **self._headers(dashboard=True),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))
        self.assertEqual(resp.json().get("plateText"), "陕A12345")
