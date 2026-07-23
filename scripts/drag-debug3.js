// Phase 1 (v3): Answer two specific questions:
// Q1: How many times does SpatialPositionStore.emit() fire during a drag?
// Q2: How many times does the node's style actually change?
// Q3: What's the state of draggingIdRef after step 1 fires?

(function() {
    if (window.__dragDebug) window.__dragDebug.cleanup();

    const logs = [];
    window.__dragLog = logs;
    const log = (...args) => {
        const msg = args.join(' ');
        logs.push(msg);
        console.log('[DRAG-DEBUG3]', msg);
    };

    const listeners = [];
    const addL = (el, ev, fn, opts) => { el.addEventListener(ev, fn, opts); listeners.push([el, ev, fn]); };
    const observers = [];

    const cleanup = () => {
        listeners.forEach(([el, ev, fn]) => el.removeEventListener(ev, fn));
        observers.forEach(o => o.disconnect());
    };

    const node = document.querySelector('[class*="vc-spatial-node"]');
    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
    if (!node || !canvas) { log('ERROR: elements not found'); return 'not found'; }

    // Q2: Count node style changes via MutationObserver
    let styleChangeCount = 0;
    const styleMO = new MutationObserver(muts => {
        for (const m of muts) {
            if (m.attributeName === 'style') {
                styleChangeCount++;
                log(`STYLE CHANGE #${styleChangeCount}: ${node.style.left} ${node.style.top}`);
            }
        }
    });
    styleMO.observe(node, { attributes: true, attributeFilter: ['style'] });
    observers.push(styleMO);

    // Q3: intercept SpatialPositionStore.emit via Vencord's webpack module system
    // Try to find the store by duck-typing on window / Vencord
    let storeEmitPatched = false;
    let emitCount = 0;

    try {
        // Vencord exposes webpack finds
        const findStore = () => {
            // Try to find a module that has emit + getRevision
            if (!window.Vencord?.Webpack) return null;
            return window.Vencord.Webpack.findByProps('getRevision', 'emit');
        };
        const store = findStore();
        if (store && store.emit) {
            const origEmit = store.emit.bind(store);
            store.emit = function() {
                emitCount++;
                log(`STORE EMIT #${emitCount}`);
                return origEmit.apply(this, arguments);
            };
            storeEmitPatched = true;
            log('Store emit patched successfully');
        } else {
            log('Could not find SpatialPositionStore via Vencord.Webpack');
        }
    } catch(e) {
        log(`Store patch error: ${e.message}`);
    }

    // Pointer events: detect pointerdown, log each move, watch for capture loss
    let moveCount = 0;
    let dragActive = false;
    let captureNode = null;
    let capturePointerId = null;

    addL(node, 'lostpointercapture', e => {
        log(`LOST CAPTURE pointerId=${e.pointerId} movesSoFar=${moveCount} styleChanges=${styleChangeCount}`);
    });
    addL(node, 'gotpointercapture', e => {
        log(`GOT CAPTURE pointerId=${e.pointerId}`);
    });

    addL(document, 'pointerdown', e => {
        if (e.target === node) {
            dragActive = true; moveCount = 0; styleChangeCount = 0; emitCount = 0;
            captureNode = node; capturePointerId = e.pointerId;
            log(`POINTERDOWN pointerId=${e.pointerId} pos=(${e.clientX.toFixed(0)},${e.clientY.toFixed(0)})`);
        }
    }, true);

    addL(document, 'pointermove', e => {
        if (!dragActive) return;
        moveCount++;
        // Log EVERY move to see full picture
        const hasCapture = node.hasPointerCapture?.(capturePointerId);
        log(`MOVE #${moveCount} pos=(${e.clientX.toFixed(0)},${e.clientY.toFixed(0)}) hasCapture=${hasCapture} emits=${emitCount} styleChanges=${styleChangeCount}`);
    }, true);

    addL(document, 'pointerup', e => {
        if (dragActive) {
            log(`POINTERUP moves=${moveCount} emits=${emitCount} styleChanges=${styleChangeCount} final=(${node.style.left},${node.style.top})`);
            dragActive = false;
        }
    }, true);

    window.__dragDebug = { cleanup, logs };
    log(`Ready. Node at ${node.style.left},${node.style.top}. storePatched=${storeEmitPatched}`);
    return `OK storePatched=${storeEmitPatched}`;
})()
