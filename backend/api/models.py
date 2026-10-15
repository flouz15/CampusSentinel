import uuid

from django.contrib.auth.models import User
from django.db import models


class Report(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reports',
    )
    location = models.CharField(max_length=255, blank=True)
    time = models.DateTimeField()
    note = models.TextField(blank=True)
    status = models.CharField(max_length=32, default='PENDING_REVIEW')
    image = models.ImageField(upload_to='reports/', null=True, blank=True)
    category = models.CharField(max_length=64, default='未分类')
    priority = models.CharField(max_length=8, default='P2')
    reporter_name = models.CharField(max_length=64, default='匿名')
    reporter_role = models.CharField(max_length=32, default='学生')
    reporter_contact = models.CharField(max_length=64, blank=True)
    department = models.CharField(max_length=64, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    confidence = models.IntegerField(null=True, blank=True)
    evidence_urls = models.JSONField(default=list, blank=True)
    handled_by = models.CharField(max_length=64, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    violation = models.BooleanField(null=True, blank=True)
    reward_points = models.IntegerField(null=True, blank=True)
    reward_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-time']


class UserProfile(models.Model):
    ROLE_STUDENT = 'student'
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_STUDENT, 'student'),
        (ROLE_ADMIN, 'admin'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    full_name = models.CharField(max_length=64, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    student_id = models.CharField(max_length=32, blank=True)
    staff_id = models.CharField(max_length=32, blank=True)
    department = models.CharField(max_length=64, blank=True)
    reward_points_total = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.user.username} ({self.role})'


class ApiToken(models.Model):
    key = models.CharField(max_length=64, primary_key=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='api_tokens')
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = uuid.uuid4().hex
        super().save(*args, **kwargs)


class RewardRecord(models.Model):
    STATUS_GRANTED = 'granted'
    STATUS_REVOKED = 'revoked'
    STATUS_PENDING = 'pending'

    STATUS_CHOICES = [
        (STATUS_GRANTED, 'granted'),
        (STATUS_REVOKED, 'revoked'),
        (STATUS_PENDING, 'pending'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rewards')
    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reward_records',
    )
    points = models.IntegerField()
    message = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_GRANTED)
    created_at = models.DateTimeField(auto_now_add=True)


class SecurityTicket(models.Model):
    STATUS_OPEN = 'open'
    STATUS_ASSIGNED = 'assigned'
    STATUS_DONE = 'done'

    STATUS_CHOICES = [
        (STATUS_OPEN, 'open'),
        (STATUS_ASSIGNED, 'assigned'),
        (STATUS_DONE, 'done'),
    ]

    CHANNEL_SMS = 'sms'
    CHANNEL_WORK_ORDER = 'work_order'
    CHANNEL_OTHER = 'other'

    CHANNEL_CHOICES = [
        (CHANNEL_SMS, 'sms'),
        (CHANNEL_WORK_ORDER, 'work_order'),
        (CHANNEL_OTHER, 'other'),
    ]

    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='security_tickets',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    channel = models.CharField(max_length=16, choices=CHANNEL_CHOICES, default=CHANNEL_WORK_ORDER)
    message = models.TextField(blank=True)
    assignee = models.CharField(max_length=64, blank=True)
    external_id = models.CharField(max_length=64, blank=True)
    legacy_source = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)


class EmailVerification(models.Model):
    PURPOSE_REGISTER = 'register'
    PURPOSE_LOGIN = 'login'
    PURPOSE_RESET_PASSWORD = 'reset_password'
    PURPOSE_CHOICES = [
        (PURPOSE_REGISTER, 'register'),
        (PURPOSE_LOGIN, 'login'),
        (PURPOSE_RESET_PASSWORD, 'reset_password'),
    ]

    email = models.EmailField()
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=16, choices=PURPOSE_CHOICES, default=PURPOSE_REGISTER)
    used = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class ContactMessage(models.Model):
    STATUS_NEW = 'new'
    STATUS_READ = 'read'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [
        (STATUS_NEW, 'new'),
        (STATUS_READ, 'read'),
        (STATUS_CLOSED, 'closed'),
    ]

    name = models.CharField(max_length=64)
    email = models.EmailField()
    phone = models.CharField(max_length=32, blank=True)
    topic = models.CharField(max_length=64, blank=True)
    message = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_NEW)
    created_at = models.DateTimeField(auto_now_add=True)


class DetectionRecord(models.Model):
    TYPE_IMAGE = 'image'
    TYPE_BATCH = 'batch'
    TYPE_VIDEO = 'video'
    TYPE_CHOICES = [
        (TYPE_IMAGE, 'image'),
        (TYPE_BATCH, 'batch'),
        (TYPE_VIDEO, 'video'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'pending'),
        (STATUS_SUCCESS, 'success'),
        (STATUS_FAILED, 'failed'),
    ]

    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='detection_records',
    )
    detect_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    batch_id = models.CharField(max_length=64, blank=True)
    source_file = models.FileField(upload_to='detections/%Y/%m/%d/')
    original_name = models.CharField(max_length=255, blank=True)
    model_name = models.CharField(max_length=64, blank=True)
    label = models.CharField(max_length=128, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    file_size = models.BigIntegerField(default=0)
    file_ext = models.CharField(max_length=16, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class EventTimelineLog(models.Model):
    STAGE_REPORT = "report"
    STAGE_DETECT = "detect"
    STAGE_ALERT = "alert"
    STAGE_DISPATCH = "dispatch"
    STAGE_PROCESS = "process"
    STAGE_RESOLVED = "resolved"
    STAGE_REPLAY = "replay"

    STAGE_CHOICES = [
        (STAGE_REPORT, "report"),
        (STAGE_DETECT, "detect"),
        (STAGE_ALERT, "alert"),
        (STAGE_DISPATCH, "dispatch"),
        (STAGE_PROCESS, "process"),
        (STAGE_RESOLVED, "resolved"),
        (STAGE_REPLAY, "replay"),
    ]

    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timeline_logs",
    )
    ticket = models.ForeignKey(
        SecurityTicket,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timeline_logs",
    )
    stage = models.CharField(max_length=16, choices=STAGE_CHOICES, default=STAGE_REPORT)
    action = models.CharField(max_length=64)
    actor = models.CharField(max_length=64, blank=True)
    payload_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class RealtimeEvent(models.Model):
    LEVEL_INFO = "info"
    LEVEL_WARN = "warn"
    LEVEL_CRITICAL = "critical"
    LEVEL_CHOICES = [
        (LEVEL_INFO, "info"),
        (LEVEL_WARN, "warn"),
        (LEVEL_CRITICAL, "critical"),
    ]

    event_type = models.CharField(max_length=64)
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_INFO)
    title = models.CharField(max_length=120)
    message = models.TextField(blank=True)
    source = models.CharField(max_length=32, default="system")
    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="realtime_events",
    )
    ticket = models.ForeignKey(
        SecurityTicket,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="realtime_events",
    )
    detection = models.ForeignKey(
        DetectionRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="realtime_events",
    )
    robot_id = models.CharField(max_length=64, blank=True)
    payload_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class RobotDevice(models.Model):
    robot_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=64, blank=True)
    online = models.BooleanField(default=False)
    battery = models.FloatField(null=True, blank=True)
    firmware = models.CharField(max_length=64, blank=True)
    network = models.CharField(max_length=64, blank=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["robot_id"]


class RobotFrame(models.Model):
    SOURCE_HTTP = "http"
    SOURCE_RTSP = "rtsp"
    SOURCE_MOCK = "mock"
    SOURCE_CHOICES = [
        (SOURCE_HTTP, "http"),
        (SOURCE_RTSP, "rtsp"),
        (SOURCE_MOCK, "mock"),
    ]

    robot = models.ForeignKey(
        RobotDevice,
        on_delete=models.CASCADE,
        related_name="frames",
    )
    frame_url_or_path = models.CharField(max_length=512)
    source_type = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_HTTP)
    captured_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-captured_at", "-id"]


class RobotRecognition(models.Model):
    robot = models.ForeignKey(
        RobotDevice,
        on_delete=models.CASCADE,
        related_name="recognitions",
    )
    label = models.CharField(max_length=128)
    confidence = models.FloatField(null=True, blank=True)
    image_url = models.CharField(max_length=512, blank=True)
    source = models.CharField(max_length=64, default="robot-camera")
    captured_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-captured_at", "-id"]


class AssistantSessionMessage(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [
        (ROLE_USER, "user"),
        (ROLE_ASSISTANT, "assistant"),
    ]

    session_id = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assistant_messages",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    meta_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class DispatchOrder(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_DONE = "done"
    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_PROCESSING, "processing"),
        (STATUS_DONE, "done"),
    ]

    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatch_orders",
    )
    ticket = models.ForeignKey(
        SecurityTicket,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="dispatch_orders",
    )
    dept = models.CharField(max_length=64, blank=True)
    assignee = models.CharField(max_length=64, blank=True)
    priority = models.CharField(max_length=16, default="P2")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expected_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]


class ScenarioTemplate(models.Model):
    name = models.CharField(max_length=64)
    scene_type = models.CharField(max_length=32, default="generic")
    script = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class ScenarioRun(models.Model):
    MODE_SIMULATION = "simulation"
    MODE_MIXED = "mixed"
    MODE_CHOICES = [
        (MODE_SIMULATION, "simulation"),
        (MODE_MIXED, "mixed"),
    ]

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_RUNNING, "running"),
        (STATUS_DONE, "done"),
        (STATUS_FAILED, "failed"),
    ]

    template = models.ForeignKey(
        ScenarioTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="runs",
    )
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_SIMULATION)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class ModelMetricSnapshot(models.Model):
    model_name = models.CharField(max_length=64)
    data_scope = models.CharField(max_length=64, blank=True)
    accuracy = models.FloatField(null=True, blank=True)
    precision = models.FloatField(null=True, blank=True)
    recall = models.FloatField(null=True, blank=True)
    f1 = models.FloatField(null=True, blank=True)
    false_alarm_rate = models.FloatField(null=True, blank=True)
    avg_latency_ms = models.FloatField(null=True, blank=True)
    sample_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ModelFeedback(models.Model):
    detection_record = models.ForeignKey(
        DetectionRecord,
        on_delete=models.CASCADE,
        related_name="model_feedbacks",
    )
    corrected_label = models.CharField(max_length=128)
    reviewer = models.CharField(max_length=64, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class GovernanceAuditRecord(models.Model):
    REVIEW_FALSE_POSITIVE = "false_positive"
    REVIEW_MISS = "miss"
    REVIEW_CONFIRM = "confirm"
    REVIEW_TYPE_CHOICES = [
        (REVIEW_FALSE_POSITIVE, "false_positive"),
        (REVIEW_MISS, "miss"),
        (REVIEW_CONFIRM, "confirm"),
    ]

    report = models.ForeignKey(
        Report,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_audits",
    )
    detection_record = models.ForeignKey(
        DetectionRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="governance_audits",
    )
    review_type = models.CharField(max_length=32, choices=REVIEW_TYPE_CHOICES)
    reviewer = models.CharField(max_length=64, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
