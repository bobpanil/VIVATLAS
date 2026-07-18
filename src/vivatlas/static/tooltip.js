/* Свои подсказки вместо системных: перехватываем hover/фокус на элементах с
 * title, прячем родной тултип (снимаем title сразу) и показываем свою плашку —
 * НО с задержкой, как у системной, чтобы не мельтешила при простом проведении
 * мышью. Без скрипта остаётся обычный системный тултип (прогрессивное улучшение).
 * Одна плашка на документ, выносится в <body> (position:fixed).
 */
(function () {
    'use strict';
    var DELAY = 500;   // мс до появления — как у системной подсказки

    var tip = document.createElement('div');
    tip.className = 'tip';
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;

    var cur = null;    // элемент, у которого сейчас снят title
    var text = '';
    var timer = null;

    // Если элемент с подсказкой убрали из DOM во время наведения (напр. значок
    // папки исчезает после переноса карточки), ни mouseleave, ни blur не придут —
    // подсказка зависла бы. Следим за удалением и сами закрываемся. Наблюдатель
    // включаем только на время показа (begin→end), чтобы не жечь ресурсы.
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
        var top = r.top - t.height - 8;          // над элементом
        if (top < 4) top = r.bottom + 8;         // не влезло — под ним
        tip.style.left = Math.round(left) + 'px';
        tip.style.top = Math.round(top) + 'px';
    }

    // Начать наведение: сразу снимаем title (гасим системную подсказку), а свою
    // показываем только спустя DELAY. Уйдут раньше — таймер отменится в end().
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
            // Вернуть title только живому элементу (удалённому — незачем).
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
    // Клавиатурный фокус — да (с той же задержкой), мышиный клик — нет.
    document.addEventListener('focusin', function (e) {
        var el = e.target.closest ? e.target.closest('[title]') : null;
        if (!el) return;
        try { if (!el.matches(':focus-visible')) return; } catch (x) { /* нет поддержки — покажем */ }
        begin(el);
    });
    // Любой клик/нажатие/прокрутка прячет (и отменяет отложенный показ).
    document.addEventListener('mousedown', end, true);
    document.addEventListener('click', end, true);
    window.addEventListener('scroll', end, true);
    window.addEventListener('resize', end);
})();
