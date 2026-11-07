/* i18n.js — 中英文双语字典 / 语言切换（零依赖）
 *
 *   window.EXO_I18N = { t, setLang, getLang, lang, onChange, applyStatic,
 *                       has, missingKeys, LANGS, DICTS }
 *
 * 用法：
 *   EXO_I18N.t('layer.ts')                     → '地表温度' / 'Surface temperature'
 *   EXO_I18N.t('kv.simDate', {y:1,m:2,d:15})   → '第 1 年 · 第 2 月 · 第 15 天'
 *   EXO_I18N.setLang('en')                     → 立即切换（静态区 + 通知订阅者）
 *   EXO_I18N.onChange(fn)                      → 语言变化后重渲染动态区
 *
 * 静态 HTML 文字用属性标注，切换时由 applyStatic() 统一替换：
 *   <span data-i18n="brand.sub">实时只读监控</span>
 *   <span data-i18n-title="title.freshness" title="meta.updated 距今">…</span>
 *   <span data-i18n-aria-label="aria.globe" aria-label="3D 球体图层显示">…</span>
 *
 * 语言来源优先级：?lang=zh|en 深链 > localStorage > 默认中文。
 * 新增语言：在 DICTS 里加一份同键字典（LANGS 数组追加语言码），
 * 并在 norm() 里登记别名即可，其余代码无需改动。
 */
(function (global) {
  'use strict';

  var LS_LANG = 'exoplasim.monitor.lang';
  var DEFAULT_LANG = 'zh';
  var LANGS = ['zh', 'en'];

  /* ---------------- 字典 ---------------- */

  var DICTS = {
    zh: {
      /* 文档 / 品牌 */
      'doc.title': '行星模拟 · 实时只读监控',
      'brand.name': '行星模拟',
      'brand.sub': '实时只读监控',

      /* 语言切换 */
      'lang.label': '界面语言',
      'lang.zh': '中文',
      'lang.en': 'EN',
      'aria.chooseRun': '选择模拟',

      /* 连接 / 状态 */
      'conn.connecting': '连接中…',
      'conn.ok': '已连接',
      'conn.bad': '连接中断（重试中）',
      'status.running': '运行中',
      'status.configuring': '配置中',
      'status.waiting': '等待中',
      'status.finished': '已结束',
      'status.error': '错误',

      /* run 选择器 */
      'picker.discovering': '正在发现模拟…',
      'picker.notFoundRetry': '未发现模拟（重试中…）',
      'picker.empty': '未发现模拟',
      'picker.item': '{model} · {id} · {n} 帧',
      'picker.item.one': '{model} · {id} · {n} 帧',
      'picker.frames': '· {n} 帧',
      'picker.frames.one': '· {n} 帧',
      'picker.tip': 'run_id: {id}\n{dir}\n{status}: {statusText}\n{frames}: {framesN}',

      /* 通用 */
      'common.waitingData': '等待数据…',
      'common.waitingParams': '等待参数…',
      'common.unknown': '未知',
      'common.yes': '是',
      'common.no': '否',
      'common.na': '—',

      /* 图层 */
      'layer.title': '图层',
      'layer.ts': '地表温度',
      'layer.tas': '2 m 气温',
      'layer.pr': '降水',
      'layer.spd': '风速',
      'layer.sic': '海冰覆盖',
      'layer.sit': '海冰厚度',
      'layer.clt': '云量',
      'layer.measured': '实测 {min} – {max}',
      'aria.unit': '温度单位',
      'title.unit': '温度显示单位',

      /* 色标 */
      'cbar.current': '当前 {min} – {max}（色标 {lo} – {hi}{unit}）',

      /* 进度 kv */
      'progress.waiting': '等待运行数据…',
      'kv.simDate': '模拟日期',
      'kv.simDateValue': '第 {y} 年 · 第 {m} 月 · 第 {d} 天',
      'kv.year': '年份',
      'kv.yearValue': '第 {y} 年 / 共 {total} 年',
      'kv.yearSolo': '第 {y} 年',
      'kv.step': 'step',
      'kv.plannedMonths': '计划月数',
      'kv.months': '{n} 月',
      'kv.doneMonths': '已完成',
      'kv.elapsed': '已运行',
      'kv.eta': 'ETA',
      'kv.frames': '帧数',
      'kv.framePeriod': '快照间隔',
      'kv.framePeriodObserved': '{last}（均值 {avg}）',
      'kv.framePeriodEstimated': '≈ {est}（估算）',
      'kv.pollInterval': '轮询周期',
      'kv.overhead': '监控开销',
      'kv.status': '状态',

      /* 顶栏右侧 */
      'fresh.justNow': '刚刚',
      'fresh.ago': '{s} s 前',
      'fresh.update': '更新 {ago}',
      'fresh.expectedIn': '预期 {s} 后更新',
      'fresh.expectedPast': '预期已过 {s}',
      'fresh.updateExpected': '更新 {ago} · {expected}',
      'fresh.title': 'meta.updated 距今 {s} s',
      'fresh.titleExpected': '距今 {age} s，快照周期 {period}（{source}）',
      'fresh.sourceMtime': '文件生成时间',
      'fresh.sourceEstimated': '模型进度估算',
      'title.freshness': 'meta.updated 距今',
      'server.na': '开销 —',
      'server.meta': 'CPU {cpu}% · 轮询 {poll} ms',
      'server.title': '监控进程自身开销\ncpu_percent={cpu}%\npoll_ms={poll}\nframes_parsed={frames}\nbytes_read={bytes}\nrss={rss} KiB\nuptime={uptime} s',
      'title.server': '监控进程自身开销',

      /* 序列图 */
      'chart.title': '序列',
      'chart.waiting': '等待首帧数据…',
      'legend.ts': 'ts 均值',
      'legend.ntr': 'ntr',

      /* 参数面板 */
      'params.title': '参数',
      'group.run': '运行',
      'group.num': '数值',
      'group.star': '恒星与轨道',
      'group.atm': '大气',
      'group.geom': '几何与边界',
      'group.src': '数据源',
      'group.other': '其他',

      /* 参数名 */
      'param.modelname': '模型名',
      'param.run_id': 'run_id',
      'param.workdir': '工作目录',
      'param.outdir': '输出目录',
      'param.resolution': '分辨率',
      'param.layers': '垂直层数',
      'param.ncpus': 'CPU 核数',
      'param.model_class': '模型类',
      'param.timestep_min': '时间步长',
      'param.snapshot_steps': '快照间隔',
      'param.steps_per_year': '每年步数',
      'param.planned_months': '计划月数',
      'param.planned_years': '计划年数',
      'param.year_total': '总年数',
      'param.run_mode': '运行模式',
      'param.restart_year': '续跑起始年',
      'param.plan_source': '时长来源',
      'param.flux_W_m2': '恒星通量',
      'param.startemp_K': '恒星有效温度',
      'param.rotationperiod_d': '自转周期',
      'param.synchronous': '同步自转',
      'param.eccentricity': '偏心率',
      'param.obliquity_deg': '黄赤交角',
      'param.substellar_lon_deg': '日下点经度',
      'param.pN2_bar': 'N₂ 分压',
      'param.pO2_bar': 'O₂ 分压',
      'param.pAr_bar': 'Ar 分压',
      'param.pCO2_bar': 'CO₂ 分压',
      'param.pCO2_ppmv': 'CO₂ 混合比',
      'param.physicsfilter': '物理滤波',
      'param.radius_Rearth': '行星半径',
      'param.gravity_m_s2': '重力加速度',
      'param.planet_radius_m': '行星半径',
      'param.nlat_model': '模式纬向格点',
      'param.nlon_model': '模式经向格点',
      'param.ntru': '谱截断',
      'param.landmap': '陆海掩膜',
      'param.topomap': '地形场',
      'param.restartfile': '初始场 restart',

      /* 参数单位（物理单位不译，只有口语单位需要） */
      'unit.steps': '步',
      'unit.months': '月',
      'unit.years': '年',
      'unit.days': '天',

      /* 时间格式 */
      'dur.min': '{m} 分 {s} 秒',
      'dur.hour': '{h} 时 {m} 分',

      /* 悬停浮层 */
      'hover.title': '悬停读数',

      /* 历史帧 / 播放 */
      'tl.waiting': '等待数据',
      'tl.frame': '帧 {i} / {n}',
      'tl.live': '实时 · {n} 帧',
      'tl.live.one': '实时 · {n} 帧',
      'frame.historyPrefix': '历史帧 · ',
      'btn.play': '播放历史帧',
      'aria.play': '播放历史帧',
      'btn.live': '回到实时',
      'aria.slider': '历史帧回溯',
      'aria.globe': '3D 球体图层显示',

      /* 图层开关 / 视角 */
      'opt.light': '光照',
      'opt.grid': '经纬网',
      'opt.land': '陆地',
      'opt.arrows': '风矢',
      'opt.resetView': '复位视角',
      'hint.globe': '拖拽旋转 · 滚轮缩放 · 双击复位',
      'hint.touch': '拖动旋转 · 双指缩放 · 点按读数',
      'ui.zoomIn': '放大',
      'ui.zoomOut': '缩小',

      /* 加载 / 降级 */
      'loading.connecting': '正在连接监控服务…',
      'gl.title': '无法初始化 WebGL2',
      'gl.msg': '当前浏览器/显卡驱动不支持 WebGL2，3D 球体无法显示。',
      'gl.note': '其余监控功能（状态、参数、序列图、历史帧）仍可正常使用。',
      'err.glInit': 'WebGL2 初始化失败：{msg}',
      'err.frame': '取帧失败：{msg}（将自动重试）',

      /* 消息条 */
      'msg.error': '错误：',
      'msg.notice': '提示：',
      'msg.backendPrefix': '后端：',

      /* 球体标记 */
      'globe.sun': '日下点',
      'globe.anti': '对侧点',

      /* 自检 */
      'st.title': '前端自检',
      'st.allPass': '全部通过 ✓',
      'st.fail': '{n} 项失败 ✗',
      'st.close': '关闭',
      'st.webgl': 'WebGL2 上下文 + 着色器',
      'st.created': '已创建',
      'st.unavailable': '不可用（已显示提示）',
      'st.layerButtons': '图层按钮 {n} 个',
      'st.pickerMatch': 'run 选项与 /api/runs 一致',
      'st.runsApi': '/api/runs',
      'st.runsCount': '{n} 个 run',
      'st.stateGrid': '/api/state grid',
      'st.layersMeta': '/api/state 全部图层元数据（含 sit）',
      'st.complete': '齐全',
      'st.missing': '缺失',
      'st.yearZeroBased': '年份从 0 开始（year_no）',
      'st.simDateMatchesYear': '模拟日期与年份一致',
      'st.tempConv': 'K→°C 换算正确',
      'st.hover': '悬停采样（0°,0°）',
  'st.hoverPointer': '悬停浮层（指针事件）',
  'st.touchReadout': '触摸点按读数',
  'st.noOverflow': '移动端无横向溢出',
      'st.noData': '无数据',
      'st.exof': '/api/frame EXOF 解析',
      'st.sitInFrame': '帧含 sit（海冰厚度）图层',
      'st.gridMatch': '帧网格与 state.grid 一致',
      'st.land': '/api/land 陆海掩膜',
      'st.empty': '空',
      'st.globeRendered': '球体已渲染（中心像素）',
      'st.readFailed': '读取失败',
      'st.webglUnavailable': 'WebGL2 不可用',
      'st.firstFrame': '首帧耗时 < 1 s',
      'st.noFrame': '未取到帧',
      'st.noThirdParty': '无第三方网络请求',
      'st.sameOrigin': '全部同源（{n} 条资源）',
      'st.failed': '失败',
      'st.localStorage': 'localStorage 可用（记住 run/图层）',
      'st.ok': '正常',
      'st.i18nDict': 'i18n 字典完整（zh/en 键对齐）',
      'st.i18nDictDetail': '{n} 个键 · 缺失 {m}',
      'st.i18nSwitch': '语言切换（中 → EN → 中）',
      'st.i18nSwitchDetail': 'EN: {text}',
      'st.freshness': '更新时间颜色随快照周期',
      'st.foreignRequests': '{n} 条外部请求',

      /* 错误信息 */
      'exof.short': '帧数据过短',
      'exof.magic': 'EXOF magic 不匹配',
      'exof.header': '帧 header 越界',
      'exof.truncated': '帧数据截断（{layer}）'
    },

    en: {
      'doc.title': 'Planetary Simulation · Live Read-Only Monitor',
      'brand.name': 'PLANETARY SIM',
      'brand.sub': 'LIVE READ-ONLY MONITOR',

      'lang.label': 'Interface language',
      'lang.zh': '中文',
      'lang.en': 'EN',
      'aria.chooseRun': 'Choose simulation',

      'conn.connecting': 'Connecting…',
      'conn.ok': 'Connected',
      'conn.bad': 'Disconnected (retrying)',
      'status.running': 'Running',
      'status.configuring': 'Configuring',
      'status.waiting': 'Waiting',
      'status.finished': 'Finished',
      'status.error': 'Error',

      'picker.discovering': 'Discovering simulations…',
      'picker.notFoundRetry': 'No simulation found (retrying…)',
      'picker.empty': 'No simulation found',
      'picker.item': '{model} · {id} · {n} frames',
      'picker.item.one': '{model} · {id} · {n} frame',
      'picker.frames': '· {n} frames',
      'picker.frames.one': '· {n} frame',
      'picker.tip': 'run_id: {id}\n{dir}\n{status}: {statusText}\n{frames}: {framesN}',

      'common.waitingData': 'Waiting for data…',
      'common.waitingParams': 'Waiting for parameters…',
      'common.unknown': 'unknown',
      'common.yes': 'Yes',
      'common.no': 'No',
      'common.na': '—',

      'layer.title': 'Layers',
      'layer.ts': 'Surface temperature',
      'layer.tas': '2 m air temperature',
      'layer.pr': 'Precipitation',
      'layer.spd': 'Wind speed',
      'layer.sic': 'Sea-ice cover',
      'layer.sit': 'Sea-ice thickness',
      'layer.clt': 'Cloud cover',
      'layer.measured': 'measured {min} – {max}',
      'aria.unit': 'Temperature unit',
      'title.unit': 'Temperature display unit',

      'cbar.current': 'current {min} – {max} (scale {lo} – {hi}{unit})',

      'progress.waiting': 'Waiting for run data…',
      'kv.simDate': 'Sim date',
      'kv.simDateValue': 'Year {y} · Month {m} · Day {d}',
      'kv.year': 'Year',
      'kv.yearValue': 'Year {y} / {total} total',
      'kv.yearSolo': 'Year {y}',
      'kv.step': 'step',
      'kv.plannedMonths': 'Planned months',
      'kv.months': '{n} mo',
      'kv.doneMonths': 'Completed',
      'kv.elapsed': 'Elapsed',
      'kv.eta': 'ETA',
      'kv.frames': 'Frames',
      'kv.framePeriod': 'Frame interval',
      'kv.framePeriodObserved': '{last} (avg {avg})',
      'kv.framePeriodEstimated': '≈ {est} (estimated)',
      'kv.pollInterval': 'Poll interval',
      'kv.overhead': 'Monitor overhead',
      'kv.status': 'Status',

      'fresh.justNow': 'just now',
      'fresh.ago': '{s} s ago',
      'fresh.update': 'Updated {ago}',
      'fresh.expectedIn': 'next in {s}',
      'fresh.expectedPast': '{s} overdue',
      'fresh.updateExpected': 'Updated {ago} · {expected}',
      'fresh.title': 'meta.updated age {s} s',
      'fresh.titleExpected': 'age {age} s, period {period} ({source})',
      'fresh.sourceMtime': 'from file mtime',
      'fresh.sourceEstimated': 'estimated',
      'title.freshness': 'age of meta.updated',
      'server.na': 'overhead —',
      'server.meta': 'CPU {cpu}% · poll {poll} ms',
      'server.title': 'monitor process overhead\ncpu_percent={cpu}%\npoll_ms={poll}\nframes_parsed={frames}\nbytes_read={bytes}\nrss={rss} KiB\nuptime={uptime} s',
      'title.server': 'monitor process overhead',

      'chart.title': 'Series',
      'chart.waiting': 'Waiting for the first frame…',
      'legend.ts': 'ts mean',
      'legend.ntr': 'ntr',

      'params.title': 'Parameters',
      'group.run': 'Run',
      'group.num': 'Numerics',
      'group.star': 'Star & orbit',
      'group.atm': 'Atmosphere',
      'group.geom': 'Geometry & boundary',
      'group.src': 'Data sources',
      'group.other': 'Other',

      'param.modelname': 'Model name',
      'param.run_id': 'run_id',
      'param.workdir': 'Work directory',
      'param.outdir': 'Output directory',
      'param.resolution': 'Resolution',
      'param.layers': 'Vertical levels',
      'param.ncpus': 'CPU cores',
      'param.model_class': 'Model class',
      'param.timestep_min': 'Time step',
      'param.snapshot_steps': 'Snapshot interval',
      'param.steps_per_year': 'Steps per year',
      'param.planned_months': 'Planned months',
      'param.planned_years': 'Planned years',
      'param.year_total': 'Total years',
      'param.run_mode': 'Run mode',
      'param.restart_year': 'Restart start year',
      'param.plan_source': 'Duration source',
      'param.flux_W_m2': 'Stellar flux',
      'param.startemp_K': 'Stellar effective temperature',
      'param.rotationperiod_d': 'Rotation period',
      'param.synchronous': 'Synchronous rotation',
      'param.eccentricity': 'Eccentricity',
      'param.obliquity_deg': 'Obliquity',
      'param.substellar_lon_deg': 'Substellar longitude',
      'param.pN2_bar': 'N₂ partial pressure',
      'param.pO2_bar': 'O₂ partial pressure',
      'param.pAr_bar': 'Ar partial pressure',
      'param.pCO2_bar': 'CO₂ partial pressure',
      'param.pCO2_ppmv': 'CO₂ mixing ratio',
      'param.physicsfilter': 'Physics filter',
      'param.radius_Rearth': 'Planet radius',
      'param.gravity_m_s2': 'Surface gravity',
      'param.planet_radius_m': 'Planet radius',
      'param.nlat_model': 'Model latitudes',
      'param.nlon_model': 'Model longitudes',
      'param.ntru': 'Spectral truncation',
      'param.landmap': 'Land–sea mask',
      'param.topomap': 'Topography',
      'param.restartfile': 'Restart file',

      'unit.steps': 'steps',
      'unit.months': 'months',
      'unit.years': 'years',
      'unit.days': 'days',

      'dur.min': '{m} min {s} s',
      'dur.hour': '{h} h {m} min',

      'hover.title': 'Hover readout',

      'tl.waiting': 'Waiting for data',
      'tl.frame': 'Frame {i} / {n}',
      'tl.live': 'Live · {n} frames',
      'tl.live.one': 'Live · {n} frame',
      'frame.historyPrefix': 'History · ',
      'btn.play': 'Play history frames',
      'aria.play': 'Play history frames',
      'btn.live': 'Back to live',
      'aria.slider': 'History frame scrubber',
      'aria.globe': '3D globe layer display',

      'opt.light': 'Lighting',
      'opt.grid': 'Graticule',
      'opt.land': 'Land',
      'opt.arrows': 'Wind arrows',
      'opt.resetView': 'Reset view',
      'hint.globe': 'Drag to rotate · wheel to zoom · double-click to reset',
      'hint.touch': 'Drag to rotate · pinch to zoom · tap to read',
      'ui.zoomIn': 'Zoom in',
      'ui.zoomOut': 'Zoom out',

      'loading.connecting': 'Connecting to monitor service…',
      'gl.title': 'WebGL2 unavailable',
      'gl.msg': 'This browser/GPU driver does not support WebGL2, so the 3D globe cannot be rendered.',
      'gl.note': 'All other monitor features (status, parameters, series chart, history frames) still work.',
      'err.glInit': 'WebGL2 init failed: {msg}',
      'err.frame': 'Frame fetch failed: {msg} (will retry automatically)',

      'msg.error': 'Error: ',
      'msg.notice': 'Notice: ',
      'msg.backendPrefix': 'backend: ',

      'globe.sun': 'Subsolar',
      'globe.anti': 'Antipode',

      'st.title': 'Front-end self-test',
      'st.allPass': 'all passed ✓',
      'st.fail': '{n} failed ✗',
      'st.close': 'Close',
      'st.webgl': 'WebGL2 context + shaders',
      'st.created': 'created',
      'st.unavailable': 'unavailable (notice shown)',
      'st.layerButtons': 'Layer buttons ({n})',
      'st.pickerMatch': 'run options match /api/runs',
      'st.runsApi': '/api/runs',
      'st.runsCount': '{n} runs',
      'st.stateGrid': '/api/state grid',
      'st.layersMeta': '/api/state all layer metadata (incl. sit)',
      'st.complete': 'complete',
      'st.missing': 'missing',
      'st.yearZeroBased': 'Year numbering starts at 0 (year_no)',
      'st.simDateMatchesYear': 'Sim date matches the year',
      'st.tempConv': 'K→°C conversion correct',
      'st.hover': 'Hover sampling (0°,0°)',
  'st.hoverPointer': 'Hover card (pointer events)',
  'st.touchReadout': 'Tap readout (touch)',
  'st.noOverflow': 'No horizontal overflow',
      'st.noData': 'no data',
      'st.exof': '/api/frame EXOF parse',
      'st.sitInFrame': 'Frame contains sit (sea-ice thickness)',
      'st.gridMatch': 'Frame grid matches state.grid',
      'st.land': '/api/land land–sea mask',
      'st.empty': 'empty',
      'st.globeRendered': 'Globe rendered (center pixel)',
      'st.readFailed': 'read failed',
      'st.webglUnavailable': 'WebGL2 unavailable',
      'st.firstFrame': 'First frame < 1 s',
      'st.noFrame': 'no frame yet',
      'st.noThirdParty': 'No third-party network requests',
      'st.sameOrigin': 'all same-origin ({n} resources)',
      'st.failed': 'failed',
      'st.localStorage': 'localStorage works (remembers run/layer)',
      'st.ok': 'ok',
      'st.i18nDict': 'i18n dictionary complete (zh/en keys aligned)',
      'st.i18nDictDetail': '{n} keys · {m} missing',
      'st.i18nSwitch': 'Language switch (zh → EN → zh)',
      'st.i18nSwitchDetail': 'EN: {text}',
      'st.freshness': 'Freshness colour follows frame period',
      'st.foreignRequests': '{n} external requests',

      'exof.short': 'frame data too short',
      'exof.magic': 'EXOF magic mismatch',
      'exof.header': 'frame header out of bounds',
      'exof.truncated': 'frame data truncated ({layer})'
    }
  };

  /* ---------------- 语言状态 ---------------- */

  var listeners = [];
  var cur = DEFAULT_LANG;

  function norm(v) {
    if (!v) return null;
    var s = String(v).toLowerCase();
    if (s.indexOf('en') === 0) return 'en';
    if (s.indexOf('zh') === 0 || s === 'cn' || s === 'chs' || s === 'chi') return 'zh';
    return null;
  }

  function dictOf(lang) { return DICTS[lang] || DICTS[DEFAULT_LANG]; }
  function hasIn(lang, key) {
    return !!(DICTS[lang] && Object.prototype.hasOwnProperty.call(DICTS[lang], key));
  }
  function has(key) { return hasIn(cur, key) || hasIn(DEFAULT_LANG, key); }

  function format(str, vars) {
    if (!vars) return str;
    return String(str).replace(/\{(\w+)\}/g, function (m, k) {
      return Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m;
    });
  }

  /* t(key, vars)：当前语言 → 中文回退 → key 本身（永不返回 undefined）
   * vars.n === 1 时优先用 "<key>.one"（英文单复数；中文两份文案相同） */
  function t(key, vars) {
    var k = key;
    if (vars && vars.n === 1 && has(key + '.one')) k = key + '.one';
    var d = dictOf(cur);
    var s = Object.prototype.hasOwnProperty.call(d, k) ? d[k]
      : (Object.prototype.hasOwnProperty.call(DICTS[DEFAULT_LANG], k) ? DICTS[DEFAULT_LANG][k] : k);
    return format(s, vars);
  }

  function getLang() { return cur; }

  /* zh/en 键差值（自检 / 开发期检查用） */
  function missingKeys() {
    var out = [];
    for (var lang in DICTS) {
      if (!Object.prototype.hasOwnProperty.call(DICTS, lang)) continue;
      var keys = Object.keys(DICTS[lang]);
      for (var i = 0; i < keys.length; i++) {
        for (var other in DICTS) {
          if (!Object.prototype.hasOwnProperty.call(DICTS, other) || other === lang) continue;
          if (!hasIn(other, keys[i])) out.push(lang + ':' + keys[i]);
        }
      }
    }
    return out;
  }

  function applyStatic(root) {
    root = root || document;
    var nodes = root.querySelectorAll('[data-i18n],[data-i18n-title],[data-i18n-aria-label],[data-i18n-placeholder]');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var key = el.getAttribute('data-i18n');
      if (key) el.textContent = t(key);
      key = el.getAttribute('data-i18n-title');
      if (key) el.title = t(key);
      key = el.getAttribute('data-i18n-aria-label');
      if (key) el.setAttribute('aria-label', t(key));
      key = el.getAttribute('data-i18n-placeholder');
      if (key) el.placeholder = t(key);
    }
    document.title = t('doc.title');
  }

  function onChange(fn) {
    if (typeof fn === 'function' && listeners.indexOf(fn) < 0) listeners.push(fn);
    return fn;
  }

  function emit() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](cur); } catch (e) { if (global.console) console.warn('[i18n] listener failed', e); }
    }
  }

  /* opts.silent：只改语言状态与 <html lang>，不刷新静态区/不通知（自检临时切换用） */
  function setLang(next, opts) {
    var lang = norm(next) || DEFAULT_LANG;
    cur = lang;
    try { localStorage.setItem(LS_LANG, lang); } catch (e) { /* private mode */ }
    if (document && document.documentElement) {
      document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
      document.documentElement.setAttribute('data-lang', lang);
    }
    if (!(opts && opts.silent)) {
      applyStatic();
      emit();
    }
    return cur;
  }

  /* ---------------- 初始化 ---------------- */

  var bootLang = DEFAULT_LANG;
  try {
    var qs = new URLSearchParams(global.location ? global.location.search : '');
    var q = norm(qs.get('lang'));
    if (q) bootLang = q;
    else {
      var saved = norm(localStorage.getItem(LS_LANG));
      if (saved) bootLang = saved;
    }
  } catch (e) { /* ignore */ }

  global.EXO_I18N = {
    t: t,
    setLang: setLang,
    getLang: getLang,
    has: has,
    missingKeys: missingKeys,
    applyStatic: applyStatic,
    onChange: onChange,
    LANGS: LANGS,
    DICTS: DICTS
  };
  /* 只读实时语言码：EXO_I18N.lang → 'zh' | 'en' */
  try {
    Object.defineProperty(global.EXO_I18N, 'lang', { get: getLang, enumerable: true });
  } catch (e) {
    global.EXO_I18N.lang = bootLang;
  }

  /* 脚本位于 </body> 前，此时静态 DOM 已就绪，先把语言落地一次 */
  setLang(bootLang);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { applyStatic(); });
  }
})(window);
