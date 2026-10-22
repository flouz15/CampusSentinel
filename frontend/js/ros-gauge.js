(function (global) {
  let ros = null;
  let reconnectTimer = null;
  let cmdVelTimer = null;
  let cmdVelPub = null;
  let pageActive = false;
  let keyboardManualOn = false;
  const subs = [];
  const speedHistory = [];
  const records = [];
  const keysDown = new Set();
  let linearX = 0;
  let angularZ = 0;
  let lastControllerStatus = '';

  function cfg() { return global.__DASHBOARD_CONFIG__ || {}; }
  function topics() { return cfg().rosTopics || {}; }
  function control() { return cfg().robotControl || {}; }
  function apiBase() { return String(cfg().apiBase || '/api').replace(/\/$/, ''); }
  function apiHeaders() {
    const key = String(cfg().dashboardApiKey || '').trim();
    return key ? { 'X-API-Key': key } : {};
  }
  function mjpegUrl() {
    const path = String((cfg().patrolRawCamera || {}).mjpegPath || '/patrol/camera-mjpeg');
    return apiBase() + (path.charAt(0) === '/' ? path : '/' + path);
  }
  function text(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null || value === '' ? '--' : String(value);
  }
  function nowText() {
    return new Date().toLocaleTimeString('zh-CN', { hour12: false });
  }
  function translateStatus(value) {
    const u = String(value || '').trim().toUpperCase();
    if (u === 'RUNNING') return '执行中';
    if (u === 'SUCCESS') return '成功完成';
    if (u === 'FAILURE') return '流程失败';
    return value || '待命';
  }
  function setConnection(message) { text('gaugeConnectionStatus', message); }

  function addBroadcast(message, state) {
    const list = document.getElementById('gaugeBroadcastList');
    if (!list) return;
    if (list.children.length === 1 && list.textContent.indexOf('等待连接机器人') >= 0) list.textContent = '';
    const item = document.createElement('li');
    item.textContent = nowText() + '  ' + message;
    list.prepend(item);
    while (list.children.length > 30) list.removeChild(list.lastElementChild);
    if (state) text('gaugeBroadcastState', state);
  }

  function setCamera(on) {
    const img = document.getElementById('gaugePatrolMjpegImg');
    if (!img) return;
    if (on) {
      const next = mjpegUrl() + '?t=' + Date.now();
      if (img.src !== next) img.src = next;
      img.hidden = false;
      img.classList.add('is-visible');
    } else {
      img.classList.remove('is-visible');
      img.hidden = true;
      img.removeAttribute('src');
    }
  }

  function setBattery(percent) {
    const sphere = document.getElementById('gaugeBatterySphere');
    const value = Math.max(0, Math.min(1, Number(percent) || 0));
    if (sphere) {
      sphere.style.setProperty('--battery-level', String(value));
      sphere.dataset.level = value < 0.2 ? 'low' : 'ok';
    }
    text('gaugeBatteryPercentLabel', Math.round(value * 100) + '%');
  }

  function drawSpeed() {
    const canvas = document.getElementById('gaugeSpeedChartCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#071019';
    ctx.fillRect(0, 0, w, h);
    ctx.strokeStyle = 'rgba(37,246,255,0.18)';
    ctx.beginPath();
    for (let i = 1; i <= 3; i += 1) {
      const y = (h - 24) * i / 4 + 12;
      ctx.moveTo(12, y); ctx.lineTo(w - 12, y);
    }
    ctx.stroke();
    if (!speedHistory.length) return;
    const max = Math.max(0.2, ...speedHistory);
    ctx.strokeStyle = '#25f6ff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    speedHistory.forEach(function (value, index) {
      const x = 12 + (w - 24) * index / Math.max(1, speedHistory.length - 1);
      const y = h - 14 - (h - 30) * (value / max);
      if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = '#8aa4b4';
    ctx.font = '12px sans-serif';
    ctx.fillText(speedHistory[speedHistory.length - 1].toFixed(2) + ' m/s', 14, 18);
  }

  function statusOf(record) {
    if (record.meter_reader_error) return 'error';
    const reading = record.meter_reading;
    if ((reading == null || reading === '') && record.meter_reading_value == null) return 'error';
    return 'normal';
  }

  function showRecord(record) {
    const status = statusOf(record);
    const badge = document.getElementById('gaugeResultStatus');
    if (badge) {
      badge.textContent = status === 'error' ? '识别异常' : '识别正常';
      badge.className = 'gauge-status-pill is-' + status;
    }
    text('gaugeReadingValue', record.meter_reading || record.meter_reading_value);
    text('gaugeIndexValue', record.gauge_index);
    text('gaugeImageIndexValue', record.image_index);
    text('gaugeLatencyValue', record.latency_ms ? record.latency_ms + ' ms' : '--');
    text('gaugeTaskStateValue', record.task_state);
    text('gaugeResultTime', record.time);
    const message = document.getElementById('gaugeResultMessage');
    if (message) {
      message.textContent = record.meter_reader_error
        ? '识别异常：' + record.meter_reader_error
        : '机器人已返回仪表 #' + (record.gauge_index || '--') + ' 的读数。';
    }
    const image = document.getElementById('gaugeResultImage');
    if (image) {
      image.src = record.capture_image_base64
        ? 'data:image/jpeg;base64,' + record.capture_image_base64
        : '';
      image.classList.toggle('is-empty', !record.capture_image_base64);
    }
  }

  function renderHistory() {
    const root = document.getElementById('gaugeHistoryList');
    if (!root) return;
    root.textContent = '';
    if (!records.length) {
      const empty = document.createElement('p'); empty.textContent = '暂无仪表检测结果。'; root.appendChild(empty); return;
    }
    records.forEach(function (record, index) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'gauge-history-item' + (index === 0 ? ' is-active' : '');
      button.innerHTML = '<strong>' + (record.meter_reading || '--') + '</strong><span>仪表 #' +
        (record.gauge_index || '--') + ' · ' + (record.time || '') + '</span>';
      button.addEventListener('click', function () {
        root.querySelectorAll('.is-active').forEach(function (el) { el.classList.remove('is-active'); });
        button.classList.add('is-active');
        showRecord(record);
      });
      root.appendChild(button);
    });
  }

  function onGaugeResult(raw) {
    let record = {};
    try { record = JSON.parse(raw || '{}'); } catch (_) { addBroadcast('收到无法解析的仪表结果', '数据异常'); return; }
    record.time = nowText();
    records.unshift(record);
    if (records.length > 50) records.pop();
    showRecord(record);
    renderHistory();
    const reading = record.meter_reading || record.meter_reading_value || '无读数';
    addBroadcast('仪表 #' + (record.gauge_index || '--') + ' 读数 ' + reading, statusOf(record) === 'error' ? '识别异常' : '已识别');
    try {
      global.dispatchEvent(new CustomEvent('gauge-result-update', {
        detail: { record: record, records: records.slice() },
      }));
    } catch (_) {}
  }

  function clearSubs(kind) {
    for (let i = subs.length - 1; i >= 0; i -= 1) {
      if (kind && subs[i].__kind !== kind) continue;
      try { subs[i].unsubscribe(); } catch (_) {}
      subs.splice(i, 1);
    }
  }
  function addSub(kind, name, type, handler) {
    if (!ros || !name) return;
    const sub = new global.ROSLIB.Topic({ ros: ros, name: name, messageType: type });
    sub.__kind = kind;
    sub.subscribe(handler);
    subs.push(sub);
  }
  function subscribeBase() {
    if (subs.some(function (s) { return s.__kind === 'base'; })) return;
    const t = topics();
    addSub('base', t.battery, t.batteryMessageType || 'rpp_msgs/BatteryState', function (msg) {
      const value = Number(msg.percentage != null ? msg.percentage : msg.percent);
      if (Number.isFinite(value)) setBattery(value > 1 ? value / 100 : value);
    });
    addSub('base', t.speed, t.speedMessageType || 'nav_msgs/Odometry', function (msg) {
      const value = Math.abs(Number(msg.twist && msg.twist.twist && msg.twist.twist.linear && msg.twist.twist.linear.x) || 0);
      speedHistory.push(value);
      if (speedHistory.length > 40) speedHistory.shift();
      drawSpeed();
    });
    addSub('base', t.controllerStatus, t.controllerStatusMessageType || 'std_msgs/String', function (msg) {
      const value = msg.data != null ? String(msg.data).trim() : '';
      if (!value || value === lastControllerStatus) return;
      lastControllerStatus = value;
      addBroadcast('巡检流程：' + translateStatus(value), translateStatus(value));
      try {
        global.dispatchEvent(new CustomEvent('gauge-dashboard-state', {
          detail: {
            rosConnected: !!(ros && ros.isConnected),
            controllerStatusRaw: value,
            controllerStatusZh: translateStatus(value),
            taskProgressLabel: translateStatus(value),
          },
        }));
      } catch (_) {}
    });
    addSub('base', t.gaugeResult || '/gauge_task/result_json', t.gaugeResultMessageType || 'std_msgs/String', function (msg) {
      onGaugeResult(msg.data);
    });
  }

  function publishTwist() {
    if (!cmdVelPub) return;
    cmdVelPub.publish({ linear: { x: linearX, y: 0, z: 0 }, angular: { x: 0, y: 0, z: angularZ } });
  }
  function setupCmd() {
    const t = topics();
    if (!ros || !t.cmdVel || cmdVelPub) return;
    cmdVelPub = new global.ROSLIB.Topic({
      ros: ros, name: t.cmdVel, messageType: t.cmdVelMessageType || 'geometry_msgs/Twist'
    });
    cmdVelPub.advertise();
  }
  function syncKeys() {
    const c = control();
    linearX = (keysDown.has('w') ? 1 : 0) - (keysDown.has('s') ? 1 : 0);
    angularZ = (keysDown.has('a') ? 1 : 0) - (keysDown.has('d') ? 1 : 0);
    linearX *= Number(c.linearScale || 0.25);
    angularZ *= Number(c.angularScale || 0.5);
    publishTwist();
  }
  function onKey(event, down) {
    if (!keyboardManualOn) return;
    const key = String(event.key || '').toLowerCase();
    if (!'wasd'.includes(key)) return;
    event.preventDefault();
    if (down) keysDown.add(key); else keysDown.delete(key);
    syncKeys();
  }
  function setManual(on) {
    keyboardManualOn = on;
    keysDown.clear(); linearX = 0; angularZ = 0;
    const hint = document.getElementById('gaugeKeyboardHint');
    const button = document.getElementById('gaugeManualToggleBtn');
    if (hint) hint.hidden = !on;
    if (button) button.textContent = on ? '取消手动控制' : '启用手动控制';
    if (on) {
      setupCmd();
      const hz = Math.min(30, Math.max(4, Number(control().publishRateHz) || 10));
      cmdVelTimer = setInterval(publishTwist, 1000 / hz);
    } else if (cmdVelTimer) {
      clearInterval(cmdVelTimer); cmdVelTimer = null; publishTwist();
    }
  }

  function bindControls() {
    const manual = document.getElementById('gaugeManualToggleBtn');
    if (manual && manual.dataset.bound !== '1') {
      manual.dataset.bound = '1';
      manual.addEventListener('click', function () { setManual(!keyboardManualOn); });
      document.addEventListener('keydown', function (e) { onKey(e, true); });
      document.addEventListener('keyup', function (e) { onKey(e, false); });
    }
    const start = document.getElementById('gaugeStartTaskBtn');
    const hint = document.getElementById('gaugeStartTaskHint');
    if (start && start.dataset.bound !== '1') {
      start.dataset.bound = '1';
      start.addEventListener('click', function () {
        start.disabled = true;
        if (hint) { hint.hidden = false; hint.className = 'patrol-start-task-hint'; hint.textContent = '正在通过服务端 SSH 连接机器人…'; }
        addBroadcast('正在启动仪表巡检任务', '启动中');
        fetch(apiBase() + '/patrol/start-task', {
          method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, apiHeaders()), body: '{}'
        }).then(function (r) { return r.json(); }).then(function (j) {
          const ok = !!j.success;
          if (hint) { hint.classList.add(ok ? 'is-ok' : 'is-error'); hint.textContent = j.message || (ok ? '任务已启动' : '启动失败'); }
          addBroadcast(j.message || (ok ? '仪表巡检任务已启动' : '任务启动失败'), ok ? '执行中' : '启动失败');
          try {
            global.dispatchEvent(new CustomEvent('gauge-dashboard-state', {
              detail: {
                rosConnected: !!(ros && ros.isConnected),
                controllerStatusRaw: ok ? 'RUNNING' : 'FAILURE',
                controllerStatusZh: ok ? '执行中' : '启动失败',
                taskProgressLabel: ok ? '仪表巡检执行中' : '启动失败',
              },
            }));
          } catch (_) {}
        }).catch(function () {
          if (hint) { hint.classList.add('is-error'); hint.textContent = '网络错误或无法连接 API'; }
          addBroadcast('无法连接任务启动接口', '启动失败');
          try {
            global.dispatchEvent(new CustomEvent('gauge-dashboard-state', {
              detail: {
                rosConnected: !!(ros && ros.isConnected),
                controllerStatusRaw: 'FAILURE',
                controllerStatusZh: '启动失败',
                taskProgressLabel: '启动失败',
              },
            }));
          } catch (_) {}
        }).finally(function () { start.disabled = false; });
      });
    }
  }

  function connect() {
    if (typeof global.ROSLIB === 'undefined') { setConnection('roslib 未加载'); return; }
    const rb = cfg().rosbridge || {};
    if (!rb.url) { setConnection('未配置 rosbridge'); return; }
    if (ros && ros.isConnected) { subscribeBase(); return; }
    ros = new global.ROSLIB.Ros({ url: rb.url });
    ros.on('connection', function () {
      setConnection('已连接 ' + rb.url);
      addBroadcast('已连接机器人 ' + rb.url, '已连接');
      subscribeBase(); setupCmd();
    });
    ros.on('error', function () { setConnection('rosbridge 连接异常'); });
    ros.on('close', function () {
      setConnection('连接已断开，准备重连');
      clearSubs(); cmdVelPub = null;
      if (!reconnectTimer) reconnectTimer = setTimeout(function () { reconnectTimer = null; if (pageActive) connect(); }, Number(rb.reconnectDelayMs || 4000));
    });
  }

  function disconnect() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    setManual(false);
    clearSubs();
    if (cmdVelPub) { try { cmdVelPub.unadvertise(); } catch (_) {} cmdVelPub = null; }
    if (ros) { try { ros.close(); } catch (_) {} ros = null; }
    setConnection('未连接');
  }

  global.rosGauge = {
    onGaugePageShown: function () {
      pageActive = true;
      bindControls(); setCamera(true); drawSpeed(); connect();
    },
    onGaugePageHidden: function () {
      pageActive = false; setCamera(false); setManual(false);
      const rb = cfg().rosbridge || {};
      if (rb.connectOnPatrolPageOnly !== false) disconnect();
    }
  };
})(window);
