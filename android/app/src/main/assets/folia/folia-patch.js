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
    });
  }

  /* ---------- 沉浸模式：轻点舞台空白区隐藏/显示控件 ---------- */
  var lastNotifiedVisible = null; // 已通知原生的可见状态（避免动画期间重复通知）

  function applyImmersive() {
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
  };

  function setupImmersive() {
    var stage = document.querySelector('.relative.w-full.h-full.overflow-hidden');
    if (!stage || stage.__foliaTapBound) return;
    stage.__foliaTapBound = true;
    stage.addEventListener('click', function (e) {
      // 点击控件（按钮/进度条等可交互元素）不触发沉浸切换
      if (e.target.closest && e.target.closest('button')) return;
      if (e.target.closest && e.target.closest('[role="slider"], [role="progressbar"]')) return;
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
    if (!controlBar || !modeBar) {
      if (!locateBars()) return;
    }
    patchModeBar();
    setupImmersive();
    if (immersive) applyImmersive();
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
    if (!h || !h.receive) { setTimeout(tryHookHost, 200); return; }
    foliaHost = h;
    var orig = h.receive;
    h.receive = function (msg) {
      try {
        if (msg && msg.type === 'time' && msg.payload && typeof msg.payload.t === 'number') {
          patchT = msg.payload.t;
        } else if (msg && msg.type === 'song') {
          patchT = 0;
          stuckCount = 0;
        }
      } catch (e) {}
      return orig.apply(this, arguments);
    };
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
  lastNotifiedVisible = !immersive; // 初始可见态，避免进页面发多余通知
  patchAll();
  tryHookHost();
  setInterval(detectStuck, 250);
  new MutationObserver(function () {
    // React 重渲染可能覆盖内联样式，观察子树变化持续补丁
    patchAll();
  }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class'] });
})();
