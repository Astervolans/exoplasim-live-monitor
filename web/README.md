# web/ — 行星模拟实时监控前端（零依赖）

`index.html` + `app.js` + `i18n.js` + `globe.js` + `colormap.js` + `style.css`。桌面与手机（竖屏/横屏）自适应，支持触控拖动、双指缩放、点按读数。
没有构建步骤，不引第三方库/CDN。数据来自 collector 的 `/api/runs`、`/api/state`、`/api/frame`、`/api/land`（见 `../../CONTRACT.md`）。

本地联调（用 fixture 回放）：

```bash
python monitor/test/mock_server.py --port 8790
# 浏览器打开 http://127.0.0.1:8790/
```

自检：`http://127.0.0.1:8790/?selftest=1`（当前 23 项，含 2 项语言检查 + 指针/触摸点按 + 陆地掩膜 + 无横向溢出）。

## Languages / 语言

**切换**：顶栏左上角 `中文 / EN` 分段按钮（`#langSeg`，class `lang-seg`）。切换立即整体生效——
静态文案（`data-i18n` 标注）与动态区（图层、进度、参数、序列图图例、悬停浮层、自检面板）一起重渲染，无需刷新页面。

**记忆与深链**：

| 来源 | 说明 |
| --- | --- |
| `?lang=zh` / `?lang=en` | 深链，优先级最高（便于截图/分享），可与其它参数组合，如 `?lang=en&layer=sit&hover=10,170`、`?lang=en&selftest=1` |
| `localStorage["exoplasim.monitor.lang"]` | 用户上次选择，取值 `zh` / `en` |
| 默认 | `zh`（中文） |

切换时同步 `<html lang>`（`zh-CN` / `en`）、`document.documentElement.dataset.lang` 与 `document.title`。

**字典与 API**：`i18n.js` 暴露 `window.EXO_I18N`：

- `EXO_I18N.t(key, vars)` — 当前语言文案，支持 `{name}` 占位符；缺失键回退中文、再回退 key 本身。
- `EXO_I18N.setLang(lang)` / `getLang()` / `EXO_I18N.lang` — 切换 / 读取语言（`setLang` 会写 localStorage、更新 `<html lang>`、翻译静态区并通知订阅者）。
- `EXO_I18N.onChange(fn)` — 语言变化回调；`app.js` 用它注册 `onLangChange()` 重渲染动态区。
- `EXO_I18N.applyStatic(root)` — 翻译静态 DOM：`data-i18n`（textContent）、`data-i18n-title`、`data-i18n-aria-label`、`data-i18n-placeholder`。
- `EXO_I18N.missingKeys()` — 各语言键对齐检查（自检项 “i18n 字典完整” 用它）。

**后端字符串的处理**（后端不改、仍返回中文）：

- `layers[*].name` → 前端按图层 key 用 `layer.<key>` 生成；
- `run.status` / `run.status_text` → 按枚举 `status.*` 生成；中文模式仍沿用 `status_text`（信息更细），英文模式只用枚举；
- `run.sim_date` → 由 `run.year_no` + `run.sim_day`（缺失时用 `run.step` / `run.steps_per_year` 折算，360 天 / 12×30 月）重新拼；
- `run.msg`（少见的后端诊断文本）→ 原样显示，英文模式加 `backend: ` 前缀，中文模式不加。

**新增一种语言**：

1. 在 `i18n.js` 的 `DICTS` 里复制一份同键字典（如 `ja: { ... }`）；
2. 把语言码加进 `LANGS`，并在 `norm()` 里登记别名（如 `ja` / `jp`）；
3. 在 `index.html` 的 `#langSeg` 里加一个按钮：`<button type="button" data-lang="ja" aria-pressed="false">日本語</button>`；
4. 跑 `?selftest=1`，若 “i18n 字典完整” 报缺键，按提示补齐即可。

其余代码（`app.js` 的 `t()` 调用、静态属性标注）不需要改动。
