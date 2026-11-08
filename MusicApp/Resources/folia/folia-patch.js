/* Folia iOS 小屏适配补丁（host.html 注入，不修改主 bundle）
 * ---------------------------------------------------------------
 * 解决的问题（iPhone 横屏 844x390 视口下的桌面设计适配）：
 * 1. 歌词网格 justify-start 导致歌词行靠左，不在屏幕正中 —— 改为居中
 * 2. 模式标签条 14 个标签总宽 ~1000px 溢出屏幕 —— 收缩字号/间距，全部可见
 * 3. 控件（控制条/模式条）无法隐藏，不能沉浸式 —— 轻点舞台空白切换隐藏/显示
 *
 * 实现方式：MutationObserver 监听 DOM 变化持续应用补丁（React 重渲染后自动恢复），
 * 避免与 React 的渲染周期竞争。
 */
(function () {
  'use strict';
  if (window.__foliaPatchLoaded) return;
  window.__foliaPatchLoaded = true;

  /* 背景让位压制 CSS：__foliaBgOff 以 !important 压过 React 重渲染重设的 inline style。
   * 关键价值：React 每次重渲染（time 消息驱动歌词更新等）会用主题状态重写背景层
   * 的 inline background，若只靠 JS 每次重设 transparent 会与渲染竞态（"稍后复活"）；
   * class + !important 让 React 的 inline 设置永远不生效，直到显式移除 class 恢复。 */
  (function injectStyle() {
    try {
      var s = document.createElement('style');
      // ★ 2026-11-03：__foliaCanvasOff 只做透明，**不再** pointer-events:none。
      //   canvas 是铺满全屏的可视化层，原本承担「点画面不误入沉浸」的命中职责
      //   （见 setImmersive/setupImmersive 的 CANVAS 分支）。加了 pointer-events:none 后
      //   点击穿透到舞台 → document 捕获监听把任何空白点击当成「切换沉浸」，
      //   导致设置自定义背景后点一下就把控件条/模式条/原生顶栏全部隐藏（"按键不显示"）。
      s.textContent = '.__foliaBgOff{background:transparent !important;background-color:transparent !important;}.__foliaCanvasOff{opacity:0 !important;}';
      (document.head || document.documentElement).appendChild(s);
    } catch (e) {}
  })();

  var immersive = false;
  var controlBar = null;
  var modeBar = null;
  var lastPatch = 0;

  /* ---------- 歌词网格：justify-start -> center（歌词行水平居中） ---------- */
  function patchLyricGrid() {
    document.querySelectorAll('.flex.flex-wrap.w-full.max-w-6xl').forEach(function (grid) {
      if (grid.style.justifyContent !== 'center') {
        grid.style.justifyContent = 'center';
      }
      // 行内项也居中（framer-motion 的歌词行是 inline-block，居中是父级 flex 行对齐）
      if (grid.style.justifyItems !== 'center') {
        grid.style.justifyItems = 'center';
      }
    });
  }

  /* ---------- 模式标签条：收缩标签，14 个全部适配屏幕 ---------- */
  function patchModeBar() {
    if (!modeBar) return;
    modeBar.style.gap = '4px';
    modeBar.style.padding = '6px 8px';
    modeBar.querySelectorAll('button').forEach(function (btn) {
      if (btn.style.fontSize !== '11px') btn.style.fontSize = '11px';
      if (btn.style.padding !== '4px 8px') btn.style.padding = '4px 8px';
      // ★ 用户主动切换 Folia 模式（点击模式标签）→ 立即解除自定义背景压制，
      //   让模式背景显示。真实 mode 消息走 bundle D 回调 t(e)+LT postMessage
      //   （网页→原生），不进 __foliaHost.receive——patch 的 receive mode 分支
      //   拦不到真实点击；原生 onModeChange 往返是唯一解除通道但存在链路风险。
      //   这里在 DOM 层 capture 监听：用户点标签瞬间网页本地就让位，不等原生
      //   往返；React 重渲染重建按钮后由 MutationObserver→patchAll→patchModeBar
      //   重新绑定（__foliaModeBound 标记防重复）。
      if (!btn.__foliaModeBound) {
        btn.__foliaModeBound = true;
        btn.addEventListener('click', function () {
          if (bgActive) {
            bgActive = false;
            applyBackgroundPatch();
          }
        }, true);
      }
    });
    // ★ 更强兜底：modeBar 级事件委托（capture）。React 重渲染会重建按钮 DOM，
    //   按钮级监听存在丢失窗口（patchAll 补绑前用户已点击新按钮则失效）；
    //   modeBar 容器本身不重建，委托一次绑定永久有效——任何模式按钮的点击
    //   都会先经此捕获并即时解除压制，与按钮级监听双保险。
    if (!modeBar.__foliaModeBarBound) {
      modeBar.__foliaModeBarBound = true;
      modeBar.addEventListener('click', function (e) {
        var t = e.target;
        if (t && t.closest && t.closest('button')) {
          if (bgActive) {
            bgActive = false;
            applyBackgroundPatch();
          }
        }
      }, true);
    }
  }

  /* ---------- 沉浸模式：轻点舞台空白区隐藏/显示控件 ---------- */
  var lastNotifiedVisible = null; // 已通知原生的可见状态（避免动画期间重复通知）

  function applyImmersive() {
    // 缓存节点被 React 重建后先重新定位，否则改的是脱离文档的旧节点，
    // 新控件条保持 React 自身的隐藏态 → 「设置背景后部分按键不显示」
    if (!barsAlive()) {
      controlBar = null;
      modeBar = null;
      locateBars();
    }
    var visible = !immersive;
    [controlBar, modeBar].forEach(function (bar) {
      if (!bar) return;
      if (bar.style.transition !== 'opacity 0.22s ease') bar.style.transition = 'opacity 0.22s ease';
      var op = immersive ? '0' : '1';
      var pe = immersive ? 'none' : 'auto';
      if (bar.style.opacity !== op) bar.style.opacity = op;
      if (bar.style.pointerEvents !== pe) bar.style.pointerEvents = pe;
    });
    // 仅在沉浸状态真正变化时通知原生侧同步顶栏（SwiftUI 顶栏与网页控件统一沉浸）
    if (lastNotifiedVisible !== visible) {
      lastNotifiedVisible = visible;
      try {
        if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.folia) {
          window.webkit.messageHandlers.folia.postMessage({ type: 'immersive', payload: { visible: visible } });
        }
      } catch (e) {}
    }
  }

  /* 原生侧主动切换沉浸（顶栏按钮触发）：与网页内轻点切换共用同一状态机 */
  window.__foliaPatch = {
    setImmersive: function (hidden) {
      var target = !!hidden;
      if (target === immersive) return;
      immersive = target;
      applyImmersive();
    },
    isImmersive: function () { return immersive; },
    /// 确保控件条/模式条可见（切模式后由原生调用）：
    /// 无论此前是否被误置为沉浸，强制退出沉浸并把两条重新显示 + 通知原生显示顶栏。
    /// 这是"选模式后模式条/歌曲一起消失"的最后一道兜底——即使某条 DOM 路径把
    /// immersive 打开了，用户选完模式也一定能看到控件。
    ensureChromeVisible: function () {
      if (immersive) {
        immersive = false;
      }
      // 引用失效时 applyImmersive 内部会自动重新 locateBars
      applyImmersive();
      // React 异步重建控件条：再补两次
      var self = this;
      setTimeout(function () { applyImmersive(); }, 120);
      setTimeout(function () { applyImmersive(); }, 400);
    },
    /// 切模式后整体重跑一遍补丁（背景让位/主题/模式条样式/沉浸可见性）
    resync: function () {
      try { applyBackgroundPatch(); } catch (e) {}
      try { patchAll(); } catch (e) {}
      try { window.__foliaPatch.ensureChromeVisible(); } catch (e) {}
    },
  };

  function setupImmersive() {
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    if (!stage || stage.__foliaTapBound) return;
    stage.__foliaTapBound = true;
    stage.addEventListener('click', function (e) {
      // 点击控件（按钮/进度条等可交互元素）不触发沉浸切换
      if (e.target.closest && e.target.closest('button')) return;
      if (e.target.closest && e.target.closest('[role="slider"], [role="progressbar"]')) return;
      // 可视化器 canvas 铺满全屏：非沉浸时点 canvas 不触发（避免点画面误入沉浸）；
      // 沉浸时点 canvas 视为空白立即恢复控件（否则全屏 canvas 覆盖一切点击无法退出）。
      if (e.target.tagName === 'CANVAS') {
        if (immersive) {
          immersive = false;
          applyImmersive();
        }
        return;
      }
      immersive = !immersive;
      applyImmersive();
    }, true);
  }

  /* ---------- 定位固定条（控制条 + 模式条） ---------- */
  function locateBars() {
    var fixed = [];
    document.querySelectorAll('div').forEach(function (el) {
      var st = el.style;
      if (st.position === 'fixed' && st.bottom !== '' && st.left === '0px' && st.right === '0px' && st.zIndex === '10') {
        fixed.push(el);
      }
    });
    // 两条固定条：按 bottom 值排序（bottom:0 是模式条，bottom:58px 是控制条）
    fixed.sort(function (a, b) {
      var ba = parseFloat(a.style.bottom) || 0;
      var bb = parseFloat(b.style.bottom) || 0;
      return ba - bb;
    });
    if (fixed.length >= 2) {
      modeBar = fixed[0];
      controlBar = fixed[1];
      return true;
    }
    return false;
  }

  function patchAll() {
    var now = Date.now();
    if (now - lastPatch < 120) return; // 防抖
    lastPatch = now;
    patchLyricGrid();
    applyBackgroundPatch(); // 舞台 React 重渲染后恢复自定义背景层（不依赖控件条定位）
    // ★ 2026-11-03：缓存引用可能已被 React 重建（节点脱离文档）。旧实现只在
    //   controlBar/modeBar 为 null 时才重新定位，一旦节点被替换，引用永远指向脱离的旧节点：
    //   applyImmersive 改的是旧节点 → 新控件条保持 React 自己的状态（可能是隐藏），
    //   patchModeBar 也只作用于旧节点。表现为「设置背景后部分控件按钮不显示」。
    if (!barsAlive()) {
      controlBar = null;
      modeBar = null;
      if (!locateBars()) return;
    }
    patchModeBar();
    setupImmersive();
    applyImmersive(); // 无条件应用：React 重渲染重建控件条后恢复其可见性（immersive 状态一致）
  }

  /// 缓存的控件条/模式条是否仍挂在文档里（React 重渲染会替换节点）
  function barsAlive() {
    var rootEl = document.documentElement;
    return !!(controlBar && modeBar && rootEl.contains(controlBar) && rootEl.contains(modeBar));
  }

  /* ---------- 时间跳变行切换动画卡住修复 ----------
   * Folia 行切换（currentLineIndex 变化）时，行内字级元素会播放归位动画；
   * 若 time 消息一次性跳变跨越行边界（如进度条 seek），切换动画不驱动，
   * 新行的字级元素全部停留在 opacity:0（歌词消失）。
   * 修复：监测激活行（歌词网格直接子元素）是否全部 opacity≈0 且持续，
   * 是则推送“微回退 + 重推”触发渲染层重算行切换动画。
   */
  var foliaHost = null;
  var patchT = 0;
  var stuckCount = 0;
  var lastRecover = 0;

  function tryHookHost() {
    if (foliaHost) return;
    var h = window.__foliaHost;
    if (!h || !h.receive) { setTimeout(tryHookHost, 50); return; }
    foliaHost = h;
    var orig = h.receive;
    h.receive = function (msg) {
      try {
        if (msg && msg.type === 'time' && msg.payload && typeof msg.payload.t === 'number') {
          patchT = msg.payload.t;
        } else if (msg && msg.type === 'song') {
          patchT = 0;
          stuckCount = 0;
        } else if (msg && msg.type === 'background') {
          setBackground(msg.payload && msg.payload.image);
        } else if (msg && msg.type === 'mode') {
          // ★ 用户主动切换 Folia 模式 → 解除自定义背景压制，让模式背景显示。
          //   mode 消息只在用户切换模式时发出（初始化不发）；此处即时让位，
          //   不等原生 background 消息往返——模式背景不被 MutationObserver 压制闪烁，
          //   实现「自定义背景 / Folia 模式背景」共存切换。
          if (bgActive) {
            bgActive = false;
            applyBackgroundPatch();
          }
        } else if (msg && msg.type === 'theme') {
          setTheme(msg.payload);
        }
      } catch (e) {}
      return orig.apply(this, arguments);
    };
  }

  /* ---------- 全屏播放页自定义背景：主题背景透明让位 ----------
   * 原生层（SwiftUI Image，scaledToFill 居中裁剪）已显示自定义背景图；
   * 网页需在存在自定义背景时透明化所有挡住原生图的层：
   *   1. html/body/#root 背景；
   *   2. 播放器根容器（#root 直接子，position:fixed + inset:0 + 带背景色，通常 #000）——
   *      实测它是整屏黑底，之前未处理导致"自定义图被黑色/AI 情绪背景覆盖"；
   *   3. 舞台内所有铺满全屏（≥98%）且带背景（backgroundColor 或渐变/封面 backgroundImage）的层。
   * 原生推 background 消息：payload.image 非空 = 开启让位；空串 = 关闭让位（恢复主题背景）。
   *
   * 压制机制：加 __foliaBgOff class（CSS !important 透明）——React 重渲染重设 inline
   * background 也不生效，从根本上消除"渲染完成 → 背景复活"竞态；恢复时移除 class。
   *
   * AI 配色换肤走独立 theme 消息（不重推 song，避免 Folia 播放器 UI 重置）：
   * 无自定义背景图时把舞台纯色背景层改为 AI 配色背景色；渐变/封面层保持原样（Folia 主题设计）。
   */
  var bgActive = false;

  /// 播放器根容器：#root 直接子中 position:fixed + inset:0 + 带背景色的 div。
  /// 实测为 `position:fixed; inset:0px; background:rgb(0,0,0)` 整屏黑底，
  /// 位于 html/body/#root 透明化范围之外，必须单独处理，否则永远盖住原生背景图。
  function findRootContainers() {
    var root = document.getElementById('root');
    if (!root) return [];
    var found = [];
    for (var i = 0; i < root.children.length; i++) {
      var el = root.children[i];
      var st = el.style;
      if (!st || st.position !== 'fixed') continue;
      var inset0 = st.inset === '0px' || (st.top === '0px' && st.bottom === '0px' && st.left === '0px' && st.right === '0px');
      if (!inset0) continue;
      if (st.background || st.backgroundColor) found.push(el);
    }
    return found;
  }

  /// 找出舞台内所有铺满全屏（宽高 ≥98%）且会遮挡原生背景图的层。
  /// 匹配三类：
  ///   - div 带 backgroundColor（主题纯色层，opacity .8/.28/.35）
  ///   - div 带 backgroundImage（radial/linear-gradient 情绪层、封面图铺满层）
  ///   - canvas（Folia 可视化器用 WebGL / paper-shaders 全屏 quad 绘制主题色粒子/波形，
  ///     无 background 属性，但铺在原生背景图之上即为"AI 情绪背景"——按尺寸直接匹配）
  /// 返回数组而非单个元素——React 重渲染可能同时存在多层背景（旧层未移除 + 新层挂载）。
  function findAllThemeBackgrounds(stage) {
    if (!stage) return [];
    var all = stage.querySelectorAll('div, canvas, img');
    var found = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.tagName === 'CANVAS' || el.tagName === 'IMG') {
        // canvas：WebGL 可视化器全屏 quad 绘制主题色粒子/波形；
        // img：封面/情绪图背景层（object-cover 铺满，transition-opacity 渐变呈现）——
        //   两者都没有可透明化的 CSS background，按全屏尺寸直接匹配，用 opacity 压制。
        var rc = el.getBoundingClientRect();
        if (rc.width >= stage.clientWidth * 0.98 && rc.height >= stage.clientHeight * 0.98) found.push(el);
        continue;
      }
      var st = el.style;
      if (!st) continue;
      var bg = st.backgroundColor || '';
      var bgi = st.backgroundImage || '';
      var hasBg = bg && bg !== 'transparent' && bg.indexOf('rgba(0, 0, 0, 0)') !== 0;
      if (!hasBg && bgi && bgi !== 'none') hasBg = true;
      if (!hasBg) continue;
      var r = el.getBoundingClientRect();
      if (r.width >= stage.clientWidth * 0.98 && r.height >= stage.clientHeight * 0.98) found.push(el);
    }
    return found;
  }

  function suppressBackground(el) {
    // canvas / img：无 CSS background 可透明化，用独立 class 整体透明（opacity:0 !important）
    if (el.tagName === 'CANVAS' || el.tagName === 'IMG') {
      if (!el.classList.contains('__foliaCanvasOff')) el.classList.add('__foliaCanvasOff');
      if (!el.classList.contains('__foliaBgOff')) el.classList.add('__foliaBgOff');
      return;
    }
    // div 背景层/根容器：背景透明让位（不能设 opacity:0——根容器内含歌词与控件条）
    if (!el.classList.contains('__foliaBgOff')) el.classList.add('__foliaBgOff');
    if (el.style.backgroundColor && el.style.backgroundColor !== 'transparent') el.style.backgroundColor = 'transparent';
    if (el.style.background && el.style.background !== 'transparent') el.style.background = 'transparent';
  }

  function restoreBackground(el) {
    if (el.classList.contains('__foliaBgOff')) el.classList.remove('__foliaBgOff');
    if (el.classList.contains('__foliaCanvasOff')) el.classList.remove('__foliaCanvasOff');
    if (el.style.backgroundColor === 'transparent') el.style.backgroundColor = '';
    if (el.style.background === 'transparent') el.style.background = '';
  }

  function setBackground(url) {
    bgActive = !!(url && url.length > 0);
    if (bgActive) bgSetTime = Date.now(); // 记录压制起始，供模式层挂载检测区分初始化窗口
    applyBackgroundPatch();
    // React 渲染是异步的：background 消息可能在渲染完成前到达，当时透明化的旧层
    // 会被 React 渲染出的新层替换。延迟重试覆盖渲染完成后的最终 DOM 状态。
    [80, 200, 500].forEach(function (d) {
      setTimeout(applyBackgroundPatch, d);
    });
  }

  function applyBackgroundPatch() {
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    var root = document.documentElement;
    // ★ body 判空：patch 已在 host.html 前置到 <head> 内同步执行，首次 applyBackgroundPatch
    //   时 <body> 尚未解析（null），`body.style` 直接访问会抛 TypeError，
    //   中断整个 IIFE（tryHookHost 轮询 / MutationObserver 注册随之失效）。
    var body = document.body;
    var rootEl = document.getElementById('root');
    var roots = findRootContainers();
    var layers = stage ? findAllThemeBackgrounds(stage) : [];
    if (bgActive) {
      if (root.style.background !== 'transparent') root.style.background = 'transparent';
      if (body && body.style.background !== 'transparent') body.style.background = 'transparent';
      if (rootEl && rootEl.style.background !== 'transparent') rootEl.style.background = 'transparent';
      roots.forEach(suppressBackground);
      layers.forEach(suppressBackground);
    } else {
      if (root.style.background === 'transparent') root.style.background = '';
      if (body && body.style.background === 'transparent') body.style.background = '';
      if (rootEl && rootEl.style.background === 'transparent') rootEl.style.background = '';
      // 恢复必须扫 __foliaBgOff class 而非 findAllThemeBackgrounds：
      // 被压制的层 backgroundColor 已是 transparent，findAllThemeBackgrounds 会跳过它们，
      // 导致 class 残留、透明压制永远无法解除（清除背景后 AI 配色无法恢复）。
      var suppressed = document.querySelectorAll('.__foliaBgOff');
      for (var i = 0; i < suppressed.length; i++) restoreBackground(suppressed[i]);
      // 无自定义背景图时，若已收到 AI 配色 theme，恢复后重新应用 AI 配色背景色
      applyThemePatch();
    }
  }

  /* ---------- AI 配色换肤（theme 消息） ---------- */
  var lastTheme = null;

  function setTheme(theme) {
    if (!theme || !theme.backgroundColor) return;
    lastTheme = theme;
    applyThemePatch();
    [80, 200, 500].forEach(function (d) {
      setTimeout(applyThemePatch, d);
    });
  }

  function applyThemePatch() {
    // 有自定义背景图时背景层保持透明让位，不应用主题色（自定义图最优先）
    if (bgActive || !lastTheme) return;
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    if (!stage) return;
    var layers = findAllThemeBackgrounds(stage);
    layers.forEach(function (el) {
      // canvas（WebGL 可视化器）/ img（封面层）不应用 CSS 背景色，跳过
      if (el.tagName === 'CANVAS' || el.tagName === 'IMG') return;
      // 只改纯色背景层为 AI 配色；渐变/封面层保持原样（Folia 主题设计，半透明叠色）
      var bgi = el.style.backgroundImage || '';
      if (bgi && bgi !== 'none') return;
      if (el.style.backgroundColor !== lastTheme.backgroundColor) {
        el.style.backgroundColor = lastTheme.backgroundColor;
      }
    });
  }

  function detectStuck() {
    if (!foliaHost) return;
    var grid = document.querySelector('.flex.flex-wrap.w-full.max-w-6xl');
    if (!grid || grid.children.length === 0) { stuckCount = 0; return; }
    var allHidden = true;
    for (var i = 0; i < grid.children.length; i++) {
      var op = parseFloat(grid.children[i].style.opacity);
      if (!isNaN(op) && op >= 0.15) { allHidden = false; break; }
    }
    var now = Date.now();
    if (allHidden) {
      stuckCount += 1;
      // 连续 ~750ms 全隐藏判定为卡住；恢复冷却 1.5s 防止循环抖动
      if (stuckCount >= 3 && now - lastRecover > 1500) {
        lastRecover = now;
        stuckCount = 0;
        var t = patchT;
        try {
          foliaHost.receive({ type: 'time', payload: { t: Math.max(t - 0.05, 0), state: 'playing' } });
          setTimeout(function () {
            foliaHost.receive({ type: 'time', payload: { t: t, state: 'playing' } });
          }, 60);
        } catch (e) {}
      }
    } else {
      stuckCount = 0;
    }
  }

  /* ---------- 挂载 ---------- */
  // ★★★ 顶层 document 级事件委托（必须在 patchAll/locateBars 之前绑定）：
  //   真机四连否的机制性根因——patchAll 里 `if(!controlBar||!modeBar){ if(!locateBars()) return; }`，
  //   React 渲染的最后一次 DOM 变更若落在 120ms 防抖窗口内，locateBars 永不重跑，
  //   modeBar/stage 永远 null，原先绑在 patchModeBar/setupImmersive 内部的 document 委托
  //   从未绑定（浏览器时序恰好让 locateBars 成功 → E2E 全过、真机全挂）。
  //   顶层绑定不依赖任何定位结果：patch 一加载即生效，React 重建任意次数都拦得住。
  // 诊断辅助：记录用户最后点击的模式名（区分「React 未切换」vs「已切换但背景未显示」）
  var lastMode = '';
  if (!document.__foliaModeBarDocBound) {
    document.__foliaModeBarDocBound = true;
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (!t.closest('button')) return;
      // 向上找 fixed + bottom:0 的祖先容器（模式条特征）
      var p = t;
      while (p && p !== document.documentElement) {
        var st = p.style;
        if (st && st.position === 'fixed' && (st.bottom === '0px' || st.bottom === '0')) {
          lastMode = (t.closest('button').textContent || '').trim();
          if (bgActive) {
            bgActive = false;
            applyBackgroundPatch();
          }
          break;
        }
        p = p.parentElement;
      }
    }, true);
  }
  if (!document.__foliaImmersiveDocBound) {
    document.__foliaImmersiveDocBound = true;
    document.addEventListener('click', function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest('button')) return;
      if (t.closest('[role="slider"], [role="progressbar"]')) return;
      // ★ 自定义背景态：非沉浸时忽略空白点击，避免用户点背景图随手一下就把
      //   控制条/模式条（opacity 0）+ 原生顶栏全部隐藏——这是"设置背景后按键不显示"的
      //   直接触发路径。沉浸态仍允许点击恢复控件。
      if (bgActive && !immersive) return;
      var p = t;
      while (p && p !== document.documentElement) {
        var st = p.style;
        if (st && st.position === 'fixed') return; // 固定条（模式条/控制条）内不触发
        p = p.parentElement;
      }
      // ★ 模式背景 canvas 铺满全屏：非沉浸时点 canvas 不触发（避免点画面误入沉浸）；
      //   沉浸时点 canvas 视为空白立即恢复控件——否则全屏 canvas 覆盖一切点击，
      //   进入沉浸后永远无法退出（真机"所有控制都没有（含换歌）"的根因）。
      if (t.tagName === 'CANVAS') {
        if (immersive) {
          immersive = false;
          applyImmersive();
        }
        return;
      }
      immersive = !immersive;
      applyImmersive();
    }, true);
  }
  // ★ 模式层挂载检测（终极兜底，完全不依赖点击事件）：用户切 Folia 模式必然导致
  //   React 挂载新模式背景层（bundle t(e) 切换渲染器），MutationObserver 观察 DOM
  //   变化，检测到「未见过的新主题层」且 bgActive 已稳定（非初始化窗口）即自动解除
  //   压制，让模式背景显示。即使 document 点击委托在极端情况下失效也能工作。
  var seenThemeEls = null;
  var bgSetTime = 0;
  function trackThemeLayers() {
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    if (!stage) return;
    // 宽松识别：stage 内所有「全屏尺寸（≥98%）」的 div/canvas/img 节点，不要求
    // 当前有背景色——React 挂载新模式层时可能先插入节点再设背景，且 MutationObserver
    // 回调里 applyBackgroundPatch 会先把新层压制成 transparent，仅用 findAllThemeBackgrounds
    // 会 miss。按尺寸识别无论是否已压制都能看到新层。
    var all = stage.querySelectorAll('div, canvas, img');
    var fresh = [];
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (seenThemeEls && seenThemeEls.has(el)) continue;
      var rc;
      try { rc = el.getBoundingClientRect(); } catch (e) { continue; }
      if (rc.width >= stage.clientWidth * 0.98 && rc.height >= stage.clientHeight * 0.98) {
        if (!seenThemeEls) {
          seenThemeEls = new Set();
          seenThemeEls.add(el);
          continue;
        }
        fresh.push(el);
      }
      if (seenThemeEls) seenThemeEls.add(el);
    }
    if (fresh.length > 0 && bgActive && (Date.now() - bgSetTime > 1500)) {
      bgActive = false;
      applyBackgroundPatch();
    }
  }
  lastNotifiedVisible = !immersive; // 初始可见态，避免进页面发多余通知
  patchAll();
  tryHookHost();
  setInterval(detectStuck, 250);
  new MutationObserver(function () {
    // React 重渲染可能覆盖内联样式，观察子树变化持续补丁。
    // 背景让位 / AI 配色换肤是状态修复，**不受 120ms 防抖限制**：
    // 每次 DOM 变化都执行，彻底消除「React 渲染完成 → 无更多变化 → 残留层」竞态。
    trackThemeLayers(); // 先检测新模式层挂载（宽松识别，压制前后都能看到新节点）
    applyBackgroundPatch();
    applyThemePatch();
    patchAll();
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });

  /* ---------- 真机诊断（原生侧 evaluateJavaScript 拉取） ----------
   * 反复被真机否决后加入：确认 hook 是否成功、bgActive 是否置位、压制层数量，
   * 区分「patch 未执行 / hook 失败 / 消息丢失 / 压制不完整」四类失效。
   * 返回 JSON 字符串；原生侧 FullScreenWebLyricView 拉取后显示在诊断浮层。 */
  window.__foliaDiag = function () {
    function bg(el) {
      if (!el) return 'n/a';
      try {
        var s = getComputedStyle(el);
        var bgi = s.backgroundImage || '';
        return (s.backgroundColor || '') + (bgi && bgi !== 'none' ? '|img' : '');
      } catch (e) { return 'err'; }
    }
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    var root = document.getElementById('root');
    // 模式条/控制条存在性（真机定位结果）
    var barInfo = (function () {
      var out = { mode: false, ctrl: false, modeBtns: 0, ctrlBtns: 0 };
      document.querySelectorAll('div').forEach(function (d) {
        var st = d.style;
        if (st.position === 'fixed' && st.bottom !== '' && st.left === '0px' && st.right === '0px' && st.zIndex === '10') {
          var b = parseFloat(st.bottom) || 0;
          if (b === 0) { out.mode = true; out.modeBtns = d.querySelectorAll('button').length; }
          if (b > 0) { out.ctrl = true; out.ctrlBtns = d.querySelectorAll('button').length; }
        }
      });
      return out;
    })();
    return JSON.stringify({
      v: '136',
      hooked: !!foliaHost,
      bgActive: bgActive,
      themeApplied: !!lastTheme,
      stageFound: !!stage,
      rootContainers: findRootContainers().length,
      themeLayers: stage ? findAllThemeBackgrounds(stage).length : -1,
      bgOff: document.querySelectorAll('.__foliaBgOff').length,
      canvasOff: document.querySelectorAll('.__foliaCanvasOff').length,
      immersive: immersive,
      lastMode: lastMode,
      modeBar: barInfo.mode,
      ctrlBar: barInfo.ctrl,
      modeBtns: barInfo.modeBtns,
      ctrlBtns: barInfo.ctrlBtns,
      htmlBg: bg(document.documentElement),
      bodyBg: bg(document.body),
      rootBg: bg(root),
      stageBg: bg(stage)
    });
  };
})();
