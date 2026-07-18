/* Управление папками-категориями: выбор иконки и порядок перетаскиванием.
 * Один код на настройки (личные папки) и админ-панель (общие) — там, где есть
 * .cat-pick / .cat-manage. На страницах без них тихо ничего не делает.
 */
(function () {
    'use strict';

    // Выбор иконки: кнопка-иконка открывает сетку; клик по значку ставит его в
    // скрытое поле и на кнопку. У существующей папки сразу сохраняем (форма
    // update), у новой — только ставим значок до нажатия «Завести».
    function iconPicker() {
        function closeAll(except) {
            document.querySelectorAll('.icon-grid').forEach(function (g) {
                if (g !== except) g.hidden = true;
            });
        }
        document.querySelectorAll('.cat-pick').forEach(function (pick) {
            pick.addEventListener('click', function (e) {
                e.stopPropagation();
                var grid = document.getElementById('grid-' + pick.getAttribute('data-target'));
                if (!grid) return;
                var willOpen = grid.hidden;
                closeAll(grid);
                grid.hidden = !willOpen;
            });
        });
        document.querySelectorAll('.icon-opt').forEach(function (opt) {
            opt.addEventListener('click', function () {
                var target = opt.getAttribute('data-target');
                var input = document.getElementById(target);
                var pick = document.querySelector('.cat-pick[data-target="' + target + '"]');
                if (input) input.value = opt.getAttribute('data-slug');
                if (pick) { pick.innerHTML = opt.innerHTML; }
                var grid = document.getElementById('grid-' + target);
                if (grid) grid.hidden = true;
                if (input) {
                    var form = input.closest('form');
                    if (form && !form.classList.contains('catrow-add')) form.submit();
                }
            });
        });
        document.addEventListener('click', function () { closeAll(null); });
    }

    // Порядок папок — перетаскиванием за ручку, в каждой управлялке отдельно. На
    // бросок собираем новый порядок id и отправляем скрытую форму (в ней — next).
    function dragReorder() {
        document.querySelectorAll('.cat-manage').forEach(function (list) {
            var rid = list.getAttribute('data-reorder');
            var dragEl = null;
            list.querySelectorAll('.catrow-grip').forEach(function (grip) {
                grip.addEventListener('dragstart', function (e) {
                    dragEl = grip.closest('.catrow');
                    try { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', ''); } catch (x) {}
                    if (dragEl) dragEl.classList.add('dragging');
                });
                grip.addEventListener('dragend', function () {
                    if (dragEl) dragEl.classList.remove('dragging');
                    dragEl = null;
                });
            });
            list.addEventListener('dragover', function (e) {
                if (!dragEl) return;
                e.preventDefault();
                var over = e.target.closest ? e.target.closest('.catrow') : null;
                if (!over || over === dragEl) return;
                var rect = over.getBoundingClientRect();
                var after = (e.clientY - rect.top) > rect.height / 2;
                list.insertBefore(dragEl, after ? over.nextSibling : over);
            });
            list.addEventListener('drop', function (e) {
                if (!dragEl) return;
                e.preventDefault();
                var ids = [];
                list.querySelectorAll('.catrow').forEach(function (li) { ids.push(li.getAttribute('data-id')); });
                var order = document.getElementById(rid + '-order');
                var form = document.getElementById(rid + '-form');
                if (order && form) { order.value = ids.join(','); form.submit(); }
            });
        });
    }

    function init() { iconPicker(); dragReorder(); }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
