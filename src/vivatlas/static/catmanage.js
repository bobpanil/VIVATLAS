/* Управление папками-категориями: выбор иконки, порядок перетаскиванием и
 * создание БЕЗ перезагрузки. Один код на настройки (личные папки) и админ-панель
 * (общие) — везде, где есть .cat-manage / .catrow-add. Всё на делегировании от
 * document, поэтому строки, добавленные скриптом на лету, работают сразу.
 */
(function () {
    'use strict';

    function closeGrids(except) {
        document.querySelectorAll('.icon-grid').forEach(function (g) {
            if (g !== except) g.hidden = true;
        });
    }

    // --- выбор иконки (делегирование) --------------------------------------
    // Кнопка-иконка открывает сетку; клик по значку ставит его в скрытое поле и
    // на кнопку. У существующей папки сразу сохраняем (форма update), у новой —
    // только ставим значок до нажатия «Завести».
    document.addEventListener('click', function (e) {
        var pick = e.target.closest ? e.target.closest('.cat-pick') : null;
        if (pick) {
            e.stopPropagation();
            var grid = document.getElementById('grid-' + pick.getAttribute('data-target'));
            if (!grid) return;
            var willOpen = grid.hidden;
            closeGrids(grid);
            grid.hidden = !willOpen;
            return;
        }
        var opt = e.target.closest ? e.target.closest('.icon-opt') : null;
        if (opt) {
            var target = opt.getAttribute('data-target');
            var input = document.getElementById(target);
            var pk = document.querySelector('.cat-pick[data-target="' + target + '"]');
            if (input) input.value = opt.getAttribute('data-slug');
            if (pk) pk.innerHTML = opt.innerHTML;
            var og = document.getElementById('grid-' + target);
            if (og) og.hidden = true;
            if (input) {
                var form = input.closest('form');
                if (form && !form.classList.contains('catrow-add')) form.submit();
            }
            return;
        }
        closeGrids(null);
    });

    // --- порядок перетаскиванием (делегирование) ---------------------------
    var dragEl = null, dragList = null;
    document.addEventListener('dragstart', function (e) {
        var grip = e.target.closest ? e.target.closest('.catrow-grip') : null;
        if (!grip) return;
        dragEl = grip.closest('.catrow');
        dragList = dragEl ? dragEl.closest('.cat-manage') : null;
        try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', ''); } catch (x) {}
        if (dragEl) dragEl.classList.add('dragging');
    });
    document.addEventListener('dragend', function () {
        if (dragEl) dragEl.classList.remove('dragging');
        dragEl = null; dragList = null;
    });
    document.addEventListener('dragover', function (e) {
        if (!dragEl || !dragList) return;
        var list = e.target.closest ? e.target.closest('.cat-manage') : null;
        if (list !== dragList) return;             // тянем только внутри своего списка
        e.preventDefault();
        var over = e.target.closest ? e.target.closest('.catrow') : null;
        if (!over || over === dragEl) return;
        var rect = over.getBoundingClientRect();
        var after = (e.clientY - rect.top) > rect.height / 2;
        dragList.insertBefore(dragEl, after ? over.nextSibling : over);
    });
    document.addEventListener('drop', function (e) {
        if (!dragEl || !dragList) return;
        e.preventDefault();
        var rid = dragList.getAttribute('data-reorder');
        var ids = [];
        dragList.querySelectorAll('.catrow').forEach(function (li) { ids.push(li.getAttribute('data-id')); });
        var order = document.getElementById(rid + '-order');
        var form = document.getElementById(rid + '-form');
        if (order && form) { order.value = ids.join(','); form.submit(); }
    });

    // --- создание папки без перезагрузки ------------------------------------
    // Отправляем форму fetch'ем; сервер отдаёт HTML новой строки, вставляем её в
    // список сразу. Без скрипта/сети — обычная отправка (сервер редиректит).
    document.addEventListener('submit', function (e) {
        var form = e.target.closest ? e.target.closest('.catrow-add') : null;
        if (!form) return;
        var nameInput = form.querySelector('input[name="name"]');
        if (nameInput && !nameInput.value.trim()) return;   // пусто — required не пустит
        e.preventDefault();
        fetch(form.action, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: new FormData(form)
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d || !d.ok) { if (d && d.error) alert(d.error); return; }
            var list = form.parentNode.querySelector('.cat-manage');
            if (!list) { form.submit(); return; }           // подстраховка (список рисуется всегда)
            var tmp = document.createElement('div');
            tmp.innerHTML = (d.html || '').trim();
            var row = tmp.firstElementChild;
            if (row) list.appendChild(row);
            // Сбросить форму добавления к исходному виду.
            if (nameInput) nameInput.value = '';
            var iconInput = form.querySelector('input[name="icon"]');
            if (iconInput) iconInput.value = '';
            var pick = form.querySelector('.cat-pick');
            if (pick) pick.innerHTML = '<span class="cat-pick-empty">+</span>';
            if (nameInput) nameInput.focus();
        }).catch(function () { form.submit(); });           // не вышло — обычная отправка
    });
})();
