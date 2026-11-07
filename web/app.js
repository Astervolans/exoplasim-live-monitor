/* app.js — 状态轮询 / 面板 / 序列图 / 多 run（零依赖）
 * 只使用 /api/runs、/api/state、/api/frame、/api/land（CONTRACT.md §2）。
 */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var D2R = Math.PI / 180;
  var REDUCED = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var QS = new URLSearchParams(location.search);

  /* 双语：所有用户可见文案都走 i18n.js（window.EXO_I18N） */
  var I18N = window.EXO_I18N;
  function t(key, vars) { return I18N.t(key, vars); }
  function lang() { return I18N.getLang(); }

  var LS_RUN = 'exoplasim.monitor.run';
  var LS_LAYER = 'exoplasim.monitor.layer';
  var LS_UNIT = 'exoplasim.monitor.unit';

  /* CONTRACT v2：ts/pr/spd/sic/sit/clt/tas */
  var LAYER_ORDER = ['ts', 'pr', 'spd', 'sic', 'sit', 'clt', 'tas'];
  var LAYER_CM = { ts: 'thermal', tas: 'thermal', pr: 'precip', spd: 'wind', sic: 'ice', sit: 'thick', clt: 'cloud' };
  var LAYER_UNIT = { ts: 'K', tas: 'K', pr: 'mm/d', spd: 'm/s', sic: '1', sit: 'm', clt: '1' };
  var TEMP_LAYERS = { ts: 1, tas: 1 };
  var PERCENT_LAYERS = { clt: 1, sic: 1 };
  var HOVER_KEYS = ['ts', 'tas', 'spd', 'sic', 'sit', 'clt'];

  /* 图层名一律由前端按 key 生成（后端 layers[*].name 只在中文下等价，英文靠 key 映射） */
  function layerName(key) { return t('layer.' + key); }

  /* 状态：英文模式由结构化 run.status 重生成，中文沿用后端 status_text（更细） */
  function statusName(status) {
    var s = status || 'waiting';
    var k = 'status.' + s;
    return I18N.has(k) ? t(k) : s;   // 未知枚举值原样显示
  }
  function statusDetail(run) {
    if (!run) return '—';
    if (lang() === 'zh' && run.status_text) return run.status_text;
    return statusName(run.status);
  }

  /* 参数单位：只有口语单位（step/month/year/day）需要翻译，物理单位原样 */
  function unitText(u) {
    if (!u) return '';
    var k = 'unit.' + u;
    return I18N.has(k) ? t(k) : u;
  }

  /* params 显示名 / 分组（分组用稳定 key，显示名走字典） */
  var PARAM_META = {
    modelname: { group: 'run' },
    run_id: { group: 'run' },
    workdir: { group: 'run' },
    outdir: { group: 'run' },

    resolution: { group: 'num' },
    layers: { group: 'num' },
    ncpus: { group: 'num' },
    model_class: { group: 'num' },
    timestep_min: { unit: 'min', group: 'num' },
    snapshot_steps: { unit: 'steps', group: 'num' },
    steps_per_year: { unit: 'steps', group: 'num' },
    planned_months: { unit: 'months', group: 'num' },
    planned_years: { unit: 'years', group: 'num' },
    year_total: { unit: 'years', group: 'num' },
    run_mode: { group: 'run' },
    restart_year: { unit: 'years', group: 'run' },
    plan_source: { group: 'run' },

    flux_W_m2: { unit: 'W/m²', group: 'star' },
    startemp_K: { unit: 'K', group: 'star' },
    rotationperiod_d: { unit: 'days', group: 'star' },
    synchronous: { group: 'star' },
    eccentricity: { group: 'star' },
    obliquity_deg: { unit: '°', group: 'star' },
    substellar_lon_deg: { unit: '°', group: 'star' },

    pN2_bar: { unit: 'bar', group: 'atm' },
    pO2_bar: { unit: 'bar', group: 'atm' },
    pAr_bar: { unit: 'bar', group: 'atm' },
    pCO2_bar: { unit: 'bar', group: 'atm' },
    pCO2_ppmv: { unit: 'ppmv', group: 'atm' },
    physicsfilter: { group: 'atm' },

    radius_Rearth: { unit: 'R⊕', group: 'geom' },
    gravity_m_s2: { unit: 'm/s²', group: 'geom' },
    planet_radius_m: { unit: 'm', group: 'geom' },
    nlat_model: { group: 'geom' },
    nlon_model: { group: 'geom' },
    ntru: { group: 'geom' },

    landmap: { group: 'src' },
    topomap: { group: 'src' },
    restartfile: { group: 'src' }
  };
  var GROUP_ORDER = ['run', 'num', 'star', 'atm', 'geom', 'src', 'other'];
  function groupName(g) { return t('group.' + g); }
  function paramLabel(k) { return t('param.' + k); }

  /* ---------------- 状态 ---------------- */

  var S = {
    runs: [], defaultRun: null, runId: null,
    st: null,
    frame: null,
    lastFrameKey: null,
    layer: 'ts',
    unit: 'K',
    opts: { light: true, grid: true, land: true, arrows: true },
    hover: null,          // 当前悬停采样 {lat,lon,model,idx,vals,col,row}
    hoverScreen: null,    // 屏幕坐标（CSS px，画布内）
    pinned: null,         // ?hover=<lat>,<lon> 固定浮层（显示系经纬度）
    lastPointerType: 'mouse',
    historyMode: false, cursor: null, playing: false, playTimer: null,
    pickerOpen: false,
    stateBusy: false, runsBusy: false,
    conn: { ok: true, errors: 0 },
    tokens: { frame: 0, land: 0 },
    clientMsg: null, clientMsgAt: 0,
    lastParamsJson: '',
    firstFrameMs: null,
    t0: (window.performance && performance.now) ? performance.now() : 0,
    showedFrame: false,
    fetched: []
  };

  var els = {};
  var globe = null;

  /* ---------------- 工具 ---------------- */

  function fmtNum(v) {
    if (v === null || v === undefined || typeof v !== 'number' || !isFinite(v)) return '—';
    var a = Math.abs(v);
    if (a >= 1e6 || (a > 0 && a < 1e-3)) return v.toExponential(2).replace('e+', 'e');
    if (a >= 1000) return v.toFixed(0);
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2).replace(/\.?0+$/, '');
    if (a === 0) return '0';
    return v.toFixed(3).replace(/\.?0+$/, '');
  }

  function unitLabel(u) {
    if (!u || u === '1') return '';
    return u;
  }

  function fmtVal(v, unit) {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no');
    if (typeof v === 'number') {
      var s;
      var a = Math.abs(v);
      if (!isFinite(v)) s = String(v);
      else if (a !== 0 && (a >= 1e6 || a < 1e-4)) s = v.toExponential(3).replace('e+', 'e');
      else if (Number.isInteger(v)) s = String(v);
      else s = String(Number(v.toFixed(4)));
      return unit ? s + ' ' + unit : s;
    }
    return String(v);
  }

  function fmtDur(s) {
    if (s === null || s === undefined || !isFinite(s)) return '—';
    if (s < 0) return '0 s';
    if (s < 60) return s.toFixed(1) + ' s';
    if (s < 3600) return t('dur.min', { m: Math.floor(s / 60), s: Math.round(s % 60) });
    return t('dur.hour', { h: Math.floor(s / 3600), m: Math.round((s % 3600) / 60) });
  }

  /* ---------- 温度单位 / 图层数值格式（CONTRACT §4） ---------- */

  function tempUnit() { return S.unit === 'C' ? '°C' : 'K'; }

  /* K → 当前单位（ts/tas，其余原样） */
  function convTemp(v) {
    if (v === null || v === undefined || !isFinite(v)) return v;
    return S.unit === 'C' ? v - 273.15 : v;
  }

  function isTemp(key) { return !!TEMP_LAYERS[key]; }
  function isPercent(key) { return !!PERCENT_LAYERS[key]; }

  /* 该图层在当前单位下的单位标签 */
  function layerUnit(key) {
    if (isTemp(key)) return tempUnit();
    if (isPercent(key)) return '%';
    return unitLabel(LAYER_UNIT[key] || '');
  }

  /* 数值 → 显示串（不含单位），按图层语义格式化 */
  function fmtLayerNum(key, v) {
    if (v === null || v === undefined || typeof v !== 'number' || !isFinite(v)) return '—';
    if (isTemp(key)) return convTemp(v).toFixed(1);
    if (isPercent(key)) return Math.round(clamp(v, 0, 1) * 100) + '%';
    if (key === 'sit') return v.toFixed(1);
    if (key === 'sic') return v.toFixed(2);
    if (key === 'pr') return v < 10 ? v.toFixed(2) : v.toFixed(1);
    if (key === 'spd') return v.toFixed(1);
    return fmtNum(v);
  }

  /* 数值 → "值 单位" */
  function fmtLayerVal(key, v) {
    var s = fmtLayerNum(key, v);
    if (s === '—') return s;
    if (isPercent(key)) return s;
    var u = layerUnit(key);
    return u ? s + ' ' + u : s;
  }

  /* 色标刻度（range 端点/中点，按单位换算） */
  function fmtRangeTick(key, v) {
    if (isTemp(key)) return convTemp(v).toFixed(1);
    if (isPercent(key)) return Math.round(v * 100) + '%';
    if (key === 'sit') return v.toFixed(1);
    return fmtNum(v);
  }

  /* 悬停坐标格式：23.9°N / 45.0°E（或 W） */
  function fmtLatLon(lat, lon) {
    var ns = lat >= 0 ? 'N' : 'S';
    var ew = lon >= 0 ? 'E' : 'W';
    var lo = lon;
    if (lo > 180) lo -= 360;
    return {
      lat: Math.abs(lat).toFixed(1) + '°' + ns,
      lon: Math.abs(lo).toFixed(1) + '°' + ew
    };
  }

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }

  function enc(s) { return encodeURIComponent(s); }

  function setClientMsg(text, isError) {
    S.clientMsg = text;
    S.clientMsgAt = Date.now();
    S.clientMsgErr = !!isError;
    renderMsg();
  }

  async function getJSON(url) {
    S.fetched.push(url);
    var res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      var err = new Error('HTTP ' + res.status);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  /* EXOF 解析（header JSON + 每个图层 nlat*nlon float32，C 序） */
  function parseExof(buffer) {
    var u8 = new Uint8Array(buffer);
    if (u8.length < 12) throw new Error(t('exof.short'));
    if (!(u8[0] === 0x45 && u8[1] === 0x58 && u8[2] === 0x4F && u8[3] === 0x46)) {
      throw new Error(t('exof.magic'));
    }
    var dv = new DataView(buffer);
    var version = dv.getUint32(4, true);
    var hlen = dv.getUint32(8, true);
    if (12 + hlen > u8.length) throw new Error(t('exof.header'));
    var header = JSON.parse(new TextDecoder('utf-8').decode(u8.subarray(12, 12 + hlen)));
    var nlat = header.nlat | 0, nlon = header.nlon | 0;
    var n = nlat * nlon;
    var off = 12 + hlen;
    var layers = {};
    var order = header.order || [];
    for (var i = 0; i < order.length; i++) {
      if (off + n * 4 > u8.length) throw new Error(t('exof.truncated', { layer: order[i] }));
      layers[order[i]] = readF32(buffer, off, n);
      off += n * 4;
    }
    return { header: header, layers: layers, version: version };
  }

  function readF32(buffer, off, n) {
    if ((off & 3) === 0) return new Float32Array(buffer, off, n);
    var src = new Uint8Array(buffer, off, n * 4);
    var copy = new Uint8Array(n * 4);
    copy.set(src);
    return new Float32Array(copy.buffer);
  }

  /* ---------------- 启动 ---------------- */

  function boot() {
    els = {
      picker: $('runPicker'), pickerBtn: $('pickerBtn'), pickerList: $('pickerList'),
      pickerLabel: $('pickerLabel'), pickerDot: $('pickerDot'),
      conn: $('conn'), connText: $('connText'),
      badge: $('statusBadge'), freshness: $('freshness'), server: $('serverMeta'),
      msg: $('msgBanner'),
      globe: $('globe'), wind: $('wind'), markers: $('markers'),
      loading: $('loading'), glFallback: $('glFallback'), glFallbackMsg: $('glFallbackMsg'),
      layerName: $('layerName'), layerMeta: $('layerMeta'), frameChip: $('frameChip'),
      layerButtons: $('layerButtons'), gridInfo: $('gridInfo'),
      cbarGrad: $('cbarGrad'), cbarTicks: $('cbarTicks'), cbarNow: $('cbarNow'),
      progressPhase: $('progressPhase'), progressPct: $('progressPct'),
      progressBar: $('progressBar'), progressKv: $('progressKv'),
      chart: $('chart'), legendTs: $('legendTs'), legendNtr: $('legendNtr'),
      paramsBody: $('paramsBody'), paramsSource: $('paramsSource'),
      runInfo: $('runInfo'), runIdText: $('runIdText'), copyRun: $('copyRun'),
      slider: $('frameSlider'), playBtn: $('playBtn'), tlLabel: $('tlLabel'), liveBtn: $('liveBtn'),
      selftest: $('selftest'), resetView: $('resetView'),
      zoomIn: $('zoomIn'), zoomOut: $('zoomOut'),
      hoverCard: $('hoverCard'), hcLat: $('hcLat'), hcLon: $('hcLon'),
      hcLayerName: $('hcLayerName'), hcLayerVal: $('hcLayerVal'), hcGrid: $('hcGrid'),
      unitSeg: $('unitSeg'), langSeg: $('langSeg')
    };

    // 恢复偏好
    var savedLayer = null, savedUnit = null;
    try {
      savedLayer = localStorage.getItem(LS_LAYER);
      savedUnit = localStorage.getItem(LS_UNIT);
    } catch (e) { /* private mode */ }
    if (LAYER_ORDER.indexOf(savedLayer) >= 0) S.layer = savedLayer;
    var qLayer = QS.get('layer');
    if (LAYER_ORDER.indexOf(qLayer) >= 0) S.layer = qLayer;
    S.unit = savedUnit === 'C' ? 'C' : 'K';
    // 深链优先（CONTRACT §4）
    var qUnit = (QS.get('unit') || '').toLowerCase();
    if (qUnit === 'c' || qUnit === '°c' || qUnit === 'celsius') S.unit = 'C';
    else if (qUnit === 'k') S.unit = 'K';
    parsePinnedHover();

    initGlobe();
    buildLayerButtons();
    buildStaticChips();
    updateUnitUI();
    updateLangUI();
    I18N.onChange(onLangChange);
    bindUI();
    drawChart();

    setInterval(tickState, 1000);
    setInterval(tickRuns, 2000);
    setInterval(renderFreshness, 500);
    setTimeout(function () { hideLoading(); }, 6000);

    tickRuns();
    renderFreshness();
    renderServerMeta();

    window.__EXO__ = {
      state: S,
      globe: globe,
      selftest: runSelfTest,
      switchRun: switchRun,
      setLayer: setLayer,
      setUnit: setUnit,
      loadFrame: loadFrame,
      tickState: tickState,
      tickRuns: tickRuns,
      hoverAt: hoverAtLatLon
    };

    if (QS.get('selftest')) {
      setTimeout(function () { runSelfTest(true); }, 2600);
    }
  }

  /* ?hover=<lat>,<lon>：固定在显示系经纬度（便于截图/分享） */
  function parsePinnedHover() {
    var hv = QS.get('hover');
    if (!hv) return;
    var parts = String(hv).split(',');
    if (parts.length !== 2) return;
    var la = parseFloat(parts[0]), lo = parseFloat(parts[1]);
    if (!isFinite(la) || !isFinite(lo)) return;
    S.pinned = { lat: clamp(la, -89.9, 89.9), lon: ((lo % 360) + 360) % 360 };
  }

  function initGlobe() {
    try {
      globe = window.ExoGlobe.create(els.globe, els.wind, els.markers, {
        reduceMotion: REDUCED,
        autoRotate: !REDUCED
      });
      globe.setOptions({ arrows: false });
    } catch (err) {
      globe = null;
      els.glFallback.hidden = false;
      els.glFallbackMsg.textContent = t('err.glInit', { msg: err && err.message ? err.message : err });
      els.globe.style.display = 'none';
      els.wind.style.display = 'none';
      els.loading.classList.add('hide');
      if (window.console) console.warn('[globe] WebGL2 不可用：', err && err.message ? err.message : err);
    }
  }

  function hideLoading() {
    if (els.loading) els.loading.classList.add('hide');
  }

  /* ---------------- /api/runs ---------------- */

  async function tickRuns() {
    if (S.runsBusy) return;
    S.runsBusy = true;
    try {
      var doc = await getJSON('/api/runs');
      if (!doc || doc.ok === false) throw new Error(doc && doc.error ? doc.error : 'bad runs');
      S.runs = Array.isArray(doc.runs) ? doc.runs : [];
      S.defaultRun = doc.default || null;
      renderRunPicker();
      if (!S.runId) {
        var stored = null;
        try { stored = localStorage.getItem(LS_RUN); } catch (e) { /* ignore */ }
        var pinned = QS.get('run');
        var pick = null;
        // ?run=<run_id> 优先（深链/截图用），其次 localStorage，其次后端 default
        if (pinned && S.runs.some(function (r) { return r.run_id === pinned; })) pick = pinned;
        else if (stored && S.runs.some(function (r) { return r.run_id === stored; })) pick = stored;
        else if (S.defaultRun && S.runs.some(function (r) { return r.run_id === S.defaultRun; })) pick = S.defaultRun;
        else if (S.runs.length) pick = S.runs[0].run_id;
        if (pick) switchRun(pick);
      } else if (!S.runs.some(function (r) { return r.run_id === S.runId; })) {
        var p2 = S.defaultRun || (S.runs[0] && S.runs[0].run_id);
        if (p2 && p2 !== S.runId) switchRun(p2);
      }
      S.conn.ok = true;
    } catch (err) {
      S.conn.ok = false;
      S.conn.errors++;
      renderConn();
      if (!S.runs.length) {
        els.pickerLabel.textContent = t('picker.notFoundRetry');
        els.pickerDot.className = 'dot status-error';
      }
    } finally {
      S.runsBusy = false;
    }
  }

  function prettyRunShort(r) {
    var id = r.run_id || '';
    return t('picker.item', { model: r.modelname || 'run', id: id.slice(-5), n: r.frame_count || 0 });
  }

  function renderRunPicker() {
    var cur = null;
    for (var i = 0; i < S.runs.length; i++) if (S.runs[i].run_id === S.runId) cur = S.runs[i];
    if (cur) {
      els.pickerLabel.textContent = prettyRunShort(cur);
      els.pickerDot.className = 'dot status-' + (cur.status || 'waiting');
      els.pickerBtn.title = t('picker.tip', {
        id: cur.run_id,
        dir: cur.workdir || '',
        status: t('kv.status'),
        statusText: statusDetail(cur),
        frames: t('kv.frames'),
        framesN: cur.frame_count || 0
      });
    } else if (S.runId) {
      els.pickerLabel.textContent = S.runId;
      els.pickerDot.className = 'dot status-waiting';
    }
    if (S.pickerOpen) return;

    var html = '';
    for (var k = 0; k < S.runs.length; k++) {
      var r = S.runs[k];
      var sel = r.run_id === S.runId;
      var st = r.status || 'waiting';
      html += '<button type="button" class="picker-item" role="option" aria-selected="' + (sel ? 'true' : 'false') +
        '" data-run="' + escapeAttr(r.run_id) + '" title="' + escapeAttr(r.run_id + '\n' + (r.workdir || '')) + '">' +
        '<span class="dot status-' + st + '"></span>' +
        '<span class="pi-model">' + escapeHtml(r.modelname || 'run') + '</span>' +
        '<span class="pi-id">· ' + escapeHtml((r.run_id || '').slice(-5)) + '</span>' +
        '<span class="pi-status">· ' + escapeHtml(statusName(st)) + '</span>' +
        '<span class="pi-count">' + escapeHtml(t('picker.frames', { n: r.frame_count || 0 })) + '</span>' +
        '</button>';
    }
    if (!html) html = '<div class="empty">' + escapeHtml(t('picker.empty')) + '</div>';
    els.pickerList.innerHTML = html;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function escapeAttr(s) { return escapeHtml(s); }

  function togglePicker(open) {
    S.pickerOpen = open === undefined ? !S.pickerOpen : !!open;
    els.pickerList.hidden = !S.pickerOpen;
    els.pickerBtn.setAttribute('aria-expanded', S.pickerOpen ? 'true' : 'false');
    if (S.pickerOpen) renderRunPicker();
  }

  function switchRun(id) {
    if (!id) return;
    if (id === S.runId && S.st) return;
    S.runId = id;
    try { localStorage.setItem(LS_RUN, id); } catch (e) { /* ignore */ }
    S.st = null;
    S.frame = null;
    S.lastFrameKey = null;
    S.historyMode = false;
    S.cursor = null;
    stopPlay();
    S.tokens.frame++;
    S.tokens.land++;
    S.clientMsg = null;
    if (globe) globe.clearData();
    renderAll();
    tickState();
    loadLand();
  }
  /* ---------------- /api/state ---------------- */

  async function tickState() {
    if (S.stateBusy) return;
    if (!S.runId) return;
    S.stateBusy = true;
    var run = S.runId;
    try {
      var st = await getJSON('/api/state?run=' + enc(run));
      if (run !== S.runId) return;
      if (!st || st.ok === false) throw new Error((st && st.error) || 'bad state');
      S.st = st;
      S.conn.ok = true;
      S.conn.errors = 0;
      renderConn();
      onState(st);
    } catch (err) {
      if (run !== S.runId) return;
      S.conn.ok = false;
      S.conn.errors++;
      renderConn();
      if (err.status === 404) {
        var fb = S.defaultRun || (S.runs[0] && S.runs[0].run_id);
        if (fb && fb !== S.runId) { switchRun(fb); return; }
      }
    } finally {
      S.stateBusy = false;
    }
  }

  function onState(st) {
    renderTopbar(st);
    renderRunInfo(st);
    renderProgress(st);
    renderParams(st);
    renderLayerMeta(st);
    renderColorbar();
    renderServerMeta(st);
    renderMsg();

    if (!S.historyMode) {
      var fid = st.frame && st.frame.id !== undefined ? st.frame.id : null;
      var key = S.runId + ':' + fid;
      if (fid !== null && (S.lastFrameKey !== key || !S.frame)) {
        loadFrame('latest');
      }
    }
    renderTimeline();
    drawChart();
    renderFrameChip();
  }

  function renderRunInfo(st) {
    if (!els.runInfo) return;
    var run = (st && st.run) || {};
    var id = run.run_id || S.runId;
    if (!id) { els.runInfo.hidden = true; return; }
    els.runInfo.hidden = false;
    var dir = run.workdir || '';
    els.runIdText.textContent = id;
    els.runIdText.title = 'run_id: ' + id + (dir ? '\nworkdir: ' + dir : '');
    els.copyRun.setAttribute('data-copy', id + (dir ? '\n' + dir : ''));
  }

  function copyRunInfo(btn) {
    var text = btn.getAttribute('data-copy') || '';
    if (!text) return;
    var ok = function () {
      btn.classList.add('done');
      btn.textContent = '✓';
      setTimeout(function () { btn.classList.remove('done'); btn.textContent = '⧉'; }, 1200);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(ok, function () { fallback(); });
      } else { fallback(); }
    } catch (e) { fallback(); }
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); ok(); } catch (e2) { /* ignore */ }
      document.body.removeChild(ta);
    }
  }

  /* 后端 msg 是后端诊断文本（中文）：英文模式加 backend: 前缀，明确出处 */
  function backendMsg(msg) {
    if (!msg) return '';
    return lang() === 'zh' ? msg : t('msg.backendPrefix') + msg;
  }

  function renderTopbar(st) {
    var run = st.run || {};
    var status = run.status || 'waiting';
    els.badge.className = 'badge status-' + status;
    els.badge.textContent = statusName(status);
    els.badge.title = statusDetail(run);
    renderFreshness();
    if (run.msg) {
      els.msg.hidden = false;
      els.msg.className = 'msg-banner' + (status === 'error' ? ' error' : '');
      els.msg.textContent = (status === 'error' ? t('msg.error') : t('msg.notice')) + backendMsg(run.msg);
    }
  }

  /* 更新时间颜色：按快照周期判断，而不是固定 5/30 s。
   * 正常（蓝）：还在预期周期内；偏晚（黄）：超过预期较久；
   * 超时（红）：超过预期一个周期以上。T85 的 30–40 s 帧周期不会提前变红。 */
  function freshnessLevel(age, period) {
    if (!period || !isFinite(period) || period <= 0) return 'ok';
    var late = age - period;
    if (late > Math.max(20, period * 1.5)) return 'dead';
    if (late > Math.max(5, period * 0.5)) return 'stale';
    return 'ok';
  }

  function renderFreshness() {
    var st = S.st;
    if (!els.freshness) return;
    if (!st || !st.meta || !st.meta.updated) {
      els.freshness.textContent = '—';
      els.freshness.className = 'fresh';
      els.freshness.title = t('title.freshness');
      return;
    }
    var run = st.run || {};
    var period = run.frame_period_avg_s || run.frame_period_median_s || run.frame_period_est_s || null;
    var age = Date.now() / 1000 - st.meta.updated;
    var ago;
    if (age < 0) { ago = t('fresh.justNow'); age = 0; }
    else if (age < 10) ago = t('fresh.ago', { s: age.toFixed(1) });
    else ago = t('fresh.ago', { s: Math.round(age) });

    var expected = '';
    if (period && run.status !== 'finished') {
      if (age < period) expected = t('fresh.expectedIn', { s: fmtSecondsShort(period - age) });
      else expected = t('fresh.expectedPast', { s: fmtSecondsShort(age - period) });
    }
    els.freshness.textContent = expected
      ? t('fresh.updateExpected', { ago: ago, expected: expected })
      : t('fresh.update', { ago: ago });

    var level = run.status === 'finished' ? 'ok' : freshnessLevel(age, period);
    els.freshness.className = 'fresh' + (level === 'dead' ? ' dead' : level === 'stale' ? ' stale' : '');
    els.freshness.title = period
      ? t('fresh.titleExpected', {
        age: age.toFixed(1),
        period: fmtSecondsShort(period),
        source: t(run.frame_period_source === 'mtime' ? 'fresh.sourceMtime' : 'fresh.sourceEstimated')
      })
      : t('fresh.title', { s: age.toFixed(1) });
  }

  function renderConn() {
    if (!els.conn) return;
    els.conn.classList.toggle('bad', !S.conn.ok);
    els.connText.textContent = S.conn.ok ? t('conn.ok') : t('conn.bad');
  }

  /* CPU / poll 文本（顶栏与 kv 共用） */
  function serverMetaText(sv) {
    var cpu = sv && typeof sv.cpu_percent === 'number' ? sv.cpu_percent.toFixed(1) : '?';
    var poll = sv && typeof sv.poll_ms === 'number' ? sv.poll_ms.toFixed(1) : '?';
    return t('server.meta', { cpu: cpu, poll: poll });
  }

  function renderServerMeta(st) {
    if (!els.server) return;
    st = st || S.st;
    if (!st || !st.server) { els.server.textContent = t('server.na'); els.server.title = ''; return; }
    var sv = st.server;
    els.server.textContent = serverMetaText(sv);
    els.server.title = t('server.title', {
      cpu: sv.cpu_percent + '%', poll: sv.poll_ms, frames: sv.frames_parsed,
      bytes: sv.bytes_read, rss: sv.rss_kib, uptime: sv.uptime_s
    });
  }

  /* 日期由结构化字段重建（sim_day 优先，缺失时用 step / steps_per_year 折算；360 天 12×30 月） */
  function simDayOf(run, st) {
    if (run && typeof run.sim_day === 'number' && isFinite(run.sim_day)) return run.sim_day;
    var spy = (run && run.steps_per_year) || (st && st.params && st.params.steps_per_year) || null;
    if (spy && run && typeof run.step === 'number') return run.step / spy * 360;
    return null;
  }

  function fmtSimDate(yearNo, simDay) {
    if (simDay === null || simDay === undefined || !isFinite(simDay)) return '—';
    var doy = ((simDay % 360) + 360) % 360;
    var month = Math.floor(doy / 30) + 1;
    var day = Math.round(doy - (month - 1) * 30);
    if (day < 1) day = 1;
    if (day > 30) day = 30;
    if (month > 12) { month = 12; day = 30; }
    var y = (yearNo === null || yearNo === undefined || !isFinite(yearNo)) ? 0 : yearNo;
    return t('kv.simDateValue', { y: y, m: month, d: day });
  }

  function fmtSecondsShort(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    if (v >= 3600) return (v / 3600).toFixed(v >= 36000 ? 0 : 1) + ' h';
    if (v >= 60) return (v / 60).toFixed(v >= 600 ? 0 : 1) + ' min';
    return (Math.round(v * 10) / 10) + ' s';
  }

  function renderProgress(st) {
    var run = st.run || {};
    var pct = typeof run.progress === 'number' ? run.progress : null;
    els.progressPhase.textContent = statusDetail(run);
    els.progressPct.textContent = pct === null ? '—' : Math.round(pct * 100) + '%';
    els.progressBar.style.width = (pct === null ? 0 : clamp(pct * 100, 0, 100)) + '%';

    var stepsPerYear = run.steps_per_year || (st.params && st.params.steps_per_year) || null;
    var stepTxt = (run.step === undefined || run.step === null) ? '—'
      : (stepsPerYear ? run.step + ' / ' + Math.round(stepsPerYear) : String(run.step));
    var yearVal = '—';
    if (run.year_no !== undefined && run.year_no !== null) {
      yearVal = run.year_total ? t('kv.yearValue', { y: run.year_no, total: run.year_total })
        : t('kv.yearSolo', { y: run.year_no });
    }
    var periodTxt = '—';
    if ((run.frame_period_source === 'mtime' || run.frame_period_source === 'observed')
        && run.frame_period_median_s) {
      periodTxt = t('kv.framePeriodObserved', {
        last: fmtSecondsShort(run.frame_period_s),
        avg: fmtSecondsShort(run.frame_period_avg_s || run.frame_period_median_s)
      });
    } else if (run.frame_period_est_s) {
      periodTxt = t('kv.framePeriodEstimated', { est: fmtSecondsShort(run.frame_period_est_s) });
    }
    var pollTxt = run.poll_interval_s ? fmtSecondsShort(run.poll_interval_s) : '—';
    var rows = [
      [t('kv.simDate'), fmtSimDate(run.year_no, simDayOf(run, st))],
      [t('kv.year'), yearVal],
      [t('kv.step'), stepTxt],
      [t('kv.plannedMonths'), run.planned_months === null || run.planned_months === undefined
        ? t('common.unknown') : t('kv.months', { n: fmtNum(run.planned_months) })],
      [t('kv.doneMonths'), run.months_done === null || run.months_done === undefined
        ? '—' : t('kv.months', { n: fmtNum(run.months_done) })],
      [t('kv.elapsed'), fmtDur(run.wall_elapsed_s)],
      [t('kv.eta'), run.eta_s === null || run.eta_s === undefined ? t('common.unknown') : fmtDur(run.eta_s)],
      [t('kv.frames'), run.frames_total === undefined || run.frames_total === null ? '—' : String(run.frames_total)],
      [t('kv.framePeriod'), periodTxt],
      [t('kv.pollInterval'), pollTxt],
      [t('kv.overhead'), serverMetaText(st.server)]
    ];
    var html = '';
    for (var i = 0; i < rows.length; i++) {
      var wide = i === 0 || i === rows.length - 1;   // 首行日期 + 末行开销占整行，避免长文案被截断
      html += '<div class="kv' + (wide ? ' wide' : '') + '"><span class="k">' + escapeHtml(rows[i][0]) +
        '</span><span class="v" title="' + escapeAttr(rows[i][1]) + '">' + escapeHtml(rows[i][1]) + '</span></div>';
    }
    els.progressKv.innerHTML = html;
  }

  function renderLayerMeta(st) {
    var meta = st && st.layers && st.layers[S.layer];
    els.layerName.textContent = layerName(S.layer);
    var unit = layerUnit(S.layer);
    var parts = [];
    if (unit) parts.push(unit);
    if (meta && meta.min !== undefined && meta.max !== undefined && meta.min !== null) {
      parts.push(t('layer.measured', {
        min: fmtLayerNum(S.layer, meta.min),
        max: fmtLayerNum(S.layer, meta.max)
      }));
    }
    els.layerMeta.textContent = parts.join(' · ') || t('common.waitingData');
    if (st && st.grid) els.gridInfo.textContent = st.grid.nlat + '×' + st.grid.nlon;
  }

  function renderColorbar() {
    var meta = S.st && S.st.layers && S.st.layers[S.layer];
    var cm = LAYER_CM[S.layer] || 'thermal';
    els.cbarGrad.style.background = window.EXO_CM.gradientCss(cm, 18);
    var range = (meta && meta.range) || [0, 1];
    var lo = range[0], hi = range[1];
    els.cbarTicks.innerHTML = '<span>' + fmtRangeTick(S.layer, lo) + '</span><span>' +
      fmtRangeTick(S.layer, (lo + hi) / 2) + '</span><span>' + fmtRangeTick(S.layer, hi) + '</span>';
    var unit = layerUnit(S.layer);
    if (meta && meta.min !== null && meta.min !== undefined && meta.max !== null && meta.max !== undefined) {
      els.cbarNow.textContent = t('cbar.current', {
        min: fmtLayerNum(S.layer, meta.min),
        max: fmtLayerNum(S.layer, meta.max),
        lo: fmtRangeTick(S.layer, lo),
        hi: fmtRangeTick(S.layer, hi),
        unit: unit ? ' ' + unit : ''
      });
    } else {
      els.cbarNow.textContent = t('common.waitingData');
    }
  }

  /* ---------------- 参数面板 ---------------- */

  function renderParams(st) {
    var run = st.run || {};
    var params = st.params || {};
    var merged = {};
    var k;
    for (k in params) if (Object.prototype.hasOwnProperty.call(params, k)) merged[k] = params[k];
    merged.run_id = run.run_id || S.runId;
    merged.workdir = run.workdir || null;
    merged.outdir = run.outdir || null;
    var json = JSON.stringify(merged);
    if (json === S.lastParamsJson) return;
    S.lastParamsJson = json;

    var groups = {};
    for (var g = 0; g < GROUP_ORDER.length; g++) groups[GROUP_ORDER[g]] = [];
    for (k in merged) {
      if (!Object.prototype.hasOwnProperty.call(merged, k)) continue;
      var meta = PARAM_META[k] || { group: 'other' };
      var gk = groups[meta.group] ? meta.group : 'other';
      groups[gk].push({ key: k, meta: meta, val: merged[k] });
    }

    var html = '';
    for (var gi = 0; gi < GROUP_ORDER.length; gi++) {
      var gname = GROUP_ORDER[gi];
      var items = groups[gname];
      if (!items || !items.length) continue;
      html += '<div class="pgroup"><h4>' + escapeHtml(groupName(gname)) + '</h4>';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        var text = fmtVal(it.val, unitText(it.meta.unit));
        html += '<div class="prow"><span class="pk" title="' + escapeAttr(it.key) + '">' +
          escapeHtml(paramLabel(it.key)) + '</span><span class="pv' + (it.val === null || it.val === undefined ? ' null' : '') +
          '" title="' + escapeAttr(String(it.val === null || it.val === undefined ? '—' : it.val)) + '">' +
          escapeHtml(text) + '</span></div>';
      }
      html += '</div>';
    }
    els.paramsBody.innerHTML = html || '<div class="empty">' + escapeHtml(t('common.waitingParams')) + '</div>';
    els.paramsSource.textContent = (st.meta && st.meta.source) || '';
  }

  /* ---------------- 帧 ---------------- */

  /* 陆海掩膜（可选）：与帧同格式，order=["lsm"] */
  async function loadLand() {
    if (!S.runId) return;
    var run = S.runId;
    var tok = ++S.tokens.land;
    var url = '/api/land?run=' + enc(run);
    S.fetched.push(url);
    try {
      var res = await fetch(url, { cache: 'no-store' });
      if (!res.ok || !globe) return;
      var buf = await res.arrayBuffer();
      if (tok !== S.tokens.land || run !== S.runId) return;
      var fr = parseExof(buf);
      var lsm = fr.layers[fr.header.order[0]] || fr.layers.lsm;
      if (lsm) globe.setLand(lsm, fr.header.nlat, fr.header.nlon);
    } catch (err) { /* 陆地可选，失败静默 */ }
  }

  async function loadFrame(id) {
    if (!S.runId) return;
    var run = S.runId;
    var tok = ++S.tokens.frame;
    var url = '/api/frame?run=' + enc(run) + '&id=' + (id === undefined || id === null ? 'latest' : enc(id));
    S.fetched.push(url);
    try {
      var res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var buf = await res.arrayBuffer();
      if (tok !== S.tokens.frame || run !== S.runId) return;
      var fr = parseExof(buf);
      S.frame = fr;
      S.lastFrameKey = run + ':' + fr.header.id;
      applyFrame(fr, run);
      if (!S.showedFrame) {
        S.showedFrame = true;
        S.firstFrameMs = (window.performance && performance.now ? performance.now() : 0) - S.t0;
        hideLoading();
      }
      renderFrameChip();
      drawChart();
    } catch (err) {
      if (tok !== S.tokens.frame || run !== S.runId) return;
      setClientMsg(t('err.frame', { msg: err && err.message ? err.message : err }), true);
    }
  }

  function applyFrame(fr, run) {
    if (!globe) return;
    var L = fr.layers;
    var nlat = fr.header.nlat, nlon = fr.header.nlon;
    var layersMeta = (S.st && S.st.layers) || {};
    for (var i = 0; i < LAYER_ORDER.length; i++) {
      var key = LAYER_ORDER[i];
      if (!L[key]) continue;
      var meta = layersMeta[key] || {};
      var range = (meta && meta.range) || [0, 1];
      globe.setData(key, L[key], nlat, nlon, range[0], range[1]);
    }
    var spdRange = (layersMeta.spd && layersMeta.spd.range) || [0, 30];
    if (L.u && L.v) {
      var grid = (S.st && S.st.grid) || {};
      var lats = grid.lat && grid.lat.length === nlat ? grid.lat : synthLat(nlat);
      var lons = grid.lon && grid.lon.length === nlon ? grid.lon : synthLon(nlon);
      globe.setWind({
        u: L.u, v: L.v, spd: L.spd || null, nlat: nlat, nlon: nlon,
        lat: lats, lon: lons, lo: spdRange[0], hi: spdRange[1]
      });
    } else {
      globe.setWind(null);
    }
    globe.setLayer(S.layer);
    globe.setColormap(LAYER_CM[S.layer] || 'thermal');
    globe.setOptions({ arrows: S.opts.arrows && S.layer === 'spd' });
    // 新帧到达后刷新悬停读数（固定点也用同一套采样）
    refreshHover();
    void run;
  }

  function synthLat(nlat) {
    var a = new Float32Array(nlat);
    for (var j = 0; j < nlat; j++) a[j] = 90 - (j + 0.5) * 180 / nlat;
    return a;
  }
  function synthLon(nlon) {
    var a = new Float32Array(nlon);
    for (var i = 0; i < nlon; i++) a[i] = i * 360 / nlon;
    return a;
  }

  function renderFrameChip() {
    var fr = S.frame;
    if (!fr) { els.frameChip.textContent = '—'; els.frameChip.className = 'frame-chip'; return; }
    var h = fr.header;
    var bits = ['#' + h.id, 'step ' + h.step];
    var hist = (S.st && S.st.history) || [];
    var entry = null;
    for (var i = 0; i < hist.length; i++) {
      if (hist[i].id === h.id) { entry = hist[i]; break; }
    }
    /* 日期一律由结构化字段重建（后端 sim_date 是中文串，英文模式不能直接用） */
    var run = (S.st && S.st.run) || {};
    var spy = run.steps_per_year || (S.st && S.st.params && S.st.params.steps_per_year) || null;
    var yearNo = null;
    if (entry) {
      if (entry.year_no !== undefined && entry.year_no !== null) yearNo = entry.year_no;
      else if (typeof entry.year === 'number') yearNo = entry.year;
    }
    if (yearNo === null) {
      if (h.year_no !== undefined && h.year_no !== null) yearNo = h.year_no;
      else if (typeof h.year === 'number') yearNo = h.year;
      else if (run.year_no !== undefined && run.year_no !== null) yearNo = run.year_no;
    }
    var simDay = null;
    if (entry && typeof entry.sim_day === 'number') simDay = entry.sim_day;
    if (simDay === null && typeof h.sim_day === 'number') simDay = h.sim_day;
    if (simDay === null && entry && spy && typeof entry.step === 'number') simDay = entry.step / spy * 360;
    if (simDay === null && !entry) simDay = simDayOf(run, S.st);
    if (yearNo !== null && simDay !== null) bits.push(fmtSimDate(yearNo, simDay));
    else if (lang() === 'zh' && entry && entry.sim_date) bits.push(entry.sim_date);
    els.frameChip.textContent = (S.historyMode ? t('frame.historyPrefix') : '') + bits.join(' · ');
    els.frameChip.className = 'frame-chip' + (S.historyMode ? ' history' : '');
  }

  /* ---------------- 悬停读数（CONTRACT §4） ----------------
   * 屏幕点 → 反投影到球面（globe.pick）→ 模型系经纬度 → 最近格点 → 采样当前帧
   * 不产生任何额外请求，全部用 S.frame 里已有的数据。
   */

  /* 最近格点索引 + 图层采样值 */
  function sampleGrid(mlat, mlon) {
    var fr = S.frame;
    if (!fr || !fr.header) return null;
    var nlat = fr.header.nlat, nlon = fr.header.nlon;
    var grid = (S.st && S.st.grid) || {};
    var lat = (grid.lat && grid.lat.length === nlat) ? grid.lat : synthLat(nlat);
    var lon = (grid.lon && grid.lon.length === nlon) ? grid.lon : synthLon(nlon);
    var j = 0, best = 1e9;
    for (var k = 0; k < nlat; k++) {
      var d = Math.abs(lat[k] - mlat);
      if (d < best) { best = d; j = k; }
    }
    var i = 0; best = 1e9;
    for (var m = 0; m < nlon; m++) {
      var dl = Math.abs(((lon[m] - mlon + 540) % 360) - 180);
      if (dl < best) { best = dl; i = m; }
    }
    var vals = {};
    var base = j * nlon + i;
    for (var n = 0; n < LAYER_ORDER.length; n++) {
      var key = LAYER_ORDER[n];
      var arr = fr.layers[key];
      vals[key] = arr ? arr[base] : null;
    }
    return { j: j, i: i, lat: lat[j], lon: lon[i], vals: vals };
  }

  function hoverAtLatLon(lat, lon, screen) {
    if (!globe) return null;
    var s = sampleGrid(lat, lon);
    if (!s) return null;
    S.hover = { lat: lat, lon: lon, grid: s };
    S.hoverScreen = screen || null;
    renderHover();
    return S.hover;
  }

  function hideHover(clear) {
    S.hoverScreen = null;
    if (clear) S.hover = null;
    if (els.hoverCard) els.hoverCard.hidden = true;
  }

  /* 指针移动：命中球面则显示，否则隐藏 */
  function onGlobePointerMove(e) {
    if (!globe || !S.frame) return;
    var rect = els.globe.getBoundingClientRect();
    var x = e.clientX - rect.left, y = e.clientY - rect.top;
    var hit = globe.pick(x, y);
    if (!hit) { hideHover(false); return; }
    var ll = globe.latLonOf(hit.world);
    hoverAtLatLon(ll.lat, ll.lon, { x: x, y: y });
  }

  /* 帧/图层/单位/坐标系变化后重新算一次（固定点靠这里） */
  function refreshHover() {
    if (S.pinned) { hoverAtLatLon(S.pinned.lat, S.pinned.lon, null); return; }
    if (S.hover && S.hoverScreen && globe && S.frame) {
      var hit = globe.pick(S.hoverScreen.x, S.hoverScreen.y);
      if (!hit) { hideHover(true); return; }
      var ll = globe.latLonOf(hit.world);
      hoverAtLatLon(ll.lat, ll.lon, S.hoverScreen);
      return;
    }
    if (S.hover && S.hoverScreen === null && globe && S.frame) {
      // 固定点重算
      hoverAtLatLon(S.hover.lat, S.hover.lon, null);
    }
  }

  function renderHover() {
    var card = els.hoverCard;
    if (!card) return;
    var hv = S.hover;
    if (!hv || !hv.grid) { card.hidden = true; return; }
    var ll = fmtLatLon(hv.lat, hv.lon);
    els.hcLat.textContent = ll.lat;
    els.hcLon.textContent = ll.lon;

    var cur = S.layer;
    els.hcLayerName.textContent = layerName(cur);
    els.hcLayerVal.textContent = fmtLayerVal(cur, hv.grid.vals[cur]);

    var rows = '';
    for (var i = 0; i < HOVER_KEYS.length; i++) {
      var k = HOVER_KEYS[i];
      var nm = layerName(k);
      rows += '<div class="hg-row' + (k === cur ? ' cur' : '') + '">' +
        '<span class="hg-k">' + escapeHtml(nm) + '</span>' +
        '<span class="hg-v">' + escapeHtml(fmtLayerVal(k, hv.grid.vals[k])) + '</span></div>';
    }
    els.hcGrid.innerHTML = rows;

    // 定位：光标右下 14px，越界则翻转到左上；固定点用投影位置
    var wrap = els.globe.parentNode;
    var W = wrap.clientWidth, H = wrap.clientHeight;
    var anchor = null;
    if (hv_hasScreen()) {
      anchor = S.hoverScreen;
    } else {
      var pw = globe.worldOf(hv.lat, hv.lon);
      var sp = globe.project(pw);
      if (!sp) { card.hidden = true; return; }
      anchor = { x: sp.x, y: sp.y };
    }
    card.hidden = false;
    var cw = card.offsetWidth, ch = card.offsetHeight;
    var off = 16;
    var x = anchor.x + off, y = anchor.y + off;
    if (x + cw > W - 4) x = anchor.x - cw - off;
    if (y + ch > H - 4) y = anchor.y - ch - off;
    x = clamp(x, 4, Math.max(4, W - cw - 4));
    y = clamp(y, 4, Math.max(4, H - ch - 4));
    card.style.transform = 'translate(' + Math.round(x) + 'px,' + Math.round(y) + 'px)';
    card.classList.toggle('pinned', !!S.pinned);
  }

  function hv_hasScreen() {
    return !!(S.hoverScreen && !S.pinned);
  }

  /* ---------------- 历史帧 / 播放 ---------------- */

  function historyArr() { return (S.st && S.st.history) || []; }

  function renderTimeline() {
    var hist = historyArr();
    var n = hist.length;
    var idx;
    if (S.historyMode && S.cursor !== null) idx = S.cursor;
    else idx = n - 1;
    els.slider.max = String(Math.max(0, n - 1));
    if (!S.historyMode) els.slider.value = String(Math.max(0, n - 1));
    else els.slider.value = String(idx);
    els.slider.disabled = n < 2;
    if (n === 0) els.tlLabel.textContent = t('tl.waiting');
    else if (S.historyMode) els.tlLabel.textContent = t('tl.frame', { i: idx + 1, n: n });
    else els.tlLabel.textContent = t('tl.live', { n: n });
    els.liveBtn.classList.toggle('hot', S.historyMode);
    els.playBtn.textContent = S.playing ? '❚❚' : '▶';
    els.playBtn.setAttribute('aria-pressed', S.playing ? 'true' : 'false');
  }

  function setHistoryCursor(idx) {
    var hist = historyArr();
    if (!hist.length) return;
    idx = clamp(idx, 0, hist.length - 1);
    S.historyMode = true;
    S.cursor = idx;
    var entry = hist[idx];
    if (entry && entry.id !== undefined) {
      if (S.frame && S.frame.header.id === entry.id) {
        renderTimeline(); renderFrameChip(); drawChart();
      } else {
        loadFrame(entry.id);
      }
    }
    renderTimeline();
    renderFrameChip();
    drawChart();
  }

  function goLive() {
    S.historyMode = false;
    S.cursor = null;
    stopPlay();
    loadFrame('latest');
    renderTimeline();
    renderFrameChip();
    drawChart();
  }

  function togglePlay() {
    if (S.playing) { stopPlay(); return; }
    var hist = historyArr();
    if (hist.length < 2) return;
    S.playing = true;
    if (!S.historyMode || S.cursor === null) S.cursor = 0;
    setHistoryCursor(S.cursor);
    S.playTimer = setInterval(function () {
      var h = historyArr();
      if (!h.length) return;
      var next = (S.cursor === null ? 0 : S.cursor + 1);
      if (next >= h.length) next = 0;
      setHistoryCursor(next);
    }, 900);
    renderTimeline();
  }

  function stopPlay() {
    S.playing = false;
    if (S.playTimer) { clearInterval(S.playTimer); S.playTimer = null; }
    if (els.playBtn) els.playBtn.textContent = '▶';
  }

  /* ---------------- 图层 / 视角 ---------------- */

  function buildLayerButtons() {
    var html = '';
    for (var i = 0; i < LAYER_ORDER.length; i++) {
      var k = LAYER_ORDER[i];
      html += '<button type="button" class="layer-btn" data-layer="' + k + '" aria-pressed="false">' +
        '<span class="ln">' + escapeHtml(layerName(k)) + '</span>' +
        '<span class="lu">' + (layerUnit(k) || '—') + '</span></button>';
    }
    els.layerButtons.innerHTML = html;
    updateLayerButtonState();
  }

  function updateLayerButtonState() {
    var btns = els.layerButtons.querySelectorAll('.layer-btn');
    for (var i = 0; i < btns.length; i++) {
      var k = btns[i].getAttribute('data-layer');
      btns[i].classList.toggle('on', k === S.layer);
      btns[i].setAttribute('aria-pressed', k === S.layer ? 'true' : 'false');
    }
  }

  function setLayer(key) {
    if (LAYER_ORDER.indexOf(key) < 0) return;
    S.layer = key;
    try { localStorage.setItem(LS_LAYER, key); } catch (e) { /* ignore */ }
    updateLayerButtonState();
    if (globe) {
      globe.setLayer(key);
      globe.setColormap(LAYER_CM[key] || 'thermal');
      globe.setOptions({ arrows: S.opts.arrows && key === 'spd' });
    }
    renderLayerMeta(S.st || {});
    renderColorbar();
    drawChart();
  }

  /* 温度单位切换：K ↔ °C（影响色标刻度、图层标题、悬停读数；本地记忆） */
  function setUnit(u) {
    var next = (u === 'C') ? 'C' : 'K';
    if (S.unit !== next) {
      S.unit = next;
      try { localStorage.setItem(LS_UNIT, next); } catch (e) { /* ignore */ }
    }
    updateUnitUI();
    refreshHover();
  }

  function updateUnitUI() {
    if (els.unitSeg) {
      var btns = els.unitSeg.querySelectorAll('[data-unit]');
      for (var i = 0; i < btns.length; i++) {
        var on = btns[i].getAttribute('data-unit') === S.unit;
        btns[i].classList.toggle('on', on);
        btns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
      }
    }
    if (els.layerButtons) {
      var lbs = els.layerButtons.querySelectorAll('.layer-btn');
      for (var j = 0; j < lbs.length; j++) {
        var lu = lbs[j].querySelector('.lu');
        if (lu) lu.textContent = layerUnit(lbs[j].getAttribute('data-layer')) || '—';
      }
    }
    if (S.st) {
      renderLayerMeta(S.st);
      renderColorbar();
    }
  }

  /* ---------------- 语言切换 ---------------- */

  function updateLangUI() {
    if (!els.langSeg) return;
    var btns = els.langSeg.querySelectorAll('[data-lang]');
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute('data-lang') === lang();
      btns[i].classList.toggle('on', on);
      btns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    els.langSeg.setAttribute('data-lang', lang());
  }

  /* 用户点击/深链切换：i18n 负责 <html lang>、localStorage、静态区；这里重渲染动态区 */
  function setLang(next) {
    if (!next) return;
    I18N.setLang(next);
    updateLangUI();
  }

  /* 语言变化后整体重渲染（无需刷新页面） */
  function onLangChange() {
    updateLangUI();
    buildLayerButtons();
    buildStaticChips();
    updateUnitUI();
    S.lastParamsJson = '__lang__';   // 强制参数面板重建
    var wasOpen = S.pickerOpen;      // 下拉打开时 renderRunPicker 会跳过重建，这里补一次
    if (wasOpen) S.pickerOpen = false;
    renderAll();
    if (wasOpen) togglePicker(true);
    renderServerMeta();
    renderFreshness();
    if (S.st) {
      renderTopbar(S.st);
      renderProgress(S.st);
      renderParams(S.st);
    }
    renderMsg();
    refreshHover();
    drawChart();
    if (globe && globe.refreshMarkerLabels) globe.refreshMarkerLabels();
    if (window.__SELFTEST__) renderSelfTest(window.__SELFTEST__);
    if (globe) globe.requestRender();
  }

  function buildStaticChips() { updateChips(); }

  function updateChips() {
    var optBtns = document.querySelectorAll('[data-opt]');
    for (var i = 0; i < optBtns.length; i++) {
      var k = optBtns[i].getAttribute('data-opt');
      var on = !!S.opts[k];
      optBtns[i].setAttribute('aria-pressed', on ? 'true' : 'false');
      optBtns[i].classList.toggle('on', on);
    }
  }

  function setOpt(key, val) {
    S.opts[key] = val;
    if (globe) {
      var o = {};
      o[key] = key === 'arrows' ? (val && S.layer === 'spd') : val;
      globe.setOptions(o);
    }
    updateChips();
  }

  /* ---------------- 事件 ---------------- */

  function bindUI() {
    els.pickerBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      togglePicker();
    });
    els.pickerList.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.picker-item') : null;
      if (!btn) return;
      var id = btn.getAttribute('data-run');
      togglePicker(false);
      if (id && id !== S.runId) switchRun(id);
    });
    document.addEventListener('click', function (e) {
      if (!S.pickerOpen) return;
      if (els.picker.contains(e.target)) return;
      togglePicker(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && S.pickerOpen) togglePicker(false);
    });

    els.layerButtons.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.layer-btn') : null;
      if (!btn) return;
      setLayer(btn.getAttribute('data-layer'));
    });

    if (els.unitSeg) {
      els.unitSeg.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-unit]') : null;
        if (!btn) return;
        e.stopPropagation();
        setUnit(btn.getAttribute('data-unit'));
      });
    }

    if (els.langSeg) {
      els.langSeg.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-lang]') : null;
        if (!btn) return;
        e.stopPropagation();
        setLang(btn.getAttribute('data-lang'));
      });
    }

    document.addEventListener('click', function (e) {
      var opt = e.target.closest ? e.target.closest('[data-opt]') : null;
      if (opt) {
        var k = opt.getAttribute('data-opt');
        setOpt(k, !S.opts[k]);
      }
    });

    if (els.globe) {
      var tapStart = null;
      els.globe.addEventListener('pointerdown', function (e) {
        S.lastPointerType = e.pointerType || 'mouse';
        if (e.pointerType === 'touch') tapStart = { x: e.clientX, y: e.clientY, t: Date.now() };
      });
      els.globe.addEventListener('pointermove', function (e) {
        S.lastPointerType = e.pointerType || 'mouse';
        onGlobePointerMove(e);
      });
      els.globe.addEventListener('pointerup', function (e) {
        if (e.pointerType !== 'touch' || !tapStart) return;
        var moved = Math.hypot(e.clientX - tapStart.x, e.clientY - tapStart.y);
        var isTap = moved < 12 && (Date.now() - tapStart.t) < 600;
        tapStart = null;
        if (!isTap || !globe) return;
        var rect = els.globe.getBoundingClientRect();
        var x = e.clientX - rect.left, y = e.clientY - rect.top;
        var hit = globe.pick(x, y);
        if (!hit) { hideHover(true); return; }
        var ll = globe.latLonOf(hit.world);
        hoverAtLatLon(ll.lat, ll.lon, { x: x, y: y });
      });
      els.globe.addEventListener('pointerleave', function (e) {
        S.lastPointerType = (e && e.pointerType) || S.lastPointerType;
        if (S.lastPointerType !== 'touch') hideHover(true);
      });
      document.addEventListener('pointerdown', function (e) {
        if (e.pointerType !== 'touch') return;
        if (e.target === els.globe) return;
        if (els.hoverCard && els.hoverCard.contains(e.target)) return;
        hideHover(true);
      });
    }
    if (els.zoomIn) {
      els.zoomIn.addEventListener('click', function () { if (globe) globe.zoomBy(1.25); });
    }
    if (els.zoomOut) {
      els.zoomOut.addEventListener('click', function () { if (globe) globe.zoomBy(0.8); });
    }
    if (els.resetView) {
      els.resetView.addEventListener('click', function () { if (globe) globe.resetView(); });
    }
    if (els.copyRun) {
      els.copyRun.addEventListener('click', function () { copyRunInfo(els.copyRun); });
    }

    els.slider.addEventListener('input', function () {
      stopPlay();
      setHistoryCursor(parseInt(els.slider.value, 10) || 0);
    });
    els.playBtn.addEventListener('click', togglePlay);
    els.liveBtn.addEventListener('click', goLive);

    els.legendTs.addEventListener('click', function () {
      S.showTs = !S.showTs;
      els.legendTs.classList.toggle('is-on', S.showTs);
      els.legendTs.setAttribute('aria-pressed', S.showTs ? 'true' : 'false');
      drawChart();
    });
    els.legendNtr.addEventListener('click', function () {
      S.showNtr = !S.showNtr;
      els.legendNtr.classList.toggle('is-on', S.showNtr);
      els.legendNtr.setAttribute('aria-pressed', S.showNtr ? 'true' : 'false');
      drawChart();
    });

    els.chart.addEventListener('click', function (e) {
      var hist = historyArr();
      if (hist.length < 2) return;
      var rect = els.chart.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var padL = 34, padR = 30;
      var plotW = Math.max(1, rect.width - padL - padR);
      var idx = Math.round(((x - padL) / plotW) * (hist.length - 1));
      stopPlay();
      setHistoryCursor(idx);
    });

    window.addEventListener('resize', function () {
      if (globe) globe.requestRender();
      drawChart();
    });
    if (window.ResizeObserver) {
      try {
        new ResizeObserver(function () { drawChart(); }).observe(els.chart.parentNode);
        // 视口/布局变化后必须重画球体（rAF 是按需渲染的）
        new ResizeObserver(function () { if (globe) globe.requestRender(); }).observe(els.globe.parentNode);
      } catch (e) { /* ignore */ }
    }
  }

  S.showTs = true;
  S.showNtr = true;

  function renderAll() {
    renderRunPicker();
    renderConn();
    renderRunInfo(S.st);
    renderLayerMeta(S.st || {});
    renderColorbar();
    renderTimeline();
    renderFrameChip();
    if (!S.st) {
      els.progressPhase.textContent = t('progress.waiting');
      els.progressPct.textContent = '—';
      els.progressBar.style.width = '0%';
      els.progressKv.innerHTML = '';
      els.badge.className = 'badge status-waiting';
      els.badge.textContent = statusName('waiting');
      els.freshness.textContent = '—';
      els.freshness.className = 'fresh';
      els.paramsBody.innerHTML = '<div class="empty">' + escapeHtml(t('common.waitingParams')) + '</div>';
      S.lastParamsJson = '__reset__';
      renderMsg();
    }
    drawChart();
  }

  function renderMsg() {
    if (!els.msg) return;
    var text = null, isErr = false, fromClient = false;
    if (S.st && S.st.run && S.st.run.msg) { text = S.st.run.msg; isErr = S.st.run.status === 'error'; }
    if (S.clientMsg && Date.now() - S.clientMsgAt < 8000) {
      if (!text) { text = S.clientMsg; isErr = !!S.clientMsgErr; fromClient = true; }
    }
    if (!text) { els.msg.hidden = true; return; }
    els.msg.hidden = false;
    els.msg.className = 'msg-banner' + (isErr ? ' error' : '');
    els.msg.textContent = (isErr ? t('msg.error') : t('msg.notice')) + (fromClient ? text : backendMsg(text));
  }

  /* ---------------- 序列图 ---------------- */

  function drawChart() {
    var cv = els.chart;
    if (!cv) return;
    var cw = cv.clientWidth, chh = cv.clientHeight;
    if (!cw || !chh) return;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    if (cv.width !== Math.round(cw * dpr) || cv.height !== Math.round(chh * dpr)) {
      cv.width = Math.round(cw * dpr);
      cv.height = Math.round(chh * dpr);
    }
    var ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, chh);

    var hist = historyArr();
    var padL = 34, padR = 30, padT = 8, padB = 15;
    var plotW = Math.max(1, cw - padL - padR);
    var plotH = Math.max(1, chh - padT - padB);

    ctx.font = '10px ui-monospace, Menlo, Consolas, monospace';
    ctx.textBaseline = 'middle';

    if (!hist.length) {
      ctx.fillStyle = '#5c6c85';
      ctx.textAlign = 'center';
      ctx.fillText(t('chart.waiting'), cw / 2, chh / 2);
      return;
    }

    var n = hist.length;
    var tsVals = [], ntrVals = [];
    var i;
    for (i = 0; i < n; i++) {
      var h = hist[i];
      if (typeof h.ts_mean === 'number' && isFinite(h.ts_mean)) tsVals.push(h.ts_mean);
      if (typeof h.ntr_mean === 'number' && isFinite(h.ntr_mean)) ntrVals.push(h.ntr_mean);
    }
    var yTs = extent(tsVals, 2.5);
    var yNtr = extent(ntrVals, 0.5);

    function px(i2) { return padL + (n <= 1 ? 0.5 : i2 / (n - 1)) * plotW; }
    function pyTs(v) { return padT + (1 - (v - yTs[0]) / (yTs[1] - yTs[0] || 1)) * plotH; }
    function pyNtr(v) { return padT + (1 - (v - yNtr[0]) / (yNtr[1] - yNtr[0] || 1)) * plotH; }

    // 网格 + 左侧刻度
    ctx.strokeStyle = 'rgba(96,128,180,0.16)';
    ctx.lineWidth = 1;
    ctx.textAlign = 'right';
    for (var ti = 0; ti <= 2; ti++) {
      var y = padT + (ti / 2) * plotH;
      ctx.beginPath();
      ctx.moveTo(padL, y + 0.5);
      ctx.lineTo(padL + plotW, y + 0.5);
      ctx.stroke();
      var val = yTs[1] - (ti / 2) * (yTs[1] - yTs[0]);
      ctx.fillStyle = '#5c6c85';
      ctx.fillText(val.toFixed(1), padL - 5, y);
    }

    // ts 折线 + 面积
    if (S.showTs && tsVals.length) {
      var grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
      grad.addColorStop(0, 'rgba(255,159,90,0.32)');
      grad.addColorStop(1, 'rgba(255,159,90,0.02)');
      ctx.beginPath();
      var started = false;
      for (i = 0; i < n; i++) {
        var hv = hist[i].ts_mean;
        if (typeof hv !== 'number' || !isFinite(hv)) continue;
        if (!started) { ctx.moveTo(px(i), pyTs(hv)); started = true; }
        else ctx.lineTo(px(i), pyTs(hv));
      }
      if (started) {
        var lastI = -1, firstI = -1;
        for (i = 0; i < n; i++) {
          if (typeof hist[i].ts_mean === 'number' && isFinite(hist[i].ts_mean)) {
            if (firstI < 0) firstI = i;
            lastI = i;
          }
        }
        ctx.lineTo(px(lastI), padT + plotH);
        ctx.lineTo(px(firstI), padT + plotH);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        ctx.beginPath();
        started = false;
        for (i = 0; i < n; i++) {
          var v2 = hist[i].ts_mean;
          if (typeof v2 !== 'number' || !isFinite(v2)) continue;
          if (!started) { ctx.moveTo(px(i), pyTs(v2)); started = true; }
          else ctx.lineTo(px(i), pyTs(v2));
        }
        ctx.strokeStyle = '#ff9f5a';
        ctx.lineWidth = 1.6;
        ctx.lineJoin = 'round';
        ctx.stroke();
      }
    }

    // ntr 折线（虚线，右侧刻度）
    if (S.showNtr && ntrVals.length) {
      ctx.beginPath();
      var st2 = false;
      for (i = 0; i < n; i++) {
        var nv = hist[i].ntr_mean;
        if (typeof nv !== 'number' || !isFinite(nv)) continue;
        if (!st2) { ctx.moveTo(px(i), pyNtr(nv)); st2 = true; }
        else ctx.lineTo(px(i), pyNtr(nv));
      }
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = 'rgba(111,183,255,0.9)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.restore();
    }

    // 数据点
    if (S.showTs) {
      for (i = 0; i < n; i++) {
        var pv = hist[i].ts_mean;
        if (typeof pv !== 'number' || !isFinite(pv)) continue;
        ctx.beginPath();
        ctx.arc(px(i), pyTs(pv), n > 40 ? 1.1 : 1.7, 0, Math.PI * 2);
        ctx.fillStyle = '#ffb374';
        ctx.fill();
      }
    }

    // 年度点（year_series）
    var ys = (S.st && S.st.year_series) || [];
    if (ys.length) {
      for (var yi = 0; yi < ys.length; yi++) {
        var yr = ys[yi];
        if (typeof yr.ts_area !== 'number' || !isFinite(yr.ts_area)) continue;
        var lastIdx = n - 1;
        for (i = n - 1; i >= 0; i--) {
          if (hist[i].year === yr.year) { lastIdx = i; break; }
        }
        var xx = px(lastIdx), yy = pyTs(yr.ts_area);
        ctx.beginPath();
        ctx.moveTo(xx, yy - 3.6);
        ctx.lineTo(xx + 3.6, yy);
        ctx.lineTo(xx, yy + 3.6);
        ctx.lineTo(xx - 3.6, yy);
        ctx.closePath();
        ctx.fillStyle = 'rgba(150,220,255,0.22)';
        ctx.fill();
        ctx.strokeStyle = '#9fd8ff';
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    }

    // 当前帧竖线
    var curIdx = (S.historyMode && S.cursor !== null) ? S.cursor : n - 1;
    var cx = px(clamp(curIdx, 0, n - 1));
    ctx.beginPath();
    ctx.moveTo(cx, padT);
    ctx.lineTo(cx, padT + plotH);
    ctx.strokeStyle = S.historyMode ? 'rgba(255,196,90,0.75)' : 'rgba(120,200,255,0.55)';
    ctx.lineWidth = 1;
    ctx.stroke();
    var curV = hist[clamp(curIdx, 0, n - 1)].ts_mean;
    if (typeof curV === 'number' && isFinite(curV)) {
      ctx.beginPath();
      ctx.arc(cx, pyTs(curV), 3, 0, Math.PI * 2);
      ctx.fillStyle = S.historyMode ? '#ffc45a' : '#7fc8ff';
      ctx.fill();
    }

    // 右侧 ntr 刻度（右对齐，避免裁切）
    if (S.showNtr && ntrVals.length) {
      ctx.textAlign = 'right';
      ctx.fillStyle = 'rgba(111,183,255,0.75)';
      ctx.fillText(yNtr[0].toFixed(1), cw - 2, padT + plotH);
      ctx.fillText(yNtr[1].toFixed(1), cw - 2, padT);
    }

    // x 轴：首末 step
    ctx.fillStyle = '#5c6c85';
    ctx.textAlign = 'left';
    ctx.fillText('#' + (hist[0].id !== undefined ? hist[0].id : 0), padL, chh - 5);
    ctx.textAlign = 'right';
    ctx.fillText('#' + (hist[n - 1].id !== undefined ? hist[n - 1].id : n - 1), padL + plotW, chh - 5);
  }

  function extent(vals, padFrac) {
    if (!vals.length) return [0, 1];
    var lo = vals[0], hi = vals[0];
    for (var i = 1; i < vals.length; i++) {
      if (vals[i] < lo) lo = vals[i];
      if (vals[i] > hi) hi = vals[i];
    }
    if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
    var pad = (hi - lo) * (padFrac === undefined ? 0.12 : 0.12);
    return [lo - pad, hi + pad];
  }

  /* ---------------- 自检 ---------------- */

  async function runSelfTest(show) {
    var items = [];
    function add(ok, name, detail) { items.push({ ok: !!ok, name: name, detail: detail === undefined ? '' : String(detail) }); }

    add(!!globe, t('st.webgl'), globe ? t('st.created') : t('st.unavailable'));
    add(document.querySelectorAll('.layer-btn').length === LAYER_ORDER.length,
      t('st.layerButtons', { n: LAYER_ORDER.length }),
      document.querySelectorAll('.layer-btn').length + '');
    add(document.querySelectorAll('.picker-item').length === S.runs.length, t('st.pickerMatch'),
      document.querySelectorAll('.picker-item').length + '/' + S.runs.length);

    var runsDoc = null;
    try { runsDoc = await getJSON('/api/runs'); } catch (e) { runsDoc = null; }
    add(runsDoc && runsDoc.ok && runsDoc.runs && runsDoc.runs.length > 0, t('st.runsApi'),
      runsDoc && runsDoc.runs ? t('st.runsCount', { n: runsDoc.runs.length }) : t('st.failed'));

    var st = null;
    try { st = await getJSON('/api/state?run=' + enc(S.runId || '')); } catch (e) { st = null; }
    var gridOk = st && st.grid && st.grid.nlat > 0 && st.grid.nlon > 0;
    add(gridOk, t('st.stateGrid'), st && st.grid ? st.grid.nlat + '×' + st.grid.nlon : t('st.failed'));

    var layersOk = st && st.layers && LAYER_ORDER.every(function (k) { return !!st.layers[k]; });
    add(layersOk, t('st.layersMeta'), layersOk ? t('st.complete') : t('st.missing'));

    // 用户反馈回归：sit 必须在帧里；年份从 0 开始且日期与年份一致；K→°C 换算正确
    if (st && st.run) {
      var yn = st.run.year_no;
      add(yn === undefined || yn === null || (typeof yn === 'number' && yn >= 0),
        t('st.yearZeroBased'), String(yn));
      var dateText = lang() === 'zh' ? String(st.run.sim_date || '')
        : fmtSimDate(yn, simDayOf(st.run, st));
      var yearToken = lang() === 'zh' ? ('第 ' + yn + ' 年') : ('Year ' + yn);
      add(!!dateText && (yn === undefined || yn === null || dateText.indexOf(yearToken) >= 0),
        t('st.simDateMatchesYear'), dateText || '—');
    }
    (function () {
      var before = S.unit;
      S.unit = 'C';
      var c = convTemp(273.15);
      S.unit = before;
      add(Math.abs(c - 0) < 1e-6, t('st.tempConv'), '273.15K → ' + c.toFixed(2) + '°C');
    })();
    (function () {
      var hv = (typeof hoverAtLatLon === 'function') ? hoverAtLatLon(0, 0, null) : null;
      var v = hv && hv.grid && hv.grid.vals ? hv.grid.vals.ts : null;
      add(v !== null && v !== undefined && isFinite(v), t('st.hover'),
        v === null || v === undefined ? t('st.noData') : 'ts=' + Number(v).toFixed(2) + ' K');
      hideHover(true);
    })();

    var fr = null;
    try {
      var res = await fetch('/api/frame?run=' + enc(S.runId || '') + '&id=latest', { cache: 'no-store' });
      S.fetched.push('/api/frame?run=' + S.runId + '&id=latest');
      if (res.ok) fr = parseExof(await res.arrayBuffer());
    } catch (e) { fr = null; }
    add(fr && fr.header.nlat > 0, t('st.exof'),
      fr ? (fr.header.nlat + '×' + fr.header.nlon + ' · ' + (fr.header.order || []).join(',')) : t('st.failed'));
    add(fr && fr.header.order && fr.header.order.indexOf('sit') >= 0,
      t('st.sitInFrame'),
      fr && fr.header.order ? fr.header.order.join(',') : '—');
    add(fr && st && fr.header.nlat === st.grid.nlat && fr.header.nlon === st.grid.nlon,
      t('st.gridMatch'), fr && st ? fr.header.nlat + '==' + st.grid.nlat : '—');

    var landOk = false, landDetail = t('st.failed');
    try {
      var lres = await fetch('/api/land?run=' + enc(S.runId || ''), { cache: 'no-store' });
      S.fetched.push('/api/land?run=' + (S.runId || ''));
      if (lres.ok) {
        var lfr = parseExof(await lres.arrayBuffer());
        var lsm = lfr.layers[lfr.header.order[0]];
        if (lsm) {
          var ones = 0;
          for (var li = 0; li < lsm.length; li++) if (lsm[li] > 0.5) ones++;
          landOk = ones > 0 && ones < lsm.length;
          landDetail = lfr.header.nlat + '×' + lfr.header.nlon + ' 陆地格点 ' +
            (100 * ones / lsm.length).toFixed(1) + '%';
        }
      }
    } catch (e) { landOk = false; }
    add(landOk, t('st.land'), landDetail);

    // 指针悬停：模拟一次 pointermove，确认浮层真的出现（用户反馈回归）
    (function () {
      if (!globe || !S.frame || !els.globe) {
        add(false, t('st.hoverPointer'), t('st.noData'));
        return;
      }
      var ok = false;
      var detail = t('st.noData');
      try {
        if (globe && globe.render) globe.render();
        window.scrollTo(0, 0);
        var r = els.globe.getBoundingClientRect();
        var ev = new PointerEvent('pointermove', {
          clientX: r.left + r.width * 0.5,
          clientY: r.top + r.height * 0.5,
          bubbles: true
        });
        els.globe.dispatchEvent(ev);
        ok = !!(els.hoverCard && !els.hoverCard.hidden && els.hcLat &&
                els.hcLat.textContent && els.hcLat.textContent !== '—');
        detail = ok ? (els.hcLat.textContent + ' ' + els.hcLon.textContent) : t('st.noData');
        if (!ok) {
          var hp = globe.pick(r.width / 2, r.height / 2);
          detail = 'rect ' + Math.round(r.width) + 'x' + Math.round(r.height) +
            ' pick=' + (hp ? 'hit' : 'miss') + ' basis=' + (globe.basis ? 'y' : 'n');
        }
      } catch (e) {
        detail = String(e && e.message ? e.message : e);
      }
      add(ok, t('st.hoverPointer'), detail);
      hideHover(true);
    })();

    // 触摸点按：模拟 pointerdown/up（pointerType=touch），确认浮层出现（手机端）
    (function () {
      if (!globe || !S.frame || !els.globe) {
        add(false, t('st.touchReadout'), t('st.noData'));
        return;
      }
      var ok = false;
      var detail = t('st.noData');
      try {
        if (globe && globe.render) globe.render();
        window.scrollTo(0, 0);
        var r = els.globe.getBoundingClientRect();
        var base = {
          bubbles: true, pointerId: 777, pointerType: 'touch', isPrimary: true,
          clientX: r.left + r.width * 0.5, clientY: r.top + r.height * 0.5,
          button: 0
        };
        els.globe.dispatchEvent(new PointerEvent('pointerdown', Object.assign({ buttons: 1 }, base)));
        els.globe.dispatchEvent(new PointerEvent('pointerup', Object.assign({ buttons: 0 }, base)));
        ok = !!(els.hoverCard && !els.hoverCard.hidden && els.hcLat &&
                els.hcLat.textContent && els.hcLat.textContent !== '—');
        detail = ok ? (els.hcLat.textContent + ' ' + els.hcLon.textContent) : t('st.noData');
        if (!ok) {
          var tp = globe.pick(r.width / 2, r.height / 2);
          detail = 'rect ' + Math.round(r.width) + 'x' + Math.round(r.height) +
            ' pick=' + (tp ? 'hit' : 'miss') + ' basis=' + (globe.basis ? 'y' : 'n');
        }
      } catch (e) {
        detail = String(e && e.message ? e.message : e);
      }
      add(ok, t('st.touchReadout'), detail);
      hideHover(true);
    })();

    // 手机/窄屏不能出现横向滚动（375/390 宽时最容易暴露）
    add(document.documentElement.scrollWidth <= window.innerWidth + 1, t('st.noOverflow'),
      document.documentElement.scrollWidth + ' / ' + window.innerWidth);

    if (globe) {
      var px = globe.probeCenter();
      var nonBlank = px && (px[0] + px[1] + px[2] > 30);
      add(nonBlank, t('st.globeRendered'), px ? 'rgb(' + px.slice(0, 3).join(',') + ')' : t('st.readFailed'));
    } else {
      add(false, t('st.globeRendered'), t('st.webglUnavailable'));
    }

    add(S.firstFrameMs !== null && S.firstFrameMs < 1000, t('st.firstFrame'),
      S.firstFrameMs === null ? t('st.noFrame') : Math.round(S.firstFrameMs) + ' ms');

    // 无第三方请求
    var entries = (window.performance && performance.getEntriesByType) ? performance.getEntriesByType('resource') : [];
    var ext = [];
    for (var i = 0; i < entries.length; i++) {
      var u = entries[i].name;
      if (u.indexOf(location.origin) !== 0 && u.indexOf('data:') !== 0 && u.indexOf('blob:') !== 0) ext.push(u);
    }
    var badFetch = S.fetched.filter(function (u) {
      return u.indexOf('http') === 0 && u.indexOf(location.origin) !== 0;
    });
    add(ext.length === 0 && badFetch.length === 0, t('st.noThirdParty'),
      ext.length ? ext.slice(0, 2).join(' ') : t('st.sameOrigin', { n: entries.length }));

    var lsOk = false;
    try { localStorage.setItem('__exo_probe', '1'); lsOk = localStorage.getItem('__exo_probe') === '1'; localStorage.removeItem('__exo_probe'); } catch (e) { lsOk = false; }
    add(lsOk, t('st.localStorage'), lsOk ? t('st.ok') : t('st.unavailable'));

    /* 语言相关：字典键对齐 */
    (function () {
      var missing = I18N.missingKeys();
      var total = Object.keys(I18N.DICTS.zh || {}).length;
      add(missing.length === 0, t('st.i18nDict'),
        t('st.i18nDictDetail', { n: total, m: missing.length }) +
        (missing.length ? ' ' + missing.slice(0, 3).join(',') : ''));
    })();

    /* 语言相关：真实切换到 EN 再切回（验证 <html lang> + 静态区 + 动态区一起变） */
    (function () {
      var before = lang();
      var enTitle = '', enBrand = '', enLayer = '', backTitle = '';
      var ok = false;
      try {
        setLang('en');
        enTitle = document.title;
        enBrand = (document.querySelector('.brand-text em') || {}).textContent || '';
        enLayer = els.layerName.textContent;
        ok = document.documentElement.lang === 'en' &&
          enTitle === I18N.DICTS.en['doc.title'] &&
          enBrand === I18N.DICTS.en['brand.sub'] &&
          enLayer === I18N.DICTS.en['layer.' + S.layer];
      } catch (e) { ok = false; }
      try { setLang(before); } catch (e2) { /* ignore */ }
      backTitle = document.title;
      ok = ok && backTitle === I18N.DICTS[before]['doc.title'];
      add(ok, t('st.i18nSwitch'), t('st.i18nSwitchDetail', { text: enLayer }));
      updateLangUI();
    })();

    // 更新时间颜色：按快照周期，而不是固定 5/30 s（T85 不会被提前判红）
    add(freshnessLevel(10, 36) === 'ok' && freshnessLevel(40, 36) === 'ok' &&
        freshnessLevel(70, 36) === 'stale' && freshnessLevel(130, 36) === 'dead' &&
        freshnessLevel(6, 5) === 'ok',
        t('st.freshness'), '10/36 ok · 70/36 stale · 130/36 dead');

    var pass = items.filter(function (x) { return x.ok; }).length;
    var fail = items.length - pass;
    var result = { pass: pass, fail: fail, items: items, runId: S.runId, ms: Date.now() };
    window.__SELFTEST__ = result;
    if (show !== false) renderSelfTest(result);
    return result;
  }

  function renderSelfTest(result) {
    var box = els.selftest;
    if (!box) return;
    var html = '<h3>' + escapeHtml(t('st.title')) + ' · ' +
      escapeHtml(result.fail === 0 ? t('st.allPass') : t('st.fail', { n: result.fail })) +
      ' <span style="color:#8698b3">(' + result.pass + '/' + (result.pass + result.fail) + ')</span>' +
      '<button type="button" class="st-close" title="' + escapeAttr(t('st.close')) +
      '" aria-label="' + escapeAttr(t('st.close')) +
      '" style="float:right;background:none;border:0;color:#8698b3;cursor:pointer;font:inherit">✕</button></h3>';
    for (var i = 0; i < result.items.length; i++) {
      var it = result.items[i];
      html += '<div class="st-item"><span class="' + (it.ok ? 'st-ok' : 'st-bad') + '">' +
        (it.ok ? '✓' : '✗') + '</span><span class="st-name">' + escapeHtml(it.name) +
        '</span><span class="st-detail">' + escapeHtml(it.detail) + '</span></div>';
    }
    box.innerHTML = html;
    box.hidden = false;
    var close = box.querySelector('.st-close');
    if (close) close.addEventListener('click', function () { box.hidden = true; });
  }

  /* ---------------- 启动 ---------------- */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
