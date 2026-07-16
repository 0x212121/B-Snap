# Models package
# Import all models here for easy access

from .camera import Camera
from .camera_group import CameraGroup
from .user import User
from .nvr import NVR
from .snapshot import Snapshot
from .snapshot_log import SnapshotLog
from .video import Video
from .camera_daily_stats import CameraDailyStats
from .camera_status_change_log import CameraStatusChangeLog
from .health import CameraHealth
from .config import Configuration
from .notification_queue import NotificationQueue
from .whitelist import WhatsappWhitelist
from .task_timing import TaskTiming
from .audit_log import AuditLog, AuditLogLegacy, AuditArchiveHistory
from .storage_metric import StorageMetric, StorageAlert
from .notification import Notification

# Additional models
from .recipient import GroupRecipient
from .log import CommandLog, ApiLog
from .email_retry_queue import EmailRetryQueue
from .remember_token import RememberToken
from .camera_email_notification_log import (
    CameraEmailNotificationLog,
    CameraEmailNotificationRecipient
)
from .health_check_status import HealthCheckStatus
from .job_execution_log import JobExecutionLog
from .sla_report import SLAReport, ScheduledReport
from .email_template import EmailTemplate
from .record_check import (
    RecordCheckRun,
    RecordFolderCheck,
    RecordFolderMapping,
    RecordFolderStatus,
    RecordSource,
    RecordStatusEvent,
)

__all__ = [
    "Camera",
    "CameraGroup",
    "User",
    "NVR",
    "Snapshot",
    "SnapshotLog",
    "Video",
    "CameraDailyStats",
    "CameraStatusChangeLog",
    "CameraHealth",
    "Configuration",
    "NotificationQueue",
    "WhatsappWhitelist",
    "TaskTiming",
    "AuditLog",
    "AuditLogLegacy",
    "AuditArchiveHistory",
    "StorageMetric",
    "StorageAlert",
    "Notification",
    "GroupRecipient",
    "CommandLog",
    "ApiLog",
    "EmailRetryQueue",
    "RememberToken",
    "CameraEmailNotificationLog",
    "CameraEmailNotificationRecipient",
    "HealthCheckStatus",
    "JobExecutionLog",
    "SLAReport",
    "ScheduledReport",
    "EmailTemplate",
    "RecordSource",
    "RecordCheckRun",
    "RecordFolderCheck",
    "RecordFolderMapping",
    "RecordFolderStatus",
    "RecordStatusEvent",
]
