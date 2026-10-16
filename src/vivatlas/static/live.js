/* Pages that keep up on their own.
 *
 * The catalogue changes under an open page all the time: a link sent from the phone
 * becomes a card a few seconds later, the AI fills a description in, a card is moved
 * to a folder on another device. Instead of pressing refresh, the page asks GET /live
 * for a small stamp of everything this person can see, every few seconds while it is
 * on screen and at once when it comes back to the front. Only when the stamp moves
 * does it fetch itself again and bring the news in place:
 *
 *   [data-live="key"]       a region swapped for the same region of the fresh page.
 *                           A card grid inside one is reconciled card by card, so
 *                           the cards that didn't change keep their nodes (no image
 *                           reloads, no flicker) and the new or changed ones fade in.
 *   [data-live-text="key"]  a counter whose text is copied over.
 *   window.vivLiveRefresh   a page with its own way to refresh (the card page) sets
 *                           this; returning false means "not now, try again later".
 *
 * A region someone is working in is left alone and retried on the next tick: the
 * focus or a half-typed value in it, a menu open in it, text selected in it. The whole
 * page waits while a card is being dragged or a scan is filling the grid.
 */
(function () {
    'use strict';
    if (!window.fetch || !window.DOMParser) return;

    var EVERY = 15000;
    var base = null;          // the stamp of what's on screen
    var busy = false;
    var again = false;        // a now() came in while a check was running
    var stopped = false;      // signed out: the poll would only collect redirects

    // Classes the page's own scripts put on a card; they aren't part of what the
    // server says the card is, so they don't make a card "changed".
    var RUNTIME = ['q-hide', 'art-fresh', 'just-added', 'dragging', 'filed'];

    function pageBusy() {
        if (document.documentElement.classList.contains('card-dragging')) return true;
        if (document.querySelector('.dragging')) return true;
        var bar = document.getElementById('scanbar');
        return !!(bar && !bar.hidden && bar.getAttribute('data-state') === 'running');
    }

    function held(el) {
        var a = document.activeElement;
        if (a && a !== document.body && el.contains(a)
            && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return true;
        if (el.querySelector('[aria-expanded="true"]')) return true;
        if (el.closest('.sheet:not([hidden])')) return true;
        var fields = el.querySelectorAll('input, textarea');
        for (var i = 0; i < fields.length; i++) {
            var f = fields[i];
            if (f.type === 'checkbox' || f.type === 'radio') {
                if (f.checked !== f.defaultChecked) return true;
            } else if (f.type !== 'hidden' && f.type !== 'file' && f.value !== f.defaultValue) {
                return true;
            }
        }
        var s = window.getSelection && window.getSelection();
        if (s && !s.isCollapsed && s.rangeCount && el.contains(s.getRangeAt(0).commonAncestorContainer)) {
            return true;
        }
        return false;
    }

    function bare(card) {
        var c = card.cloneNode(true);
        RUNTIME.forEach(function (k) { c.classList.remove(k); });
        if (!c.getAttribute('class')) c.removeAttribute('class');
        // An emptied inline style (an extension or a tool sets one and takes it back)
        // is no change either.
        Array.prototype.forEach.call(c.querySelectorAll('[style=""]'), function (e) {
            e.removeAttribute('style');
        });
        if (c.getAttribute('style') === '') c.removeAttribute('style');
        return c.outerHTML;
    }

    // The grid node itself stays (the search filter and the scan bar hold on to it);
    // only its cards are swapped, and only those that differ.
    function reconcile(grid, fresh) {
        var had = {};
        Array.prototype.forEach.call(grid.children, function (c) {
            var id = c.getAttribute('data-id');
            if (id) had[id] = c;
        });
        var next = [];
        // A copy: adopting a node takes it out of fresh.children as we walk it.
        Array.prototype.slice.call(fresh.children).forEach(function (n) {
            var id = n.getAttribute('data-id');
            var old = id && had[id];
            if (old && bare(old) === n.outerHTML) {
                if (old.classList.contains('q-hide')) n.classList.add('q-hide');
                next.push(old);
            } else {
                n.classList.add('art-fresh');
                next.push(document.adoptNode(n));
            }
        });
        grid.replaceChildren.apply(grid, next);
    }

    function swap(el, from) {
        if (el.innerHTML === from.innerHTML) return;
        var grid = el.querySelector('.grid'), fresh = from.querySelector('.grid');
        if (grid && fresh && el.children.length === 1 && from.children.length === 1) {
            reconcile(grid, fresh);
            return;
        }
        el.innerHTML = from.innerHTML;
        // The region itself has no box (display: contents), so what fades is inside.
        Array.prototype.forEach.call(el.children, function (c) {
            if (c.tagName !== 'SCRIPT') c.classList.add('art-fresh');
        });
    }

    function hasWork() {
        return typeof window.vivLiveRefresh === 'function'
            || !!document.querySelector('[data-live], [data-live-text]');
    }

    // Resolves true when everything on screen is now current, false when a part was
    // held back (the stamp then stays old, so the next tick tries again).
    function refresh() {
        if (typeof window.vivLiveRefresh === 'function') {
            return Promise.resolve(window.vivLiveRefresh()).then(function (r) { return r !== false; });
        }
        var regions = document.querySelectorAll('[data-live]');
        var texts = document.querySelectorAll('[data-live-text]');
        if (!regions.length && !texts.length) return Promise.resolve(true);
        if (pageBusy()) return Promise.resolve(false);
        return fetch(location.href, { credentials: 'same-origin' })
            .then(function (r) { return r.ok && !r.redirected ? r.text() : null; })
            .then(function (html) {
                if (html === null) return false;
                // The page may have become busy while it was fetched.
                if (pageBusy()) return false;
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var all = true;
                Array.prototype.forEach.call(regions, function (el) {
                    var from = doc.querySelector('[data-live="' + el.getAttribute('data-live') + '"]');
                    if (!from) return;
                    if (held(el)) { all = false; return; }
                    swap(el, from);
                });
                Array.prototype.forEach.call(texts, function (el) {
                    var from = doc.querySelector('[data-live-text="' + el.getAttribute('data-live-text') + '"]');
                    if (from && from.textContent !== el.textContent) el.textContent = from.textContent;
                });
                document.dispatchEvent(new CustomEvent('viv:live'));
                return all;
            });
    }

    function check(force) {
        if (busy && force) again = true;
        if (stopped || busy || document.visibilityState === 'hidden' || !hasWork()) {
            return Promise.resolve();
        }
        busy = true;
        return fetch('/live', { credentials: 'same-origin', cache: 'no-store',
                                headers: { 'Accept': 'application/json' } })
            .then(function (r) {
                if (r.redirected || r.status === 401) { stopped = true; return null; }
                var json = (r.headers.get('content-type') || '').indexOf('json') !== -1;
                return r.ok && json ? r.json() : null;
            })
            .then(function (d) {
                if (!d || !d.v) return;
                if (base === null && !force) { base = d.v; return; }
                if (d.v === base && !force) return;
                return refresh().then(function (ok) { if (ok) base = d.v; });
            })
            .catch(function () { /* offline for a moment: the next tick asks again */ })
            .then(function () {
                busy = false;
                if (again) { again = false; return check(true); }
            });
    }

    // For a page script that knows something just changed (a save it was waiting
    // for went through): bring it in now rather than on the next tick.
    window.vivLive = { now: function () { return check(true); } };

    setInterval(check, EVERY);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') check();
    });
    window.addEventListener('focus', function () { check(); });
    // After the page's own scripts: the card page sets its hook below this file.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { check(); });
    } else {
        check();
    }
})();
