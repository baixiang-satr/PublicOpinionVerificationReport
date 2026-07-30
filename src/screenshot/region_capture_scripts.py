"""Injected browser scripts for the interactive region-capture window.

Kept in a dedicated module so :mod:`src.screenshot.region_capture` stays
under the repository's per-file line limit.  ``OVERLAY_JS`` is the floating
toolbar injected into every browsed page; ``SELECTION_HTML`` is the
frozen-screen rubber-band selection tab opened after the OS-level grab.
"""

OVERLAY_JS = r"""
(() => {
  // 选区在独立的“冻结屏幕”页进行，about: 页面不注入工具条。
  if (location.protocol === 'about:') return;
  if (window.__poirShotInit) return;
  window.__poirShotInit = true;
  const BINDING = '__poirRegionCapture';
  const send = (payload) => {
    try {
      if (typeof window[BINDING] === 'function') window[BINDING](JSON.stringify(payload));
    } catch (err) { /* binding unavailable */ }
  };

  let host, statusEl, startBtn, cancelBtn;
  let busy = false;

  const TEXT_BROWSE = '截图模式：浏览到目标内容后点「开始框选」，可截取整个屏幕（含地址栏 URL）';
  const TEXT_ARMING = '正在准备屏幕截图…';
  const TEXT_CLOSING = '正在关闭…';

  function setStatus(text) { if (statusEl) statusEl.textContent = text; }

  function boot() {
    if (!document.documentElement || document.getElementById('__poir-shot-host')) return;
    host = document.createElement('div');
    host.id = '__poir-shot-host';
    host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483647;';
    (document.body || document.documentElement).appendChild(host);
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = [
      '<style>',
      ':host{all:initial}',
      '.tb{position:fixed;top:12px;right:12px;display:flex;align-items:center;gap:8px;',
      'background:rgba(32,33,36,.94);color:#fff;border-radius:8px;padding:8px 12px;',
      'font:13px/1.4 "Microsoft YaHei",system-ui,sans-serif;',
      'box-shadow:0 4px 16px rgba(0,0,0,.35);pointer-events:auto;',
      'user-select:none;max-width:72vw}',
      '.tb button{border:0;border-radius:5px;padding:5px 12px;font-size:13px;',
      'cursor:pointer;color:#fff}',
      '.start{background:#2f7cf6}.quit{background:#6b7280}',
      '.tb button:disabled{opacity:.5;cursor:default}',
      '</style>',
      '<div class="tb"><span class="st"></span>',
      '<button type="button" class="start">开始框选</button>',
      '<button type="button" class="quit">取消截图</button></div>'
    ].join('');
    statusEl = root.querySelector('.st');
    startBtn = root.querySelector('.start');
    cancelBtn = root.querySelector('.quit');

    startBtn.addEventListener('click', () => {
      if (busy) return;
      busy = true;
      setStatus(TEXT_ARMING);
      // 先隐藏工具条再截屏，保证冻结画面里只有目标内容
      host.style.visibility = 'hidden';
      send({ action: 'arm' });
    });
    cancelBtn.addEventListener('click', () => {
      if (busy) return;
      busy = true;
      startBtn.disabled = true;
      cancelBtn.disabled = true;
      setStatus(TEXT_CLOSING);
      send({ action: 'cancel' });
    });
    setStatus(TEXT_BROWSE);
  }

  window.__poirRegionCaptureHide = () => {
    if (host) host.style.visibility = 'hidden';
  };
  window.__poirRegionCaptureReset = (message) => {
    busy = false;
    if (host) host.style.visibility = 'visible';
    setStatus(typeof message === 'string' && message ? message : TEXT_BROWSE);
  };

  const tryBoot = () => {
    try { boot(); } catch (err) { /* 浮层尽力而为，不影响页面本身 */ }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryBoot, { once: true });
  } else {
    tryBoot();
  }
})();
"""

SELECTION_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>框选截图区域</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;background:#151618;
    font:13px/1.4 "Microsoft YaHei",system-ui,sans-serif;}
  #view{position:fixed;inset:0;cursor:crosshair;user-select:none;}
  #shot{position:absolute;pointer-events:none;}
  #box{position:fixed;border:2px solid #2f7cf6;
    box-shadow:0 0 0 9999px rgba(0,0,0,.45);pointer-events:none;display:none;
    box-sizing:border-box;}
  #tip{position:fixed;top:10px;left:50%;transform:translateX(-50%);
    background:rgba(32,33,36,.92);color:#fff;border-radius:6px;padding:6px 12px;
    pointer-events:none;white-space:nowrap;box-shadow:0 2px 10px rgba(0,0,0,.35);}
  #bar{position:fixed;display:none;gap:6px;}
  #bar button{border:0;border-radius:5px;padding:7px 14px;font-size:13px;
    cursor:pointer;color:#fff;}
  #ok{background:#2f9e63}#re{background:#6b7280}#quit{background:#b4453c}
</style>
</head>
<body>
<div id="view"><img id="shot" alt="" draggable="false"></div>
<div id="box"></div>
<div id="bar">
  <button type="button" id="ok">✓ 保存</button>
  <button type="button" id="re">↺ 重选</button>
  <button type="button" id="quit">✕ 取消</button>
</div>
<div id="tip"></div>
<script>
(function () {
  var IW = __IMG_W__, IH = __IMG_H__;
  var view = document.getElementById('view');
  var img = document.getElementById('shot');
  var box = document.getElementById('box');
  var bar = document.getElementById('bar');
  var tip = document.getElementById('tip');
  var scale = 1, offX = 0, offY = 0;
  var mode = 'select';
  var dragging = false, sx = 0, sy = 0, rect = null;
  var TIP_SELECT = '按住鼠标左键拖拽框选要保存的屏幕区域（含地址栏 URL），ESC 返回继续浏览';

  img.src = '__IMG_SRC__';

  function send(p) {
    try {
      if (typeof window.__poirRegionCapture === 'function') {
        window.__poirRegionCapture(JSON.stringify(p));
      }
    } catch (err) { /* binding unavailable */ }
  }

  function layout() {
    scale = Math.min(window.innerWidth / IW, window.innerHeight / IH);
    var w = IW * scale, h = IH * scale;
    offX = (window.innerWidth - w) / 2;
    offY = (window.innerHeight - h) / 2;
    img.style.left = offX + 'px';
    img.style.top = offY + 'px';
    img.style.width = w + 'px';
    img.style.height = h + 'px';
  }

  function updateBox(x, y, w, h) {
    rect = { x: x, y: y, w: w, h: h };
    box.style.display = 'block';
    box.style.left = x + 'px';
    box.style.top = y + 'px';
    box.style.width = w + 'px';
    box.style.height = h + 'px';
  }

  function showBar() {
    var pad = 8;
    bar.style.display = 'flex';
    var left = rect.x + rect.w - bar.offsetWidth;
    var top = rect.y + rect.h + pad;
    if (top + bar.offsetHeight > window.innerHeight - 4) {
      top = Math.max(4, rect.y - bar.offsetHeight - pad);
    }
    bar.style.left = Math.max(4, left) + 'px';
    bar.style.top = top + 'px';
  }

  function reset(message) {
    mode = 'select';
    rect = null;
    dragging = false;
    box.style.display = 'none';
    bar.style.display = 'none';
    tip.textContent = message || TIP_SELECT;
  }
  window.__poirSelectReset = reset;

  view.addEventListener('pointerdown', function (e) {
    if (mode !== 'select' || e.button !== 0) return;
    dragging = true;
    sx = e.clientX;
    sy = e.clientY;
    try { view.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
    bar.style.display = 'none';
    updateBox(sx, sy, 0, 0);
    e.preventDefault();
  });
  view.addEventListener('pointermove', function (e) {
    if (!dragging || mode !== 'select') return;
    updateBox(
      Math.min(sx, e.clientX),
      Math.min(sy, e.clientY),
      Math.abs(e.clientX - sx),
      Math.abs(e.clientY - sy)
    );
  });
  view.addEventListener('pointerup', function () {
    if (!dragging || mode !== 'select') return;
    dragging = false;
    if (rect && rect.w >= 4 && rect.h >= 4) {
      mode = 'confirm';
      showBar();
    } else {
      box.style.display = 'none';
      rect = null;
    }
  });

  document.getElementById('re').addEventListener('click', function () { reset(); });
  document.getElementById('quit').addEventListener('click', function () {
    send({ action: 'abort' });
  });
  document.getElementById('ok').addEventListener('click', function () {
    if (!rect || mode !== 'confirm') return;
    mode = 'saving';
    bar.style.display = 'none';
    tip.textContent = '正在保存截图…';
    var x1 = Math.max(0, Math.round((rect.x - offX) / scale));
    var y1 = Math.max(0, Math.round((rect.y - offY) / scale));
    var x2 = Math.min(IW, Math.round((rect.x + rect.w - offX) / scale));
    var y2 = Math.min(IH, Math.round((rect.y + rect.h - offY) / scale));
    send({ action: 'confirm', x: x1, y: y1, width: x2 - x1, height: y2 - y1 });
  });
  window.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (mode === 'confirm') {
      reset();
    } else {
      send({ action: 'abort' });
    }
    e.preventDefault();
  }, true);
  window.addEventListener('resize', layout);
  layout();
  reset();
})();
</script>
</body>
</html>
"""
