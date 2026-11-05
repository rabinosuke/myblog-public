/** 次の行動だけを予約し、非表示中の行動や手動操作後の予約を持ち越さない。 */
export function createCocoAutonomy(player, { random = Math.random, now = () => performance.now() } = {}) {
    let enabled = false;
    let panelOpen = false;
    let state = null;
    let timer = null;
    let running = null;
    let quietUntil = 0;
    let lastMotion = null;
    let hasActed = false;
    const failed = new Set();

    function clearTimer() {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
    }

    function cancelRun() {
        // stopの通知や古いPromiseが戻っても、取り消した自動行動を再予約しない。
        running = null;
        if (state?.busy && state.source === "auto") player.stop({ source: "auto" });
    }

    function eligible() {
        return enabled && !panelOpen && state?.canAnimate && !state.busy;
    }

    function chooseAction() {
        const position = player.getPosition();
        // 新しい仕草も散歩と同じ一回再生として扱い、動きが重ならないようにする。
        const choices = [
            ["walk", position.span > 0 ? 30 : 0], ["wave", 8], ["think", 6],
            ["happy", 6], ["listen", 6], ["hop", 6], ["sit", 3], ["doze", 2],
            ["surprised", 2], ["dejected", 1], ["peek", position.offset > -24 ? 2 : 0],
            ["nyan", 10], ["bashful", 8], ["heart", 8], ["wink", 8], ["sway", 8], ["pout", 5]
        ].filter(([name, weight]) => weight > 0 && name !== lastMotion && !failed.has(name));
        if (!choices.length) return null;
        let point = random() * choices.reduce((sum, [, weight]) => sum + weight, 0);
        const name = choices.find(([, weight]) => (point -= weight) < 0)?.[0] ?? choices.at(-1)[0];
        if (name !== "walk") return { name };
        // 端では必ず画面の内側へ向け、短い散歩で本文の閲覧を妨げ続けない。
        const margin = Math.min(24, position.span / 4);
        const direction = position.offset >= -margin ? -1 : position.offset <= -position.span + margin ? 1 : random() < 0.5 ? -1 : 1;
        return { name, direction, durationMs: 1500 + Math.round(random() * 1700) };
    }

    function schedule() {
        if (!eligible() || running) {
            clearTimer();
            return;
        }
        if (timer !== null) return;
        const idleWait = hasActed ? 10000 + random() * 12000 : 8000 + random() * 6000;
        const delay = Math.max(idleWait, quietUntil - now());
        timer = window.setTimeout(() => {
            timer = null;
            if (!eligible() || running) return;
            const action = chooseAction();
            if (!action) return;
            const token = {};
            running = token;
            hasActed = true;
            const completion = action.name === "walk"
                ? player.walk(action.direction, { travel: true, source: "auto", durationMs: action.durationMs })
                : player.play(action.name, { source: "auto" });
            Promise.resolve(completion).then(completed => {
                if (running !== token) return;
                running = null;
                if (completed) lastMotion = action.name;
                else {
                    failed.add(action.name);
                    quietUntil = Math.max(quietUntil, now() + 30000);
                }
                schedule();
            });
        }, delay);
    }

    return {
        update(nextState) {
            state = nextState;
            if (!state.canAnimate) cancelRun();
            schedule();
        },
        setEnabled(value) {
            enabled = value;
            clearTimer();
            if (!enabled) cancelRun();
            schedule();
        },
        setPanelOpen(value) {
            panelOpen = value;
            clearTimer();
            if (panelOpen) cancelRun();
            schedule();
        },
        manualInteraction() {
            // 手動操作直後は必ず待ち、見たい動きを自動行動で上書きしない。
            quietUntil = now() + 20000;
            clearTimer();
            cancelRun();
            schedule();
        }
    };
}
