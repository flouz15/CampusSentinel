const API_BASE_URL =
  (typeof window !== 'undefined' && window.__AMAP_APP__ && window.__AMAP_APP__.apiBase) ||
  'http://127.0.0.1:5000/api';

let governanceClosureDisplay = '--';
let governanceClosureBarPct = 0;
const SCHOOL_CENTER = [109.186171, 34.366146];
const SCHOOL_NAME = '西安工程大学临潼校区';

const WGS84_GCJ_PI = Math.PI;
const WGS84_GCJ_A = 6378245.0;
const WGS84_GCJ_EE = 0.00669342162296594323;

function wgs84OutOfChina(lng, lat) {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271;
}

function wgs84TransformLat(lng, lat) {
  let ret =
    -100.0 +
    2.0 * lng +
    3.0 * lat +
    0.2 * lat * lat +
    0.1 * lng * lat +
    0.2 * Math.sqrt(Math.abs(lng));
  ret +=
    ((20.0 * Math.sin(6.0 * lng * WGS84_GCJ_PI) + 20.0 * Math.sin(2.0 * lng * WGS84_GCJ_PI)) * 2.0) / 3.0;
  ret +=
    ((20.0 * Math.sin(lat * WGS84_GCJ_PI) + 40.0 * Math.sin((lat / 3.0) * WGS84_GCJ_PI)) * 2.0) / 3.0;
  ret +=
    ((160.0 * Math.sin((lat / 12.0) * WGS84_GCJ_PI) + 320 * Math.sin((lat * WGS84_GCJ_PI) / 30.0)) * 2.0) /
    3.0;
  return ret;
}

function wgs84TransformLng(lng, lat) {
  let ret =
    300.0 +
    lng +
    2.0 * lat +
    0.1 * lng * lng +
    0.1 * lng * lat +
    0.1 * Math.sqrt(Math.abs(lng));
  ret +=
    ((20.0 * Math.sin(6.0 * lng * WGS84_GCJ_PI) + 20.0 * Math.sin(2.0 * lng * WGS84_GCJ_PI)) * 2.0) / 3.0;
  ret +=
    ((20.0 * Math.sin(lng * WGS84_GCJ_PI) + 40.0 * Math.sin((lng / 3.0) * WGS84_GCJ_PI)) * 2.0) / 3.0;
  ret +=
    ((150.0 * Math.sin((lng / 12.0) * WGS84_GCJ_PI) + 300.0 * Math.sin((lng / 30.0) * WGS84_GCJ_PI)) *
      2.0) /
    3.0;
  return ret;
}

/**
 * WGS-84（如 sensor_msgs/NavSatFix）→ GCJ-02，与高德底图一致。境外坐标不偏移。
 * @returns {[number, number]} [lng, lat]
 */
function wgs84ToGcj02(lngWgs, latWgs) {
  const lng = Number(lngWgs);
  const lat = Number(latWgs);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return [lng, lat];
  if (wgs84OutOfChina(lng, lat)) return [lng, lat];
  let dLat = wgs84TransformLat(lng - 105.0, lat - 35.0);
  let dLng = wgs84TransformLng(lng - 105.0, lat - 35.0);
  const radLat = (lat / 180.0) * WGS84_GCJ_PI;
  let magic = Math.sin(radLat);
  magic = 1 - WGS84_GCJ_EE * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180.0) / (((WGS84_GCJ_A * (1 - WGS84_GCJ_EE)) / (magic * sqrtMagic)) * WGS84_GCJ_PI);
  dLng = (dLng * 180.0) / ((WGS84_GCJ_A / sqrtMagic) * Math.cos(radLat) * WGS84_GCJ_PI);
  return [lng + dLng, lat + dLat];
}

function isValidRosNavFixDetail(d) {
  return (
    d &&
    Number.isFinite(d.lat) &&
    Number.isFinite(d.lng) &&
    d.lat !== 0 &&
    d.lng !== 0
  );
}

const mapIds = ['leaflet-map'];
const maps = {};
const mapMarkers = {};

/** 首页 ROS /fix 定位点，不参与 clearSearch 的 mapMarkers 清理 */
let rosNavSatHomeMarker = null;
let rosNavSatHomeCenteredOnce = false;
/** 机器人 GCJ-02 轨迹（与高德对齐），自首次有效定位起累积 */
let rosNavSatTrackPolyline = null;
let rosNavSatTrackPath = [];
/** 首页地图：ROS 违停事件点位（与检索清空无关） */
let homeViolationMarkers = [];

const campusLocations = {
  图书馆: { pos: [109.186171, 34.366146], type: 'building' },
  食堂: { pos: [109.185200, 34.367500], type: 'canteen' },
  停车场: { pos: [109.188055, 34.363273], type: 'parking' },
  超市: { pos: [109.185000, 34.365500], type: 'shop' },
  体育场: { pos: [109.184392, 34.361558], type: 'sports' },
  行政楼: { pos: [109.186559, 34.364108], type: 'building' },
};

let currentPage = 'home';
let alertMarkers = {};
let lastAlertId = 0;
let alertPollingInterval = null;
let activeStageAlert = null;
let pendingStageAction = null;
let pendingStageTimelineHighlightReportId = '';
let activeAlertRecords = [];
let activeCategoryFilter = '';
let activeHourFilter = -1;
let activeStatusFilter = '';
let evParkingStats = { total: 0, byLabel: [], days: 30 };
let roadDefectStats = { total: 0, byLabel: [], days: 30 };
let hasCompletedInitialAlertLoad = false;
let activePickerKey = '';

function formatStageTime(raw) {
  const dt = new Date(raw || '');
  if (Number.isNaN(dt.getTime())) return '--:--';
  return dt.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' });
}

function stageLabel(stage = '') {
  const value = String(stage || '').toLowerCase();
  const map = {
    report: '上报进入平台',
    detect: '模型完成检测',
    alert: '告警生成联动',
    dispatch: '调度单已写入',
    process: '人工处置推进',
    resolved: '事件完成闭环',
    replay: '复盘记录已归档',
  };
  return map[value] || stage || '系统事件';
}

function stageDetectionStateText(status = '') {
  const value = String(status || '').toLowerCase();
  if (value === 'success') return '检测成功';
  if (value === 'failed') return '检测失败';
  if (value === 'pending') return '检测中';
  return status || '待检测';
}

function stageFormatConfidence(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  const ratio = num > 1 ? num / 100 : num;
  return `${(ratio * 100).toFixed(1)}%`;
}

function stageHistoryHref(plateText = '') {
  const text = String(plateText || '').trim();
  if (!text) return '#';
  return `#search=${encodeURIComponent(text)}`;
}

async function fetchStagePlateSummary(plateText = '') {
  const text = String(plateText || '').trim();
  if (!text) return null;
  const response = await fetch(`${API_BASE_URL}/detections/plate-summary?plateText=${encodeURIComponent(text)}`);
  const data = await response.json();
  if (!response.ok || !data.success) throw new Error('PLATE_SUMMARY_FAILED');
  return data;
}

async function copyStageText(value, successText = '复制成功') {
  const raw = String(value || '').trim();
  if (!raw || raw === '--') return;
  await navigator.clipboard.writeText(raw);
  const statusEl = document.getElementById('stageDetectionStatus');
  if (statusEl) statusEl.textContent = successText;
}

function formatHours(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return '--';
  if (num < 1) return `${Math.round(num * 60)} 分钟`;
  return `${num.toFixed(1)} 小时`;
}

async function fetchTicketTotal(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value != null && String(value).trim() !== '') query.set(key, String(value));
  });
  query.set('page', '1');
  query.set('pageSize', '1');
  const response = await fetch(`${API_BASE_URL}/tickets?${query.toString()}`);
  const data = await response.json();
  if (!response.ok || !data.success) throw new Error('TICKET_TOTAL_FAILED');
  return Number(data.total || 0);
}

function renderStageLoad(assigneeOpen = null, departmentOpen = null, advice = '--') {
  const assigneeEl = document.getElementById('stageTicketAssigneeLoadValue');
  const departmentEl = document.getElementById('stageTicketDepartmentLoadValue');
  const adviceEl = document.getElementById('stageTicketAdviceValue');
  if (!assigneeEl || !departmentEl || !adviceEl) return;
  assigneeEl.textContent = assigneeOpen == null ? '--' : `${assigneeOpen} 单`;
  departmentEl.textContent = departmentOpen == null ? '--' : `${departmentOpen} 单`;
  adviceEl.textContent = advice;
}

async function loadStageAssigneeLoad(ticket = null) {
  if (!ticket) {
    renderStageLoad(null, null, '--');
    return;
  }

  const assignee = String(ticket.assignee || '').trim();
  const department = String(ticket.department || '').trim();

  try {
    const tasks = [];
    tasks.push(
      assignee
        ? Promise.all([
            fetchTicketTotal({ assignee, status: 'pending' }),
            fetchTicketTotal({ assignee, status: 'processing' }),
          ]).then(([pending, processing]) => pending + processing)
        : Promise.resolve(null),
    );
    tasks.push(
      department
        ? Promise.all([
            fetchTicketTotal({ department, status: 'pending' }),
            fetchTicketTotal({ department, status: 'processing' }),
          ]).then(([pending, processing]) => pending + processing)
        : Promise.resolve(null),
    );

    const [assigneeOpen, departmentOpen] = await Promise.all(tasks);
    let advice = '当前负载平稳，可按既定流程推进。';
    if (!assignee) {
      advice = '工单尚未分配负责人，建议优先派单。';
    } else if ((assigneeOpen || 0) >= 4) {
      advice = '负责人当前负载偏高，建议调度支援或重新分派。';
    } else if ((departmentOpen || 0) >= 8) {
      advice = '责任单位待办较多，建议启动协同值守。';
    }
    renderStageLoad(assigneeOpen, departmentOpen, advice);
  } catch {
    renderStageLoad(null, null, '负责人负载获取失败');
  }
}

function renderStageTicket(ticket = null, statusText = '等待加载', fallbackLink = '#') {
  const statusEl = document.getElementById('stageTicketStatusText');
  const assigneeEl = document.getElementById('stageTicketAssigneeValue');
  const departmentEl = document.getElementById('stageTicketDepartmentValue');
  const stateEl = document.getElementById('stageTicketStateValue');
  const slaEl = document.getElementById('stageTicketSlaValue');
  const elapsedEl = document.getElementById('stageTicketElapsedValue');
  const messageEl = document.getElementById('stageTicketMessageValue');
  const linkEl = document.getElementById('stageTicketDirectLink');
  if (!statusEl || !assigneeEl || !departmentEl || !stateEl || !slaEl || !elapsedEl || !messageEl || !linkEl) return;

  statusEl.textContent = statusText;
  linkEl.href = fallbackLink;
  if (!ticket) {
    assigneeEl.textContent = '--';
    departmentEl.textContent = '--';
    stateEl.textContent = '--';
    slaEl.textContent = '--';
    elapsedEl.textContent = '--';
    messageEl.textContent = '--';
    renderStageLoad(null, null, '--');
    return;
  }

  assigneeEl.textContent = ticket.assignee || '待分配';
  departmentEl.textContent = ticket.department || '值守中心';
  stateEl.textContent = ticket.overdue ? `${stageStatusText(ticket.status)} · 已超时` : stageStatusText(ticket.status);
  slaEl.textContent = ticket.slaHours ? `${ticket.slaHours} 小时 SLA` : '--';
  elapsedEl.textContent = formatHours(ticket.elapsedHours);
  messageEl.textContent = ticket.message || '暂无补充说明';
}

async function loadStageTicket(reportId) {
  if (!reportId) {
    renderStageTicket(null, '无上报编号');
    return;
  }

  const link = `#ticket=${encodeURIComponent(reportId)}`;
  renderStageTicket(null, '加载中...', link);
  try {
    const response = await fetch(`${API_BASE_URL}/tickets?reportId=${encodeURIComponent(reportId)}&page=1&pageSize=1`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      renderStageTicket(null, '加载失败', link);
      return;
    }
    const ticket = Array.isArray(data.tickets) && data.tickets.length ? data.tickets[0] : null;
    renderStageTicket(ticket, ticket ? (ticket.overdue ? '已同步工单 · 超时提醒' : '已同步工单') : '暂无工单', link);
    await loadStageAssigneeLoad(ticket);
  } catch {
    renderStageTicket(null, '网络异常', link);
  }
}

function renderStageEvidence(imageUrl = '', statusText = '等待加载', fallbackLink = '#') {
  const statusEl = document.getElementById('stageEvidenceStatus');
  const frameEl = document.getElementById('stageEvidenceFrame');
  const imgEl = document.getElementById('stageEvidenceImage');
  const emptyEl = document.getElementById('stageEvidenceEmpty');
  const linkEl = document.getElementById('stageEvidenceLink');
  if (!statusEl || !frameEl || !imgEl || !emptyEl || !linkEl) return;

  statusEl.textContent = statusText;
  linkEl.href = fallbackLink;

  if (!imageUrl) {
    frameEl.classList.add('is-empty');
    imgEl.hidden = true;
    imgEl.removeAttribute('src');
    emptyEl.hidden = false;
    emptyEl.textContent = '暂无证据画面';
    return;
  }

  frameEl.classList.remove('is-empty');
  imgEl.hidden = false;
  imgEl.onerror = () => {
    frameEl.classList.add('is-empty');
    imgEl.hidden = true;
    imgEl.removeAttribute('src');
    emptyEl.hidden = false;
    emptyEl.textContent = '证据画面加载失败';
    statusEl.textContent = '证据加载失败';
    linkEl.href = fallbackLink;
  };
  imgEl.src = imageUrl;
  emptyEl.hidden = true;
}

async function loadStageReportEvidence(reportId, fallbackLink) {
  if (!reportId) {
    renderStageEvidence('', '无上报编号', fallbackLink);
    return;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/reports/${reportId}`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      renderStageEvidence('', '原图加载失败', fallbackLink);
      return;
    }
    const report = data.report || {};
    const imageUrl = report.imageUrl || (Array.isArray(report.evidenceUrls) && report.evidenceUrls.length ? report.evidenceUrls[0] : '');
    renderStageEvidence(imageUrl || '', imageUrl ? '回退到举报原图' : '暂无原图', imageUrl || fallbackLink);
  } catch {
    renderStageEvidence('', '原图加载失败', fallbackLink);
  }
}

function renderStageDetection(record = null, statusText = '等待加载') {
  const statusEl = document.getElementById('stageDetectionStatus');
  const stateEl = document.getElementById('stageDetectionStateValue');
  const labelEl = document.getElementById('stageDetectionLabelValue');
  const confidenceEl = document.getElementById('stageDetectionConfidenceValue');
  const modelEl = document.getElementById('stageDetectionModelValue');
  const plateEl = document.getElementById('stageDetectionPlateValue');
  const plateConfidenceEl = document.getElementById('stageDetectionPlateConfidenceValue');
  const copyBtn = document.getElementById('stageCopyPlateBtn');
  const linkEl = document.getElementById('stageDetectionLink');
  if (!statusEl || !stateEl || !labelEl || !confidenceEl || !modelEl || !plateEl || !plateConfidenceEl || !copyBtn || !linkEl) return;
  let historyLink = document.getElementById('stagePlateHistoryLink');
  if (!historyLink && linkEl.parentElement) {
    historyLink = document.createElement('a');
    historyLink.id = 'stagePlateHistoryLink';
    historyLink.className = 'stage-detail-link';
    historyLink.target = '_blank';
    historyLink.rel = 'noreferrer';
    historyLink.textContent = '按车牌检索历史事件';
    linkEl.parentElement.insertBefore(historyLink, copyBtn);
  }

  statusEl.textContent = statusText;
  if (!record) {
    stateEl.textContent = '--';
    labelEl.textContent = '--';
    confidenceEl.textContent = '--';
    modelEl.textContent = '--';
    plateEl.textContent = '--';
    plateConfidenceEl.textContent = '--';
    copyBtn.disabled = true;
    copyBtn.onclick = null;
    linkEl.href = '#';
    if (historyLink) {
      historyLink.href = '#';
      historyLink.style.display = 'none';
    }
    renderStagePlateSummary(null);
    return;
  }

  stateEl.textContent = stageDetectionStateText(record.status);
  labelEl.textContent = record.label || '待生成';
  confidenceEl.textContent = stageFormatConfidence(record.confidence);
  modelEl.textContent = record.modelName || '未返回';
  plateEl.textContent = record.plateText || '--';
  plateConfidenceEl.textContent = stageFormatConfidence(record.plateConfidence);
  copyBtn.disabled = !record.plateText;
  copyBtn.onclick = record.plateText
    ? async () => {
        try {
          await copyStageText(record.plateText, '车牌号已复制');
        } catch {
          statusEl.textContent = '复制失败';
        }
      }
    : null;
  linkEl.href = record.reportId ? `#report=${encodeURIComponent(record.reportId)}` : '#';
  if (historyLink) {
    historyLink.href = stageHistoryHref(record.plateText);
    historyLink.style.display = record.plateText ? 'inline-flex' : 'none';
  }
}

function renderStagePlateSummary(summary = null) {
  const block = document.getElementById('stageDetectionSummary');
  if (!block) return;
  let panel = document.getElementById('stagePlateSummaryCard');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'stagePlateSummaryCard';
    panel.className = 'source-summary-card';
    block.appendChild(panel);
  }
  if (!summary || !summary.plateText) {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }
  panel.hidden = false;
  const historyHref = stageHistoryHref(summary.plateText);
  const alertHref = summary.latestReportId
    ? `#alert=${encodeURIComponent(summary.latestReportId)}`
    : '';
  panel.innerHTML = `
    <div class="source-summary-eyebrow">PLATE HISTORY</div>
    <div class="source-summary-title">车牌 ${summary.plateText} 已累计出现 ${summary.totalCount || 0} 次</div>
    <div class="source-summary-text">最近出现时间：${summary.lastSeenAt ? new Date(summary.lastSeenAt).toLocaleString() : '--'}。该统计可直接展示持续治理与复发事件追踪能力。</div>
    <div class="source-summary-emphasis">${summary.latestReportId ? `最近关联告警：${summary.latestReportId}` : '暂未关联告警编号'}</div>
    <div class="detail-actions" style="margin-top:12px;">
      <a class="stage-detail-link" href="${historyHref}" target="_blank" rel="noreferrer">查看该车牌全部记录</a>
      ${alertHref ? `<a class="stage-detail-link" href="${alertHref}" target="_blank" rel="noreferrer">查看最近关联告警</a>` : ''}
    </div>
  `;
}

async function loadStageDetection(reportId) {
  if (!reportId) {
    renderStageDetection(null, '无上报编号');
    renderStageEvidence('', '无上报编号', '#');
    return;
  }

  const fallbackLink = `#report=${encodeURIComponent(reportId)}`;
  renderStageDetection(null, '加载中...');
  renderStageEvidence('', '加载中...', fallbackLink);
  try {
    const response = await fetch(`${API_BASE_URL}/detections?reportId=${encodeURIComponent(reportId)}&page=1&pageSize=1`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      renderStageDetection(null, '加载失败');
      renderStagePlateSummary(null);
      await loadStageReportEvidence(reportId, fallbackLink);
      return;
    }
    const record = Array.isArray(data.records) && data.records.length ? data.records[0] : null;
    renderStageDetection(record, record ? '已同步最新检测' : '暂无检测记录');
    if (record?.plateText) {
      try {
        const summary = await fetchStagePlateSummary(record.plateText);
        renderStagePlateSummary(summary);
      } catch {
        renderStagePlateSummary(null);
      }
    } else {
      renderStagePlateSummary(null);
    }
    if (record?.fileUrl) {
      renderStageEvidence(record.fileUrl, '已同步检测画面', record.fileUrl);
    } else {
      await loadStageReportEvidence(reportId, fallbackLink);
    }
  } catch {
    renderStageDetection(null, '网络异常');
    renderStagePlateSummary(null);
    await loadStageReportEvidence(reportId, fallbackLink);
  }
}

function renderStageTimeline(items = [], statusText = '等待加载', highlightId = null) {
  const listEl = document.getElementById('stageTimelineList');
  const statusEl = document.getElementById('stageTimelineStatus');
  if (!listEl || !statusEl) return;

  statusEl.textContent = statusText;
  if (!items.length) {
    listEl.innerHTML = `
      <li class="stage-timeline-item is-empty">
        <span class="stage-timeline-time">--:--</span>
        <div>
          <div class="stage-timeline-title">暂无闭环日志</div>
          <div class="stage-timeline-meta">当前事件还没有可展示的时间线记录，后续状态更新后会自动补全。</div>
        </div>
      </li>
    `;
    return;
  }

  listEl.innerHTML = items
    .map((item) => `
      <li class="stage-timeline-item ${highlightId != null && String(item.id) === String(highlightId) ? 'is-recent-log' : ''}">
        <span class="stage-timeline-time">${formatStageTime(item.createdAt)}</span>
        <div>
          <div class="stage-timeline-title">${stageLabel(item.stage)} · ${item.action || '状态更新'}</div>
          <div class="stage-timeline-meta">${item.actor || '系统'} · ${item.payload?.message || item.payload?.note || '已写入平台时间线，可用于答辩展示闭环证据。'}</div>
        </div>
      </li>
    `)
    .join('');
}

async function loadStageTimeline(reportId) {
  if (!reportId) {
    renderStageTimeline([], '无上报编号');
    return;
  }

  renderStageTimeline([], '加载中...');
  try {
    const response = await fetch(`${API_BASE_URL}/reports/${reportId}/timeline`);
    const data = await response.json();
    if (!response.ok || !data.success) {
      renderStageTimeline([], '加载失败');
      return;
    }
    const timeline = Array.isArray(data.timeline) ? data.timeline.slice(-4).reverse() : [];
    const highlightId = pendingStageTimelineHighlightReportId === reportId && timeline.length ? timeline[0].id : null;
    renderStageTimeline(timeline, timeline.length ? (highlightId ? `已同步 ${timeline.length} 条真实日志 · 最新操作已高亮` : `已同步 ${timeline.length} 条真实日志`) : '暂无日志', highlightId);
    if (pendingStageTimelineHighlightReportId === reportId) pendingStageTimelineHighlightReportId = '';
  } catch {
    renderStageTimeline([], '网络异常');
  }
}

function stageBadgeClass(status = '') {
  const value = String(status || '').toLowerCase();
  if (['resolved', 'success'].includes(value)) return 'is-success';
  if (['processing', 'pending'].includes(value)) return 'is-processing';
  if (['failed', 'error'].includes(value)) return 'is-failed';
  return 'is-pending';
}

function stageStatusText(status = '') {
  const value = String(status || '').toLowerCase();
  if (value === 'resolved') return '已解决';
  if (value === 'processing') return '处理中';
  if (value === 'pending') return '待联动';
  return status || '待联动';
}

function setStageActionStatus(text = '等待确认', level = '') {
  const el = document.getElementById('stageActionModalStatus');
  if (!el) return;
  el.textContent = text;
  el.dataset.level = level;
}

function closeStageActionModal() {
  const modal = document.getElementById('stageActionModal');
  if (!modal) return;
  modal.hidden = true;
  pendingStageAction = null;
  setStageActionStatus('等待确认');
}

function openStageActionModal(status) {
  if (!activeStageAlert?.reportId) return;
  pendingStageAction = {
    status,
    reportId: activeStageAlert.reportId,
    alertId: String(activeStageAlert.id || ''),
  };
  const modal = document.getElementById('stageActionModal');
  const titleEl = document.getElementById('stageActionModalTitle');
  const subtitleEl = document.getElementById('stageActionModalSubtitle');
  const eventEl = document.getElementById('stageActionModalEvent');
  const assigneeEl = document.getElementById('stageActionModalAssignee');
  const reasonEl = document.getElementById('stageActionModalReason');
  if (!modal || !titleEl || !subtitleEl || !eventEl || !assigneeEl || !reasonEl) return;

  titleEl.textContent = status === 'resolved' ? '确认标记为已解决' : '确认推进到处理中';
  subtitleEl.textContent = status === 'resolved'
    ? '请填写结案原因或现场结果，系统会同步更新时间线和工单摘要。'
    : '请填写处置原因或交接说明，系统会同步更新时间线和工单摘要。';
  eventEl.textContent = activeStageAlert.title || activeStageAlert.reportId;
  assigneeEl.value = document.getElementById('stageTicketAssigneeValue')?.textContent === '待分配'
    ? ''
    : (document.getElementById('stageTicketAssigneeValue')?.textContent || '');
  reasonEl.value = '';
  modal.hidden = false;
  setStageActionStatus('请填写原因后确认提交');
  setTimeout(() => reasonEl.focus(), 0);
}

function closeStageDetail() {
  const overlay = document.getElementById('stageDetailOverlay');
  if (!overlay) return;
  overlay.hidden = true;
  activeStageAlert = null;
  renderStageTimeline([], '等待加载');
  renderStageDetection(null, '等待加载');
  renderStageEvidence('', '等待加载', '#');
  renderStageTicket(null, '等待加载', '#');
}

function openStageDetail(alert = {}) {
  const overlay = document.getElementById('stageDetailOverlay');
  if (!overlay) return;

  activeStageAlert = alert;
  const status = String(alert.status || 'pending').toLowerCase();
  const titleEl = document.getElementById('stageDetailTitle');
  const subtitleEl = document.getElementById('stageDetailSubtitle');
  const statusEl = document.getElementById('stageDetailStatus');
  const levelEl = document.getElementById('stageDetailLevel');
  const sourceEl = document.getElementById('stageDetailSource');
  const locationEl = document.getElementById('stageDetailLocation');
  const reportIdEl = document.getElementById('stageDetailReportId');
  const actionEl = document.getElementById('stageDetailAction');
  const alertLink = document.getElementById('stageAlertLink');
  const ticketLink = document.getElementById('stageTicketLink');
  const processBtn = document.getElementById('stageProcessBtn');
  const resolveBtn = document.getElementById('stageResolveBtn');

  if (titleEl) titleEl.textContent = alert.title || '校园联动事件';
  if (subtitleEl) subtitleEl.textContent = alert.description || '当前事件已进入指挥面板，可直接推进处置状态并跳转到门户页继续联动。';
  if (statusEl) {
    statusEl.className = `stage-detail-badge ${stageBadgeClass(status)}`;
    statusEl.textContent = stageStatusText(status);
  }
  if (levelEl) {
    levelEl.className = `stage-detail-badge ${stageBadgeClass(alert.severity || status)}`;
    levelEl.textContent = alert.severity || '普通';
  }
  if (sourceEl) sourceEl.textContent = alert.category || alert.source || '地图联动';
  if (locationEl) locationEl.textContent = Number.isFinite(Number(alert.longitude)) && Number.isFinite(Number(alert.latitude))
    ? `${Number(alert.longitude).toFixed(6)}, ${Number(alert.latitude).toFixed(6)}`
    : '定位待同步';
  if (reportIdEl) reportIdEl.textContent = alert.reportId || alert.id || '--';
  if (actionEl) {
    actionEl.textContent = status === 'resolved'
      ? '建议切换到复盘页或检测记录页，展示完整闭环。'
      : status === 'processing'
        ? '建议继续查看工单页和责任单位执行进度。'
        : '建议先转处理中，再跳转到告警中心或工单页继续联动。';
  }
  if (alertLink) {
    alertLink.href = alert.reportId ? `#alert=${encodeURIComponent(alert.reportId)}` : '#';
  }
  if (ticketLink) ticketLink.href = alert.reportId ? `#ticket=${encodeURIComponent(alert.reportId)}` : '#';
  if (processBtn) processBtn.disabled = status === 'processing' || status === 'resolved';
  if (resolveBtn) resolveBtn.disabled = status === 'resolved';

  overlay.hidden = false;
  loadStageTimeline(alert.reportId || '');
  loadStageDetection(alert.reportId || '');
  loadStageTicket(alert.reportId || '');
}

function tickClock() {
  const clock = document.getElementById('clock');
  const date = document.getElementById('date');
  const now = new Date();
  if (clock) clock.textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
  if (date) {
    date.textContent = now.toLocaleDateString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
    });
  }
}

function initNavigation() {
  const navButtons = document.querySelectorAll('.nav-item');
  const pages = document.querySelectorAll('.page');
  navButtons.forEach((button) => {
    button.addEventListener('click', () => {
      const target = button.dataset.page;
      if (!target) return;
      currentPage = target;
      navButtons.forEach((item) => item.classList.remove('is-active'));
      button.classList.add('is-active');
      pages.forEach((page) => {
        page.classList.toggle('is-active', page.dataset.page === target);
      });
      if (target === 'patrol') {
        window.rosRoadDefect?.onRoadDefectPageHidden?.();
        window.rosGauge?.onGaugePageHidden?.();
        window.rosPatrol?.onPatrolPageShown?.();
      } else if (target === 'road-defect') {
        window.rosPatrol?.onPatrolPageHidden?.();
        window.rosGauge?.onGaugePageHidden?.();
        window.rosRoadDefect?.onRoadDefectPageShown?.();
      } else if (target === 'gauge') {
        window.rosPatrol?.onPatrolPageHidden?.();
        window.rosRoadDefect?.onRoadDefectPageHidden?.();
        window.rosGauge?.onGaugePageShown?.();
      } else {
        window.rosPatrol?.onPatrolPageHidden?.();
        window.rosRoadDefect?.onRoadDefectPageHidden?.();
        window.rosGauge?.onGaugePageHidden?.();
      }
      const targetMapId = target === 'home' ? 'leaflet-map' : null;
      if (targetMapId && !maps[targetMapId]) initMap(targetMapId);
      const targetMap = targetMapId ? maps[targetMapId] : null;
      if (targetMap?.resize) {
        setTimeout(() => targetMap.resize(), 0);
      }
    });
  });
}

function bindStageDetailActions() {
  document.getElementById('stageDetailCloseBtn')?.addEventListener('click', closeStageDetail);
  document.querySelector('[data-close-stage-detail="true"]')?.addEventListener('click', closeStageDetail);

  document.getElementById('stageProcessBtn')?.addEventListener('click', () => openStageActionModal('processing'));
  document.getElementById('stageResolveBtn')?.addEventListener('click', () => openStageActionModal('resolved'));

  const modal = document.getElementById('stageActionModal');
  const closeBtn = document.getElementById('stageActionModalClose');
  const cancelBtn = document.getElementById('stageActionModalCancel');
  const confirmBtn = document.getElementById('stageActionModalConfirm');
  const assigneeEl = document.getElementById('stageActionModalAssignee');
  const reasonEl = document.getElementById('stageActionModalReason');
  if (!modal || !closeBtn || !cancelBtn || !confirmBtn || !assigneeEl || !reasonEl) return;

  const close = () => closeStageActionModal();
  closeBtn.addEventListener('click', close);
  cancelBtn.addEventListener('click', close);
  modal.querySelector('[data-close-stage-action="true"]')?.addEventListener('click', close);

  confirmBtn.addEventListener('click', async () => {
    if (!pendingStageAction?.reportId || !pendingStageAction?.alertId) return;
    const assignee = assigneeEl.value.trim();
    const message = reasonEl.value.trim();
    if (!message) {
      setStageActionStatus('请填写处置原因后再提交', 'warn');
      reasonEl.focus();
      return;
    }

    confirmBtn.disabled = true;
    setStageActionStatus('正在提交处置操作...', 'warn');
    try {
      await handleAlert(pendingStageAction.alertId, pendingStageAction.reportId, pendingStageAction.status, { assignee, message });
      closeStageActionModal();
    } catch {
      setStageActionStatus('处置提交失败，请稍后重试', 'error');
    } finally {
      confirmBtn.disabled = false;
    }
  });
}

function getMapByPage(pageName = currentPage) {
  const id = pageName === 'home' ? 'leaflet-map' : `leaflet-map-${pageName}`;
  return maps[id] || maps['leaflet-map'];
}

function initMap(containerId) {
  const container = document.getElementById(containerId);
  if (!container) return null;
  if (typeof AMap === 'undefined') {
    container.innerHTML = '<div style="padding:18px;color:#98989a">地图 SDK 未加载</div>';
    return null;
  }

  try {
    const map = new AMap.Map(containerId, {
      center: SCHOOL_CENTER,
      zoom: 16,
      mapStyle: containerId === 'leaflet-map' ? 'amap://styles/normal' : 'amap://styles/dark',
      viewMode: '2D',
    });
    map.addControl(new AMap.Scale());
    map.addControl(new AMap.ToolBar({ position: { top: '10px', right: '10px' } }));

    maps[containerId] = map;
    mapMarkers[containerId] = [];
    setTimeout(() => {
      try {
        map.resize?.();
      } catch (error) {
        console.error('[map-resize-error]', containerId, error);
      }
    }, 50);
    return map;
  } catch (error) {
    console.error('[map-init-error]', containerId, error);
    container.innerHTML = `<div style="padding:18px;color:#ff8f8f">地图初始化失败：${error?.message || error}</div>`;
    return null;
  }
}

function clearMarkers(containerId) {
  const map = maps[containerId];
  if (!map) return;
  const markers = mapMarkers[containerId] || [];
  while (markers.length) {
    map.remove(markers.pop());
  }
}

function addMarker(containerId, lng, lat, title, ext = {}) {
  const map = maps[containerId];
  if (!map || typeof AMap === 'undefined') return null;
  const marker = new AMap.Marker({
    position: [lng, lat],
    title: title || '定位点',
  });
  marker.setMap(map);
  mapMarkers[containerId].push(marker);

  if (ext.description || ext.status) {
    const infoWindow = new AMap.InfoWindow({
      offset: new AMap.Pixel(0, -20),
      content: `<div style="padding:8px;min-width:220px"><strong>${title || '定位点'}</strong><br/>${ext.description || ''}${ext.status ? `<br/>状态: ${ext.status}` : ''}</div>`,
    });
    marker.on('click', () => infoWindow.open(map, [lng, lat]));
  }
  return marker;
}

function updateRosNavSatHomeMarker(lng, lat) {
  const mapId = 'leaflet-map';
  const map = maps[mapId];
  if (!map || typeof AMap === 'undefined') return;
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return;
  const pos = [lng, lat];
  if (rosNavSatHomeMarker) {
    rosNavSatHomeMarker.setPosition(pos);
  } else {
    rosNavSatHomeMarker = new AMap.Marker({
      position: pos,
      title: '机器人实时位置（/fix）',
      zIndex: 120,
    });
    rosNavSatHomeMarker.setMap(map);
  }
  if (!rosNavSatHomeCenteredOnce) {
    const z = map.getZoom?.();
    if (z == null || z < 17) {
      map.setZoom(17);
    }
    rosNavSatHomeCenteredOnce = true;
  }
  if (typeof map.panTo === 'function') {
    map.panTo(pos);
  } else {
    map.setCenter(pos);
  }
}

function appendRosNavSatTrackPoint(gcjLng, gcjLat) {
  const map = maps['leaflet-map'];
  if (!map || typeof AMap === 'undefined') return;
  if (!Number.isFinite(gcjLng) || !Number.isFinite(gcjLat)) return;
  const prev = rosNavSatTrackPath[rosNavSatTrackPath.length - 1];
  if (prev && prev[0] === gcjLng && prev[1] === gcjLat) return;
  rosNavSatTrackPath.push([gcjLng, gcjLat]);
  if (!rosNavSatTrackPolyline) {
    rosNavSatTrackPolyline = new AMap.Polyline({
      path: rosNavSatTrackPath.slice(),
      strokeColor: '#25f6ff',
      strokeOpacity: 0.9,
      strokeWeight: 4,
      zIndex: 90,
      lineJoin: 'round',
      lineCap: 'round',
    });
    map.add(rosNavSatTrackPolyline);
  } else {
    rosNavSatTrackPolyline.setPath(rosNavSatTrackPath);
  }
}

function navSatFixEmitIntervalSec() {
  const ms = Number(window.__DASHBOARD_CONFIG__?.rosTopics?.navSatFixEmitIntervalMs);
  if (Number.isFinite(ms) && ms > 0) return Math.max(1, Math.round(ms / 1000));
  return 3;
}

/** 首页右侧「定位状况」卡片：与 ros-navsat-fix 同步 */
function updateHomeGpsStatusPanel(detail) {
  const badge = document.getElementById('homeGpsStatusBadge');
  const latEl = document.getElementById('homeGpsLat');
  const lngEl = document.getElementById('homeGpsLng');
  const hintEl = document.getElementById('homeGpsHint');
  if (!badge || !latEl || !lngEl) return;

  const topic = String(window.__DASHBOARD_CONFIG__?.rosTopics?.navSatFix || '').trim();
  const sec = navSatFixEmitIntervalSec();

  const setHint = (text) => {
    if (hintEl) hintEl.textContent = text || '';
  };

  if (!topic) {
    badge.textContent = '未配置定位话题';
    badge.className = 'home-gps-badge is-muted';
    latEl.textContent = '--';
    lngEl.textContent = '--';
    setHint('在 config/dashboard.json 的 rosTopics 中设置 navSatFix（例如 /fix）');
    return;
  }

  let lat;
  let lng;
  if (detail?.fallbackToDefault) {
    [lng, lat] = SCHOOL_CENTER;
    badge.textContent = '未获取有效定位';
    badge.className = 'home-gps-badge is-warn';
    latEl.textContent = lat.toFixed(10);
    lngEl.textContent = lng.toFixed(10);
    setHint(
      `地图暂用默认坐标（高德 GCJ-02）；话题 ${topic} 无有效 WGS-84 或为零（约每 ${sec} 秒检测）`,
    );
    return;
  }

  if (detail && detail.gcj02FromWgs && isValidRosNavFixDetail(detail)) {
    lat = detail.lat;
    lng = detail.lng;
    badge.textContent = '已获取有效定位';
    badge.className = 'home-gps-badge is-ok';
    latEl.textContent = lat.toFixed(10);
    lngEl.textContent = lng.toFixed(10);
    setHint(
      `订阅 ${topic} · 约每 ${sec} 秒更新 · 已为高德对齐将 WGS-84 转为 GCJ-02（显示与地图一致）`,
    );
    return;
  }

  badge.textContent = '等待定位数据';
  badge.className = 'home-gps-badge is-muted';
  latEl.textContent = '--';
  lngEl.textContent = '--';
  setHint(`已配置 ${topic}，等待首帧有效数据…`);
}

function syncHomeGpsPanelInitial() {
  updateHomeGpsStatusPanel(null);
}

function tryEnsureRosGpsConnection() {
  window.rosPatrol?.ensureGpsConnection?.();
}

function centerAllMaps(lng, lat) {
  Object.values(maps).forEach((map) => {
    map.setCenter([lng, lat]);
    map.setZoom(17);
  });
}

function renderResult(title, html) {
  const panel = document.getElementById('resultPanel');
  const titleEl = document.getElementById('resultTitle');
  const contentEl = document.getElementById('resultContent');
  if (!panel || !titleEl || !contentEl) return;
  titleEl.textContent = title;
  contentEl.innerHTML = html;
  panel.classList.add('is-visible');
}

function closeResultPanel() {
  const panel = document.getElementById('resultPanel');
  if (panel) panel.classList.remove('is-visible');
}

async function geocodeAddress(address) {
  const url = `${API_BASE_URL}/geocode?address=${encodeURIComponent(address)}`;
  const response = await fetch(url);
  const data = await response.json();
  if (!data.success || !data.location) return null;
  return data.location;
}

async function searchPOI(keywords) {
  const url = `${API_BASE_URL}/poi/search?keywords=${encodeURIComponent(keywords)}&city=西安`;
  const response = await fetch(url);
  const data = await response.json();
  if (!data.success || !Array.isArray(data.pois) || data.pois.length === 0) return null;
  return data.pois[0];
}

async function handleSearch() {
  const input = document.getElementById('searchInput');
  const query = (input?.value || '').trim();
  if (!query) {
    renderResult('搜索结果', '<div class="loading">请输入关键词</div>');
    return;
  }

  const local = campusLocations[query];
  if (local) {
    const [lng, lat] = local.pos;
    const activeMap = getMapByPage();
    addMarker(activeMap.getContainer().id, lng, lat, query, { description: SCHOOL_NAME });
    activeMap.setCenter([lng, lat]);
    activeMap.setZoom(18);
    renderResult('搜索结果', `<div>已定位到 <strong>${query}</strong><br/>经纬度: ${lng.toFixed(6)}, ${lat.toFixed(6)}</div>`);
    return;
  }

  try {
    const loc = await geocodeAddress(query);
    if (loc) {
      const activeMap = getMapByPage();
      addMarker(activeMap.getContainer().id, loc.lng, loc.lat, query, { description: '地址解析结果' });
      centerAllMaps(loc.lng, loc.lat);
      renderResult('地址解析', `<div>地址: ${query}<br/>经纬度: ${loc.lng.toFixed(6)}, ${loc.lat.toFixed(6)}</div>`);
      return;
    }

    const poi = await searchPOI(query);
    if (poi) {
      const activeMap = getMapByPage();
      addMarker(activeMap.getContainer().id, poi.lng, poi.lat, poi.name, { description: poi.address || '' });
      centerAllMaps(poi.lng, poi.lat);
      renderResult('POI 搜索', `<div><strong>${poi.name}</strong><br/>${poi.address || ''}<br/>经纬度: ${poi.lng.toFixed(6)}, ${poi.lat.toFixed(6)}</div>`);
      return;
    }

    renderResult('搜索结果', '<div class="loading">未找到匹配地点</div>');
  } catch (error) {
    renderResult('搜索结果', `<div class="loading">搜索失败: ${error.message}</div>`);
  }
}

function clearSearch() {
  const input = document.getElementById('searchInput');
  if (input) input.value = '';
  Object.keys(mapMarkers).forEach(clearMarkers);
  closeResultPanel();
}

function quickSearchPOI(keyword) {
  const input = document.getElementById('searchInput');
  if (input) input.value = keyword;
  return handleSearch();
}

function toggleMapLayer(layerType) {
  const styleMap = {
    normal: 'amap://styles/normal',
    dark: 'amap://styles/dark',
  };
  const mapStyle = styleMap[layerType] || 'amap://styles/normal';
  Object.values(maps).forEach((map) => map.setMapStyle(mapStyle));

  ['normal', 'dark'].forEach((type) => {
    const btn = document.getElementById(`btn-${type}`);
    if (btn) btn.classList.toggle('is-active', type === layerType);
  });
}

async function refreshWeather() {
  const city = '西安';
  try {
    const response = await fetch(`${API_BASE_URL}/weather?city=${city}`);
    const data = await response.json();
    if (!data.success) return;
    const valueEl = document.querySelector('.status-item.weather .status-value');
    if (valueEl) {
      const cityText = data.city || city;
      const weatherText = data.weather || '多云';
      const tempText = data.temperature || '--';
      valueEl.textContent = `${cityText} ${weatherText} ${tempText}℃`;
      valueEl.title = `湿度 ${data.humidity || '--'}% · 风向 ${data.winddirection || '--'} · 风力 ${data.windpower || '--'} 级 · 更新时间 ${data.reporttime || '--'}`;
    }
  } catch (_) {
    // 天气刷新失败不阻塞主流程。
  }
}

async function updateAlertStatus(reportId, status, payload = {}) {
  if (!reportId) return false;
  try {
    const response = await fetch(`${API_BASE_URL}/alerts/${reportId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, ...payload }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function fetchAlerts(sinceId = 0) {
  try {
    const url = sinceId > 0
      ? `${API_BASE_URL}/alerts?since_id=${sinceId}&limit=20`
      : `${API_BASE_URL}/alerts?limit=20`;
    const response = await fetch(url);
    const data = await response.json();
    if (!data.success) return [];
    if (typeof data.latestId === 'number') {
      lastAlertId = Math.max(lastAlertId, data.latestId);
    }
    return Array.isArray(data.alerts) ? data.alerts : [];
  } catch {
    return [];
  }
}

function addAlertMarker(map, alert) {
  if (!map || typeof alert.latitude !== 'number' || typeof alert.longitude !== 'number') return;

  const severityColors = {
    critical: '#ff0000',
    high: '#ff4444',
    medium: '#ff7b2c',
    low: '#f4b13b',
  };
  const color = severityColors[alert.severity] || '#ff7b2c';

  const markerKey = `${map.getContainer().id}:${String(alert.id ?? '')}`;
  if (alertMarkers[markerKey]) {
    map.remove(alertMarkers[markerKey].marker);
    if (alertMarkers[markerKey].circle) map.remove(alertMarkers[markerKey].circle);
  }

  const marker = new AMap.Marker({
    position: [alert.longitude, alert.latitude],
    content: `<div style="width:16px;height:16px;background:${color};border:2px solid #fff;border-radius:50%;box-shadow:0 0 10px ${color}"></div>`,
    title: alert.title || '告警',
    zIndex: 220,
  });

  const circle = new AMap.Circle({
    center: [alert.longitude, alert.latitude],
    radius: 35,
    strokeColor: color,
    strokeWeight: 2,
    fillColor: color,
    fillOpacity: 0.2,
  });

  marker.on('click', () => {
    openAlertInfoOnMap(map, alert);
    openStageDetail(alert);
  });

  const visible = isAlertInActiveCategory(alert);
  marker.setMap(visible ? map : null);
  circle.setMap(visible ? map : null);
  alertMarkers[markerKey] = { marker, circle, alert, mapId: map.getContainer().id };
}

function showAlertNotification(alert) {
  const notification = document.createElement('div');
  notification.className = 'alert-notification';
  notification.innerHTML = `
    <div style="position:fixed;top:120px;right:20px;background:#151515;border:1px solid #3a3a3a;padding:12px 14px;border-radius:8px;z-index:10000;min-width:260px">
      <div style="color:#9eff00;font-weight:700">新告警</div>
      <div style="margin-top:6px;color:#e6e6e6">${alert.title || '违规告警'}</div>
      <div style="margin-top:4px;color:#98989a;font-size:12px">${alert.reporter || '匿名'} · ${alert.category || '未分类'}</div>
    </div>
  `;
  document.body.appendChild(notification);
  setTimeout(() => notification.remove(), 3200);
}

function normalizeAlertCategory(alert) {
  return String(alert?.category || '未分类').trim() || '未分类';
}

function getAlertTimeHour(alert) {
  const raw = alert?.createdAt || alert?.timestamp || alert?.updatedAt || '';
  const dt = new Date(raw);
  if (Number.isNaN(dt.getTime())) return -1;
  return dt.getHours();
}

function isAlertInActiveCategory(alert) {
  if (!activeCategoryFilter) return true;
  return normalizeAlertCategory(alert) === activeCategoryFilter;
}

function isAlertInActiveHour(alert) {
  if (activeHourFilter < 0) return true;
  return getAlertTimeHour(alert) === activeHourFilter;
}

function isAlertInActiveStatus(alert) {
  if (!activeStatusFilter) return true;
  return String(alert?.status || 'pending').toLowerCase() === activeStatusFilter;
}

function getSortedActiveAlerts() {
  return [...activeAlertRecords]
    .sort((a, b) => (Number(b?.id) || 0) - (Number(a?.id) || 0));
}

function getAlertsForHourPanel() {
  return getSortedActiveAlerts().filter((alert) => isAlertInActiveCategory(alert) && isAlertInActiveStatus(alert));
}

function getVisibleAlerts() {
  return getSortedActiveAlerts().filter((alert) => isAlertInActiveCategory(alert) && isAlertInActiveHour(alert) && isAlertInActiveStatus(alert));
}

function renderAlertList() {
  const list = document.getElementById('homeAlertList');
  const listTargets = list ? [list] : [];
  if (!listTargets.length) return;

  const alerts = getVisibleAlerts().slice(0, 10);
  const html = !alerts.length
    ? '<div class="alert-item"><span class="alert-dot low"></span><div><div class="alert-title">暂无匹配告警</div><div class="alert-meta">可用上方筛选调整类型、时段与状态</div></div><span class="alert-state">空</span></div>'
    : alerts
      .map((alert) => {
        const severity = String(alert?.severity || '').toLowerCase();
        const dotClass = severity === 'critical' || severity === 'high' ? 'high' : severity === 'medium' ? 'medium' : 'low';
        const timeText = formatStageTime(alert.updatedAt || alert.createdAt || alert.timestamp || '');
        return `<div class="alert-item" data-alert-id="${String(alert.id || '')}"><span class="alert-dot ${dotClass}"></span><div><div class="alert-title">${alert.title || '违规告警'}</div><div class="alert-meta">${timeText} · ${normalizeAlertCategory(alert)}</div></div><span class="alert-state">${stageStatusText(alert.status || 'pending')}</span></div>`;
      })
      .join('');

  listTargets.forEach((list) => {
    list.innerHTML = html;
    list.querySelectorAll('.alert-item[data-alert-id]').forEach((item) => {
      const alertId = item.getAttribute('data-alert-id') || '';
      const alert = alerts.find((entry) => String(entry?.id || '') === alertId);
      if (!alert) return;
      item.addEventListener('click', () => {
        focusAlertOnMap(alert, 'leaflet-map');
        openStageDetail(alert);
      });
    });
  });
}

function applyCategoryFilterToMap() {
  Object.values(alertMarkers).forEach((entry) => {
    const visible = isAlertInActiveCategory(entry.alert) && isAlertInActiveHour(entry.alert) && isAlertInActiveStatus(entry.alert);
    const map = maps[entry.mapId];
    if (!map) return;
    entry.marker?.setMap(visible ? map : null);
    if (entry.circle) entry.circle.setMap(visible ? map : null);
  });
}

function getPrimaryAlertRef(alertId) {
  const idText = String(alertId || '');
  if (!idText) return null;
  return Object.values(alertMarkers).find((entry) => entry?.mapId === 'leaflet-map' && String(entry?.alert?.id || '') === idText) || null;
}

function removeAlertFromAllMaps(alertId) {
  const idText = String(alertId || '');
  if (!idText) return;
  Object.entries(alertMarkers).forEach(([key, entry]) => {
    if (String(entry?.alert?.id || '') !== idText) return;
    entry.marker?.setMap(null);
    if (entry.circle) entry.circle.setMap(null);
    delete alertMarkers[key];
  });
}

async function handleMapAlertAction(encodedAlertId, status, mapId = 'leaflet-map') {
  const alertId = decodeURIComponent(String(encodedAlertId || ''));
  if (!alertId || !status) return;
  const sourceAlert = activeAlertRecords.find((item) => String(item?.id || '') === String(alertId));
  if (!sourceAlert) return;

  const defaultMessage = status === 'resolved' ? '地图快捷操作：已标记为已解决' : '地图快捷操作：已推进到处理中';
  try {
    await handleAlert(alertId, sourceAlert.reportId || '', status, { message: defaultMessage });
    const updatedAlert = activeAlertRecords.find((item) => String(item?.id || '') === String(alertId));
    if (updatedAlert) {
      focusAlertOnMap(updatedAlert, mapId);
      return;
    }
    if (status === 'resolved') {
      const resolvedAlert = {
        ...sourceAlert,
        status: 'resolved',
        updatedAt: new Date().toISOString(),
        description: defaultMessage,
      };
      focusAlertOnMap(resolvedAlert, mapId);
    }
  } catch {
    // 失败时由 handleAlert 内部展示状态提示。
  }
}

function openAlertInfoOnMap(map, alert) {
  if (!map || !alert || typeof AMap === 'undefined') return;
  if (!Number.isFinite(Number(alert.longitude)) || !Number.isFinite(Number(alert.latitude))) return;
  const statusText = stageStatusText(alert.status || 'pending');
  const timeText = formatStageTime(alert.updatedAt || alert.createdAt || alert.timestamp || '');
  const encodedAlertId = encodeURIComponent(String(alert.id || ''));
  const status = String(alert.status || 'pending').toLowerCase();
  const actionHtml = encodedAlertId
    ? `<div style="display:flex;gap:8px;margin-top:8px"><button type="button" style="flex:1;height:28px;border-radius:6px;border:1px solid rgba(37,246,255,.35);background:rgba(37,246,255,.12);color:#0a2a2f;cursor:pointer" ${status === 'processing' || status === 'resolved' ? 'disabled' : ''} onclick="window.mapAlertProcess && window.mapAlertProcess('${encodedAlertId}')">转处理中</button><button type="button" style="flex:1;height:28px;border-radius:6px;border:1px solid rgba(158,255,0,.35);background:rgba(158,255,0,.18);color:#245000;cursor:pointer" ${status === 'resolved' ? 'disabled' : ''} onclick="window.mapAlertResolve && window.mapAlertResolve('${encodedAlertId}')">标记已解决</button></div>`
    : '';
  const infoWindow = new AMap.InfoWindow({
    offset: new AMap.Pixel(0, -24),
    closeWhenClickMap: true,
    content: `<div style="min-width:240px;padding:8px 10px"><strong style="display:block;margin-bottom:6px">${alert.title || '违规告警'}</strong><div style="font-size:12px;color:#666">状态：${statusText}</div><div style="font-size:12px;color:#666">类型：${normalizeAlertCategory(alert)}</div><div style="font-size:12px;color:#666">时间：${timeText}</div>${actionHtml}</div>`,
  });
  infoWindow.open(map, [Number(alert.longitude), Number(alert.latitude)]);
}

function focusAlertOnMap(alert, mapId = 'leaflet-map') {
  if (!alert) return false;
  const map = maps[mapId] || maps['leaflet-map'];
  if (!map) return false;
  const lng = Number(alert.longitude);
  const lat = Number(alert.latitude);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return false;
  map.setCenter([lng, lat]);
  map.setZoom(Math.max(Number(map.getZoom?.() || 16), 18));
  openAlertInfoOnMap(map, alert);
  return true;
}

function focusAlertById(alertId, mapId = 'leaflet-map') {
  const idText = String(alertId || '');
  if (!idText) return false;
  const alert = activeAlertRecords.find((item) => String(item?.id || '') === idText);
  if (!alert) return false;
  return focusAlertOnMap(alert, mapId);
}

function popupAlertAtCoordinate(longitude, latitude, options = {}) {
  const lng = Number(longitude);
  const lat = Number(latitude);
  const mapId = options.mapId || 'leaflet-map';
  const map = maps[mapId] || maps['leaflet-map'];
  if (!map || !Number.isFinite(lng) || !Number.isFinite(lat)) return false;

  const alert = {
    id: options.id || `manual-${Date.now()}`,
    title: options.title || '坐标告警',
    category: options.category || '手动触发',
    status: options.status || 'pending',
    severity: options.severity || 'high',
    description: options.description || '已在指定经纬度触发地图告警弹窗',
    longitude: lng,
    latitude: lat,
    timestamp: new Date().toISOString(),
  };

  addAlertMarker(map, alert);
  map.setCenter([lng, lat]);
  map.setZoom(Math.max(Number(map.getZoom?.() || 16), 18));
  openAlertInfoOnMap(map, alert);
  return true;
}

function updateAlertList() {
  renderAlertList();
}

function applyHomeFilters() {
  renderHomeLeftInsights();
  renderAlertList();
  applyCategoryFilterToMap();
  syncHomeCommandCenter();
  initAreaMonitor();
}

function setActiveCategoryFilter(category = '') {
  const next = String(category || '').trim();
  activeCategoryFilter = next && activeCategoryFilter !== next ? next : '';
  activePickerKey = '';
  applyHomeFilters();
}

function setActiveHourFilter(hour = -1) {
  const next = Number(hour);
  activeHourFilter = Number.isInteger(next) && next >= 0 && next <= 23 && activeHourFilter !== next ? next : -1;
  activePickerKey = '';
  applyHomeFilters();
}

function setActiveStatusFilter(status = '') {
  const next = String(status || '').trim().toLowerCase();
  activeStatusFilter = next && activeStatusFilter !== next ? next : '';
  activePickerKey = '';
  applyHomeFilters();
}

function clearHomeFilters() {
  activeCategoryFilter = '';
  activeHourFilter = -1;
  activeStatusFilter = '';
  activePickerKey = '';
  applyHomeFilters();
}

async function fetchGovernanceMetrics() {
  const empty = {
    closureRate: '--',
    avgHandleMinutes: '--',
    nightCoverageRate: '--',
    hotspotCount: '0',
    hotspotRows: [],
    recurrenceRate: '--',
    recurrenceRows: [],
    falsePositiveRate: '--',
    missRate: '--',
    definitions: {},
  };

  try {
    const response = await fetch(`${API_BASE_URL}/dashboard/metrics`);
    const data = await response.json();
    if (!response.ok || !data.success || !data.metrics) return empty;
    const metrics = data.metrics;
    return {
      closureRate: metrics.closureRate != null ? `${metrics.closureRate}%` : '--',
      avgHandleMinutes: metrics.avgHandleMinutes != null ? `${Math.round(metrics.avgHandleMinutes)}m` : '--',
      nightCoverageRate: metrics.nightCoverageRate != null ? `${metrics.nightCoverageRate}%` : '--',
      hotspotCount: String(metrics.hotspotCount || 0),
      hotspotRows: Array.isArray(metrics.hotspotTopN) ? metrics.hotspotTopN.map((item) => ({ name: item.name, count: item.count, ratio: item.ratio, level: item.level })) : [],
      recurrenceRate: metrics.recurrenceRate != null ? `${metrics.recurrenceRate}%` : '--',
      recurrenceRows: Array.isArray(metrics.recurrenceTopN) ? metrics.recurrenceTopN.map((item) => ({ name: item.name, count: item.count, ratio: item.ratio, level: item.level })) : [],
      falsePositiveRate: metrics.falsePositiveRate != null ? `${metrics.falsePositiveRate}%` : '--',
      missRate: metrics.missRate != null ? `${metrics.missRate}%` : '--',
      definitions: data.definitions || {},
    };
  } catch {
    return empty;
  }
}

function openMetricsHelpModal(content = '') {
  const modal = document.getElementById('metricsHelpModal');
  const body = document.getElementById('metricsHelpBody');
  if (!modal || !body) return;
  body.textContent = content || '暂无指标口径说明';
  modal.hidden = false;
}

function closeMetricsHelpModal() {
  const modal = document.getElementById('metricsHelpModal');
  if (modal) modal.hidden = true;
}

async function refreshGovernanceMetrics() {
  const metrics = await fetchGovernanceMetrics();
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  const setTitle = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.title = value;
  };
  const definitionsText = [
    ['闭环率', metrics.definitions.closureRate],
    ['平均处置时长', metrics.definitions.avgHandleMinutes],
    ['夜间覆盖率', metrics.definitions.nightCoverageRate],
    ['复发率', metrics.definitions.recurrenceRate],
    ['误报率', metrics.definitions.falsePositiveRate],
    ['漏报率', metrics.definitions.missRate],
  ].filter((item) => item[1]).map(([label, text]) => `${label}：${text}`).join('\n');

  setText('homeStatClosedLoop', metrics.closureRate);
  setText('homeStatAvgHandle', metrics.avgHandleMinutes);
  setText('homeStatNightCoverage', metrics.nightCoverageRate);
  setText('homeStatHotspotCount', metrics.hotspotCount);
  setTitle('homeStatClosedLoop', metrics.definitions.closureRate || '口径：已解决工单数 / 工单总数');
  setTitle('homeStatAvgHandle', metrics.definitions.avgHandleMinutes || '口径：工单 elapsedHours 的平均值，换算为分钟');
  setTitle('homeStatNightCoverage', metrics.definitions.nightCoverageRate || '口径：20:00-06:00 夜间时段工单占比，用作夜间覆盖代理指标');
  setTitle('homeStatHotspotCount', '口径：按区域聚合后的高风险热区 TopN 数量');
  setText('homeStatClosedLoopTrend', '工单闭环');
  setText('homeStatAvgHandleTrend', '工单口径');
  setText('homeStatNightCoverageTrend', '20:00-06:00');
  setText('homeStatHotspotCountTrend', `Top${metrics.hotspotRows.length || 0}`);

  const crRaw = metrics.closureRate;
  governanceClosureDisplay = crRaw != null && crRaw !== '--' ? String(crRaw) : '--';
  const crNum = parseFloat(String(crRaw).replace(/%/g, ''));
  governanceClosureBarPct = Number.isFinite(crNum) ? Math.min(100, Math.max(0, crNum)) : 0;

  const homeHelpBtn = document.getElementById('homeMetricsHelpBtn');
  if (homeHelpBtn) homeHelpBtn.onclick = () => openMetricsHelpModal(definitionsText || '暂无指标口径说明');

  const setClosure = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  setClosure('homeOverlayClosedLoop', governanceClosureDisplay);
}

function syncHomeCommandCenter(focusAlert = null) {
  const alerts = getVisibleAlerts();
  const pendingCount = alerts.filter((item) => String(item?.status || 'pending').toLowerCase() === 'pending').length;
  const processingCount = alerts.filter((item) => String(item?.status || '').toLowerCase() === 'processing').length;
  const activeCount = alerts.length;

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  setText('homeOverlayActiveAlerts', String(activeCount));
  setText('homeOverlayProcessing', String(processingCount));
  setText('homeOverlayClosedLoop', governanceClosureDisplay);

  renderHomeActiveTaskPanel();
}

function focusLatestAlert() {
  const latest = getVisibleAlerts()[0];
  if (!latest) return;
  focusAlertOnMap(latest, 'leaflet-map');
  openStageDetail(latest);
}

function processNewAlerts(alerts) {
  if (!Array.isArray(alerts) || alerts.length === 0 || typeof AMap === 'undefined') return;
  const primaryMap = maps['leaflet-map'];
  const shouldAutoFocusNewAlerts = hasCompletedInitialAlertLoad;

  alerts.forEach((alert) => {
    const idNum = Number(alert.id) || 0;
    if (idNum > lastAlertId) lastAlertId = idNum;
    const recordIndex = activeAlertRecords.findIndex((item) => String(item?.id || '') === String(alert.id || ''));
    const isNewAlert = recordIndex < 0;
    if (!isNewAlert) {
      activeAlertRecords[recordIndex] = { ...activeAlertRecords[recordIndex], ...alert };
    } else {
      activeAlertRecords.push(alert);
      if (shouldAutoFocusNewAlerts) {
        showAlertNotification(alert);
        if (String(alert.status || 'pending').toLowerCase() !== 'resolved') {
          focusAlertOnMap(alert, 'leaflet-map');
        }
      }
    }

    addAlertMarker(primaryMap, alert);
  });

  hasCompletedInitialAlertLoad = true;
  renderAlertList();
  applyCategoryFilterToMap();
  syncHomeCommandCenter(alerts[alerts.length - 1] || null);
  initAreaMonitor();
  renderHomeLeftInsights();
  void syncHomeOverviewByTickets();
  void refreshGovernanceMetrics();
}

async function handleAlert(alertId, reportIdOrStatus, maybeStatus, payload = {}) {
  let reportId = reportIdOrStatus;
  let status = maybeStatus;
  const ref = getPrimaryAlertRef(alertId);

  if (!status) {
    status = reportIdOrStatus;
    reportId = ref?.alert?.reportId || '';
  }

  const ok = await updateAlertStatus(reportId, status, payload);
  if (!ok) {
    renderStageTimeline([], '状态更新失败');
    throw new Error('UPDATE_FAILED');
  }
  if (!ref) return;

  const activeRecord = activeAlertRecords.find((item) => String(item?.id || '') === String(alertId));
  if (activeRecord) {
    activeRecord.status = status;
    if (payload.message) activeRecord.description = payload.message;
  }

  Object.values(alertMarkers).forEach((entry) => {
    if (String(entry?.alert?.id || '') !== String(alertId)) return;
    entry.alert.status = status;
    if (payload.message) entry.alert.description = payload.message;
  });

  if (status === 'resolved') {
    removeAlertFromAllMaps(alertId);
    activeAlertRecords = activeAlertRecords.filter((item) => String(item?.id || '') !== String(alertId));
    syncHomeCommandCenter();
    initAreaMonitor();
    renderHomeLeftInsights();
    void syncHomeOverviewByTickets();
    void refreshGovernanceMetrics();
    pendingStageTimelineHighlightReportId = reportId;
    if (activeStageAlert && String(activeStageAlert.id || '') === String(alertId)) {
      openStageDetail(ref.alert);
      return;
    }
    return;
  }

  Object.values(alertMarkers).forEach((entry) => {
    if (String(entry?.alert?.id || '') !== String(alertId)) return;
    if (status === 'processing' && entry.circle) {
      entry.circle.setOptions({ strokeColor: '#9eff00', fillColor: '#9eff00' });
    }
  });

  syncHomeCommandCenter(ref.alert);
  initAreaMonitor();
  renderHomeLeftInsights();
  void syncHomeOverviewByTickets();
  void refreshGovernanceMetrics();

  if (activeStageAlert && String(activeStageAlert.id || '') === String(alertId)) {
    pendingStageTimelineHighlightReportId = reportId;
    openStageDetail(ref.alert);
  }
}

function startAlertPolling(interval = 5000) {
  if (alertPollingInterval) {
    clearInterval(alertPollingInterval);
  }

  fetchAlerts(lastAlertId).then(processNewAlerts);
  alertPollingInterval = setInterval(async () => {
    const alerts = await fetchAlerts(lastAlertId);
    if (alerts.length) processNewAlerts(alerts);
  }, interval);
}

function stopAlertPolling() {
  if (!alertPollingInterval) return;
  clearInterval(alertPollingInterval);
  alertPollingInterval = null;
}

function initAreaMonitor() {
  const el = document.getElementById('areaMonitorScroll');
  if (!el) return;

  const alerts = getSortedActiveAlerts();
  if (!alerts.length) {
    el.innerHTML = '<div class="monitor-item"><span>暂无实时区域事件</span><span class="monitor-state">等待告警</span></div>';
    return;
  }

  const grouped = new Map();
  alerts.forEach((alert) => {
    const key = String(alert.location || alert.reporter || '未知区域').trim() || '未知区域';
    if (!grouped.has(key)) grouped.set(key, { total: 0, pending: 0, processing: 0, resolved: 0 });
    const item = grouped.get(key);
    item.total += 1;
    const status = String(alert.status || 'pending').toLowerCase();
    if (status === 'processing') item.processing += 1;
    else if (status === 'resolved') item.resolved += 1;
    else item.pending += 1;
  });

  const rows = [...grouped.entries()]
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 6)
    .map(([name, summary]) => {
      const stateText = summary.pending > 0
        ? `待处置 ${summary.pending}`
        : summary.processing > 0
          ? `处理中 ${summary.processing}`
          : `已解决 ${summary.resolved}`;
      return `<div class="monitor-item"><span>${name}</span><span class="monitor-state">${stateText}</span></div>`;
    });

  el.innerHTML = rows.join('');
}

async function refreshEvParkingStats() {
  try {
    const response = await fetch(`${API_BASE_URL}/detections/ev-parking-stats?days=30`);
    const data = await response.json();
    if (data.success) {
      evParkingStats = {
        total: Number(data.total) || 0,
        byLabel: Array.isArray(data.byLabel) ? data.byLabel : [],
        days: Number(data.days) || 30,
      };
    }
  } catch (err) {
    console.warn('[ev-parking-stats]', err);
  }
  renderHomeLeftInsights();
}

function renderEvParkingInsights() {
  const pieEl = document.getElementById('homeCategoryPie');
  const pieTotalEl = document.getElementById('homeCategoryPieTotal');
  const pieLegendEl = document.getElementById('homeCategoryLegend');
  const rankEl = document.getElementById('homeCategoryRank');
  syncHomeTaskCardHighlight();
  if (!(pieEl && pieTotalEl && pieLegendEl)) return;

  const rows = evParkingStats.byLabel || [];
  const total = Number(evParkingStats.total) || 0;

  if (!total) {
    pieEl.style.background = 'conic-gradient(rgba(0,224,255,0.2) 0deg 360deg)';
    pieTotalEl.textContent = '0';
    pieLegendEl.innerHTML =
      '<div class="category-legend-item"><span class="category-legend-left"><span class="category-legend-dot" style="background:rgba(0,224,255,0.4)"></span><span>暂无违停相关识别</span></span><span class="category-legend-ratio">—</span></div>';
    if (rankEl) {
      rankEl.innerHTML = `<div class="rank-header"><span>识别标签</span><span>次数</span><span>占比</span><span>说明</span></div>
        <div class="rank-row"><span>—</span><span>0</span><span>0%</span><span class="risk low">暂无数据</span></div>`;
    }
    pieEl.onclick = null;
    return;
  }

  const palette = ['#25f6ff', '#21f6a2', '#f4b13b', '#ff7b2c', '#7d93a9', '#a78bfa'];
  let degreeStart = 0;
  const segments = [];
  const legendRows = [];
  rows.forEach((item, index) => {
    const count = Number(item.count) || 0;
    const ratio = total > 0 ? count / total : 0;
    const degreeEnd = degreeStart + ratio * 360;
    const color = palette[index % palette.length];
    segments.push(`${color} ${degreeStart.toFixed(1)}deg ${degreeEnd.toFixed(1)}deg`);
    legendRows.push(
      `<div class="category-legend-item"><span class="category-legend-left"><span class="category-legend-dot" style="background:${color}"></span><span>${item.label || '未命名'}</span></span><span class="category-legend-ratio">${(ratio * 100).toFixed(1)}%</span></div>`,
    );
    degreeStart = degreeEnd;
  });
  pieEl.style.background = segments.length ? `conic-gradient(${segments.join(',')})` : 'conic-gradient(rgba(0,224,255,0.2) 0deg 360deg)';
  pieTotalEl.textContent = String(total);
  pieLegendEl.innerHTML = legendRows.join('');
  pieEl.onclick = null;

  if (rankEl) {
    rankEl.innerHTML = `<div class="rank-header"><span>识别标签</span><span>次数</span><span>占比</span><span>说明</span></div>${rows
      .map((item) => {
        const count = Number(item.count) || 0;
        const ratio = total > 0 ? ((count / total) * 100).toFixed(1) : '0';
        return `<div class="rank-row"><span>${item.label || '未命名'}</span><span>${count}</span><span>${ratio}%</span><span class="risk low">识别</span></div>`;
      })
      .join('')}`;
  }
}


async function refreshRoadDefectStats() {
  try {
    const response = await fetch(`${API_BASE_URL}/detections/road-defect-stats?days=30`);
    const data = await response.json();
    if (data.success) {
      roadDefectStats = {
        total: Number(data.total) || 0,
        byLabel: Array.isArray(data.byLabel) ? data.byLabel : [],
        days: Number(data.days) || 30,
      };
    }
  } catch (err) {
    console.warn('[road-defect-stats]', err);
  }
  renderHomeLeftInsights();
}

function renderRoadDefectInsights() {
  const totalEl = document.getElementById('homeRoadDefectTotal');
  const subEl = document.getElementById('homeRoadDefectSub');
  const barsEl = document.getElementById('homeRoadDefectBars');
  const rankEl = document.getElementById('homeRoadDefectRank');
  syncHomeTaskCardHighlight();
  if (!(totalEl && barsEl)) return;

  const rows = Array.isArray(roadDefectStats.byLabel) ? roadDefectStats.byLabel : [];
  const total = Number(roadDefectStats.total) || 0;
  const top = rows.slice(0, 6);
  const maxCount = Math.max(1, ...top.map((item) => Number(item.count) || 0));
  const palette = ['#25f6ff', '#21f6a2', '#f4b13b', '#ff7b2c', '#7d93a9', '#a78bfa'];

  totalEl.textContent = String(total);
  if (subEl) {
    subEl.textContent = total
      ? `覆盖 ${top.length || rows.length} 类缺陷标签`
      : '近30天暂无道路缺陷识别';
  }

  if (!total) {
    barsEl.innerHTML = `
      <div class="road-defect-bar-row">
        <span class="road-defect-bar-label">暂无数据</span>
        <span class="road-defect-bar-track"><span class="road-defect-bar-fill" style="width:8%"></span></span>
        <span class="road-defect-bar-count">0</span>
      </div>`;
    if (rankEl) {
      rankEl.innerHTML = `<div class="rank-header"><span>缺陷标签</span><span>次数</span><span>占比</span><span>说明</span></div>
        <div class="rank-row"><span>--</span><span>0</span><span>0%</span><span class="risk low">等待数据</span></div>`;
    }
    return;
  }

  barsEl.innerHTML = top
    .map((item, index) => {
      const label = String(item.label || '未标注').trim() || '未标注';
      const count = Number(item.count) || 0;
      const width = Math.max(8, (count / maxCount) * 100);
      const color = palette[index % palette.length];
      return `<div class="road-defect-bar-row" title="${label}">
        <span class="road-defect-bar-label">${label}</span>
        <span class="road-defect-bar-track"><span class="road-defect-bar-fill" style="width:${width.toFixed(1)}%;background:linear-gradient(90deg, ${color}55, ${color})"></span></span>
        <span class="road-defect-bar-count">${count}</span>
      </div>`;
    })
    .join('');

  if (rankEl) {
    rankEl.innerHTML =
      `<div class="rank-header"><span>缺陷标签</span><span>次数</span><span>占比</span><span>说明</span></div>` +
      top
        .map((item) => {
          const label = String(item.label || '未标注').trim() || '未标注';
          const count = Number(item.count) || 0;
          const ratio = total > 0 ? Math.round((count / total) * 100) : 0;
          const risk =
            ratio >= 40 ? 'high' : ratio >= 20 ? 'mid' : 'low';
          const riskText =
            risk === 'high' ? '高频缺陷' : risk === 'mid' ? '需关注' : '低占比';
          return `<div class="rank-row"><span>${label}</span><span>${count}</span><span>${ratio}%</span><span class="risk ${risk}">${riskText}</span></div>`;
        })
        .join('');
  }
}


function renderHomeLeftInsights() {
  renderEvParkingInsights();
  renderRoadDefectInsights();
}

async function syncHomeOverviewByTickets() {
  // 今日数据概览卡片已从首页移除，保留空函数避免旧调用报错。
}

function initAllMaps() {
  initMap('leaflet-map');
}

function bindHomeMapCommandPanel() {
  const panel = document.getElementById('homeMapCommandPanel');
  const toggleBtn = document.getElementById('homeMapCommandToggleBtn');
  if (!panel || !toggleBtn) return;
  toggleBtn.addEventListener('click', () => {
    const collapsed = panel.classList.toggle('is-collapsed');
    toggleBtn.textContent = collapsed ? '展开控制台' : '收起控制台';
    const homeMap = maps['leaflet-map'];
    if (homeMap?.resize) {
      setTimeout(() => homeMap.resize(), 0);
    }
  });
}

const campusMap = {
  refreshWeather,
  refreshAlerts: () => fetchAlerts(0).then(processNewAlerts),
};

window.handleSearch = handleSearch;
window.clearSearch = clearSearch;
window.quickSearchPOI = quickSearchPOI;
window.toggleMapLayer = toggleMapLayer;
window.closeResultPanel = closeResultPanel;
window.handleAlert = handleAlert;
window.mapAlertProcess = (encodedAlertId, mapId = 'leaflet-map') => handleMapAlertAction(encodedAlertId, 'processing', mapId);
window.mapAlertResolve = (encodedAlertId, mapId = 'leaflet-map') => handleMapAlertAction(encodedAlertId, 'resolved', mapId);
window.campusMap = campusMap;
window.alertSystem = {
  start: startAlertPolling,
  stop: stopAlertPolling,
  fetch: fetchAlerts,
  getMarkers: () => alertMarkers,
  handleAlert,
  focusAlertById,
  popupAlertAtCoordinate,
  clearHomeFilters,
};

function syncHomeRobotPatrolIdFromConfig() {
  const rid = String(window.__DASHBOARD_CONFIG__?.patrolRobotId || 'RBT-01').trim() || 'RBT-01';
  const homeEl = document.getElementById('homeRobotPatrolId');
  if (homeEl) homeEl.textContent = rid;
}

const HOME_TASK_SCENES = {
  'ev-parking': {
    key: 'ev-parking',
    title: '电动车违停巡检',
    page: 'patrol',
    tagId: 'homeEvParkingRangeTag',
  },
  'road-defect': {
    key: 'road-defect',
    title: '道路缺陷巡检',
    page: 'road-defect',
    tagId: 'homeRoadDefectRangeTag',
  },
  gauge: {
    key: 'gauge',
    title: '仪表巡检',
    page: 'gauge',
    tagId: 'homeGaugeFeedTag',
  },
};

const homeActiveTask = {
  scene: '',
  status: 'idle',
  runId: '',
  startedAt: '',
  finishedAt: '',
  progressLabel: '待命',
  findingCount: 0,
  latestFinding: '',
  timelineNote: '当前无活跃任务',
  findings: [],
  reportPromptVisible: false,
  reportPromptHandled: false,
  robotConnected: false,
};

function normalizeTaskStatus(raw) {
  const value = String(raw || '').trim().toUpperCase();
  if (value === 'RUNNING' || value === '执行中' || value === '进行中' || value === '启动中') return 'running';
  if (value === 'SUCCESS' || value === '成功完成' || value === '完成' || value === '已完成') return 'success';
  if (value === 'FAILURE' || value === '流程失败' || value === '失败' || value === '启动失败' || value === '数据异常' || value === '识别异常') {
    return 'failure';
  }
  return 'idle';
}

function formatTaskClock(value = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString('zh-CN', { hour12: false });
}

function navigateToPage(target) {
  if (!target) return;
  const button = document.querySelector(`.nav-item[data-page="${target}"]`);
  if (button) button.click();
}

function syncHomeTaskCardHighlight() {
  document.querySelectorAll('.home-task-progress-card[data-task-scene]').forEach((card) => {
    const scene = card.getAttribute('data-task-scene');
    const active = Boolean(homeActiveTask.scene) && scene === homeActiveTask.scene && homeActiveTask.status !== 'idle';
    card.classList.toggle('is-active-task', active);
  });

  Object.values(HOME_TASK_SCENES).forEach((scene) => {
    const tag = document.getElementById(scene.tagId);
    if (!tag) return;
    if (homeActiveTask.scene === scene.key && homeActiveTask.status === 'running') {
      tag.textContent = '执行中';
    } else if (homeActiveTask.scene === scene.key && homeActiveTask.status === 'success') {
      tag.textContent = '已完成';
    } else if (homeActiveTask.scene === scene.key && homeActiveTask.status === 'failure') {
      tag.textContent = '异常';
    } else if (!(homeActiveTask.scene === scene.key && homeActiveTask.status !== 'idle')) {
      if (scene.key === 'gauge') tag.textContent = '待命';
      else tag.textContent = '待命';
    }
  });
}

function getHomeTaskCurrentView() {
  const scene = HOME_TASK_SCENES[homeActiveTask.scene];
  if (!scene || homeActiveTask.status === 'idle') {
    if (!homeActiveTask.robotConnected) {
      return {
        statusText: '空闲',
        statusClass: 'map-focus-status status-resolved',
        title: '当前无活跃任务',
        meta: '机器人未连接',
        label: '空闲',
        text: '当前无活跃任务',
        currentClass: 'is-idle',
      };
    }
    return {
      statusText: '空闲',
      statusClass: 'map-focus-status status-resolved',
      title: '当前无活跃任务',
      meta: '等待任务启动',
      label: '空闲',
      text: '当前无活跃任务',
      currentClass: 'is-idle',
    };
  }

  if (homeActiveTask.status === 'running') {
    return {
      statusText: '执行中',
      statusClass: 'map-focus-status status-processing',
      title: scene.title,
      meta: [
        homeActiveTask.runId ? `任务 #${homeActiveTask.runId}` : '',
        homeActiveTask.progressLabel || '现场执行中',
        `发现 ${homeActiveTask.findingCount}`,
      ]
        .filter(Boolean)
        .join(' · '),
      label: homeActiveTask.progressLabel || '现场执行中',
      text: homeActiveTask.latestFinding || homeActiveTask.timelineNote || '任务执行中',
      currentClass: 'is-running',
    };
  }

  if (homeActiveTask.status === 'success') {
    return {
      statusText: '已完成',
      statusClass: 'map-focus-status status-resolved',
      title: scene.title,
      meta: [
        homeActiveTask.runId ? `任务 #${homeActiveTask.runId}` : '',
        homeActiveTask.finishedAt ? `完成于 ${homeActiveTask.finishedAt}` : '任务已完成',
        `发现 ${homeActiveTask.findingCount}`,
      ]
        .filter(Boolean)
        .join(' · '),
      label: '任务已完成',
      text: homeActiveTask.latestFinding || `本轮共回传 ${homeActiveTask.findingCount} 条结果`,
      currentClass: 'is-done',
    };
  }

  return {
    statusText: '异常',
    statusClass: 'map-focus-status',
    title: scene.title,
    meta: homeActiveTask.progressLabel || '任务异常',
    label: homeActiveTask.progressLabel || '任务异常',
    text: homeActiveTask.latestFinding || homeActiveTask.timelineNote || '任务结束异常',
    currentClass: 'is-error',
  };
}

function renderHomeActiveTaskPanel() {
  const statusEl = document.getElementById('homeFocusStatus');
  const reportBtn = document.getElementById('homeFocusReportBtn');
  const currentRoot = document.getElementById('homeTaskCurrent');
  const currentLabel = document.getElementById('homeTaskCurrentLabel');
  const currentText = document.getElementById('homeTaskCurrentText');
  const view = getHomeTaskCurrentView();
  const setText = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };

  if (statusEl) {
    statusEl.textContent = view.statusText;
    statusEl.className = view.statusClass;
  }
  setText('homeFocusTitle', view.title);
  setText('homeFocusMeta', view.meta);
  if (currentLabel) currentLabel.textContent = view.label;
  if (currentText) currentText.textContent = view.text;
  if (currentRoot) {
    currentRoot.className = `home-task-current ${view.currentClass}`;
  }
  if (reportBtn) reportBtn.hidden = homeActiveTask.status !== 'success';
  syncHomeTaskCardHighlight();
}

function showHomeTaskReportPrompt() {
  const prompt = document.getElementById('homeTaskReportPrompt');
  const title = document.getElementById('homeTaskReportPromptTitle');
  const text = document.getElementById('homeTaskReportPromptText');
  const scene = HOME_TASK_SCENES[homeActiveTask.scene];
  if (!prompt || !scene) return;
  if (title) title.textContent = `${scene.title}已完成`;
  if (text) text.textContent = `本轮共回传 ${homeActiveTask.findingCount} 条结果，是否立即生成任务报告？`;
  prompt.hidden = false;
  homeActiveTask.reportPromptVisible = true;
}

function hideHomeTaskReportPrompt() {
  const prompt = document.getElementById('homeTaskReportPrompt');
  if (prompt) prompt.hidden = true;
  homeActiveTask.reportPromptVisible = false;
}

function buildHomeTaskReport() {
  const scene = HOME_TASK_SCENES[homeActiveTask.scene] || HOME_TASK_SCENES['ev-parking'];
  const durationText =
    homeActiveTask.startedAt && homeActiveTask.finishedAt
      ? `${homeActiveTask.startedAt} 至 ${homeActiveTask.finishedAt}`
      : homeActiveTask.startedAt || '--';
  return {
    title: `${scene.title}任务报告`,
    subtitle: homeActiveTask.runId ? `任务 #${homeActiveTask.runId}` : '本轮巡检自动汇总',
    summary: `${scene.title}已结束。本轮状态为「${
      homeActiveTask.status === 'success' ? '已完成' : homeActiveTask.status === 'failure' ? '异常结束' : homeActiveTask.progressLabel
    }」，共回传 ${homeActiveTask.findingCount} 条关键发现。${
      homeActiveTask.latestFinding ? `最近一条：${homeActiveTask.latestFinding}。` : ''
    }`,
    metrics: [
      { label: '任务场景', value: scene.title },
      { label: '发现数量', value: String(homeActiveTask.findingCount) },
      { label: '执行时段', value: durationText },
    ],
    timeline: [
      { title: '任务启动', text: homeActiveTask.startedAt ? `启动于 ${homeActiveTask.startedAt}` : '已启动' },
      { title: '现场执行', text: homeActiveTask.progressLabel || '已执行' },
      {
        title: '结果回传',
        text: homeActiveTask.findingCount
          ? `累计回传 ${homeActiveTask.findingCount} 条，最近：${homeActiveTask.latestFinding || '--'}`
          : '本轮无新增发现',
      },
      {
        title: '任务完成',
        text: homeActiveTask.finishedAt ? `完成于 ${homeActiveTask.finishedAt}` : '已结束',
      },
    ],
    findings: homeActiveTask.findings.length
      ? homeActiveTask.findings.slice(0, 8)
      : [{ title: '暂无关键发现', text: '本轮任务未回传检测结果。' }],
  };
}

async function openHomeTaskReportModal() {
  const modal = document.getElementById('homeTaskReportModal');
  const report = buildHomeTaskReport();
  void persistHomeTaskReportToRepo().then((saved) => {
    if (saved?.report?.url) {
      const subtitle = document.getElementById('homeTaskReportSubtitle');
      if (subtitle) subtitle.textContent = `${report.subtitle} · 已写入 ${saved.report.url}`;
    }
  });
  const title = document.getElementById('homeTaskReportTitle');
  const subtitle = document.getElementById('homeTaskReportSubtitle');
  const summary = document.getElementById('homeTaskReportSummary');
  const metrics = document.getElementById('homeTaskReportMetrics');
  const timeline = document.getElementById('homeTaskReportTimeline');
  const findings = document.getElementById('homeTaskReportFindings');
  if (!modal) return;

  if (title) title.textContent = report.title;
  if (subtitle) subtitle.textContent = report.subtitle;
  if (summary) summary.textContent = report.summary;
  if (metrics) {
    metrics.innerHTML = report.metrics
      .map((item) => `<div class="home-task-report-metric"><span>${item.label}</span><strong>${item.value}</strong></div>`)
      .join('');
  }
  if (timeline) {
    timeline.innerHTML = report.timeline
      .map((item) => `<div class="home-task-report-timeline-item"><strong>${item.title}</strong><span>${item.text}</span></div>`)
      .join('');
  }
  if (findings) {
    findings.innerHTML = report.findings
      .map((item) => `<div class="home-task-report-finding"><strong>${item.title}</strong><span>${item.text}</span></div>`)
      .join('');
  }
  hideHomeTaskReportPrompt();
  modal.hidden = false;
}

function closeHomeTaskReportModal() {
  const modal = document.getElementById('homeTaskReportModal');
  if (modal) modal.hidden = true;
}

function resolvePatrolReportUrl(url) {
  const value = String(url || '').trim();
  if (!value) return '';
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith('/')) {
    return `${window.location.origin}${value}`;
  }
  return new URL(value, window.location.href).href;
}

async function fetchPatrolReports(limit = 50) {
  const response = await fetch(`${API_BASE_URL}/patrol/reports?limit=${limit}`);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.success) {
    throw new Error(data?.message || data?.error || `HTTP ${response.status}`);
  }
  return Array.isArray(data.items) ? data.items : [];
}

async function fetchPatrolTaskStatus() {
  const response = await fetch(`${API_BASE_URL}/patrol/task/status`);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.success) {
    throw new Error(data?.message || data?.error || `HTTP ${response.status}`);
  }
  return data;
}

async function requestPatrolTaskStart(scene, { dryRun = true } = {}) {
  const response = await fetch(`${API_BASE_URL}/patrol/task/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scene, dryRun }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.success) {
    throw new Error(data?.message || data?.error || `HTTP ${response.status}`);
  }
  return data;
}

async function requestPatrolReportGenerate(source = 'dashboard') {
  const response = await fetch(`${API_BASE_URL}/patrol/reports/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok || !data?.success) {
    throw new Error(data?.message || data?.error || `HTTP ${response.status}`);
  }
  return data;
}

function applyAssistantTaskState(task = {}) {
  if (!task || typeof task !== 'object') return;
  const scene = String(task.scene || '').trim();
  if (!scene || !HOME_TASK_SCENES[scene]) return;
  updateHomeActiveTaskFromScene(scene, {
    controllerStatusRaw: task.status,
    taskProgressLabel: task.progressLabel,
    taskRunSerial: task.runId,
    violationCount: Number(task.findingCount) || 0,
  });
  if (Array.isArray(task.findings) && task.findings.length && !homeActiveTask.findings.length) {
    homeActiveTask.findings = task.findings.slice(0, 20).map((item) => ({
      title: item.title || '关键发现',
      text: item.text || '',
      at: item.at || formatTaskClock(),
    }));
    homeActiveTask.latestFinding = task.latestFinding || homeActiveTask.latestFinding;
    renderHomeActiveTaskPanel();
  }
}

async function persistHomeTaskReportToRepo() {
  try {
    if (homeActiveTask.status !== 'success' && homeActiveTask.status !== 'failure') {
      return null;
    }
    await fetch(`${API_BASE_URL}/patrol/task/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        scene: homeActiveTask.scene,
        status: homeActiveTask.status,
        progressLabel: homeActiveTask.progressLabel,
        findingCount: homeActiveTask.findingCount,
        latestFinding: homeActiveTask.latestFinding,
        runId: homeActiveTask.runId,
        message: homeActiveTask.timelineNote,
      }),
    });
    return await requestPatrolReportGenerate('dashboard');
  } catch (error) {
    console.warn('[patrol-report-persist]', error);
    return null;
  }
}

function getPatrolReportsIndexUrl() {
  try {
    return new URL('../report/index.html', window.location.href).href;
  } catch (_error) {
    return `${window.location.origin}/report/index.html`;
  }
}

function openPatrolReportsIndex() {
  const url = getPatrolReportsIndexUrl();
  window.open(url, '_blank', 'noopener,noreferrer');
}

function bindPatrolReportsEntry() {
  document.getElementById('homePatrolReportsFab')?.addEventListener('click', openPatrolReportsIndex);
}

function pushHomeTaskFinding(title, text) {
  const finding = {
    title: String(title || '关键发现'),
    text: String(text || ''),
    at: formatTaskClock(),
  };
  homeActiveTask.findings.unshift(finding);
  if (homeActiveTask.findings.length > 20) homeActiveTask.findings = homeActiveTask.findings.slice(0, 20);
  homeActiveTask.findingCount += 1;
  homeActiveTask.latestFinding = `${finding.title} · ${finding.text}`;
  renderHomeActiveTaskPanel();
}

function updateHomeActiveTaskFromScene(sceneKey, detail = {}) {
  const scene = HOME_TASK_SCENES[sceneKey];
  if (!scene) return;

  const nextStatus = normalizeTaskStatus(detail.controllerStatusRaw || detail.status || detail.state);
  const previousScene = homeActiveTask.scene;
  const previousStatus = homeActiveTask.status;

  if (typeof detail.rosConnected === 'boolean') {
    homeActiveTask.robotConnected = detail.rosConnected;
  }

  if (nextStatus === 'idle') {
    if (homeActiveTask.scene === sceneKey && previousStatus === 'running') {
      // 短暂空状态不立刻清空，避免闪烁
      homeActiveTask.progressLabel = detail.taskProgressLabel || homeActiveTask.progressLabel;
      renderHomeActiveTaskPanel();
    }
    return;
  }

  if (homeActiveTask.scene && homeActiveTask.scene !== sceneKey && homeActiveTask.status === 'running') {
    // 同时只允许一个任务高亮，后来的执行场景覆盖当前活跃任务
  }

  homeActiveTask.scene = sceneKey;
  homeActiveTask.status = nextStatus;
  homeActiveTask.runId =
    detail.taskRunSerial != null && String(detail.taskRunSerial).trim() !== ''
      ? String(detail.taskRunSerial)
      : homeActiveTask.runId || '';
  homeActiveTask.progressLabel = detail.taskProgressLabel || detail.controllerStatusZh || homeActiveTask.progressLabel;

  if (nextStatus === 'running' && (previousScene !== sceneKey || previousStatus !== 'running')) {
    homeActiveTask.startedAt = formatTaskClock();
    homeActiveTask.finishedAt = '';
    homeActiveTask.findingCount = Number(detail.violationCount) || 0;
    homeActiveTask.latestFinding = '';
    homeActiveTask.findings = [];
    homeActiveTask.reportPromptHandled = false;
    homeActiveTask.timelineNote = `${scene.title}执行中`;
    hideHomeTaskReportPrompt();
    closeHomeTaskReportModal();
  }

  if (typeof detail.violationCount === 'number' && detail.violationCount >= homeActiveTask.findingCount) {
    homeActiveTask.findingCount = detail.violationCount;
  }

  if (nextStatus === 'success' || nextStatus === 'failure') {
    homeActiveTask.finishedAt = homeActiveTask.finishedAt || formatTaskClock();
    homeActiveTask.timelineNote =
      nextStatus === 'success' ? `${scene.title}已完成` : `${scene.title}异常结束`;
    if (nextStatus === 'success' && !homeActiveTask.reportPromptHandled) {
      homeActiveTask.reportPromptHandled = true;
      showHomeTaskReportPrompt();
    }
  }

  renderHomeActiveTaskPanel();
}

function updateHomeRobotDashboardPanel(detail) {
  const conn = document.getElementById('homeRobotRosConn');
  const bt = document.getElementById('homeRobotBtStatus');
  const vc = document.getElementById('homeRobotViolationCount');
  if (conn) {
    conn.textContent = detail.rosConnected ? '已连接' : '未连接';
    conn.classList.remove('is-ok', 'is-off');
    conn.classList.add(detail.rosConnected ? 'is-ok' : 'is-off');
  }
  if (bt) bt.textContent = detail.controllerStatusZh || '--';
  if (vc) vc.textContent = String(detail.violationCount ?? 0);
  homeActiveTask.robotConnected = Boolean(detail.rosConnected);
  if (homeActiveTask.status === 'idle') renderHomeActiveTaskPanel();
}

function addHomeViolationMapMarker(gcjLng, gcjLat, detail = {}) {
  const map = maps['leaflet-map'];
  if (!map || typeof AMap === 'undefined') return;
  if (!Number.isFinite(gcjLng) || !Number.isFinite(gcjLat)) return;
  const seq = detail.seq != null ? String(detail.seq) : '';
  const marker = new AMap.Marker({
    position: [gcjLng, gcjLat],
    content:
      '<div style="width:18px;height:18px;background:#ff7b2c;border:2px solid #fff;border-radius:50%;box-shadow:0 0 12px rgba(255,123,44,0.85)" title="电动车违停"></div>',
    title: seq ? `违停 #${seq}` : '违停发生点',
    zIndex: 200,
  });
  marker.setMap(map);
  homeViolationMarkers.push(marker);
  const ev0 = Array.isArray(detail.plate_ocr_events) ? detail.plate_ocr_events[0] : null;
  const plateRaw = ev0 && (ev0.plate_text ?? ev0.plateText);
  const plate = plateRaw != null ? String(plateRaw).trim() : '';
  marker.on('click', () => {
    const infoWindow = new AMap.InfoWindow({
      offset: new AMap.Pixel(0, -24),
      content: `<div style="padding:8px;min-width:200px"><strong>违停发生点</strong>${seq ? `<div style="margin-top:4px;font-size:12px">seq ${seq}</div>` : ''}${plate ? `<div style="margin-top:4px;font-size:12px">车牌 ${plate}</div>` : ''}</div>`,
    });
    infoWindow.open(map, [gcjLng, gcjLat]);
  });
}

function bindRobotDashboardListeners() {
  window.addEventListener('robot-dashboard-state', (event) => {
    const d = event?.detail;
    if (!d) return;
    updateHomeRobotDashboardPanel(d);
    updateHomeActiveTaskFromScene('ev-parking', d);
  });
  window.addEventListener('road-defect-dashboard-state', (event) => {
    const d = event?.detail;
    if (!d) return;
    updateHomeActiveTaskFromScene('road-defect', d);
  });
  window.addEventListener('gauge-dashboard-state', (event) => {
    const d = event?.detail;
    if (!d) return;
    updateHomeActiveTaskFromScene('gauge', d);
  });
  window.addEventListener('robot-violation-event', (event) => {
    const d = event?.detail || {};
    let lng;
    let lat;
    if (
      Number.isFinite(d.robotLngWgs) &&
      Number.isFinite(d.robotLatWgs) &&
      d.robotLngWgs !== 0 &&
      d.robotLatWgs !== 0
    ) {
      [lng, lat] = wgs84ToGcj02(d.robotLngWgs, d.robotLatWgs);
    } else {
      [lng, lat] = SCHOOL_CENTER;
    }
    addHomeViolationMapMarker(lng, lat, d);
    const wrap = document.getElementById('homeRobotViolationPreview');
    const img = document.getElementById('homeRobotViolationFrame');
    const metaEl = document.getElementById('homeRobotViolationMeta');
    const b64 = d.frame_jpeg_base64;
    const ev0 = Array.isArray(d.plate_ocr_events) ? d.plate_ocr_events[0] : null;
    const pt = ev0 && (ev0.plate_text ?? ev0.plateText) != null ? String(ev0.plate_text ?? ev0.plateText).trim() : '';
    if (wrap && img && b64) {
      wrap.hidden = false;
      const raw = String(b64).replace(/^data:image\/\w+;base64,/, '');
      img.src = `data:image/jpeg;base64,${raw}`;
    }
    if (metaEl) {
      metaEl.textContent = [
        d.seq != null ? `seq ${d.seq}` : '',
        d.latency_ms != null ? `延迟 ${d.latency_ms} ms` : '',
        pt ? `车牌 ${pt}` : '',
      ]
        .filter(Boolean)
        .join(' · ');
    }
    if (homeActiveTask.scene !== 'ev-parking' || homeActiveTask.status === 'idle') {
      updateHomeActiveTaskFromScene('ev-parking', {
        controllerStatusRaw: 'RUNNING',
        taskProgressLabel: '发现违停',
        taskRunSerial: homeActiveTask.runId || '',
      });
    }
    pushHomeTaskFinding(
      d.seq != null ? `违停 #${d.seq}` : '违停发现',
      pt ? `车牌 ${pt}` : '已回传抓拍画面',
    );
  });
  window.addEventListener('road-defect-detection-event', (event) => {
    const d = event?.detail || {};
    if (homeActiveTask.scene !== 'road-defect' || homeActiveTask.status === 'idle') {
      updateHomeActiveTaskFromScene('road-defect', {
        controllerStatusRaw: 'RUNNING',
        taskProgressLabel: '发现道路缺陷',
        taskRunSerial: homeActiveTask.runId || '',
      });
    }
    pushHomeTaskFinding(
      d.seq != null ? `缺陷 #${d.seq}` : '道路缺陷发现',
      '已回传缺陷检测结果',
    );
  });
}

function escapeHomeText(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setHomeGaugeFeedStatus(statusText, tagText) {
  const statusEl = document.getElementById('homeGaugeLatestStatus');
  if (statusEl) statusEl.textContent = statusText || '等待结果';
  syncHomeTaskCardHighlight();
}

function renderHomeGaugeFeed(records = []) {
  const listEl = document.getElementById('homeGaugeFeedList');
  const readingEl = document.getElementById('homeGaugeLatestReading');
  const indexEl = document.getElementById('homeGaugeLatestIndex');
  if (!listEl) return;

  const rows = Array.isArray(records) ? records.slice(0, 8) : [];
  if (!rows.length) {
    if (readingEl) readingEl.textContent = '--';
    if (indexEl) indexEl.textContent = '--';
    setHomeGaugeFeedStatus('等待结果');
    listEl.innerHTML = '<div class="home-gauge-feed-empty">暂无仪表巡检结果，等待机器人回传。</div>';
    return;
  }

  const latest = rows[0] || {};
  const reading = latest.meter_reading ?? latest.meter_reading_value ?? '--';
  const index = latest.gauge_index ?? '--';
  const hasError = Boolean(latest.meter_reader_error);
  if (readingEl) readingEl.textContent = String(reading);
  if (indexEl) indexEl.textContent = String(index);
  setHomeGaugeFeedStatus(hasError ? '识别异常' : '识别正常');

  listEl.innerHTML = rows
    .map((record) => {
      const itemReading = escapeHomeText(record.meter_reading ?? record.meter_reading_value ?? '--');
      const itemIndex = escapeHomeText(record.gauge_index ?? '--');
      const itemTime = escapeHomeText(record.time || '--');
      const itemState = record.meter_reader_error
        ? `异常：${escapeHomeText(record.meter_reader_error)}`
        : escapeHomeText(record.task_state || '已识别');
      return `<div class="home-gauge-feed-item"><div class="home-gauge-feed-item-top"><span>仪表 #${itemIndex}</span><strong>${itemReading}</strong></div><div class="home-gauge-feed-item-meta">${itemTime} · ${itemState}</div></div>`;
    })
    .join('');
}

function bindHomeGaugeFeedListener() {
  window.addEventListener('gauge-result-update', (event) => {
    const detail = event?.detail || {};
    const records = Array.isArray(detail.records) ? detail.records : detail.record ? [detail.record] : [];
    renderHomeGaugeFeed(records);
    const latest = records[0];
    if (!latest) return;
    if (homeActiveTask.scene !== 'gauge' || homeActiveTask.status === 'idle') {
      updateHomeActiveTaskFromScene('gauge', {
        controllerStatusRaw: 'RUNNING',
        taskProgressLabel: '仪表结果回传中',
        taskRunSerial: homeActiveTask.runId || '',
      });
    }
    pushHomeTaskFinding(
      latest.gauge_index != null ? `仪表 #${latest.gauge_index}` : '仪表识别',
      latest.meter_reader_error
        ? `异常：${latest.meter_reader_error}`
        : `读数 ${latest.meter_reading ?? latest.meter_reading_value ?? '--'}`,
    );
  });
}

const homeAgentState = {
  busy: false,
  messages: [],
  sessionId: '',
};

function setHomeAgentStatus(text) {
  const tag = document.getElementById('homeAgentStatusTag');
  if (tag) tag.textContent = text || '接口待接入';
}

function setHomeAgentHint(text) {
  const hint = document.getElementById('homeAgentHint');
  if (hint) hint.textContent = text || '预留接口：window.homeAgentApi';
}

function setHomeAgentBusy(busy) {
  homeAgentState.busy = Boolean(busy);
  const sendBtn = document.getElementById('homeAgentSendBtn');
  const clearBtn = document.getElementById('homeAgentClearBtn');
  const input = document.getElementById('homeAgentInput');
  if (sendBtn) sendBtn.disabled = homeAgentState.busy;
  if (clearBtn) clearBtn.disabled = homeAgentState.busy;
  if (input) input.disabled = homeAgentState.busy;
}

function renderHomeAgentMessages() {
  const root = document.getElementById('homeAgentMessages');
  if (!root) return;
  if (!homeAgentState.messages.length) {
    root.innerHTML = '<div class="home-agent-msg is-system" data-role="system">智能巡检助手已就绪。可对话启动电动车违停 / 道路缺陷 / 仪表巡检，查询任务状态，并在完成后生成巡检报告。</div>';
    return;
  }
  root.innerHTML = homeAgentState.messages
    .map((msg) => {
      const role = String(msg.role || 'assistant');
      const cls = role === 'user' ? 'is-user' : role === 'system' ? 'is-system' : 'is-assistant';
      return `<div class="home-agent-msg ${cls}" data-role="${escapeHomeText(role)}">${escapeHomeText(msg.content || '')}</div>`;
    })
    .join('');
  root.scrollTop = root.scrollHeight;
}

function appendHomeAgentMessage(role, content) {
  const text = String(content || '').trim();
  if (!text) return null;
  const message = {
    id: `agent-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: role || 'assistant',
    content: text,
    createdAt: new Date().toISOString(),
  };
  homeAgentState.messages.push(message);
  if (homeAgentState.messages.length > 80) {
    homeAgentState.messages = homeAgentState.messages.slice(-80);
  }
  renderHomeAgentMessages();
  return message;
}

function clearHomeAgentMessages() {
  homeAgentState.messages = [];
  renderHomeAgentMessages();
}

async function defaultHomeAgentSend(payload) {
  const content = String(payload?.content || '').trim();
  if (!content) {
    return { role: 'system', content: '请输入有效内容后再发送。' };
  }
  const response = await fetch(`${API_BASE_URL}/assistant/ask`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      question: content,
      sessionId: homeAgentState.sessionId || undefined,
      topK: 5,
      mode: 'default',
    }),
  });

  let data = null;
  try {
    data = await response.json();
  } catch (_error) {
    data = null;
  }

  if (!response.ok || !data?.success) {
    const detail =
      data?.deepseekError ||
      data?.dashscopeError ||
      data?.llmError ||
      data?.ragError ||
      data?.error ||
      `HTTP ${response.status}`;
    throw new Error(detail);
  }

  if (data.sessionId) {
    homeAgentState.sessionId = data.sessionId;
  }

  const answer = String(data.answer || '').trim();
  if (!answer) {
    throw new Error('助手未返回有效内容');
  }

  if (data.task) {
    applyAssistantTaskState(data.task);
  }

  return {
    role: 'assistant',
    content: answer,
    provider: data.provider || '',
    model: data.model || '',
    taskAction: data.taskAction || '',
    task: data.task || null,
    report: data.report || null,
  };
}

async function submitHomeAgentMessage(rawContent) {
  const content = String(rawContent || '').trim();
  if (!content || homeAgentState.busy) return null;

  appendHomeAgentMessage('user', content);
  setHomeAgentBusy(true);
  setHomeAgentStatus('处理中');
  setHomeAgentHint('正在调用 DeepSeek V4 Pro…');

  try {
    const api = window.homeAgentApi || {};
    const customSender = typeof api.send === 'function' && api.send !== defaultHomeAgentSend ? api.send : null;
    const sender = customSender || defaultHomeAgentSend;
    const result = await sender({
      content,
      messages: homeAgentState.messages.slice(),
    });
    const replyRole = result?.role || 'assistant';
    const replyContent = result?.content || result?.message || '智能巡检助手未返回内容。';
    appendHomeAgentMessage(replyRole, replyContent);
    setHomeAgentStatus('已接入 DeepSeek V4 Pro');
    setHomeAgentHint('已接入 DeepSeek V4 Pro');
    return result;
  } catch (error) {
    appendHomeAgentMessage('system', `调用失败：${error?.message || error || '未知错误'}`);
    setHomeAgentStatus('调用失败');
    setHomeAgentHint('大模型调用失败，请稍后重试');
    return null;
  } finally {
    setHomeAgentBusy(false);
  }
}

function bindHomeAgentPanel() {
  const form = document.getElementById('homeAgentComposer');
  const input = document.getElementById('homeAgentInput');
  const clearBtn = document.getElementById('homeAgentClearBtn');
  if (!form || !input) return;

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const content = input.value;
    input.value = '';
    void submitHomeAgentMessage(content);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  clearBtn?.addEventListener('click', () => {
    clearHomeAgentMessages();
    homeAgentState.sessionId = '';
    setHomeAgentStatus('已接入 DeepSeek V4 Pro');
    setHomeAgentHint('已接入 DeepSeek V4 Pro');
  });

  window.homeAgentApi = window.homeAgentApi || {};
  window.homeAgentApi.appendMessage = appendHomeAgentMessage;
  window.homeAgentApi.clearMessages = clearHomeAgentMessages;
  window.homeAgentApi.setStatus = setHomeAgentStatus;
  window.homeAgentApi.setHint = setHomeAgentHint;
  window.homeAgentApi.getMessages = () => homeAgentState.messages.slice();
  if (typeof window.homeAgentApi.send !== 'function') {
    window.homeAgentApi.send = defaultHomeAgentSend;
  }

  renderHomeAgentMessages();
  setHomeAgentStatus('已接入 DeepSeek V4 Pro');
  setHomeAgentHint('已接入 DeepSeek V4 Pro');
}

function bootDashboard() {
  tickClock();
  setInterval(tickClock, 1000);
  initNavigation();
  bindStageDetailActions();
  initAllMaps();
  window.addEventListener('ros-navsat-fix', (event) => {
    const d = event?.detail;
    if (isValidRosNavFixDetail(d) && !d.fallbackToDefault) {
      const [gcjLng, gcjLat] = wgs84ToGcj02(d.lng, d.lat);
      updateHomeGpsStatusPanel({
        lat: gcjLat,
        lng: gcjLng,
        gcj02FromWgs: true,
      });
      updateRosNavSatHomeMarker(gcjLng, gcjLat);
      appendRosNavSatTrackPoint(gcjLng, gcjLat);
      return;
    }
    updateHomeGpsStatusPanel(d || null);
    if (d?.fallbackToDefault) {
      const [lng, lat] = SCHOOL_CENTER;
      updateRosNavSatHomeMarker(lng, lat);
    }
  });
  syncHomeGpsPanelInitial();
  syncHomeRobotPatrolIdFromConfig();
  bindRobotDashboardListeners();
  bindHomeGaugeFeedListener();
  bindHomeAgentPanel();
  bindPatrolReportsEntry();
  void fetchPatrolTaskStatus()
    .then((data) => {
      if (data?.task) applyAssistantTaskState(data.task);
    })
    .catch((error) => console.warn('[patrol-task-status]', error));
  tryEnsureRosGpsConnection();
  bindHomeMapCommandPanel();
  initAreaMonitor();
  const plateValueEl = document.getElementById('stageDetectionPlateValue');
  if (plateValueEl) {
    const labelEl = plateValueEl.parentElement?.querySelector('label');
    if (labelEl) labelEl.textContent = '车牌号';
  }
  const plateConfidenceEl = document.getElementById('stageDetectionPlateConfidenceValue');
  if (plateConfidenceEl) {
    const labelEl = plateConfidenceEl.parentElement?.querySelector('label');
    if (labelEl) labelEl.textContent = '车牌置信';
  }
  const copyPlateBtn = document.getElementById('stageCopyPlateBtn');
  if (copyPlateBtn) copyPlateBtn.textContent = '复制车牌号';
  document.getElementById('homeFocusActionBtn')?.addEventListener('click', () => {
    const scene = HOME_TASK_SCENES[homeActiveTask.scene];
    if (scene?.page) {
      navigateToPage(scene.page);
      return;
    }
    focusLatestAlert();
  });
  document.getElementById('homeFocusReportBtn')?.addEventListener('click', () => {
    void openHomeTaskReportModal();
  });
  document.getElementById('homeTaskReportGenerateBtn')?.addEventListener('click', () => {
    void openHomeTaskReportModal();
  });
  document.getElementById('homeTaskReportLaterBtn')?.addEventListener('click', () => {
    hideHomeTaskReportPrompt();
  });
  document.getElementById('homeTaskReportCloseBtn')?.addEventListener('click', closeHomeTaskReportModal);
  document.querySelector('[data-close-task-report="true"]')?.addEventListener('click', closeHomeTaskReportModal);
  document.getElementById('metricsHelpCloseBtn')?.addEventListener('click', closeMetricsHelpModal);
  document.querySelector('[data-close-metrics-help="true"]')?.addEventListener('click', closeMetricsHelpModal);
  syncHomeCommandCenter();
  renderHomeLeftInsights();
  void refreshEvParkingStats();
  void refreshRoadDefectStats();
  void refreshGovernanceMetrics();
  void refreshWeather();
  startAlertPolling(5000);
  window.addEventListener(
    'ros-patrol-ready',
    () => {
      syncHomeGpsPanelInitial();
      tryEnsureRosGpsConnection();
    },
    { once: true },
  );
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bootDashboard);
} else {
  bootDashboard();
}
