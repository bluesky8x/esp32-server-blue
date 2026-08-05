import { log } from '../../utils/logger.js?v=0205';

const DEFAULT_DURATION_MS = 5000;
const MIN_DURATION_MS = 100;
const MAX_DURATION_MS = 30000;

/** @type {{ timer: ReturnType<typeof setTimeout>|null, raf: number|null, action: string|null, baseX: number, baseY: number }} */
const state = {
    timer: null,
    raf: null,
    action: null,
    baseX: 0,
    baseY: 0,
};

const ACTION_LABELS = {
    stop: '🛑 Dừng motor (simulator)',
    forward: '⬆️ Tiến (simulator)',
    backward: '⬇️ Lùi (simulator)',
    turn_left: '⬅️ Quay trái (simulator)',
    turn_right: '➡️ Quay phải (simulator)',
    move: '🤖 Di chuyển (simulator)',
};

function clampDurationMs(raw) {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) {
        return DEFAULT_DURATION_MS;
    }
    return Math.min(MAX_DURATION_MS, Math.max(MIN_DURATION_MS, Math.round(n)));
}

export function parseMotorDurationMs(toolArgs = {}) {
    if (toolArgs.duration_ms != null) {
        return clampDurationMs(toolArgs.duration_ms);
    }
    if (toolArgs.duration_sec != null) {
        return clampDurationMs(Number(toolArgs.duration_sec) * 1000);
    }
    return DEFAULT_DURATION_MS;
}

export function resolveMoveActionFromWheel(left, right) {
    if (left > 0 && right > 0) return 'forward';
    if (left < 0 && right < 0) return 'backward';
    if (left < 0 && right > 0) return 'turn_left';
    if (left > 0 && right < 0) return 'turn_right';
    return 'move';
}

function ensureOverlay() {
    let el = document.getElementById('motorSimBadge');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'motorSimBadge';
    el.style.cssText = [
        'position:fixed',
        'left:50%',
        'top:72px',
        'transform:translateX(-50%)',
        'z-index:9999',
        'padding:10px 16px',
        'border-radius:12px',
        'background:rgba(20,24,40,0.82)',
        'color:#fff',
        'font:600 14px/1.3 system-ui,sans-serif',
        'box-shadow:0 8px 24px rgba(0,0,0,0.25)',
        'pointer-events:none',
        'opacity:0',
        'transition:opacity 0.2s ease',
    ].join(';');
    document.body.appendChild(el);
    return el;
}

function getLive2dModel() {
    const mgr = window.live2dManager;
    return mgr && mgr.live2dModel ? mgr.live2dModel : null;
}

function motionOffset(action, t) {
    const wave = Math.sin(t * Math.PI * 2);
    switch (action) {
        case 'forward':
            return { dx: 0, dy: -10 * wave };
        case 'backward':
            return { dx: 0, dy: 10 * wave };
        case 'turn_left':
            return { dx: -8 * wave, dy: 0, rot: -0.04 * wave };
        case 'turn_right':
            return { dx: 8 * wave, dy: 0, rot: 0.04 * wave };
        default:
            return { dx: 4 * wave, dy: 0 };
    }
}

function stopMotionAnimation(resetPosition = true) {
    if (state.raf != null) {
        cancelAnimationFrame(state.raf);
        state.raf = null;
    }
    if (state.timer != null) {
        clearTimeout(state.timer);
        state.timer = null;
    }
    const model = getLive2dModel();
    if (model && resetPosition) {
        model.x = state.baseX;
        model.y = state.baseY;
        if (typeof model.rotation === 'number') {
            model.rotation = 0;
        }
    }
    state.action = null;
    const badge = document.getElementById('motorSimBadge');
    if (badge) {
        badge.style.opacity = '0';
    }
}

function runMotionAnimation(action, durationMs) {
    const model = getLive2dModel();
    if (model) {
        state.baseX = model.x;
        state.baseY = model.y;
    }
    const started = performance.now();

    const tick = (now) => {
        if (state.action !== action) return;
        const elapsed = now - started;
        const t = Math.min(1, elapsed / durationMs);
        const off = motionOffset(action, t * 3);
        if (model) {
            model.x = state.baseX + (off.dx || 0);
            model.y = state.baseY + (off.dy || 0);
            if (typeof model.rotation === 'number' && off.rot) {
                model.rotation = off.rot;
            }
        }
        const badge = document.getElementById('motorSimBadge');
        if (badge) {
            const secLeft = Math.max(0, Math.ceil((durationMs - elapsed) / 1000));
            const base = ACTION_LABELS[action] || action;
            badge.textContent = `${base} · ${secLeft}s`;
        }
        if (elapsed < durationMs) {
            state.raf = requestAnimationFrame(tick);
        }
    };

    state.raf = requestAnimationFrame(tick);
    state.timer = setTimeout(() => {
        stopMotionAnimation(true);
    }, durationMs);
}

/**
 * Run timed motor simulation using server-provided duration_ms.
 * Returns immediately; motion runs asynchronously until duration elapses.
 */
export function showMotorSimulation(action, label, durationMs = DEFAULT_DURATION_MS) {
    const ms = clampDurationMs(durationMs);
    if (action === 'stop') {
        stopMotionAnimation(true);
        const badge = ensureOverlay();
        badge.textContent = label || ACTION_LABELS.stop;
        badge.style.opacity = '1';
        setTimeout(() => {
            if (state.action == null) badge.style.opacity = '0';
        }, 800);
        log(`[motor sim] stop`, 'success');
        return { duration_ms: 0 };
    }

    stopMotionAnimation(false);
    state.action = action;
    const badge = ensureOverlay();
    const sec = Math.round(ms / 1000);
    badge.textContent = label || `${ACTION_LABELS[action] || action} · ${sec}s`;
    badge.style.opacity = '1';
    log(`[motor sim] ${action} ${sec}s (duration_ms=${ms})`, 'success');
    runMotionAnimation(action, ms);
    return { duration_ms: ms };
}

export function stopMotorSimulation() {
    stopMotionAnimation(true);
}
