/* Свой выпадающий список вместо системного <select>.
 *
 * Прогрессивное улучшение: настоящий <select> остаётся в форме (значение
 * отправляется, работает без скрипта), а поверх строится своя кнопка + список.
 * Список выносится в <body> (position:fixed) — иначе прокручиваемая модалка его
 * обрезала бы. Доступно с клавиатуры: стрелки/Home/End/Enter/Esc, набор по
 * буквам, aria-activedescendant. Значение синхронизируется в select + событие
 * change (чтобы существующие onchange-обработчики, напр. смена языка, срабатывали).
 */
(function () {
    'use strict';
    var seq = 0;
    var openApi = null;

    function enhance(select) {
        if (select.getAttribute('data-dd')) return;
        select.setAttribute('data-dd', '1');
        // Скрытый <select> Chrome всё равно проверяет на required и молча не даёт
        // отправить форму (сообщение показать негде). Снимаем required с него и
        // проверяем сами при отправке — а без скрипта select виден и required
        // работает как обычно.
        if (select.required) { select.dataset.ddRequired = '1'; select.required = false; }
        var uid = 'dd' + (++seq);

        var wrap = document.createElement('div');
        wrap.className = 'dd';
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);
        select.classList.add('dd-native');
        select.tabIndex = -1;
        select.setAttribute('aria-hidden', 'true');

        // Подпись поля (напр. «Язык») — держим отдельно: имя кнопки собираем из
        // неё И текущего значения, иначе скринридер называл бы только поле и
        // молчал о выбранном («Язык», без «English»).
        var fieldLabel = select.getAttribute('aria-label') || '';

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'dd-btn';
        btn.id = uid + '-btn';
        // role=combobox (а не просто button): только на нём валиден
        // aria-activedescendant при навигации стрелками по списку.
        btn.setAttribute('role', 'combobox');
        btn.setAttribute('aria-haspopup', 'listbox');
        btn.setAttribute('aria-expanded', 'false');
        if (select.disabled) btn.disabled = true;
        btn.innerHTML = '<span class="dd-label"></span>' +
            '<svg class="dd-caret" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        wrap.appendChild(btn);
        var label = btn.querySelector('.dd-label');

        var list = document.createElement('div');
        list.className = 'dd-list';
        list.id = uid + '-list';
        list.setAttribute('role', 'listbox');
        if (select.getAttribute('aria-label')) list.setAttribute('aria-label', select.getAttribute('aria-label'));
        list.hidden = true;
        btn.setAttribute('aria-controls', list.id);
        // Список вынесен в <body>, поэтому логически «привязываем» его к кнопке
        // (aria-owns) — тогда aria-activedescendant на опции разрешается.
        btn.setAttribute('aria-owns', list.id);

        var opts = [];
        [].forEach.call(select.options, function (o, i) {
            var el = document.createElement('div');
            el.className = 'dd-opt';
            el.id = uid + '-o' + i;
            el.setAttribute('role', 'option');
            el.textContent = o.textContent;
            el.dataset.value = o.value;
            var d = o.getAttribute('dir'); if (d) el.dir = d;
            if (o.disabled) el.setAttribute('aria-disabled', 'true');
            list.appendChild(el);
            opts.push(el);
        });
        document.body.appendChild(list);

        var active = -1;

        function syncLabel() {
            var o = select.options[select.selectedIndex];
            var val = o ? o.textContent : '';
            label.textContent = val;
            var d = o && o.getAttribute('dir'); label.dir = d || '';
            // Имя кнопки = «Поле, значение», чтобы значение звучало и в свёрнутом
            // виде. Если подписи поля нет или она совпала со значением (напр.
            // плейсхолдер «Добавить в папку») — не повторяем.
            var name = (fieldLabel && val && fieldLabel !== val)
                ? fieldLabel + ', ' + val
                : (val || fieldLabel);
            if (name) btn.setAttribute('aria-label', name);
            else btn.removeAttribute('aria-label');
            opts.forEach(function (el) {
                el.setAttribute('aria-selected', el.dataset.value === select.value ? 'true' : 'false');
            });
        }
        function enabled(i) {
            return i >= 0 && i < opts.length && opts[i].getAttribute('aria-disabled') !== 'true';
        }
        function setActive(i) {
            if (!enabled(i)) return;
            if (active >= 0) opts[active].classList.remove('act');
            active = i;
            opts[active].classList.add('act');
            btn.setAttribute('aria-activedescendant', opts[active].id);
            opts[active].scrollIntoView({ block: 'nearest' });
        }
        // Следующая доступная опция в направлении step, начиная от from
        // (включительно). Отключённые (aria-disabled) пропускаем.
        function seek(from, step) {
            for (var i = from; i >= 0 && i < opts.length; i += step) {
                if (enabled(i)) return i;
            }
            return -1;
        }
        function position() {
            var r = btn.getBoundingClientRect();
            list.style.minWidth = r.width + 'px';
            // В RTL прижимаем правый край к кнопке и растём влево, иначе — левый.
            var rtl = getComputedStyle(btn).direction === 'rtl';
            if (rtl) {
                list.style.right = (window.innerWidth - r.right) + 'px';
                list.style.left = 'auto';
            } else {
                list.style.left = r.left + 'px';
                list.style.right = 'auto';
            }
            var below = window.innerHeight - r.bottom;
            var above = r.top;
            // Раскрываем вверх, только если снизу не помещается, а сверху места
            // больше. maxHeight — по реально доступной высоте (без завышенного
            // порога), чтобы список не уезжал за край на низком экране.
            if (below < list.scrollHeight && above > below) {
                list.style.top = 'auto';
                list.style.bottom = (window.innerHeight - r.top + 4) + 'px';
                list.style.maxHeight = Math.max(0, above - 12) + 'px';
            } else {
                list.style.bottom = 'auto';
                list.style.top = (r.bottom + 4) + 'px';
                list.style.maxHeight = Math.max(0, below - 12) + 'px';
            }
        }
        function reposition() { if (!list.hidden) position(); }

        var api = {
            open: function () {
                if (openApi && openApi !== api) openApi.close();
                openApi = api;
                syncLabel();
                list.hidden = false;
                position();
                btn.setAttribute('aria-expanded', 'true');
                // Начинаем с выбранной опции; если она отключена (плейсхолдер) —
                // с первой доступной.
                var start = seek(select.selectedIndex >= 0 ? select.selectedIndex : 0, 1);
                setActive(start >= 0 ? start : seek(0, 1));
                document.addEventListener('scroll', reposition, true);
                window.addEventListener('resize', reposition);
            },
            close: function () {
                if (list.hidden) return;
                list.hidden = true;
                btn.setAttribute('aria-expanded', 'false');
                btn.removeAttribute('aria-activedescendant');
                if (active >= 0) { opts[active].classList.remove('act'); active = -1; }
                if (openApi === api) openApi = null;
                document.removeEventListener('scroll', reposition, true);
                window.removeEventListener('resize', reposition);
            },
            isOpen: function () { return !list.hidden; },
            wrap: wrap, list: list, btn: btn,
        };
        select.ddApi = api;

        function choose(i) {
            var el = opts[i];
            if (!el || el.getAttribute('aria-disabled') === 'true') return;
            if (select.value !== el.dataset.value) {
                select.value = el.dataset.value;
                select.dispatchEvent(new Event('change', { bubbles: true }));
            }
            syncLabel();
            api.close();
            btn.focus();
        }

        btn.addEventListener('click', function () { api.isOpen() ? api.close() : api.open(); });
        btn.addEventListener('keydown', function (e) {
            if (!api.isOpen()) {
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault(); api.open();
                }
                return;
            }
            var nxt;
            if (e.key === 'ArrowDown') { e.preventDefault(); nxt = seek(active + 1, 1); if (nxt >= 0) setActive(nxt); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); nxt = seek(active - 1, -1); if (nxt >= 0) setActive(nxt); }
            else if (e.key === 'Home') { e.preventDefault(); nxt = seek(0, 1); if (nxt >= 0) setActive(nxt); }
            else if (e.key === 'End') { e.preventDefault(); nxt = seek(opts.length - 1, -1); if (nxt >= 0) setActive(nxt); }
            else if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(active); }
            // Esc гасим здесь же (stopPropagation), иначе он всплывёт к модалке и
            // закроет её целиком, а не только список.
            else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); api.close(); }
            else if (e.key === 'Tab') { api.close(); }
            else if (e.key.length === 1) {
                var q = e.key.toLowerCase(), start = (active + 1) % opts.length;
                for (var k = 0; k < opts.length; k++) {
                    var idx = (start + k) % opts.length;
                    if (enabled(idx) && opts[idx].textContent.trim().toLowerCase().indexOf(q) === 0) { setActive(idx); break; }
                }
            }
        });
        list.addEventListener('mousedown', function (e) {
            var el = e.target.closest ? e.target.closest('.dd-opt') : null;
            if (el) { e.preventDefault(); choose(opts.indexOf(el)); }
        });
        list.addEventListener('mousemove', function (e) {
            var el = e.target.closest ? e.target.closest('.dd-opt') : null;
            if (el && enabled(opts.indexOf(el))) setActive(opts.indexOf(el));
        });

        syncLabel();
    }

    function closeOnOutside(e) {
        if (!openApi) return;
        if (openApi.btn.contains(e.target) || openApi.list.contains(e.target)) return;
        openApi.close();
    }

    // Скрытый <select> (display:none) браузер не проверяет на required, поэтому
    // сами повторяем это: не даём отправить форму, пока в обязательном списке
    // ничего не выбрано, — открываем его вместо ошибки на сервере.
    function guardSubmit(e) {
        var form = e.target;
        if (!form || !form.querySelectorAll) return;
        var bad = null;
        form.querySelectorAll('select[data-dd-required]').forEach(function (s) {
            if (!bad && !s.value) bad = s;
        });
        if (bad && bad.ddApi) {
            e.preventDefault();
            e.stopPropagation();
            bad.ddApi.btn.focus();
            bad.ddApi.open();
        }
    }

    function init() {
        document.querySelectorAll('select').forEach(enhance);
        document.addEventListener('mousedown', closeOnOutside, true);
        document.addEventListener('submit', guardSubmit, true);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    // На случай динамически добавленных select (напр. будущие формы).
    window.enhanceDropdowns = function (root) {
        (root || document).querySelectorAll('select:not([data-dd])').forEach(enhance);
    };
})();
