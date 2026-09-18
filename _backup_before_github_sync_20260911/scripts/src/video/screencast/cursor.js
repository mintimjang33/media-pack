/* 스크린캐스트용 가짜 커서 오버레이. CDP 합성클릭은 OS 커서를 안 움직이므로
   페이지에 직접 커서를 그려 이동·클릭 파문을 시각화한다. npm 의존 없음(자체 30줄).
   window.__cur.move(x,y,ms) / .click() / .mask(rects) 를 노출. 좌표=CSS 픽셀.
   window.__CUR_HIDE=1 이면(post 모드 — 커서를 Remotion 이 후처리로 그림) 점·파문을
   숨기고 mask() 만 동작한다. */
(function () {
  if (window.__cur) return;
  var HIDE = !!window.__CUR_HIDE;
  var dot = document.createElement("div");
  dot.style.cssText =
    "position:fixed;left:0;top:0;width:22px;height:22px;margin:-11px 0 0 -11px;" +
    "border-radius:50%;background:rgba(0,0,0,.35);border:2px solid #fff;" +
    "box-shadow:0 1px 4px rgba(0,0,0,.4);z-index:2147483647;pointer-events:none;" +
    "transform:translate(-100px,-100px);will-change:transform;transition:none;";
  var x = -100, y = -100;
  function place() { dot.style.transform = "translate(" + x + "px," + y + "px)"; }
  function ready() {
    if (!HIDE && document.body && !dot.isConnected) document.body.appendChild(dot);
    place();
  }
  window.__cur = {
    _x: function () { return x; },
    _y: function () { return y; },
    move: function (tx, ty, ms) {
      ready();
      if (HIDE) { x = tx; y = ty; return Promise.resolve(true); }
      ms = ms || 500;
      var sx = x, sy = y, t0 = performance.now();
      return new Promise(function (res) {
        function step(now) {
          var p = Math.min(1, (now - t0) / ms);
          var e = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2; // easeInOutQuad
          x = sx + (tx - sx) * e; y = sy + (ty - sy) * e; place();
          if (p < 1) requestAnimationFrame(step); else res(true);
        }
        requestAnimationFrame(step);
      });
    },
    click: function () {
      if (HIDE) return true;
      ready();
      var r = document.createElement("div");
      r.style.cssText =
        "position:fixed;left:0;top:0;width:20px;height:20px;margin:-10px 0 0 -10px;" +
        "border-radius:50%;border:2px solid #4c9ffe;z-index:2147483646;pointer-events:none;" +
        "transform:translate(" + x + "px," + y + "px) scale(.3);opacity:.9;" +
        "transition:transform .45s ease-out,opacity .45s ease-out;";
      document.body.appendChild(r);
      requestAnimationFrame(function () {
        r.style.transform = "translate(" + x + "px," + y + "px) scale(3)";
        r.style.opacity = "0";
      });
      setTimeout(function () { r.remove(); }, 500);
      dot.style.background = "rgba(76,159,254,.5)";
      setTimeout(function () { dot.style.background = "rgba(0,0,0,.35)"; }, 150);
      return true;
    },
    mask: function (rects) {
      (rects || []).forEach(function (m) {
        var b = document.createElement("div");
        b.style.cssText =
          "position:fixed;left:" + m.x + "px;top:" + m.y + "px;width:" + m.w + "px;height:" + m.h +
          "px;background:#20242c;z-index:2147483645;pointer-events:none;border-radius:4px;";
        document.body.appendChild(b);
      });
      return true;
    },
  };
  ready();
})();
