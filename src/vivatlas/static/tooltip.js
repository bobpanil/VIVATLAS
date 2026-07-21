/* Custom tooltips instead of the system ones: we intercept hover/focus on
 * elements with title, hide the native tooltip (strip title immediately) and
 * show our own chip — BUT with a delay, like the system one, so it doesn't
 * flicker on a simple mouse pass. Without the script the ordinary system
 * tooltip remains (progressive enhancement).
 * One chip per document, moved into <body> (position:fixed).
 */
(function () {
    'use strict';
    var DELAY = 500;   // ms before it appears — like the system tooltip

    var tip = document.createElement('div');
    tip.className = 'tip';
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;

    var cur = null;    // element whose title is currently stripped
    var text = '';
    var timer = null;

    // If an element with a tooltip is removed from the DOM while hovered (e.g.
    // the folder icon disappears after a card is moved), neither mouseleave nor
    // blur will fire — the tooltip would get stuck. We watch for removal and
    // close ourselves. We enable the observer only while showing (begin→end),
    // so as not to burn resources.
    var obs = null;
    if (typeof MutationObserver !== 'undefined') {
        obs = new MutationObserver(function () { if (cur && !cur.isConnected) end(); });
    }

    function attach() {
        if (!document.body) { document.addEventListener('DOMContentLoaded', attach); return; }
        document.body.appendChild(tip);
    }
    attach();

    function place(el) {
        var r = el.getBoundingClientRect();
        tip.style.maxWidth = Math.min(280, window.innerWidth - 16) + 'px';
        var t = tip.getBoundingClientRect();
        var left = r.left + r.width / 2 - t.width / 2;
        left = Math.max(8, Math.min(left, window.innerWidth - t.width - 8));
        var top = r.top - t.height - 8;          // above the element
        if (top < 4) top = r.bottom + 8;         // didn't fit — below it
        tip.style.left = Math.round(left) + 'px';
        tip.style.top = Math.round(top) + 'px';
    }

    // Start hover: strip title immediately (kill the system tooltip), and show
    // ours only after DELAY. If they leave earlier — the timer is cancelled in end().
    function begin(el) {
        if (el === cur) return;
        end();
        var t = el.getAttribute('title');
        if (!t) return;
        cur = el;
        text = t;
        el.removeAttribute('title');
        el.addEventListener('mouseleave', end);
        el.addEventListener('blur', end);
        if (obs) obs.observe(document.documentElement, { childList: true, subtree: true });
        timer = setTimeout(function () {
            timer = null;
            if (!cur) return;
            tip.textContent = text;
            tip.hidden = false;
            place(cur);
            tip.classList.remove('show');
            void tip.offsetWidth;
            tip.classList.add('show');
        }, DELAY);
    }

    function end() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (obs) obs.disconnect();
        if (cur) {
            // Restore title only to a live element (no point for a removed one).
            if (text && cur.isConnected) cur.setAttribute('title', text);
            cur.removeEventListener('mouseleave', end);
            cur.removeEventListener('blur', end);
            cur = null;
            text = '';
        }
        tip.classList.remove('show');
        tip.hidden = true;
    }

    document.addEventListener('mouseover', function (e) {
        var el = e.target.closest ? e.target.closest('[title]') : null;
        if (el) begin(el);
    });
    // Keyboard focus — yes (with the same delay), mouse click — no.
    document.addEventListener('focusin', function (e) {
        var el = e.target.closest ? e.target.closest('[title]') : null;
        if (!el) return;
        try { if (!el.matches(':focus-visible')) return; } catch (x) { /* no support — show it */ }
        begin(el);
    });
    // Any click/press/scroll hides it (and cancels the pending show).
    document.addEventListener('mousedown', end, true);
    document.addEventListener('click', end, true);
    window.addEventListener('scroll', end, true);
    window.addEventListener('resize', end);
})();
