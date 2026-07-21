/* Category-folder management: icon selection, drag-to-reorder, and creation
 * WITHOUT a reload. One code path for settings (personal folders) and the admin
 * panel (shared) — everywhere there is a .cat-manage / .catrow-add. Everything is
 * delegated from document, so rows added by the script on the fly work at once.
 */
(function () {
    'use strict';

    function closeGrids(except) {
        document.querySelectorAll('.icon-grid').forEach(function (g) {
            if (g !== except) g.hidden = true;
        });
    }

    // --- icon selection (delegation) --------------------------------------
    // The icon button opens the grid; clicking an icon sets it in the hidden field
    // and on the button. For an existing folder we save immediately (update form);
    // for a new one — just set the icon until "Create" is pressed.
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

    // --- drag-to-reorder (delegation) ---------------------------
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
        if (list !== dragList) return;             // only drag within its own list
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

    // --- create a folder without reloading ------------------------------------
    // We submit the form via fetch; the server returns the new row's HTML, and we
    // insert it into the list at once. Without script/network — a plain submit (server redirects).
    document.addEventListener('submit', function (e) {
        var form = e.target.closest ? e.target.closest('.catrow-add') : null;
        if (!form) return;
        var nameInput = form.querySelector('input[name="name"]');
        if (nameInput && !nameInput.value.trim()) return;   // empty — required won't allow it
        e.preventDefault();
        fetch(form.action, {
            method: 'POST',
            headers: { 'Accept': 'application/json' },
            body: new FormData(form)
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d || !d.ok) { if (d && d.error) alert(d.error); return; }
            var list = form.parentNode.querySelector('.cat-manage');
            if (!list) { form.submit(); return; }           // safety net (the list is always rendered)
            var tmp = document.createElement('div');
            tmp.innerHTML = (d.html || '').trim();
            var row = tmp.firstElementChild;
            if (row) list.appendChild(row);
            // Reset the add form to its initial state.
            if (nameInput) nameInput.value = '';
            var iconInput = form.querySelector('input[name="icon"]');
            if (iconInput) iconInput.value = '';
            var pick = form.querySelector('.cat-pick');
            if (pick) pick.innerHTML = '<span class="cat-pick-empty">+</span>';
            if (nameInput) nameInput.focus();
        }).catch(function () { form.submit(); });           // failed — plain submit
    });
})();
