/**
 * 加载项目根目录 config/dashboard.json，写入 window.__DASHBOARD_CONFIG__ 与 window.__AMAP_APP__.apiBase
 * 支持 JSON 外的 // 行注释与块注释（slash-star，字符串内除外）
 */
(function () {
  const sc = document.currentScript;
  const scriptUrl = sc && sc.src ? new URL(sc.src) : new URL('js/load-config.js', window.location.href);
  const configUrl = new URL('../../config/dashboard.json', scriptUrl);

  function normalizeApi(base) {
    let api = String(base || 'http://127.0.0.1:5000/api').replace(/\/+$/, '');
    if (!/\/api$/i.test(api)) api += '/api';
    return api;
  }

  /** 去除 JSON 中的注释，不处理字符串内的引号转义之外的边界情况 */
  function stripJsonComments(text) {
    const s = String(text || '');
    let out = '';
    let i = 0;
    let inString = false;
    let escape = false;
    while (i < s.length) {
      const c = s[i];
      if (inString) {
        out += c;
        if (escape) {
          escape = false;
        } else if (c === '\\') {
          escape = true;
        } else if (c === '"') {
          inString = false;
        }
        i += 1;
        continue;
      }
      if (c === '"') {
        out += c;
        inString = true;
        i += 1;
        continue;
      }
      if (c === '/' && s[i + 1] === '/') {
        i += 2;
        while (i < s.length && s[i] !== '\n' && s[i] !== '\r') i += 1;
        continue;
      }
      if (c === '/' && s[i + 1] === '*') {
        i += 2;
        while (i < s.length - 1 && !(s[i] === '*' && s[i + 1] === '/')) i += 1;
        i += 2;
        continue;
      }
      out += c;
      i += 1;
    }
    return out;
  }

  window.__DASHBOARD_CONFIG_READY__ = fetch(configUrl)
    .then(function (r) {
      if (!r.ok) throw new Error('config fetch ' + r.status);
      return r.text();
    })
    .then(function (text) {
      return JSON.parse(stripJsonComments(text));
    })
    .then(function (cfg) {
      window.__DASHBOARD_CONFIG__ = cfg;
      window.__AMAP_APP__ = Object.assign({}, window.__AMAP_APP__ || {}, {
        apiBase: normalizeApi(cfg.apiBase),
      });
      return cfg;
    })
    .catch(function (err) {
      console.error('[dashboard-config]', err);
      window.__DASHBOARD_CONFIG__ = window.__DASHBOARD_CONFIG__ || {};
      window.__AMAP_APP__ = Object.assign({}, window.__AMAP_APP__ || {}, {
        apiBase: normalizeApi(),
      });
      return window.__DASHBOARD_CONFIG__;
    });
})();
