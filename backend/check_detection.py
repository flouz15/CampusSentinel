import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')
django.setup()

from api.models import DetectionRecord, Report

# 查询检测记录 ID=15
r = DetectionRecord.objects.get(id=15)
payload = r.result_payload

print('=== 检测记录 ID=15 详情 ===\n')
print(f'Report ID: {payload.get("reportId")}')
print(f'检测延迟: {payload.get("latency_ms")} ms')
print(f'检测到的对象数: {len(payload.get("detections", []))}')
print(f'违规项数: {len(payload.get("violations", []))}')

print('\n检测对象:')
for d in payload.get('detections', []):
    print(f'  - {d["cls"]}: 置信度 {d["conf"]:.2f}, 违规: {d.get("violation", "N/A")}')
    if d.get('plate_text'):
        print(f'    车牌号: {d["plate_text"]} (置信度: {d.get("plate_conf", 0):.2f})')

# 查询关联的报告
report_id = payload.get('reportId')
if report_id:
    report = Report.objects.get(id=report_id)
    print(f'\n=== 关联的报告信息 ===')
    print(f'位置: {report.location}')
    print(f'类别: {report.category}')
    print(f'违规状态: {report.violation}')
    print(f'奖励积分: {report.reward_points}')
    print(f'奖励信息: {report.reward_message}')
    print(f'报告时间: {report.time}')
    print(f'创建时间: {report.created_at}')
