/**
 * 道路缺陷巡检页：经 rosbridge WebSocket 订阅相机/电量/速度，发布 cmd_vel 手动控制。
 * 依赖全局 ROSLIB 与 window.__DASHBOARD_CONFIG__
 */
(function (global) {
  let ros = null;
  let subs = [];
  let cmdVelPub = null;
  let cmdVelTimer = null;
  let currentTwist = { linear: { x: 0, y: 0, z: 0 }, angular: { x: 0, y: 0, z: 0 } };
  let reconnectTimer = null;
  let roadDefectPageActive = false;
  /** 为 true 时仅订阅违停检测可视化话题，否则订阅原始 cameraImage */
  let detectionVisActive = false;
  let keyboardManualOn = false;
  const keyboardKeysDown = new Set();
  const speedHistory = [];
  const SPEED_MAX = 80;
  /** 曲线纵轴固定为 0～SPEED_Y_MAX（|v| 超过上限时画在顶线） */
  const SPEED_Y_MAX = 1;
  const SPEED_CHART_W = 320;
  const SPEED_CHART_H = 140;

  function cfg() {
    return global.__DASHBOARD_CONFIG__ || {};
  }

  /** road-defect can later use dedicated config keys; fallback keeps current patrol wiring. */
  function featureCfg() {
    const c = cfg();
    return c.roadDefect || c.roadDefectPatrol || c;
  }

  function topics() {
    const c = featureCfg();
    return c.rosTopics || cfg().rosTopics || {};
  }

  function control() {
    const c = featureCfg();
    return c.robotControl || cfg().robotControl || {};
  }

  function patrolApiBase() {
    const base =
      (global.__AMAP_APP__ && global.__AMAP_APP__.apiBase) || 'http://127.0.0.1:5000/api';
    return String(base).replace(/\/+$/, '');
  }

  /**
   * 与 robot/status 等接口一致：非本机访问 Django 时需在服务端配置 DASHBOARD_API_KEY，
   * 并在 dashboard.json 中设置同名 dashboardApiKey，否则会返回 401。
   */
  function patrolApiExtraHeaders() {
    const key = String(cfg().dashboardApiKey || cfg().xDashboardKey || '').trim();
    if (!key) return {};
    return { 'X-Dashboard-Key': key };
  }

  function rawCameraUsesMjpegApi() {
    const p = cfg().patrolRawCamera || {};
    return String(p.source || '').toLowerCase() === 'mjpeg_api';
  }

  function patrolMjpegUrl() {
    let path = String((cfg().patrolRawCamera || {}).mjpegPath || 'patrol/camera-mjpeg').replace(/^\//, '');
    return patrolApiBase() + '/' + path;
  }

  let mjpegUrlBust = 0;
  let patrolTaskTimer = null;

  function patrolRobotId() {
    const c = featureCfg();
    return String(c.patrolRobotId || c.robotId || cfg().patrolRobotId || 'RBT-01').trim() || 'RBT-01';
  }

  /** 0 = 关闭轮询 */
  function patrolTaskStatusPollMs() {
    const v = Number(cfg().patrolTaskStatusPollMs);
    if (v === 0) return 0;
    if (Number.isFinite(v)) return Math.max(2000, Math.min(120000, v));
    return 5000;
  }

  function formatPatrolBattery(bat) {
    if (bat == null || bat === '') return '--';
    const n = Number(bat);
    if (!Number.isFinite(n)) return '--';
    if (n >= 0 && n <= 1) return `${Math.round(n * 100)}%`;
    return `${Math.round(n)}%`;
  }

  function formatPatrolIsoLocal(iso) {
    if (!iso) return '--';
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return d.toLocaleString('zh-CN', { hour12: false });
    } catch (_) {
      return String(iso);
    }
  }

  function setPatrolTaskField(id, text, stateClass) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.classList.remove('is-ok', 'is-off');
    if (stateClass) el.classList.add(stateClass);
  }

  function updatePatrolTaskStatusUi(st, stats, errHint) {
    const foot = document.getElementById('roadDefectTaskStatusFoot');
    setPatrolTaskField('roadDefectTaskRobotId', st && st.success ? st.robotId || patrolRobotId() : patrolRobotId());
    if (!st || !st.success) {
      if (foot) foot.textContent = errHint || '';
      return;
    }
    if (stats && stats.success) {
      if (foot) foot.textContent = stats.note || errHint || '';
    } else if (foot) {
      foot.textContent =
        errHint || (stats && (stats.error || !stats.success) ? 'robot/stats 暂不可用' : '') || '';
    }
  }

  function stopPatrolTaskStatusPoll() {
    if (patrolTaskTimer) {
      clearInterval(patrolTaskTimer);
      patrolTaskTimer = null;
    }
  }

  function fetchPatrolTaskStatusOnce() {
    const ms = patrolTaskStatusPollMs();
    if (ms <= 0) return;
    const base = patrolApiBase();
    const rid = encodeURIComponent(patrolRobotId());
    const authHdr = patrolApiExtraHeaders();
    global.Promise.all([
      global
        .fetch(`${base}/robot/status?robotId=${rid}`, { headers: authHdr })
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return {};
        }),
      global
        .fetch(`${base}/robot/stats?robotId=${rid}`, { headers: authHdr })
        .then(function (r) {
          return r.json();
        })
        .catch(function () {
          return {};
        }),
    ])
      .then(function (results) {
        const st = results[0];
        const stats = results[1];
        if (!st || st.success !== true) {
          updatePatrolTaskStatusUi(
            st || { success: false },
            stats,
            st && st.error ? `robot/status：${st.error}` : '无法读取机器人状态（网络或鉴权）',
          );
          return;
        }
        if (!stats || stats.success !== true) {
          updatePatrolTaskStatusUi(
            st,
            stats || { success: false },
            stats && stats.error ? `robot/stats：${stats.error}` : '',
          );
          return;
        }
        updatePatrolTaskStatusUi(st, stats, '');
      })
      .catch(function () {
        updatePatrolTaskStatusUi(null, null, '拉取任务状态失败');
      });
  }

  function startPatrolTaskStatusPoll() {
    stopPatrolTaskStatusPoll();
    const ms = patrolTaskStatusPollMs();
    if (ms <= 0) {
      updatePatrolTaskStatusUi(null, null, '');
      const foot = document.getElementById('roadDefectTaskStatusFoot');
      if (foot) {
        foot.textContent = needsRosDashboardTopics()
          ? '任务态由 ROS 话题同步（见 config/dashboard.json · rosTopics）'
          : '可在 config/dashboard.json 中配置 rosTopics.controllerStatus 等以启用 ROS 任务态';
      }
      return;
    }
    fetchPatrolTaskStatusOnce();
    patrolTaskTimer = setInterval(fetchPatrolTaskStatusOnce, ms);
  }

  function setPatrolMjpegStreamActive(on) {
    const mjpegEl = document.getElementById('roadDefectPatrolMjpegImg');
    const canvas = document.getElementById('roadDefectCameraCanvas');
    if (!mjpegEl || !canvas) return;
    if (on && rawCameraUsesMjpegApi()) {
      if (!mjpegEl.classList.contains('is-visible')) {
        mjpegUrlBust += 1;
        mjpegEl.src = patrolMjpegUrl() + '?_bust=' + mjpegUrlBust;
      }
      mjpegEl.classList.add('is-visible');
      mjpegEl.removeAttribute('hidden');
      canvas.style.visibility = 'hidden';
    } else {
      mjpegEl.removeAttribute('src');
      mjpegEl.classList.remove('is-visible');
      mjpegEl.setAttribute('hidden', '');
      canvas.style.visibility = '';
    }
  }

  function applyPatrolViewportForRawCamera() {
    if (detectionVisActive) {
      setPatrolMjpegStreamActive(false);
      return;
    }
    if (rawCameraUsesMjpegApi()) {
      setPatrolMjpegStreamActive(true);
    } else {
      setPatrolMjpegStreamActive(false);
    }
  }

  function setStatus(text) {
    const el = document.getElementById('roadDefectRosConnectionStatus');
    if (el) el.textContent = text;
  }

  function setBatterySphere(percent01) {
    const pct = Math.max(0, Math.min(100, percent01 * 100));
    const sphere = document.getElementById('roadDefectBatterySphere');
    const label = document.getElementById('roadDefectBatteryPercentLabel');
    if (label) label.textContent = Math.round(pct) + '%';
    if (sphere) {
      sphere.style.setProperty('--battery-level', String(pct / 100));
      sphere.dataset.level = pct < 20 ? 'low' : pct < 50 ? 'mid' : 'ok';
    }
  }

  function decodeImageBase64(dataB64) {
    const binary = atob(dataB64);
    const len = binary.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  /**
   * 将 rosbridge 序列化后的「字节字段」转为 Uint8Array，对齐 Python：
   *   np_arr = np.frombuffer(msg.data, dtype=np.uint8)
   *   bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
   * 即 data 为 JPEG/PNG 等压缩流的原始字节，而非已解压像素。
   */
  function rosBinaryFieldToUint8(data) {
    if (data == null || data === '') return null;
    if (data instanceof Uint8Array) return data.byteLength ? data : null;
    if (Array.isArray(data)) {
      try {
        const u = new Uint8Array(data);
        return u.byteLength ? u : null;
      } catch (_) {
        return null;
      }
    }
    if (data && typeof data === 'object' && data.type === 'Buffer' && Array.isArray(data.data)) {
      try {
        const u = new Uint8Array(data.data);
        return u.byteLength ? u : null;
      } catch (_) {
        return null;
      }
    }
    if (typeof data === 'string') {
      const trimmed = data.replace(/\s/g, '');
      if (!trimmed.length) return null;
      const b64norm = trimmed.replace(/-/g, '+').replace(/_/g, '/');
      const pad = b64norm.length % 4;
      const padded = pad ? b64norm + '===='.slice(pad) : b64norm;
      try {
        const u = decodeImageBase64(padded);
        return u.byteLength ? u : null;
      } catch (_) {
        /* 非 base64 时按 Latin-1 单字节串处理（少数桥接形态） */
        try {
          const n = data.length;
          const u = new Uint8Array(n);
          for (let i = 0; i < n; i++) u[i] = data.charCodeAt(i) & 0xff;
          return u.byteLength ? u : null;
        } catch (_) {
          return null;
        }
      }
    }
    return null;
  }

  /** 按文件头推断 MIME，等价于 imdecode 根据码流识别格式 */
  function inferCompressedImageMime(bytes, formatHint) {
    if (bytes && bytes.byteLength >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
      return 'image/jpeg';
    }
    if (
      bytes &&
      bytes.byteLength >= 8 &&
      bytes[0] === 0x89 &&
      bytes[1] === 0x50 &&
      bytes[2] === 0x4e &&
      bytes[3] === 0x47
    ) {
      return 'image/png';
    }
    if (
      bytes &&
      bytes.byteLength >= 12 &&
      bytes[0] === 0x52 &&
      bytes[1] === 0x49 &&
      bytes[2] === 0x46 &&
      bytes[3] === 0x46 &&
      bytes[8] === 0x57 &&
      bytes[9] === 0x45 &&
      bytes[10] === 0x42 &&
      bytes[11] === 0x50
    ) {
      return 'image/webp';
    }
    const f = String(formatHint || '').toLowerCase();
    if (f.includes('png')) return 'image/png';
    if (f.includes('webp')) return 'image/webp';
    if (f.includes('jpg') || f.includes('jpeg')) return 'image/jpeg';
    return 'image/jpeg';
  }

  /**
   * 将压缩图像字节流显示到 canvas（浏览器解码 ≈ cv2.imdecode）。
   */
  function drawCompressedImageBytesToCanvas(canvas, imgEl, bytes, formatHint) {
    if (!canvas || !imgEl || !bytes || !bytes.byteLength) return;
    const mime = inferCompressedImageMime(bytes, formatHint);
    const gen = (patrolCameraDecodeGen += 1);
    try {
      const blob = new Blob([bytes], { type: mime });
      const url = URL.createObjectURL(blob);
      const revoke = function () {
        try {
          URL.revokeObjectURL(url);
        } catch (_) {}
      };
      imgEl.onload = function () {
        if (gen !== patrolCameraDecodeGen) {
          revoke();
          return;
        }
        if (imgEl.naturalWidth && imgEl.naturalHeight) {
          stretchDrawOntoPatrolViewport(canvas, imgEl);
        }
        revoke();
      };
      imgEl.onerror = function () {
        revoke();
        setStatus('检测画面解码失败（码流可能损坏或非 JPEG/PNG）');
      };
      imgEl.src = url;
    } catch (_) {
      setStatus('检测画面解码失败');
    }
  }

  /** 解码后的原始帧先画到此 canvas，再拉伸到巡检视口（与 .robot-camera-viewport 的 16:9 一致） */
  let patrolFrameStaging = null;

  /** 巡检相机：每帧只保留最新消息，在 rAF 中绘制，避免 ROS 消息快于屏幕刷新时主线程积压 */
  let patrolCameraPending = null;
  let patrolCameraRafId = 0;
  /** 递增以丢弃过期的 img.onload（新帧已到达时不再往 canvas 画旧图） */
  let patrolCameraDecodeGen = 0;

  let patrolViewportBw = 0;
  let patrolViewportBh = 0;
  let patrolViewportResizeObs = null;

  function stopPatrolViewportResizeObserver() {
    if (patrolViewportResizeObs) {
      try {
        patrolViewportResizeObs.disconnect();
      } catch (_) {}
      patrolViewportResizeObs = null;
    }
    patrolViewportBw = 0;
    patrolViewportBh = 0;
  }

  function updatePatrolViewportSizeFromWrap(wrap) {
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = Math.min(2, global.devicePixelRatio || 1);
    patrolViewportBw = Math.max(32, Math.round(rect.width * dpr));
    patrolViewportBh = Math.max(18, Math.round(rect.height * dpr));
  }

  function ensurePatrolViewportResizeObserver(displayCanvas) {
    if (!displayCanvas) return;
    const wrap = displayCanvas.closest('.robot-camera-viewport');
    if (!wrap) return;
    if (patrolViewportResizeObs) {
      updatePatrolViewportSizeFromWrap(wrap);
      return;
    }
    updatePatrolViewportSizeFromWrap(wrap);
    if (typeof ResizeObserver === 'undefined') return;
    patrolViewportResizeObs = new ResizeObserver(function () {
      updatePatrolViewportSizeFromWrap(wrap);
    });
    patrolViewportResizeObs.observe(wrap);
  }

  function cancelPatrolCameraFrameSchedule() {
    if (patrolCameraRafId) {
      try {
        global.cancelAnimationFrame(patrolCameraRafId);
      } catch (_) {}
      patrolCameraRafId = 0;
    }
    patrolCameraPending = null;
    patrolCameraDecodeGen += 1;
  }

  /** ---------- 行为树状态 / 违停违规（首页 + 巡检页共用）---------- */
  let controllerStatusRawLatest = '';
  let controllerSampleTimer = null;
  let prevControllerForTaskSerial = '';
  let taskRunSerial = 0;
  let violationCountSession = 0;
  let violationArmed = false;
  let lastViolationArmTime = 0;

  function controllerStatusTopicName() {
    return String(topics().controllerStatus || '').trim();
  }

  function controllerSampleIntervalMs() {
    const v = Number(topics().controllerStatusSampleIntervalMs);
    if (Number.isFinite(v)) return Math.max(250, Math.min(60000, v));
    return 1000;
  }

  function violationSkewMs() {
    const v = Number(topics().violationBoolStringMaxSkewMs);
    if (Number.isFinite(v)) return Math.max(50, Math.min(2000, v));
    return 200;
  }

  function violationHasTopicName() {
    return String(topics().violationHasViolation || '').trim();
  }

  function violationDetectionTopicName() {
    return String(topics().violationDetection || '').trim();
  }

  function needsRosDashboardTopics() {
    return (
      !!controllerStatusTopicName() ||
      (!!violationHasTopicName() && !!violationDetectionTopicName())
    );
  }

  function translateBehaviorTreeStatus(upper) {
    const u = String(upper || '').trim().toUpperCase();
    if (u === 'RUNNING') return '执行中';
    if (u === 'SUCCESS') return '成功完成';
    if (u === 'FAILURE') return '流程失败';
    if (u === 'INVALID') return '未激活';
    if (!u) return '--';
    return upper;
  }

  function taskProgressLabelFromStatus(upper) {
    const u = String(upper || '').trim().toUpperCase();
    if (u === 'RUNNING') return '进行中';
    if (u === 'SUCCESS') return '完成';
    if (u === 'FAILURE') return '失败';
    if (u === 'INVALID') return '待机';
    if (!u) return '--';
    return '--';
  }

  function stopControllerSampleTimer() {
    if (controllerSampleTimer) {
      clearInterval(controllerSampleTimer);
      controllerSampleTimer = null;
    }
  }

  function tickControllerTaskSerial() {
    const u = String(controllerStatusRawLatest || '').trim().toUpperCase();
    if (u === 'RUNNING' && prevControllerForTaskSerial !== 'RUNNING') {
      taskRunSerial += 1;
    }
    prevControllerForTaskSerial = u;
  }

  function setPatrolRosTaskFields() {
    const u = String(controllerStatusRawLatest || '').trim().toUpperCase();
    const idEl = document.getElementById('roadDefectTaskMissionId');
    const vEl = document.getElementById('roadDefectCount');
    const pEl = document.getElementById('roadDefectTaskProgress');
    if (idEl) {
      idEl.textContent =
        u === 'RUNNING' || u === 'SUCCESS' || u === 'FAILURE'
          ? String(taskRunSerial || 1)
          : taskRunSerial > 0
            ? String(taskRunSerial)
            : '--';
    }
    if (vEl) vEl.textContent = String(violationCountSession);
    if (pEl) pEl.textContent = taskProgressLabelFromStatus(u);
  }

  function publishRobotDashboardState() {
    const u = String(controllerStatusRawLatest || '').trim().toUpperCase();
    const rosOk = isRosOpen();
    setPatrolRosTaskFields();
    try {
      global.dispatchEvent(
        new CustomEvent('road-defect-dashboard-state', {
          detail: {
            rosConnected: rosOk,
            controllerStatusRaw: String(controllerStatusRawLatest || '').trim(),
            controllerStatusZh: translateBehaviorTreeStatus(u),
            taskRunSerial: taskRunSerial,
            violationCount: violationCountSession,
            taskProgressLabel: taskProgressLabelFromStatus(u),
          },
        }),
      );
    } catch (_) {}
  }

  function onControllerSampleTimer() {
    tickControllerTaskSerial();
    publishRobotDashboardState();
  }

  function subscribeRobotDashboardTopics() {
    if (!ros || typeof global.ROSLIB === 'undefined') return;
    if (subs.some(function (s) {
      return s.__kind === 'dashboard';
    })) {
      return;
    }
    const t = topics();
    const ctopic = controllerStatusTopicName();
    if (ctopic) {
      const sub = new global.ROSLIB.Topic({
        ros: ros,
        name: ctopic,
        messageType: t.controllerStatusMessageType || 'std_msgs/String',
      });
      sub.__kind = 'dashboard';
      sub.subscribe(function (msg) {
        controllerStatusRawLatest = msg.data != null ? String(msg.data) : '';
      });
      subs.push(sub);
      stopControllerSampleTimer();
      controllerSampleTimer = setInterval(onControllerSampleTimer, controllerSampleIntervalMs());
      onControllerSampleTimer();
    }
    const vHas = violationHasTopicName();
    const vDet = violationDetectionTopicName();
    if (vHas && vDet) {
      const subB = new global.ROSLIB.Topic({
        ros: ros,
        name: vHas,
        messageType: t.violationHasViolationMessageType || 'std_msgs/Bool',
      });
      subB.__kind = 'dashboard';
      subB.subscribe(function (msg) {
        if (msg.data === true) {
          violationArmed = true;
          lastViolationArmTime = Date.now();
        }
      });
      subs.push(subB);
      const subS = new global.ROSLIB.Topic({
        ros: ros,
        name: vDet,
        messageType: t.violationDetectionMessageType || 'std_msgs/String',
      });
      subS.__kind = 'dashboard';
      subS.subscribe(function (msg) {
        const skew = violationSkewMs();
        const within = Date.now() - lastViolationArmTime <= skew;
        if (!violationArmed && !within) return;
        violationArmed = false;
        const raw = msg.data != null ? String(msg.data) : '';
        let parsed = null;
        try {
          parsed = JSON.parse(raw);
        } catch (_) {
          return;
        }
        violationCountSession += 1;
        const fix = latestNavFix;
        try {
          global.dispatchEvent(
            new CustomEvent('road-defect-detection-event', {
              detail: {
                seq: parsed.seq,
                latency_ms: parsed.latency_ms,
                frame_jpeg_base64: parsed.frame_jpeg_base64,
                plate_ocr_events: parsed.plate_ocr_events,
                robotLatWgs: fix && Number.isFinite(fix.lat) ? fix.lat : null,
                robotLngWgs: fix && Number.isFinite(fix.lng) ? fix.lng : null,
              },
            }),
          );
        } catch (_) {}
        publishRobotDashboardState();
      });
      subs.push(subS);
    }
    if (!ctopic) {
      publishRobotDashboardState();
    }
  }

  function ensurePatrolFrameStaging(w, h) {
    if (!patrolFrameStaging) patrolFrameStaging = document.createElement('canvas');
    if (patrolFrameStaging.width !== w || patrolFrameStaging.height !== h) {
      patrolFrameStaging.width = w;
      patrolFrameStaging.height = h;
    }
    return patrolFrameStaging;
  }

  /**
   * 将显示 canvas 的像素尺寸对齐到视口（高 DPR），保证与 CSS 16:9 区域一致；随后把 source 非等比拉伸铺满。
   */
  function stretchDrawOntoPatrolViewport(displayCanvas, source) {
    if (!displayCanvas || !source) return;
    ensurePatrolViewportResizeObserver(displayCanvas);
    let bw = patrolViewportBw;
    let bh = patrolViewportBh;
    if (!bw || !bh) {
      const wrap = displayCanvas.closest('.robot-camera-viewport');
      const rect = wrap ? wrap.getBoundingClientRect() : { width: 640, height: 360 };
      const dpr = Math.min(2, global.devicePixelRatio || 1);
      bw = Math.max(32, Math.round(rect.width * dpr));
      bh = Math.max(18, Math.round(rect.height * dpr));
    }
    if (displayCanvas.width !== bw || displayCanvas.height !== bh) {
      displayCanvas.width = bw;
      displayCanvas.height = bh;
    }
    const ctx = displayCanvas.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    /* 实时视频用 low 可明显减轻 CPU/GPU；违停可视化以流畅优先 */
    ctx.imageSmoothingQuality = 'low';
    ctx.drawImage(source, 0, 0, bw, bh);
  }

  function drawSensorImage(canvas, msg) {
    const w = msg.width | 0;
    const h = msg.height | 0;
    if (!w || !h || !msg.data) return;
    const enc = String(msg.encoding || 'rgb8').toLowerCase();
    const step = msg.step | 0 || w * 3;
    const bytes = rosBinaryFieldToUint8(msg.data);
    if (!bytes) return false;
    if (enc === 'jpeg' || enc === 'png' || enc === 'jpg') return false;
    const staging = ensurePatrolFrameStaging(w, h);
    const sctx = staging.getContext('2d');
    const imgData = sctx.createImageData(w, h);
    const d = imgData.data;
    if (enc === 'rgb8') {
      let p = 0;
      for (let y = 0; y < h; y++) {
        const row = y * step;
        for (let x = 0; x < w; x++) {
          const i = row + x * 3;
          d[p++] = bytes[i];
          d[p++] = bytes[i + 1];
          d[p++] = bytes[i + 2];
          d[p++] = 255;
        }
      }
    } else if (enc === 'bgr8') {
      let p = 0;
      for (let y = 0; y < h; y++) {
        const row = y * step;
        for (let x = 0; x < w; x++) {
          const i = row + x * 3;
          d[p++] = bytes[i + 2];
          d[p++] = bytes[i + 1];
          d[p++] = bytes[i];
          d[p++] = 255;
        }
      }
    } else {
      setStatus('不支持的图像编码: ' + enc);
      return true;
    }
    sctx.putImageData(imgData, 0, 0);
    stretchDrawOntoPatrolViewport(canvas, staging);
    return true;
  }

  /**
   * sensor_msgs/Image：支持 rgb8/bgr8 与压缩编码（jpeg/png），用于原始相机与带框检测结果画面。
   */
  function renderPatrolImageToCanvas(canvas, imgEl, msg) {
    if (!msg || !msg.data) return;
    const enc = String(msg.encoding || 'rgb8').toLowerCase();
    if (enc === 'jpeg' || enc === 'jpg' || enc === 'png' || enc === 'png8') {
      if (!imgEl) return;
      const bytes = rosBinaryFieldToUint8(msg.data);
      if (!bytes) {
        setStatus('图像数据为空或无法解析（需为 JPEG/PNG 压缩字节流）');
        return;
      }
      drawCompressedImageBytesToCanvas(canvas, imgEl, bytes, enc);
      return;
    }
    drawSensorImage(canvas, msg);
  }

  function showCompressedImage(imgEl, canvas, msg) {
    if (!msg || !imgEl) return;
    const bytes = rosBinaryFieldToUint8(msg.data);
    if (!bytes) {
      setStatus('压缩图像数据为空或无法解析');
      return;
    }
    const format = String(msg.format || 'jpeg').toLowerCase();
    drawCompressedImageBytesToCanvas(canvas, imgEl, bytes, format);
  }

  function flushPatrolCameraPendingFrame() {
    patrolCameraRafId = 0;
    const p = patrolCameraPending;
    patrolCameraPending = null;
    if (!p || !p.canvas) return;
    const detMt = p.detMt || '';
    if (String(detMt).indexOf('CompressedImage') >= 0) {
      if (p.imgEl) showCompressedImage(p.imgEl, p.canvas, p.msg);
    } else {
      renderPatrolImageToCanvas(p.canvas, p.imgEl, p.msg);
    }
    if (patrolCameraPending) {
      patrolCameraRafId = global.requestAnimationFrame(flushPatrolCameraPendingFrame);
    }
  }

  function schedulePatrolCameraFrame(canvas, imgEl, detMt, msg) {
    patrolCameraPending = { canvas: canvas, imgEl: imgEl, detMt: detMt, msg: msg };
    if (!patrolCameraRafId) {
      patrolCameraRafId = global.requestAnimationFrame(flushPatrolCameraPendingFrame);
    }
  }

  function pushSpeed(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return;
    speedHistory.push(n);
    while (speedHistory.length > SPEED_MAX) speedHistory.shift();
    drawSpeedChart();
  }

  function drawSpeedChart() {
    const canvas = document.getElementById('roadDefectSpeedChartCanvas');
    if (!canvas) return;
    const w = SPEED_CHART_W;
    const h = SPEED_CHART_H;
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext('2d');
    const padL = 36;
    const padR = 8;
    const padT = 10;
    const padB = 22;
    const plotW = w - padL - padR;
    const plotH = h - padT - padB;

    ctx.fillStyle = 'rgba(10, 18, 28, 0.92)';
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = 'rgba(37, 246, 255, 0.35)';
    ctx.lineWidth = 1;
    ctx.fillStyle = 'rgba(125, 147, 169, 0.9)';
    ctx.font = '10px system-ui,sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const frac = i / 4;
      const y = padT + plotH * frac;
      const scaleVal = SPEED_Y_MAX * (1 - frac);
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillText(scaleVal.toFixed(2), padL - 4, y + 4);
    }
    ctx.textAlign = 'left';

    if (speedHistory.length < 2) {
      ctx.fillStyle = 'rgba(152, 152, 154, 0.9)';
      ctx.font = '12px system-ui,sans-serif';
      ctx.fillText('等待速度话题…', padL, padT + plotH / 2);
      ctx.fillStyle = 'rgba(125, 147, 169, 0.75)';
      ctx.font = '10px system-ui,sans-serif';
      ctx.fillText('纵轴 0～' + SPEED_Y_MAX + '（|v|）', padL, h - 6);
      return;
    }

    ctx.strokeStyle = '#25f6ff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    speedHistory.forEach(function (val, i) {
      const x = padL + (i / (SPEED_MAX - 1)) * plotW;
      const norm = Math.min(1, Math.abs(val) / SPEED_Y_MAX);
      const y = padT + plotH * (1 - norm);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    const last = speedHistory[speedHistory.length - 1];
    const lastAbs = Math.abs(last);
    ctx.fillStyle = '#9eff00';
    ctx.font = '11px system-ui,sans-serif';
    ctx.fillText('|v| = ' + lastAbs.toFixed(3) + ' m/s', padL, h - 6);
  }

  function clearSubs() {
    cancelPatrolCameraFrameSchedule();
    subs.forEach(function (s) {
      try {
        s.unsubscribe();
      } catch (_) {}
    });
    subs = [];
  }

  function navSatTopicName() {
    return String(topics().navSatFix || '').trim();
  }

  function navSatEmitIntervalMs() {
    const v = Number(topics().navSatFixEmitIntervalMs);
    if (Number.isFinite(v)) return Math.max(500, Math.min(60000, v));
    return 3000;
  }

  function isRosOpen() {
    return !!(ros && ros.socket && ros.socket.readyState === WebSocket.OPEN);
  }

  function shouldMaintainRosConnection() {
    return roadDefectPageActive || !!navSatTopicName() || needsRosDashboardTopics();
  }

  function removePatrolKindSubs() {
    cancelPatrolCameraFrameSchedule();
    subs = subs.filter(function (s) {
      if (s.__kind === 'patrol' || s.__patrolCamera) {
        try {
          s.unsubscribe();
        } catch (_) {}
        return false;
      }
      return true;
    });
  }

  let latestNavFix = null;
  let gpsEmitTimer = null;

  function stopGpsEmitTimer() {
    if (gpsEmitTimer) {
      clearInterval(gpsEmitTimer);
      gpsEmitTimer = null;
    }
  }

  function startGpsEmitTimer() {
    if (gpsEmitTimer) return;
    const ms = navSatEmitIntervalMs();
    gpsEmitTimer = setInterval(function () {
      if (latestNavFix) {
        global.dispatchEvent(
          new CustomEvent('road-defect-navsat-fix', {
            detail: { lat: latestNavFix.lat, lng: latestNavFix.lng },
          }),
        );
      } else {
        global.dispatchEvent(
          new CustomEvent('road-defect-navsat-fix', {
            detail: { fallbackToDefault: true },
          }),
        );
      }
    }, ms);
  }

  function subscribeNavSatFix() {
    if (!ros || !navSatTopicName()) return;
    if (subs.some(function (s) { return s.__kind === 'gps'; })) {
      startGpsEmitTimer();
      return;
    }
    const t = topics();
    const sub = new global.ROSLIB.Topic({
      ros: ros,
      name: navSatTopicName(),
      messageType: t.navSatFixMessageType || 'sensor_msgs/NavSatFix',
    });
    sub.__kind = 'gps';
    sub.subscribe(function (msg) {
      const lat = Number(msg.latitude);
      const lng = Number(msg.longitude);
      const ok =
        Number.isFinite(lat) &&
        Number.isFinite(lng) &&
        lat !== 0 &&
        lng !== 0;
      const hadFix = !!latestNavFix;
      if (ok) {
        latestNavFix = { lat: lat, lng: lng };
      } else {
        latestNavFix = null;
        if (hadFix) {
          global.dispatchEvent(
            new CustomEvent('road-defect-navsat-fix', { detail: { fallbackToDefault: true } }),
          );
        }
      }
    });
    subs.push(sub);
    startGpsEmitTimer();
  }

  function subscribePatrolTopicsFull() {
    if (!ros) return;
    removePatrolKindSubs();
    subscribePatrolCamera();
    const t = topics();
    if (t.battery) {
      const sub = new global.ROSLIB.Topic({
        ros: ros,
        name: t.battery,
        messageType: t.batteryMessageType || 'sensor_msgs/BatteryState',
      });
      sub.__kind = 'patrol';
      sub.subscribe(function (msg) {
        let p = Number(msg.percentage);
        if (p > 1.001) p = p / 100;
        if (!Number.isFinite(p) && Number.isFinite(msg.charge) && Number.isFinite(msg.capacity) && msg.capacity > 0) {
          p = msg.charge / msg.capacity;
        }
        if (!Number.isFinite(p)) p = 0;
        setBatterySphere(Math.max(0, Math.min(1, p)));
      });
      subs.push(sub);
    }
    if (t.speed) {
      const sub = new global.ROSLIB.Topic({
        ros: ros,
        name: t.speed,
        messageType: t.speedMessageType || 'nav_msgs/Odometry',
        throttle_rate: 100,
      });
      sub.__kind = 'patrol';
      sub.subscribe(function (msg) {
        const tw = msg.twist && msg.twist.twist;
        if (!tw || !tw.linear) return;
        const vx = Number(tw.linear.x) || 0;
        const vy = Number(tw.linear.y) || 0;
        pushSpeed(Math.sqrt(vx * vx + vy * vy));
      });
      subs.push(sub);
    }
  }

  function removePatrolCameraSubs() {
    cancelPatrolCameraFrameSchedule();
    subs = subs.filter(function (s) {
      if (s.__patrolCamera) {
        try {
          s.unsubscribe();
        } catch (_) {}
        return false;
      }
      return true;
    });
  }

  function detectionVisualizationTopicName() {
    const t = topics();
    return String(t.detectionVisualizationTopic || t.cameraCompressed || '').trim();
  }

  function detectionVisualizationMessageType() {
    const t = topics();
    return (
      t.detectionVisualizationMessageType ||
      t.cameraCompressedMessageType ||
      'sensor_msgs/Image'
    );
  }

  function subscribePatrolCamera() {
    const t = topics();
    const canvas = document.getElementById('roadDefectCameraCanvas');
    const imgEl = document.getElementById('roadDefectCameraImg');
    if (!canvas) return;

    const detTopic = detectionVisualizationTopicName();
    const detMt = detectionVisualizationMessageType();

    if (detectionVisActive && detTopic) {
      setPatrolMjpegStreamActive(false);
      if (!ros) return;
      removePatrolCameraSubs();
      const sub = new global.ROSLIB.Topic({
        ros: ros,
        name: detTopic,
        messageType: detMt,
        /* rosbridge 侧限流；显示侧再由 rAF 合帧，避免消息快于刷新时积压 */
        throttle_rate: 200,
      });
      sub.__patrolCamera = true;
      sub.__kind = 'patrol';
      sub.subscribe(function (msg) {
        schedulePatrolCameraFrame(canvas, imgEl, detMt, msg);
      });
      subs.push(sub);
      return;
    }

    removePatrolCameraSubs();

    if (rawCameraUsesMjpegApi()) {
      applyPatrolViewportForRawCamera();
      return;
    }

    if (!ros) return;

    if (t.cameraImage) {
      const sub = new global.ROSLIB.Topic({
        ros: ros,
        name: t.cameraImage,
        messageType: t.cameraMessageType || 'sensor_msgs/Image',
        throttle_rate: 200,
      });
      sub.__patrolCamera = true;
      sub.__kind = 'patrol';
      sub.subscribe(function (msg) {
        schedulePatrolCameraFrame(canvas, imgEl, '', msg);
      });
      subs.push(sub);
    }
  }

  function resubscribePatrolCameraOnly() {
    removePatrolCameraSubs();
    subscribePatrolCamera();
  }

  function updateDetectionVisToggleUi() {
    const btn = document.getElementById('roadDefectDetectionVisToggleBtn');
    if (!btn) return;
    btn.textContent = detectionVisActive ? '关闭缺陷检测结果' : '显示缺陷检测结果';
    btn.classList.toggle('is-active', detectionVisActive);
  }

  function bindDetectionVisToggle() {
    const btn = document.getElementById('roadDefectDetectionVisToggleBtn');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', function () {
      const detTopic = detectionVisualizationTopicName();
      if (!detectionVisActive && !detTopic) {
        if (typeof global.alert === 'function') {
          global.alert(
            '未配置违停检测可视化话题。请在 config/dashboard.json 的 rosTopics 中设置 detectionVisualizationTopic（及 detectionVisualizationMessageType）。',
          );
        }
        return;
      }
      detectionVisActive = !detectionVisActive;
      updateDetectionVisToggleUi();
      resubscribePatrolCameraOnly();
    });
  }

  function teardownRos() {
    stopGpsEmitTimer();
    stopControllerSampleTimer();
    controllerStatusRawLatest = '';
    prevControllerForTaskSerial = '';
    latestNavFix = null;
    clearSubs();
    stopCmdVelLoop();
    if (cmdVelPub) {
      try {
        cmdVelPub.unadvertise();
      } catch (_) {}
      cmdVelPub = null;
    }
    if (ros) {
      try {
        ros.close();
      } catch (_) {}
      ros = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect() {
    if (!shouldMaintainRosConnection()) return;
    const delay = (cfg().rosbridge && cfg().rosbridge.reconnectDelayMs) || 4000;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      if (shouldMaintainRosConnection()) connectInternal();
    }, delay);
  }

  function connectInternal() {
    const rb = cfg().rosbridge || {};
    const url = rb.url || 'ws://127.0.0.1:9090';
    if (typeof global.ROSLIB === 'undefined') {
      setStatus('ROSLIB 未加载');
      return;
    }
    if (isRosOpen()) {
      subscribeNavSatFix();
      subscribeRobotDashboardTopics();
      if (roadDefectPageActive) {
        subscribePatrolTopicsFull();
        setupCmdVel();
      }
      return;
    }
    teardownRos();
    setStatus('正在连接 ' + url + ' …');
    ros = new global.ROSLIB.Ros({ url: url });

    ros.on('connection', function () {
      setStatus('已连接 rosbridge');
      subscribeNavSatFix();
      subscribeRobotDashboardTopics();
      publishRobotDashboardState();
      if (roadDefectPageActive) {
        subscribePatrolTopicsFull();
        setupCmdVel();
      }
    });
    ros.on('error', function () {
      setStatus('rosbridge 错误');
    });
    ros.on('close', function () {
      setStatus('已断开，将重试…');
      publishRobotDashboardState();
      scheduleReconnect();
    });
  }

  function setupCmdVel() {
    const t = topics();
    if (!roadDefectPageActive || !t.cmdVel || typeof global.ROSLIB === 'undefined') return;
    if (cmdVelPub) return;
    cmdVelPub = new global.ROSLIB.Topic({
      ros: ros,
      name: t.cmdVel,
      messageType: t.cmdVelMessageType || 'geometry_msgs/Twist',
    });
    cmdVelPub.advertise();
  }

  function publishTwist() {
    if (!cmdVelPub || !ros) return;
    const twist = new global.ROSLIB.Message({
      linear: { x: currentTwist.linear.x, y: 0, z: 0 },
      angular: { x: 0, y: 0, z: currentTwist.angular.z },
    });
    cmdVelPub.publish(twist);
  }

  function startCmdVelLoop() {
    const hz = Math.min(30, Math.max(4, Number(control().publishRateHz) || 10));
    if (cmdVelTimer) clearInterval(cmdVelTimer);
    cmdVelTimer = setInterval(publishTwist, 1000 / hz);
  }

  function stopCmdVelLoop() {
    if (cmdVelTimer) {
      clearInterval(cmdVelTimer);
      cmdVelTimer = null;
    }
    currentTwist = { linear: { x: 0, y: 0, z: 0 }, angular: { x: 0, y: 0, z: 0 } };
    publishTwist();
  }

  function setMotion(linearX, angularZ) {
    const c = control();
    const ls = Number(c.linearScale) || 0.25;
    const az = Number(c.angularScale) || 0.5;
    currentTwist.linear.x = linearX * ls;
    currentTwist.angular.z = angularZ * az;
    publishTwist();
  }

  function syncMotionFromKeyboardKeys() {
    let lin = 0;
    let ang = 0;
    if (keyboardKeysDown.has('w')) lin += 1;
    if (keyboardKeysDown.has('s')) lin -= 1;
    if (keyboardKeysDown.has('a')) ang += 1;
    if (keyboardKeysDown.has('d')) ang -= 1;
    setMotion(lin, ang);
    if (lin !== 0 || ang !== 0) {
      startCmdVelLoop();
    } else {
      stopCmdVelLoop();
    }
  }

  function onKeyboardManualKeyDown(e) {
    if (!keyboardManualOn || !roadDefectPageActive) return;
    const code = e.code;
    const key = e.key && e.key.length === 1 ? e.key.toLowerCase() : '';
    let handled = false;
    if (code === 'KeyW' || key === 'w') {
      keyboardKeysDown.add('w');
      handled = true;
    } else if (code === 'KeyS' || key === 's') {
      keyboardKeysDown.add('s');
      handled = true;
    } else if (code === 'KeyA' || key === 'a') {
      keyboardKeysDown.add('a');
      handled = true;
    } else if (code === 'KeyD' || key === 'd') {
      keyboardKeysDown.add('d');
      handled = true;
    }
    if (handled) {
      e.preventDefault();
      syncMotionFromKeyboardKeys();
    }
  }

  function onKeyboardManualKeyUp(e) {
    if (!keyboardManualOn) return;
    const code = e.code;
    const key = e.key && e.key.length === 1 ? e.key.toLowerCase() : '';
    let handled = false;
    if (code === 'KeyW' || key === 'w') {
      keyboardKeysDown.delete('w');
      handled = true;
    } else if (code === 'KeyS' || key === 's') {
      keyboardKeysDown.delete('s');
      handled = true;
    } else if (code === 'KeyA' || key === 'a') {
      keyboardKeysDown.delete('a');
      handled = true;
    } else if (code === 'KeyD' || key === 'd') {
      keyboardKeysDown.delete('d');
      handled = true;
    }
    if (handled) {
      e.preventDefault();
      syncMotionFromKeyboardKeys();
    }
  }

  function updateManualControlUi() {
    const btn = document.getElementById('roadDefectManualToggleBtn');
    const hint = document.getElementById('roadDefectKeyboardHint');
    if (btn) {
      btn.textContent = keyboardManualOn ? '取消手动控制' : '启用手动控制';
      btn.classList.toggle('is-active', keyboardManualOn);
    }
    if (hint) hint.hidden = !keyboardManualOn;
  }

  function setKeyboardManualEnabled(on) {
    if (on) {
      if (keyboardManualOn) return;
      keyboardManualOn = true;
      global.addEventListener('keydown', onKeyboardManualKeyDown, true);
      global.addEventListener('keyup', onKeyboardManualKeyUp, true);
      updateManualControlUi();
      return;
    }
    global.removeEventListener('keydown', onKeyboardManualKeyDown, true);
    global.removeEventListener('keyup', onKeyboardManualKeyUp, true);
    keyboardManualOn = false;
    keyboardKeysDown.clear();
    stopCmdVelLoop();
    updateManualControlUi();
  }

  function bindPatrolStartTask() {
    const btn = document.getElementById('roadDefectStartTaskBtn');
    const hint = document.getElementById('roadDefectStartTaskHint');
    if (!btn || btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', function () {
      if (btn.disabled) return;
      if (hint) {
        hint.hidden = false;
        hint.textContent = '正在通过服务端 SSH 连接机器人…';
        hint.classList.remove('is-error', 'is-ok');
      }
      btn.disabled = true;
      const headers = Object.assign(
        { 'Content-Type': 'application/json' },
        patrolApiExtraHeaders(),
      );
      global
        .fetch(patrolApiBase() + '/patrol/start-task', {
          method: 'POST',
          headers: headers,
          body: '{}',
        })
        .then(function (r) {
          return r.text().then(function (text) {
            var j = {};
            try {
              j = text ? JSON.parse(text) : {};
            } catch (_) {
              j = {
                success: false,
                error: 'BAD_RESPONSE',
                message: 'API 返回非 JSON（HTTP ' + r.status + '）',
              };
            }
            return { status: r.status, j: j };
          });
        })
        .catch(function () {
          return { status: 0, j: { success: false, message: '网络错误或无法连接 API' } };
        })
        .then(function (pack) {
          btn.disabled = false;
          if (!hint) return;
          hint.hidden = false;
          const j = pack.j || {};
          if (pack.status === 401 || j.error === 'AUTH_REQUIRED') {
            hint.classList.add('is-error');
            hint.classList.remove('is-ok');
            hint.textContent =
              'API 拒绝访问（401）：请在 config/dashboard.json 中配置 dashboardApiKey（与后端 DASHBOARD_API_KEY 一致），或使用本机 127.0.0.1 访问后端。';
            return;
          }
          if (j.success) {
            hint.classList.add('is-ok');
            hint.classList.remove('is-error');
            var msg = j.message || '已在机器人上执行启动命令';
            if (j.stdout) msg += '\n' + String(j.stdout).slice(0, 400);
            hint.textContent = msg;
          } else {
            hint.classList.add('is-error');
            hint.classList.remove('is-ok');
            var errLine = j.message || j.error || '执行失败';
            if (j.error === 'CONFIG_MISSING') {
              errLine +=
                '\n（请复制 config/patrol_ssh.example.json 为 config/patrol_ssh.json 并放在项目 config 目录）';
            }
            hint.textContent = errLine;
            if (j.stderr) hint.textContent += '\n' + String(j.stderr).slice(0, 400);
          }
        });
    });
  }

  function bindManualControls() {
    const root = document.getElementById('roadDefectManualControl');
    const btn = document.getElementById('roadDefectManualToggleBtn');
    if (!root || !btn || root.dataset.bound === '1') return;
    root.dataset.bound = '1';

    btn.addEventListener('click', function () {
      if (keyboardManualOn) {
        setKeyboardManualEnabled(false);
        return;
      }
      if (typeof global.alert === 'function') {
        global.alert(
          '已启用手动控制：请使用键盘 W / S 控制前进与后退，A / D 控制左转与右转；可同时按下组合转向。再次点击「取消手动控制」将停止发送速度指令。',
        );
      }
      setKeyboardManualEnabled(true);
    });
  }

  global.rosRoadDefect = {
    ensureConnection: function () {
      if (!navSatTopicName() && !needsRosDashboardTopics()) return;
      if (typeof global.ROSLIB === 'undefined') return;
      if (isRosOpen()) {
        subscribeNavSatFix();
        subscribeRobotDashboardTopics();
        return;
      }
      connectInternal();
    },
    onRoadDefectPageShown: function () {
      roadDefectPageActive = true;
      setPatrolTaskField('roadDefectTaskRobotId', patrolRobotId());
      bindManualControls();
      bindPatrolStartTask();
      bindDetectionVisToggle();
      updateDetectionVisToggleUi();
      applyPatrolViewportForRawCamera();
      drawSpeedChart();
      startPatrolTaskStatusPoll();
      publishRobotDashboardState();
      if (isRosOpen()) {
        subscribePatrolTopicsFull();
        setupCmdVel();
        return;
      }
      connectInternal();
    },
    onRoadDefectPageHidden: function () {
      stopPatrolViewportResizeObserver();
      stopPatrolTaskStatusPoll();
      roadDefectPageActive = false;
      detectionVisActive = false;
      updateDetectionVisToggleUi();
      setPatrolMjpegStreamActive(false);
      setKeyboardManualEnabled(false);
      removePatrolKindSubs();
      stopCmdVelLoop();
      if (cmdVelPub) {
        try {
          cmdVelPub.unadvertise();
        } catch (_) {}
        cmdVelPub = null;
      }
      const rb = cfg().rosbridge || {};
      const keepForGps = !!navSatTopicName() || needsRosDashboardTopics();
      if (rb.connectOnPatrolPageOnly !== false && !keepForGps) {
        teardownRos();
        setStatus('未连接');
      } else {
        setStatus(
          keepForGps
            ? navSatTopicName()
              ? '定位与任务话题后台订阅中'
              : '任务话题后台订阅中'
            : '未连接',
        );
      }
    },
    disconnect: teardownRos,
  };

  try {
    global.dispatchEvent(new Event('ros-road-defect-ready'));
  } catch (e) {
    /* ignore */
  }
})(window);
