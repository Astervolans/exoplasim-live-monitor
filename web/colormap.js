/* colormap.js — 色标（LUT）：GL 纹理 + 2D 用色 + 色标条 CSS。零依赖。 */
(function (global) {
  'use strict';

  /* 停靠点：[位置 0..1, [r,g,b]]，线性插值 */
  var STOPS = {
    /* 温度：冷蓝 → 青 → 绿 → 黄 → 橙红 */
    thermal: [
      [0.00, [26, 32, 86]],
      [0.10, [42, 62, 152]],
      [0.22, [44, 116, 190]],
      [0.34, [52, 164, 172]],
      [0.46, [98, 196, 130]],
      [0.58, [186, 214, 92]],
      [0.70, [240, 200, 74]],
      [0.82, [242, 148, 60]],
      [0.92, [222, 84, 58]],
      [1.00, [148, 30, 46]]
    ],
    /* 降水：深夜蓝 → 青 → 绿 → 黄 → 红 */
    precip: [
      [0.00, [12, 20, 46]],
      [0.06, [24, 46, 104]],
      [0.16, [30, 88, 158]],
      [0.30, [30, 142, 186]],
      [0.44, [44, 186, 160]],
      [0.58, [116, 206, 104]],
      [0.72, [214, 218, 78]],
      [0.84, [240, 160, 56]],
      [0.93, [232, 84, 62]],
      [1.00, [226, 46, 108]]
    ],
    /* 风速：紫 → 蓝 → 青 → 绿 → 黄 → 橙 */
    wind: [
      [0.00, [38, 20, 88]],
      [0.14, [44, 54, 148]],
      [0.30, [34, 112, 180]],
      [0.46, [32, 168, 178]],
      [0.62, [72, 196, 130]],
      [0.76, [168, 214, 88]],
      [0.88, [240, 200, 74]],
      [1.00, [238, 118, 52]]
    ],
    /* 海冰：暗蓝 → 冰蓝 → 白 */
    ice: [
      [0.00, [12, 26, 52]],
      [0.22, [22, 58, 104]],
      [0.46, [52, 118, 172]],
      [0.68, [116, 180, 214]],
      [0.86, [190, 224, 242]],
      [1.00, [248, 252, 255]]
    ],
    /* 海冰厚度：深紫 → 蓝 → 冰蓝 → 白（0–9 m） */
    thick: [
      [0.00, [16, 18, 40]],
      [0.18, [40, 46, 96]],
      [0.38, [44, 96, 156]],
      [0.58, [70, 150, 196]],
      [0.78, [140, 200, 226]],
      [0.92, [208, 236, 248]],
      [1.00, [252, 254, 255]]
    ],
    /* 云量：近黑 → 灰 → 白 */
    cloud: [
      [0.00, [10, 13, 20]],
      [0.30, [46, 54, 70]],
      [0.56, [96, 108, 128]],
      [0.76, [156, 168, 186]],
      [0.90, [212, 220, 232]],
      [1.00, [252, 253, 255]]
    ]
  };

  var CACHE = Object.create(null);

  function clamp01(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }

  function stopsFor(name) { return STOPS[name] || STOPS.thermal; }

  /* n 级 RGBA8 LUT（GL 纹理用） */
  function lut(name, n) {
    n = n || 256;
    var key = (name || 'thermal') + '@' + n;
    if (CACHE[key]) return CACHE[key];
    var stops = stopsFor(name);
    var out = new Uint8Array(n * 4);
    var k = 0;
    for (var i = 0; i < n; i++) {
      var t = n === 1 ? 0 : i / (n - 1);
      while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
      var a = stops[k], b = stops[k + 1];
      var span = b[0] - a[0];
      var f = span > 1e-9 ? (t - a[0]) / span : 0;
      f = clamp01(f);
      out[i * 4 + 0] = Math.round(a[1][0] + (b[1][0] - a[1][0]) * f);
      out[i * 4 + 1] = Math.round(a[1][1] + (b[1][1] - a[1][1]) * f);
      out[i * 4 + 2] = Math.round(a[1][2] + (b[1][2] - a[1][2]) * f);
      out[i * 4 + 3] = 255;
    }
    CACHE[key] = out;
    return out;
  }

  /* rgb 字符串，t ∈ [0,1] */
  function rgb(name, t) {
    var l = lut(name, 256);
    var i = Math.round(clamp01(t) * 255) * 4;
    return 'rgb(' + l[i] + ',' + l[i + 1] + ',' + l[i + 2] + ')';
  }

  function rgba(name, t, a) {
    var l = lut(name, 256);
    var i = Math.round(clamp01(t) * 255) * 4;
    return 'rgba(' + l[i] + ',' + l[i + 1] + ',' + l[i + 2] + ',' + a + ')';
  }

  /* 色标条背景 CSS */
  function gradientCss(name, steps) {
    steps = steps || 16;
    var parts = [];
    for (var i = 0; i < steps; i++) {
      var t = i / (steps - 1);
      parts.push(rgb(name, t) + ' ' + (t * 100).toFixed(1) + '%');
    }
    return 'linear-gradient(90deg, ' + parts.join(', ') + ')';
  }

  /* 名称列表（调试/自检用） */
  function names() { return Object.keys(STOPS); }

  global.EXO_CM = { lut: lut, rgb: rgb, rgba: rgba, gradientCss: gradientCss, names: names };
})(window);
