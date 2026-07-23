// Phase 1 (v2): Gather evidence — store logs in window.__dragLog for retrieval
(function() {
    if (window.__dragDebug) window.__dragDebug.cleanup();

    const logs = [];
    window.__dragLog = logs;
    const log = (...args) => {
        const msg = args.join(' ');
        logs.push(msg);
        console.log('[DRAG-DEBUG]', msg);
    };

    const listeners = [];
    const addL = (el, ev, fn, opts) => { el.addEventListener(ev, fn, opts); listeners.push([el, ev, fn, opts]); };
    const cleanup = () => {
        listeners.forEach(([el, ev, fn, opts]) => el.removeEventListener(ev, fn, opts));
        mo.disconnect();
    };

    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
    const getNodes = () => document.querySelectorAll('[class*="vc-spatial-node"]');

    if (!canvas) { log('ERROR: canvas not found'); return; }

    // Tag nodes for identity tracking
    let tagCount = 0;
    const tagNodes = () => {
        getNodes().forEach(n => {
            if (!n._debugTag) {
                n._debugTag = ++tagCount;
                log(`Tagged node #${n._debugTag} userId=${n.dataset.participant}`);
            }
        });
    };
    tagNodes();

    const observeNode = (n) => {
        addL(n, 'lostpointercapture', e => {
            log(`LOST CAPTURE #${n._debugTag} (userId=${n.dataset.participant}) pointerId=${e.pointerId}`);
        });
        addL(n, 'gotpointercapture', e => {
            log(`GOT CAPTURE #${n._debugTag} (userId=${n.dataset.participant}) pointerId=${e.pointerId}`);
        });
        // Also listen for pointermove ON the node (vs. document)
        addL(n, 'pointermove', e => {
            if (window.__dragActive) {
                window.__nodeMoveCount = (window.__nodeMoveCount || 0) + 1;
            }
        });
    };
    getNodes().forEach(observeNode);

    // MutationObserver to detect node remounting
    const mo = new MutationObserver(mutations => {
        for (const m of mutations) {
            for (const added of m.addedNodes) {
                if (added.nodeType === 1 && added.className?.includes?.('vc-spatial-node')) {
                    log(`NODE ADDED userId=${added.dataset?.participant} tag=${added._debugTag ?? 'NONE=remounted'}`);
                    tagNodes();
                    observeNode(added);
                }
            }
            for (const removed of m.removedNodes) {
                if (removed.nodeType === 1 && removed.className?.includes?.('vc-spatial-node')) {
                    log(`NODE REMOVED userId=${removed.dataset?.participant} tag=${removed._debugTag}`);
                }
            }
        }
    });
    mo.observe(canvas, { childList: true, subtree: true });

    // Track pointermove at document level with capture details
    let moveCount = 0;
    let captureNode = null;
    let capturePointerId = null;
    window.__dragActive = false;
    window.__nodeMoveCount = 0;

    addL(document, 'pointerdown', e => {
        const t = e.target;
        if (t?.className?.includes?.('vc-spatial-node')) {
            window.__dragActive = true;
            moveCount = 0;
            window.__nodeMoveCount = 0;
            captureNode = t;
            capturePointerId = e.pointerId;
            log(`POINTERDOWN on #${t._debugTag} userId=${t.dataset.participant} pointerId=${e.pointerId} pos=(${e.clientX.toFixed(0)},${e.clientY.toFixed(0)})`);
        }
    }, true);

    addL(document, 'pointermove', e => {
        if (!window.__dragActive) return;
        moveCount++;

        if (moveCount % 3 === 1) {
            const hasCapture = captureNode?.hasPointerCapture?.(capturePointerId);
            // Find actual capture holder
            let holder = null;
            getNodes().forEach(n => { if (n.hasPointerCapture?.(capturePointerId)) holder = n; });
            log(`MOVE #${moveCount} pos=(${e.clientX.toFixed(0)},${e.clientY.toFixed(0)}) captureNode=#${captureNode?._debugTag} hasCapture=${hasCapture} actualHolder=#${holder?._debugTag ?? 'none'} nodeMoves=${window.__nodeMoveCount}`);
        }
    }, true);

    addL(document, 'pointerup', e => {
        if (window.__dragActive) {
            const hasCapture = captureNode?.hasPointerCapture?.(capturePointerId);
            log(`POINTERUP total moves=${moveCount} captureNode=#${captureNode?._debugTag} hasCapture=${hasCapture}`);
            window.__dragActive = false;
        }
    }, true);

    // Also intercept the store emit to see when re-renders fire
    const positionStore = window.__dragDebugStore;

    window.__dragDebug = { cleanup, logs };
    log(`Ready — ${tagCount} node(s) tagged. Drag now.`);
    return `OK — ${tagCount} node(s) tagged`;
})()
