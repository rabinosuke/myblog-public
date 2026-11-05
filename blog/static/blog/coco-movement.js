// 座標だけを管理し、歩くコマと同じ時計で位置を進めて足運びとのずれを防ぐ。
export function createCocoMovement(avatar) {
    const gap = 16;
    let offset = 0;
    let span = 0;
    let width = 0;
    let run = null;
    let lastElapsed = 0;

    function render() {
        avatar.style.setProperty("--coco-offset-x", `${offset}px`);
        avatar.dataset.cocoX = offset.toFixed(2);
    }

    function pixelsPerMs(config) {
        return config.stridePx * (width / config.frameWidth) / config.periodMs;
    }

    function refreshBounds() {
        const measuredWidth = avatar.getBoundingClientRect().width;
        // OFF中は幅が0になるため、表示時に測り直すまで現在の位置を保持する。
        if (measuredWidth <= 0) return;
        width = measuredWidth;
        span = Math.max(0, document.documentElement.clientWidth - width - gap * 2);
        offset = Math.max(-span, Math.min(0, offset));
        if (run) {
            // 画面幅が変わっても、直前の位置から新しい表示倍率で歩き続ける。
            run.origin = offset;
            run.fromElapsed = lastElapsed;
            run.speed = pixelsPerMs(run.config);
        }
        render();
    }

    window.addEventListener("resize", refreshBounds);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(refreshBounds).observe(avatar);
    render();

    return {
        refreshBounds,
        snapshot() {
            return { offset, span, width };
        },
        setOffset(value) {
            refreshBounds();
            offset = Math.max(-span, Math.min(0, value));
            render();
        },
        start(direction, config) {
            refreshBounds();
            if (span <= 0 || (direction < 0 ? offset <= -span : offset >= 0)) return false;
            lastElapsed = 0;
            run = { direction, config, origin: offset, fromElapsed: 0, speed: pixelsPerMs(config) };
            return true;
        },
        step(elapsed) {
            if (!run) return false;
            lastElapsed = elapsed;
            const next = run.origin + run.direction * run.speed * (elapsed - run.fromElapsed);
            offset = Math.max(-span, Math.min(0, next));
            render();
            return run.direction < 0 ? offset <= -span : offset >= 0;
        },
        stop() {
            run = null;
        }
    };
}
