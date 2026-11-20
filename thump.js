// Adds a short "thump" effect to any button whenever it receives a click
// event: real mouse, keyboard (Enter/Space), element.click(), or synthetic
// events dispatched by automation tools such as Playwright.
//
// Rapid clicks overlap rather than restart: every click spawns its own
// highlight ring (with a random color) and its own additive squish animation.
(function () {
    var SELECTOR = 'button, input[type="button"], input[type="submit"], [role="button"], .button, .button2';
    var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function randomColor() {
        return 'hsl(' + Math.floor(Math.random() * 360) + ', 100%, 55%)';
    }

    function spawnRing(el, color) {
        var r = el.getBoundingClientRect();
        var ring = document.createElement('div');
        ring.className = 'thump-ring';
        ring.style.left = r.left + 'px';
        ring.style.top = r.top + 'px';
        ring.style.width = r.width + 'px';
        ring.style.height = r.height + 'px';
        ring.style.borderRadius = getComputedStyle(el).borderRadius;
        ring.style.setProperty('--thump-color', color);
        ring.addEventListener('animationend', function () { ring.remove(); });
        setTimeout(function () { ring.remove(); }, 2000); // fallback cleanup
        document.body.appendChild(ring);
    }

    function squish(el) {
        if (reduceMotion || !el.animate) return;
        // composite: 'add' stacks concurrent squishes on top of each other
        // (and on top of any existing transform) instead of replacing them.
        try {
            el.animate([
                { transform: 'scale(1)' },
                { transform: 'scale(0.82)', offset: 0.2 },
                { transform: 'scale(1.12)', offset: 0.55 },
                { transform: 'scale(0.97)', offset: 0.8 },
                { transform: 'scale(1)' }
            ], { duration: 550, easing: 'ease-out', composite: 'add' });
        } catch (err) { /* composite unsupported: skip squish */ }
    }

    document.addEventListener('click', function (e) {
        var el = e.target && e.target.closest ? e.target.closest(SELECTOR) : null;
        if (!el) return;
        spawnRing(el, randomColor());
        squish(el);
    }, true);
})();
