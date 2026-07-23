// Phase 1: Gather evidence about drag behavior
// Checks: node remounting, pointer capture loss, document listener continuity

(function() {
    // Clean up previous run
    if (window.__dragDebug) {
        window.__dragDebug.cleanup();
    }

    const log = (...args) => console.log('[DRAG-DEBUG]', ...args);
    const listeners = [];
    const addL = (el, ev, fn, opts) => { el.addEventListener(ev, fn, opts); listeners.push([el, ev, fn]); };
    const cleanup = () => listeners.forEach(([el, ev, fn]) => el.removeEventListener(ev, fn));

    // Find spatial canvas and nodes
    const canvas = document.querySelector('[class*="vc-spatial-canvas"]');
    const nodes = () => document.querySelectorAll('[class*="vc-spatial-node"]');

    if (!canvas) {
        log('ERROR: canvas not found — is the spatial modal open?');
        return 'canvas not found';
    }

    // Tag existing nodes with stable identity markers to detect remounting
    let tagCount = 0;
    const tagNodes = () => {
        nodes().forEach(n => {
            if (!n._debugTag) {
                n._debugTag = ++tagCount;
                log(`Tagged node #${n._debugTag} userId=${n.dataset.participant}`);
            }
        });
    };
    tagNodes();

    // Watch for lostpointercapture on all nodes (now and future)
    const observeNode = (n) => {
        addL(n, 'lostpointercapture', e => {
            log(`lostpointercapture on #${n._debugTag} (userId=${n.dataset.participant}) pointerId=${e.pointerId}`);
        });
        addL(n, 'gotpointercapture', e => {
            log(`gotpointercapture on #${n._debugTag} (userId=${n.dataset.participant}) pointerId=${e.pointerId}`);
        });
    };
    nodes().forEach(observeNode);

    // MutationObserver: detect if the canvas children change (nodes added/removed = remount)
    const mo = new MutationObserver(mutations => {
        for (const m of mutations) {
            for (const added of m.addedNodes) {
                if (added.nodeType === 1 && added.className?.includes?.('vc-spatial-node')) {
                    log(`NODE ADDED: userId=${added.dataset?.participant} _debugTag=${added._debugTag ?? 'none (NEW DOM node)'}`);
                    tagNodes();
                    observeNode(added);
                }
            }
            for (const removed of m.removedNodes) {
                if (removed.nodeType === 1 && removed.className?.includes?.('vc-spatial-node')) {
                    log(`NODE REMOVED: userId=${removed.dataset?.participant} _debugTag=${removed._debugTag ?? 'none'}`);
                }
            }
        }
    });
    mo.observe(canvas, { childList: true, subtree: true });
    listeners.push([{ removeEventListener: () => mo.disconnect() }, 'x', () => {}]);

    // Document-level pointermove: count events during drag
    let moveCount = 0;
    let dragActive = false;
    let captureNode = null;
    let capturePointerId = null;

    addL(document, 'pointerdown', e => {
        if (e.target.className?.includes?.('vc-spatial-node')) {
            dragActive = true;
            moveCount = 0;
            captureNode = e.target;
            capturePointerId = e.pointerId;
            log(`pointerdown on #${captureNode._debugTag} userId=${captureNode.dataset.participant} pointerId=${e.pointerId}`);
        }
    }, true);

    addL(document, 'pointermove', e => {
        if (!dragActive) return;
        moveCount++;

        // Every 5 moves: check whether capture is still held
        if (moveCount % 5 === 1) {
            const stillHeld = captureNode?.hasPointerCapture?.(capturePointerId);
            const currentTag = captureNode?._debugTag;
            // Also check all nodes to see if a DIFFERENT node has capture
            let captureHolder = null;
            nodes().forEach(n => {
                if (n.hasPointerCapture?.(capturePointerId)) captureHolder = n;
            });
            log(`move #${moveCount} | captureNode #${currentTag} hasCapture=${stillHeld} | actualHolder=#${captureHolder?._debugTag ?? 'none'}`);
        }
    }, true);

    addL(document, 'pointerup', e => {
        if (dragActive) {
            log(`pointerup: total moves=${moveCount} pointerId=${e.pointerId}`);
            dragActive = false;
            captureNode = null;
        }
    }, true);

    window.__dragDebug = { cleanup };

    log('Instrumentation installed. Open the spatial modal and drag a participant node.');
    log(`Nodes tagged: ${tagCount}`);
    return `OK — ${tagCount} node(s) tagged`;
})()
