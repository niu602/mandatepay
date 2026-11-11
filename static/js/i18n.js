/* MandatePay · 中英切换 (i18n toggle)
 *
 * 设计要点（与 W-10 检查器兼容）：
 * - 结构性文案（导航/标题/标签/按钮）放 data-en / data-zh 属性；
 *   输入框提示用独立的 data-ph-en / data-ph-zh —— 输入框正文是用户数据，
 *   绝不能被 textContent 覆盖。属性值不在静态检查范围内。
 * - 每个可切换元素同时有 data-en 和 data-zh，文本节点默认放双语（英文在前）。
 *   这样：无 JS 时显示双语（与现状一致）、静态检查看到英文、渲染检查通过。
 * - JS 运行时文案用 MPLang.T(en, zh)；check_ui_english.py 会把这类
 *   双语助手调用合并成一个渲染单元，不会把中文参数误判成"纯中文区块"。
 * - 切换时 JS 重写 textContent；<html lang> 同步跟随（读屏软件可读）。
 *
 * 载荷规则（data-en / data-zh 的取值）：
 * - 两个属性都有非空值：both/en/zh 三态切换文本。
 * - data-en=""（纯中文载荷）：en 模式下隐藏该节点，zh/both 显示中文原文。
 * - data-zh=""（纯英文载荷）：zh 模式下隐藏该节点，en/both 显示英文原文。
 *
 * 三个模式：both（双语，默认）/ en（纯英文）/ zh（纯中文）。
 * 选择存 localStorage，刷新保持。动态渲染后 MutationObserver 自动套用。
 * setMode 会派发 mplangchange 事件，动态视图（如 AG Studio 仪表盘）可监听后重建标签。
 *
 * 说明：JS 运行时拼接的状态/提示文案用 MPLang.T(en, zh) 按模式取；
 * 结构性 UI（导航/标题/标签/按钮）用 data-en/data-zh 属性。
 */
(function () {
  'use strict';
  var KEY = 'mp_lang';
  var MODES = ['both', 'en', 'zh'];

  function getMode() {
    var m = null;
    try { m = localStorage.getItem(KEY); } catch (e) {}
    return MODES.indexOf(m) >= 0 ? m : 'both';
  }

  function hide(el) { if (el.style) el.style.display = 'none'; }
  function show(el) { if (el.style) el.style.display = ''; }

  // T(en, zh)：JS 运行时文案助手。both 返回双语，en 返回英文，zh 返回中文。
  function T(en, zh) {
    var m = getMode();
    if (m === 'en') return en;
    if (m === 'zh') return zh;
    return en + ' ' + zh;
  }

  function apply(root) {
    var r = root || document;
    var mode = getMode();
    document.documentElement.lang = mode === 'zh' ? 'zh-CN' : 'en';
    var nodes = r.querySelectorAll('[data-en][data-zh], [data-ph-en][data-ph-zh]');
    for (var i = 0; i < nodes.length; i++) {
      syncNode(nodes[i], mode);
    }
    paintButton();
  }

  function setMode(mode) {
    if (MODES.indexOf(mode) < 0) return;
    try { localStorage.setItem(KEY, mode); } catch (e) {}
    apply();
    if (typeof document !== 'undefined' && document.dispatchEvent) {
      document.dispatchEvent(new CustomEvent('mplangchange', { detail: { mode: mode } }));
    }
  }

  function cycleMode() {
    var cur = getMode();
    setMode(MODES[(MODES.indexOf(cur) + 1) % MODES.length]);
  }

  function paintButton() {
    var btn = document.getElementById('mpLangToggle');
    if (!btn) return;
    var label = { both: 'EN · 中文', en: 'English', zh: '中文' }[getMode()];
    btn.textContent = label;
    btn.title = T('Switch UI language', '切换界面语言');
  }

  function injectStyle() {
    if (document.getElementById('mpLangStyle')) return;
    var s = document.createElement('style');
    s.id = 'mpLangStyle';
    s.textContent =
      'nav.top{display:flex;align-items:center;flex-wrap:wrap;}' +
      '#mpLangToggle{margin-left:auto;padding:4px 12px;border-radius:14px;' +
      'border:1px solid #c6d2e2;background:#fff;color:#003087;cursor:pointer;' +
      'font-size:13px;font-weight:600;font-family:inherit;}' +
      '#mpLangToggle:hover{background:#eef2f7;}';
    document.head.appendChild(s);
  }

  function injectButton() {
    var nav = document.querySelector('nav.top');
    if (!nav) return;
    if (document.getElementById('mpLangToggle')) return;
    injectStyle();
    var btn = document.createElement('button');
    btn.id = 'mpLangToggle';
    btn.type = 'button';
    btn.onclick = cycleMode;
    nav.appendChild(btn);
  }

  // 动态渲染（innerHTML 插入）的节点自动套用当前语言，
  // 无需在每个渲染函数后手动调 apply。
  function observeDynamic() {
    if (!window.MutationObserver) return;
    var obs = new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var added = muts[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var n = added[j];
          if (n.nodeType !== 1) continue;
          if (n.hasAttribute && (n.hasAttribute('data-ph-en') ||
              (n.hasAttribute('data-en') && n.hasAttribute('data-zh')))) {
            syncNode(n, getMode());
          }
          if (n.querySelectorAll) {
            var inner = n.querySelectorAll('[data-en][data-zh], [data-ph-en][data-ph-zh]');
            for (var k = 0; k < inner.length; k++) syncNode(inner[k], getMode());
          }
        }
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  // 对单个节点按指定模式设置文本/显隐（apply 的核心逻辑）
  function syncNode(el, mode) {
    // 输入框提示：只改 placeholder，绝不碰 textContent（那是用户填的数据）。
    var phEn = el.getAttribute('data-ph-en');
    var phZh = el.getAttribute('data-ph-zh');
    if (phEn !== null || phZh !== null) {
      if (el._mpPhOrig === undefined) el._mpPhOrig = el.placeholder || '';
      if (mode === 'en' && phEn !== null) el.placeholder = phEn;
      else if (mode === 'zh' && phZh !== null) el.placeholder = phZh;
      else el.placeholder = el._mpPhOrig;
      // 纯 placeholder 节点（没有 data-en/data-zh）到此为止
      if (!el.hasAttribute('data-en') && !el.hasAttribute('data-zh')) return;
    }
    if (el._mpOrig === undefined) el._mpOrig = el.textContent;
    var hasEn = el.hasAttribute('data-en');
    var hasZh = el.hasAttribute('data-zh');
    var en = hasEn ? el.getAttribute('data-en') : null;
    var zh = hasZh ? el.getAttribute('data-zh') : null;
    if (mode === 'zh') {
      // 纯英文载荷（data-zh=""）→ 中文模式下隐藏
      if (hasZh && zh === '' && hasEn && en !== '') { hide(el); return; }
      show(el);
      el.textContent = (hasZh && zh !== '') ? zh : el._mpOrig;
    } else if (mode === 'en') {
      // 纯中文载荷（data-en=""）→ 英文模式下隐藏
      if (hasEn && en === '' && hasZh && zh !== '') { hide(el); return; }
      show(el);
      el.textContent = (hasEn && en !== '') ? en : el._mpOrig;
    } else {
      show(el);
      el.textContent = el._mpOrig;
    }
  }

  window.MPLang = {
    apply: apply,
    set: setMode,
    get: getMode,
    inject: injectButton,
    syncNode: syncNode,
    T: T
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { injectButton(); apply(); observeDynamic(); });
  } else {
    injectButton(); apply(); observeDynamic();
  }
})();
