/* globe.js — 手写 WebGL2 球体渲染 + canvas2D 风矢叠加（零依赖）
 *
 * 坐标系：只使用**模型系**（自转轴为极），与 ExoPlaSim 的网格一致。
 *   纹理 row0 = 北（lat +90），col0 = lon 0°；太阳方向固定为模型系 (-1,0,0)
 *   即日下点 (lat 0°, lon 180°)；对侧点 (0°, 0°)。
 */
(function (global) {
  'use strict';

  var D2R = Math.PI / 180;

  /* ---- 少量用户可见文案跟随界面语言（i18n.js 未加载时回退中文） ---- */
  function tr(key, zhFallback) {
    var I = global.EXO_I18N;
    if (!I || !I.t) return zhFallback;
    var v = I.t(key);
    return v === key ? zhFallback : v;
  }
  function pickLang(zh, en) {
    var I = global.EXO_I18N;
    return (I && I.getLang && I.getLang() === 'en') ? en : zh;
  }

  /* ---------------- 数学 ---------------- */

  function mul4(a, b) {
    var o = new Float32Array(16);
    for (var c = 0; c < 4; c++) {
      for (var r = 0; r < 4; r++) {
        o[c * 4 + r] =
          a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
      }
    }
    return o;
  }

  function perspective(fovy, aspect, near, far) {
    var f = 1 / Math.tan(fovy / 2);
    var nf = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (far + near) * nf, -1,
      0, 0, 2 * far * near * nf, 0
    ]);
  }

  function lookAt(eye, center, up) {
    var zx = eye[0] - center[0], zy = eye[1] - center[1], zz = eye[2] - center[2];
    var zl = Math.hypot(zx, zy, zz) || 1;
    zx /= zl; zy /= zl; zz /= zl;
    var xx = up[1] * zz - up[2] * zy;
    var xy = up[2] * zx - up[0] * zz;
    var xz = up[0] * zy - up[1] * zx;
    var xl = Math.hypot(xx, xy, xz);
    if (xl < 1e-6) { xx = 1; xy = 0; xz = 0; xl = 1; }
    xx /= xl; xy /= xl; xz /= xl;
    var yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    return new Float32Array([
      xx, yx, zx, 0,
      xy, yy, zy, 0,
      xz, yz, zz, 0,
      -(xx * eye[0] + xy * eye[1] + xz * eye[2]),
      -(yx * eye[0] + yy * eye[1] + yz * eye[2]),
      -(zx * eye[0] + zy * eye[1] + zz * eye[2]),
      1
    ]);
  }

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }

  /* ---------------- 球体几何（等经纬细分，UV = 模型经纬） ---------------- */

  function buildSphere(nlatSeg, nlonSeg) {
    var nv = (nlatSeg + 1) * (nlonSeg + 1);
    var pos = new Float32Array(nv * 3);
    var uv = new Float32Array(nv * 2);
    var p = 0, q = 0;
    for (var j = 0; j <= nlatSeg; j++) {
      var lat = (90 - (180 * j) / nlatSeg) * D2R;
      var cl = Math.cos(lat), sl = Math.sin(lat);
      for (var i = 0; i <= nlonSeg; i++) {
        var lon = (360 * i / nlonSeg) * D2R;
        pos[p++] = cl * Math.cos(lon);
        pos[p++] = cl * Math.sin(lon);
        pos[p++] = sl;
        uv[q++] = i / nlonSeg;
        uv[q++] = j / nlatSeg;
      }
    }
    var idx = new Uint16Array(nlatSeg * nlonSeg * 6);
    var k = 0, row = nlonSeg + 1;
    for (var jj = 0; jj < nlatSeg; jj++) {
      for (var ii = 0; ii < nlonSeg; ii++) {
        var a = jj * row + ii, b = a + 1, c = a + row, d = c + 1;
        idx[k++] = a; idx[k++] = b; idx[k++] = d;
        idx[k++] = a; idx[k++] = d; idx[k++] = c;
      }
    }
    return { pos: pos, uv: uv, idx: idx, count: idx.length };
  }

  /* ---------------- 着色器 ---------------- */

  var VS = [
    '#version 300 es',
    'precision highp float;',
    'in vec3 aPos;',
    'in vec2 aUV;',
    'uniform mat4 uVP;',
    'out vec2 vUV;',
    'out vec3 vWorld;',
    'void main() {',
    '  vec3 p = aPos;',
    '  vUV = aUV;',
    '  vWorld = p;',
    '  gl_Position = uVP * vec4(p, 1.0);',
    '}'
  ].join('\n');

  var FS = [
    '#version 300 es',
    'precision highp float;',
    'in vec2 vUV;',
    'in vec3 vWorld;',
    'uniform sampler2D uData;',
    'uniform sampler2D uLut;',
    'uniform sampler2D uLand;',
    'uniform vec2 uTexel;',
    'uniform float uNlat;',
    'uniform vec3 uSun;',
    'uniform vec3 uCam;',
    'uniform float uLight;',
    'uniform float uGridOn;',
    'uniform float uLandOn;',
    'uniform float uHasData;',
    'uniform float uHasLand;',
    'out vec4 outColor;',
    '',
    'float gridMask(vec2 uv) {',
    '  vec2 cells = vec2(12.0, 6.0);', // 经度每 30° / 纬度每 30°
    '  vec2 g = uv * cells;',
    '  vec2 w = max(fwidth(g), vec2(1e-5));',
    '  vec2 d = abs(fract(g + 0.5) - 0.5) / w;',
    '  return 1.0 - min(min(d.x, d.y), 1.0);',
    '}',
    '',
    'void main() {',
    '  float halfv = 0.5 / max(uNlat, 1.0);',
    '  vec2 uv = vec2(vUV.x, clamp(vUV.y, halfv, 1.0 - halfv));',
    '  vec3 n = normalize(vWorld);',
    '  vec3 base = vec3(0.085, 0.105, 0.145);',
    '  if (uHasData > 0.5) {',
    '    float t = clamp(texture(uData, uv).r, 0.0, 1.0);',
    '    base = texture(uLut, vec2(t, 0.5)).rgb;',
    '  }',
    '  if (uLandOn > 0.5 && uHasLand > 0.5) {',
    '    float l = texture(uLand, uv).r;',
    '    vec2 dHalf = uTexel * 0.5;',
    '    float gx = texture(uLand, uv + vec2(dHalf.x, 0.0)).r - texture(uLand, uv - vec2(dHalf.x, 0.0)).r;',
    '    float gy = texture(uLand, uv + vec2(0.0, dHalf.y)).r - texture(uLand, uv - vec2(0.0, dHalf.y)).r;',
    '    float coast = clamp(length(vec2(gx, gy)) * 1.4, 0.0, 1.0);',
    '    float landMask = smoothstep(0.42, 0.62, l);',
    '    float lum = dot(base, vec3(0.299, 0.587, 0.114));',
    '    base = mix(base, vec3(lum) * 0.82 + vec3(0.035, 0.032, 0.028), landMask * 0.75);',
    '    base = mix(base, vec3(0.84, 0.83, 0.80), smoothstep(0.16, 0.55, coast) * 0.42);',
    '  }',
    '  if (uLight > 0.5) {',
    '    float ndl = dot(n, normalize(uSun));',
    '    base *= (0.24 + 0.92 * max(ndl, 0.0));',
    '  }',
    '  base = mix(base, vec3(0.80, 0.88, 1.0), gridMask(vUV) * 0.24 * uGridOn);',
    '  vec3 vdir = normalize(uCam - vWorld);',
    '  float fres = pow(1.0 - clamp(abs(dot(n, vdir)), 0.0, 1.0), 3.0);',
    '  base += vec3(0.16, 0.34, 0.62) * fres * 0.55;',
    '  outColor = vec4(base, 1.0);',
    '}'
  ].join('\n');

  function compile(gl, type, src, tag) {
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      var log = gl.getShaderInfoLog(sh);
      gl.deleteShader(sh);
      throw new Error(pickLang(tag + ' 编译失败：', tag + ' compile failed: ') + log);
    }
    return sh;
  }

  /* ---------------- Globe ---------------- */

  function Globe(canvas, windCanvas, markerHost, opts) {
    opts = opts || {};
    this.canvas = canvas;
    this.windCanvas = windCanvas;
    this.windCtx = windCanvas ? windCanvas.getContext('2d') : null;
    this.markerHost = markerHost || null;
    this.reduceMotion = !!opts.reduceMotion;
    this.autoRotate = opts.autoRotate !== false && !this.reduceMotion;
    this.dragging = false;
    this.raf = 0;
    this.lastTs = 0;
    this.anim = null;
    this.current = null;
    this.dataTex = Object.create(null);
    this.landRec = null;
    this.grid = { nlat: 64, nlon: 128 };
    this.wind = null;
    this.options = { light: true, grid: true, land: true, arrows: true };

    var gl = canvas.getContext('webgl2', {
      antialias: true, alpha: false, depth: true, stencil: false,
      preserveDrawingBuffer: false, powerPreference: 'high-performance'
    });
    if (!gl) throw new Error(pickLang('当前浏览器/显卡不支持 WebGL2', 'this browser/GPU does not support WebGL2'));
    this.gl = gl;
    this._initGL();
    this._initCamera();
    this._initMarkers();
    this._bindEvents();
    this.requestRender();
  }

  Globe.prototype._initGL = function () {
    var gl = this.gl;
    var vs = compile(gl, gl.VERTEX_SHADER, VS, pickLang('顶点着色器', 'vertex shader'));
    var fs = compile(gl, gl.FRAGMENT_SHADER, FS, pickLang('片元着色器', 'fragment shader'));
    var prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      throw new Error(pickLang('着色器链接失败：', 'shader link failed: ') + gl.getProgramInfoLog(prog));
    }
    gl.deleteShader(vs);
    gl.deleteShader(fs);
    this.prog = prog;

    this.loc = {
      aPos: gl.getAttribLocation(prog, 'aPos'),
      aUV: gl.getAttribLocation(prog, 'aUV'),
      uVP: gl.getUniformLocation(prog, 'uVP'),
      uData: gl.getUniformLocation(prog, 'uData'),
      uLut: gl.getUniformLocation(prog, 'uLut'),
      uLand: gl.getUniformLocation(prog, 'uLand'),
      uTexel: gl.getUniformLocation(prog, 'uTexel'),
      uNlat: gl.getUniformLocation(prog, 'uNlat'),
      uSun: gl.getUniformLocation(prog, 'uSun'),
      uCam: gl.getUniformLocation(prog, 'uCam'),
      uLight: gl.getUniformLocation(prog, 'uLight'),
      uGridOn: gl.getUniformLocation(prog, 'uGridOn'),
      uLandOn: gl.getUniformLocation(prog, 'uLandOn'),
      uHasData: gl.getUniformLocation(prog, 'uHasData'),
      uHasLand: gl.getUniformLocation(prog, 'uHasLand')
    };

    var mesh = buildSphere(128, 256);
    this.count = mesh.count;
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    var vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.pos, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(this.loc.aPos);
    gl.vertexAttribPointer(this.loc.aPos, 3, gl.FLOAT, false, 0, 0);
    var tbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, tbo);
    gl.bufferData(gl.ARRAY_BUFFER, mesh.uv, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(this.loc.aUV);
    gl.vertexAttribPointer(this.loc.aUV, 2, gl.FLOAT, false, 0, 0);
    var ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW);
    gl.bindVertexArray(null);

    // 1×1 占位纹理
    this.dummyTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.dummyTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, 1, 1, 0, gl.RED, gl.UNSIGNED_BYTE, new Uint8Array([0]));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);

    // LUT
    this.lutTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    var lut = global.EXO_CM.lut('thermal', 256);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this.lutName = 'thermal';
  };

  Globe.prototype._initCamera = function () {
    // 模型系默认视角：让日下点（-x）附近的昼面朝向观察者
    this.camDefaults = {
      model: { yaw: 206 * D2R, pitch: -32 * D2R, dist: 3.05 }
    };
    var d = this.camDefaults.model;
    this.cam = { yaw: d.yaw, pitch: d.pitch, dist: d.dist };
    this.eye = [0, 0, 3.05];
    this.vp = new Float32Array(16);
  };

  Globe.prototype._initMarkers = function () {
    this.markers = [];
    if (!this.markerHost) return;
    var host = this.markerHost;
    function make(cls, glyph, label, pm) {
      var el = document.createElement('div');
      el.className = 'marker ' + cls;
      var g = document.createElement('span');
      g.className = 'glyph';
      g.textContent = glyph;
      var t = document.createElement('span');
      t.className = 'mlabel';
      t.textContent = label;
      el.appendChild(g);
      el.appendChild(t);
      host.appendChild(el);
      return { el: el, pm: pm };
    }
    var sunM = make('sun', '☀', tr('globe.sun', '日下点'), [-1, 0, 0]);
    var antiM = make('anti', '✕', tr('globe.anti', '对侧点'), [1, 0, 0]);
    this.markers.push(sunM, antiM);
    this.markerLabels = {
      sun: sunM.el.querySelector('.mlabel'),
      anti: antiM.el.querySelector('.mlabel')
    };
  };

  /* 语言切换后刷新标记文字（app.js 的 onLangChange 调用） */
  Globe.prototype.refreshMarkerLabels = function () {
    if (!this.markerLabels) return;
    if (this.markerLabels.sun) this.markerLabels.sun.textContent = tr('globe.sun', '日下点');
    if (this.markerLabels.anti) this.markerLabels.anti.textContent = tr('globe.anti', '对侧点');
  };

  Globe.prototype._bindEvents = function () {
    var self = this;
    var cv = this.canvas;

    cv.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      cv.setPointerCapture && cv.setPointerCapture(e.pointerId);
      self.dragging = true;
      self.autoRotate = false;
      self.anim = null;
      self.lastPtr = { x: e.clientX, y: e.clientY };
    });
    cv.addEventListener('pointermove', function (e) {
      if (!self.dragging) return;
      var dx = e.clientX - self.lastPtr.x;
      var dy = e.clientY - self.lastPtr.y;
      self.lastPtr = { x: e.clientX, y: e.clientY };
      self.cam.yaw -= dx * 0.0062;
      self.cam.pitch = clamp(self.cam.pitch + dy * 0.0055, -1.35, 1.35);
      self.requestRender();
    });
    function stopDrag(e) {
      if (!self.dragging) return;
      self.dragging = false;
      try { cv.releasePointerCapture && cv.releasePointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      self.requestRender();
    }
    cv.addEventListener('pointerup', stopDrag);
    cv.addEventListener('pointercancel', stopDrag);
    cv.addEventListener('wheel', function (e) {
      e.preventDefault();
      self.autoRotate = false;
      self.cam.dist = clamp(self.cam.dist * Math.exp(e.deltaY * 0.0011), 1.75, 6.5);
      self.requestRender();
    }, { passive: false });
    cv.addEventListener('dblclick', function () { self.resetView(); });
    cv.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  };

  /* ---- 数据 ---- */

  Globe.prototype.setGrid = function (grid) {
    if (!grid || !grid.nlat || !grid.nlon) return;
    this.grid = { nlat: grid.nlat, nlon: grid.nlon };
    this.requestRender();
  };

  Globe.prototype.setData = function (key, data, nlat, nlon, lo, hi) {
    var gl = this.gl;
    var n = nlat * nlon;
    if (!data || data.length < n) return false;
    if (!this._tmp || this._tmp.length < n) this._tmp = new Uint8Array(n);
    var bytes = this._tmp;
    var span = (hi - lo) || 1;
    for (var i = 0; i < n; i++) {
      var t = (data[i] - lo) / span;
      if (!(t > 0)) t = 0; else if (t > 1) t = 1;
      bytes[i] = (t * 255 + 0.5) | 0;
    }
    var rec = this.dataTex[key];
    if (!rec || rec.nlat !== nlat || rec.nlon !== nlon) {
      if (rec) gl.deleteTexture(rec.tex);
      rec = { tex: gl.createTexture(), nlat: nlat, nlon: nlon };
      this.dataTex[key] = rec;
      gl.bindTexture(gl.TEXTURE_2D, rec.tex);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    }
    gl.bindTexture(gl.TEXTURE_2D, rec.tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, nlon, nlat, 0, gl.RED, gl.UNSIGNED_BYTE, bytes);
    this.grid = { nlat: nlat, nlon: nlon };
    return true;
  };

  Globe.prototype.setLand = function (data, nlat, nlon) {
    var gl = this.gl;
    var n = nlat * nlon;
    if (!data || data.length < n) return false;
    var bytes = new Uint8Array(n);
    for (var i = 0; i < n; i++) bytes[i] = data[i] > 0.5 ? 255 : 0;
    if (!this.landRec) {
      this.landRec = { tex: gl.createTexture(), nlat: nlat, nlon: nlon };
      gl.bindTexture(gl.TEXTURE_2D, this.landRec.tex);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    }
    var rec = this.landRec;
    rec.nlat = nlat; rec.nlon = nlon;
    gl.bindTexture(gl.TEXTURE_2D, rec.tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, nlon, nlat, 0, gl.RED, gl.UNSIGNED_BYTE, bytes);
    return true;
  };

  Globe.prototype.setColormap = function (name) {
    if (!name || name === this.lutName) return;
    var gl = this.gl;
    this.lutName = name;
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
      global.EXO_CM.lut(name, 256));
    this.requestRender();
  };

  Globe.prototype.setLayer = function (key) {
    this.current = key;
    this.requestRender();
  };

  Globe.prototype.setWind = function (wind) {
    this.wind = wind;
    this.requestRender();
  };

  Globe.prototype.setOptions = function (o) {
    var changed = false;
    for (var k in o) {
      if (Object.prototype.hasOwnProperty.call(o, k) && this.options[k] !== o[k]) {
        this.options[k] = o[k];
        changed = true;
      }
    }
    if (changed) this.requestRender();
  };

  Globe.prototype.clearData = function () {
    var gl = this.gl;
    for (var k in this.dataTex) {
      if (this.dataTex[k]) gl.deleteTexture(this.dataTex[k].tex);
    }
    this.dataTex = Object.create(null);
    this.current = null;
    this.wind = null;
    if (this.landRec) { gl.deleteTexture(this.landRec.tex); this.landRec = null; }
    this.requestRender();
  };

  Globe.prototype.resetView = function () {
    var d = this.camDefaults.model;
    this.autoRotate = false;
    this.animateTo({ yaw: d.yaw, pitch: d.pitch, dist: d.dist }, this.reduceMotion ? 0 : 480);
  };

  Globe.prototype.animateTo = function (target, ms) {
    if (!ms || this.reduceMotion) {
      if (target.yaw !== undefined) this.cam.yaw = target.yaw;
      if (target.pitch !== undefined) this.cam.pitch = target.pitch;
      if (target.dist !== undefined) this.cam.dist = target.dist;
      this.requestRender();
      return;
    }
    var from = { yaw: this.cam.yaw, pitch: this.cam.pitch, dist: this.cam.dist };
    var dy = target.yaw - from.yaw;
    while (dy > Math.PI) dy -= Math.PI * 2;
    while (dy < -Math.PI) dy += Math.PI * 2;
    this.anim = { from: from, to: target, dy: dy, t: 0, dur: ms / 1000 };
    this.requestRender();
  };

  /* ---- 渲染 ---- */

  Globe.prototype.requestRender = function () {
    if (this.raf) return;
    var self = this;
    this.raf = global.requestAnimationFrame(function (ts) { self._frame(ts); });
  };

  Globe.prototype._frame = function (ts) {
    this.raf = 0;
    var dt = this.lastTs ? Math.min(0.12, (ts - this.lastTs) / 1000) : 0.016;
    this.lastTs = ts;
    var animating = false;
    if (this.anim) {
      var a = this.anim;
      a.t += dt;
      var f = Math.min(1, a.t / a.dur);
      var e = f < 0.5 ? 4 * f * f * f : 1 - Math.pow(-2 * f + 2, 3) / 2;
      this.cam.yaw = a.from.yaw + a.dy * e;
      this.cam.pitch = a.from.pitch + (a.to.pitch - a.from.pitch) * e;
      this.cam.dist = a.from.dist + (a.to.dist - a.from.dist) * e;
      if (f >= 1) this.anim = null;
      animating = true;
    } else if (this.autoRotate && !this.dragging) {
      this.cam.yaw += 0.085 * dt;
      animating = true;
    }
    this.resizeIfNeeded();
    this.render();
    if (animating) {
      this.requestRender();
    } else {
      this.lastTs = 0;
    }
  };

  Globe.prototype.resizeIfNeeded = function () {
    var dpr = Math.min(2, global.devicePixelRatio || 1);
    var w = Math.max(1, this.canvas.clientWidth);
    var h = Math.max(1, this.canvas.clientHeight);
    var W = Math.round(w * dpr), H = Math.round(h * dpr);
    if (this.canvas.width !== W || this.canvas.height !== H) {
      this.canvas.width = W;
      this.canvas.height = H;
      return true;
    }
    return false;
  };

  Globe.prototype.worldVec = function (pm) {
    return [pm[0], pm[1], pm[2]];
  };

  Globe.prototype.updateCamera = function () {
    var aspect = Math.max(0.2, (this.canvas.clientWidth || 1) / (this.canvas.clientHeight || 1));
    var cp = Math.cos(this.cam.pitch), sp = Math.sin(this.cam.pitch);
    var d = this.cam.dist;
    var eye = [d * cp * Math.cos(this.cam.yaw), d * cp * Math.sin(this.cam.yaw), d * sp];
    this.eye = eye;
    // 相机基（与 lookAt 一致），拾取射线要用
    var zl = Math.hypot(eye[0], eye[1], eye[2]) || 1;
    var zx = eye[0] / zl, zy = eye[1] / zl, zz = eye[2] / zl;
    var xx = -zy, xy = zx, xz = 0;                     // cross((0,0,1), z)
    var xl = Math.hypot(xx, xy, xz) || 1;
    xx /= xl; xy /= xl; xz /= xl;
    var yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    this.basis = { r: [xx, xy, xz], u: [yx, yy, yz], f: [-zx, -zy, -zz] };
    this.aspect = aspect;
    var proj = perspective(45 * D2R, aspect, 0.05, 24);
    var view = lookAt(eye, [0, 0, 0], [0, 0, 1]);
    this.vp = mul4(proj, view);
  };

  /* 屏幕坐标（CSS px，相对画布左上角）→ 球面点；未命中返回 null */
  Globe.prototype.pick = function (px, py) {
    if (!this.basis) return null;
    var W = this.canvas.clientWidth || 1, H = this.canvas.clientHeight || 1;
    var ndcX = (px / W) * 2 - 1;
    var ndcY = 1 - (py / H) * 2;
    var tanHalf = Math.tan(45 * D2R / 2);
    var b = this.basis;
    var sx = ndcX * tanHalf * this.aspect, sy = ndcY * tanHalf;
    var dx = b.r[0] * sx + b.u[0] * sy + b.f[0];
    var dy = b.r[1] * sx + b.u[1] * sy + b.f[1];
    var dz = b.r[2] * sx + b.u[2] * sy + b.f[2];
    var dl = Math.hypot(dx, dy, dz) || 1;
    dx /= dl; dy /= dl; dz /= dl;
    var o = this.eye;
    var bb = o[0] * dx + o[1] * dy + o[2] * dz;
    var cc = o[0] * o[0] + o[1] * o[1] + o[2] * o[2] - 1;
    var disc = bb * bb - cc;
    if (disc <= 0) return null;
    var t = -bb - Math.sqrt(disc);
    if (!(t > 0)) return null;
    var p = [o[0] + t * dx, o[1] + t * dy, o[2] + t * dz];
    var pl = Math.hypot(p[0], p[1], p[2]) || 1;
    return { world: [p[0] / pl, p[1] / pl, p[2] / pl], screen: { x: px, y: py } };
  };

  /* 世界（显示）坐标 → 显示系经纬度（度，lon 0..360） */
  Globe.prototype.latLonOf = function (p) {
    var lat = Math.asin(clamp(p[2], -1, 1)) / D2R;
    var lon = Math.atan2(p[1], p[0]) / D2R;
    if (lon < 0) lon += 360;
    if (lon >= 360) lon -= 360;
    return { lat: lat, lon: lon };
  };

  /* 显示系经纬度 → 世界（显示）坐标 */
  Globe.prototype.worldOf = function (latDeg, lonDeg) {
    var la = latDeg * D2R, lo = lonDeg * D2R;
    var cl = Math.cos(la);
    return [cl * Math.cos(lo), cl * Math.sin(lo), Math.sin(la)];
  };

  Globe.prototype.project = function (p) {
    var m = this.vp;
    var cw = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    if (cw <= 1e-5) return null;
    var cx = m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12];
    var cy = m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13];
    var W = this.canvas.clientWidth || 1, H = this.canvas.clientHeight || 1;
    return { x: (cx / cw * 0.5 + 0.5) * W, y: (0.5 - cy / cw * 0.5) * H, w: cw };
  };

  Globe.prototype.render = function () {
    var gl = this.gl;
    if (!gl || gl.isContextLost()) return;
    this.updateCamera();
    var W = this.canvas.width, H = this.canvas.height;
    gl.viewport(0, 0, W, H);
    gl.clearColor(0.027, 0.039, 0.067, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.disable(gl.BLEND);
    gl.disable(gl.CULL_FACE);

    var L = this.loc;
    gl.useProgram(this.prog);
    gl.bindVertexArray(this.vao);
    gl.uniformMatrix4fv(L.uVP, false, this.vp);
    gl.uniform1f(L.uNlat, this.grid.nlat);
    gl.uniform2f(L.uTexel, 1 / this.grid.nlon, 1 / this.grid.nlat);
    gl.uniform3fv(L.uSun, [-1, 0, 0]);
    gl.uniform3fv(L.uCam, this.eye);
    gl.uniform1f(L.uLight, this.options.light ? 1 : 0);
    gl.uniform1f(L.uGridOn, this.options.grid ? 1 : 0);
    gl.uniform1f(L.uLandOn, this.options.land ? 1 : 0);

    var rec = this.current ? this.dataTex[this.current] : null;
    gl.uniform1f(L.uHasData, rec ? 1 : 0);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, rec ? rec.tex : this.dummyTex);
    gl.uniform1i(L.uData, 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.uniform1i(L.uLut, 1);
    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, this.landRec ? this.landRec.tex : this.dummyTex);
    gl.uniform1i(L.uLand, 2);
    gl.uniform1f(L.uHasLand, this.landRec ? 1 : 0);

    gl.drawElements(gl.TRIANGLES, this.count, gl.UNSIGNED_SHORT, 0);
    gl.bindVertexArray(null);

    this.drawWind();
    this.updateMarkers();
  };

  /* 屏幕中心像素（自检用：「不是白屏/黑屏」） */
  Globe.prototype.probeCenter = function () {
    try {
      this.render();
      var gl = this.gl;
      var W = this.canvas.width, H = this.canvas.height;
      var px = new Uint8Array(4);
      gl.readPixels((W / 2) | 0, (H / 2) | 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
      return [px[0], px[1], px[2], px[3]];
    } catch (e) {
      return null;
    }
  };

  Globe.prototype.updateMarkers = function () {
    if (!this.markers || !this.markers.length) return;
    var eye = this.eye;
    for (var i = 0; i < this.markers.length; i++) {
      var m = this.markers[i];
      var pw = this.worldVec(m.pm);
      var face = pw[0] * eye[0] + pw[1] * eye[1] + pw[2] * eye[2];
      if (face <= 1.0) {
        if (m.el.classList.contains('on')) m.el.classList.remove('on');
        continue;
      }
      var s = this.project(pw);
      if (!s) { m.el.classList.remove('on'); continue; }
      m.el.style.transform = 'translate(' + s.x.toFixed(1) + 'px,' + s.y.toFixed(1) + 'px) translate(-50%,-50%)';
      if (!m.el.classList.contains('on')) m.el.classList.add('on');
    }
  };

  /* ---- 风矢（canvas2D 叠加，天然遮挡剔除 + 可控线宽） ---- */

  Globe.prototype.drawWind = function () {
    var ctx = this.windCtx;
    if (!ctx) return;
    var W = this.canvas.clientWidth || 1;
    var H = this.canvas.clientHeight || 1;
    var dpr = Math.min(2, global.devicePixelRatio || 1);
    var wc = this.windCanvas;
    var bw = Math.round(W * dpr), bh = Math.round(H * dpr);
    if (wc.width !== bw || wc.height !== bh) { wc.width = bw; wc.height = bh; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var wind = this.wind;
    if (!this.options.arrows || !wind || !wind.u || !wind.v) return;

    var eye = this.eye;
    var eyeLen = Math.hypot(eye[0], eye[1], eye[2]);
    var stride = Math.max(2, Math.round(wind.nlat / 20));
    var jStart = Math.floor((stride - 1) / 2);
    var lo = wind.lo, hi = wind.hi;
    var span = (hi - lo) || 1;
    var buckets = [];
    var dark = [];
    var i, j, k;

    for (j = jStart; j < wind.nlat; j += stride) {
      var lat = wind.lat[j] * D2R;
      var cl = Math.cos(lat), sl = Math.sin(lat);
      for (i = 0; i < wind.nlon; i += stride) {
        var lon = wind.lon[i] * D2R;
        var clon = Math.cos(lon), slon = Math.sin(lon);
        var px = cl * clon, py = cl * slon, pz = sl;
        var wx = px, wy = py, wz = pz;
        var face = wx * eye[0] + wy * eye[1] + wz * eye[2];
        if (face <= 1.004) continue;
        var idx = j * wind.nlon + i;
        var u = wind.u[idx], v = wind.v[idx];
        if (!isFinite(u) || !isFinite(v)) continue;
        var sp = wind.spd ? wind.spd[idx] : Math.hypot(u, v);
        if (!isFinite(sp)) sp = Math.hypot(u, v);
        // 模型系本地东/北单位向量
        var ex = -slon, ey = clon, ez = 0;
        var nx = -sl * clon, ny = -sl * slon, nz = cl;
        var dx = u * ex + v * nx, dy = u * ey + v * ny, dz = u * ez + v * nz;
        var dl = Math.hypot(dx, dy, dz);
        if (!(dl > 1e-5)) continue;
        dx /= dl; dy /= dl; dz /= dl;
        var ddx = dx, ddy = dy, ddz = dz;
        var t2 = 0.105;
        var qx = wx + ddx * t2, qy = wy + ddy * t2, qz = wz + ddz * t2;
        var ql = Math.hypot(qx, qy, qz) || 1;
        qx /= ql; qy /= ql; qz /= ql;
        var s0 = this.project([wx, wy, wz]);
        var s1 = this.project([qx, qy, qz]);
        if (!s0 || !s1) continue;
        var sx = s1.x - s0.x, sy = s1.y - s0.y;
        var L0 = Math.hypot(sx, sy);
        if (L0 < 0.35) continue;
        sx /= L0; sy /= L0;
        var tn = clamp((sp - lo) / span, 0, 1);
        var len = 6 + 16 * tn;
        var vis = clamp((face - 1) / Math.max(1e-3, eyeLen - 1), 0, 1);
        var alpha = 0.20 + 0.78 * vis;
        var hx = s0.x + sx * len, hy = s0.y + sy * len;
        var back = 4.4 + 3.6 * tn;
        var ang = 0.44;
        var ca = Math.cos(ang), sa = Math.sin(ang);
        var b1x = hx - (sx * ca - sy * sa) * back;
        var b1y = hy - (sx * sa + sy * ca) * back;
        var b2x = hx - (sx * ca + sy * sa) * back;
        var b2y = hy - (-sx * sa + sy * ca) * back;
        dark.push(s0.x, s0.y, hx, hy, hx, hy, b1x, b1y, hx, hy, b2x, b2y);
        var bi = Math.min(15, Math.max(0, Math.round(tn * 15)));
        if (!buckets[bi]) buckets[bi] = [];
        buckets[bi].push(s0.x, s0.y, hx, hy, hx, hy, b1x, b1y, hx, hy, b2x, b2y, alpha);
      }
    }

    // 先画一层暗描边（一次 stroke），再按速度分桶上色（≤16 次 stroke）
    ctx.lineCap = 'round';
    ctx.lineWidth = 3.0;
    ctx.strokeStyle = 'rgba(0,0,0,0.34)';
    ctx.beginPath();
    for (k = 0; k < dark.length; k += 12) {
      ctx.moveTo(dark[k], dark[k + 1]); ctx.lineTo(dark[k + 2], dark[k + 3]);
      ctx.moveTo(dark[k + 4], dark[k + 5]); ctx.lineTo(dark[k + 6], dark[k + 7]);
      ctx.moveTo(dark[k + 8], dark[k + 9]); ctx.lineTo(dark[k + 10], dark[k + 11]);
    }
    ctx.stroke();

    ctx.lineWidth = 1.5;
    for (var b = 0; b < buckets.length; b++) {
      var arr = buckets[b];
      if (!arr) continue;
      ctx.strokeStyle = global.EXO_CM.rgb('wind', (b + 0.5) / 16);
      ctx.beginPath();
      for (k = 0; k < arr.length; k += 13) {
        ctx.moveTo(arr[k], arr[k + 1]); ctx.lineTo(arr[k + 2], arr[k + 3]);
        ctx.moveTo(arr[k + 4], arr[k + 5]); ctx.lineTo(arr[k + 6], arr[k + 7]);
        ctx.moveTo(arr[k + 8], arr[k + 9]); ctx.lineTo(arr[k + 10], arr[k + 11]);
      }
      ctx.globalAlpha = 0.92;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  };

  Globe.prototype.debug = function () {
    return {
      ok: !!this.gl,
      dims: this.grid,
      current: this.current,
      layers: Object.keys(this.dataTex),
      land: !!this.landRec,
      options: JSON.parse(JSON.stringify(this.options)),
      cam: { yaw: this.cam.yaw / D2R, pitch: this.cam.pitch / D2R, dist: this.cam.dist },
      autoRotate: this.autoRotate
    };
  };

  /* ---------------- 工厂 ---------------- */

  global.ExoGlobe = {
    create: function (canvas, windCanvas, markerHost, opts) {
      return new Globe(canvas, windCanvas, markerHost, opts);
    }
  };
})(window);
