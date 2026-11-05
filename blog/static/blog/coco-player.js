import { createCocoMovement } from "./coco-movement.js?v=20261031-coco-actions";

/** 全身のコマと再生時間をJSONに分け、素材の差し替えで再生コードを変えずに済むようにする。 */
export function createCocoPlayer(avatar, { onStateChange = () => {} } = {}) {
    const canvas = avatar.querySelector("canvas");
    const still = avatar.querySelector("img");
    const context = canvas?.getContext("2d");

    // Canvasが使えない環境でも、元の静止画による表示を残す。
    if (!canvas || !still || !context) {
        onStateChange({ motion: "still", busy: false, canWalk: false, canAnimate: false, reason: "unavailable" });
        return { setVisible() {}, play: async () => false, wave: async () => false, walk: async () => false, stop() {}, resetPosition() {}, getPosition: () => ({ offset: 0, span: 0 }) };
    }

    const movement = createCocoMovement(avatar);

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const assets = new Map();
    const loading = new Map();
    const actions = new Set(["wave", "hop", "talk", "listen", "think", "happy", "surprised", "dejected", "sit", "doze", "peek", "nyan", "bashful", "heart", "wink", "sway", "pout"]);
    let catalogPromise = null;
    let enabled = false;
    let pageActive = true;
    let active = null;
    let actionTask = null;
    let requestId = null;
    let reducedTimer = null;
    let startedAt = null;
    let drawnFrame = -1;
    let revision = 0;

    let lastReason = "";
    let lastSource = "manual";

    function notify(reason) {
        if (reason !== undefined) lastReason = reason;
        const state = {
            motion: actionTask?.name ?? avatar.dataset.cocoMotion,
            direction: actionTask?.direction ?? 0,
            travel: actionTask?.travel ?? false,
            loading: !!actionTask && active?.name !== actionTask.name && reducedTimer === null,
            canWalk: canAnimate(),
            canAnimate: canAnimate(),
            busy: !!actionTask,
            source: actionTask?.source ?? lastSource,
            position: movement.snapshot(),
            reason: lastReason
        };
        avatar.dataset.cocoAction = state.motion;
        avatar.dataset.cocoTravel = String(state.travel && active?.name === "walk");
        avatar.dataset.cocoSource = state.source;
        onStateChange(state);
    }

    function showStill() {
        canvas.hidden = true;
        still.hidden = false;
        avatar.dataset.cocoMotion = "still";
        avatar.dataset.cocoFacing = "front";
        avatar.dataset.cocoFrame = "0";
    }

    function stop() {
        if (requestId !== null) window.cancelAnimationFrame(requestId);
        if (reducedTimer !== null) window.clearTimeout(reducedTimer);
        requestId = null;
        reducedTimer = null;
        startedAt = null;
        active = null;
        movement.stop();
        drawnFrame = -1;
        revision += 1;
        avatar.dataset.cocoPlaying = "false";
    }

    async function manifestFor(name) {
        if (avatar.dataset[`${name}Manifest`]) return new URL(avatar.dataset[`${name}Manifest`], document.baseURI);
        const catalogUrl = new URL(avatar.dataset.motionCatalog, document.baseURI);
        catalogPromise ??= fetch(catalogUrl).then(async response => {
            if (!response.ok) throw new Error("モーション一覧を取得できませんでした。");
            const catalog = await response.json();
            if (catalog.version !== 1 || !catalog.motions) throw new Error("モーション一覧が不正です。");
            return catalog;
        });
        const catalog = await catalogPromise;
        const path = catalog.motions[name];
        if (typeof path !== "string" || !path) throw new Error("モーションが一覧にありません。");
        return new URL(path, catalogUrl);
    }

    function loadMotion(name) {
        if (loading.has(name)) return loading.get(name);

        const pending = (async () => {
            const manifestUrl = await manifestFor(name);
            const response = await fetch(manifestUrl);
            if (!response.ok) throw new Error("モーションの定義を取得できませんでした。");
            const config = await response.json();

            // sequenceは0始まりのコマ番号、holdMsは各表示の長さ。必ず画像の範囲と合計時間を検査する。
            if (!Number.isInteger(config.frameWidth) || config.frameWidth <= 0 ||
                !Number.isInteger(config.frameHeight) || config.frameHeight <= 0 ||
                typeof config.image !== "string" || !config.image ||
                !Array.isArray(config.frames) || config.frames.length === 0 ||
                !Array.isArray(config.sequence) || config.sequence.length === 0 ||
                !Array.isArray(config.holdMs) || config.sequence.length !== config.holdMs.length ||
                !config.sequence.every(index => Number.isInteger(index) && index >= 0 && index < config.frames.length) ||
                !config.holdMs.every(duration => Number.isFinite(duration) && duration > 0) ||
                config.holdMs.reduce((sum, duration) => sum + duration, 0) !== config.periodMs ||
                (config.reducedFrame !== undefined &&
                    (!Number.isInteger(config.reducedFrame) || config.reducedFrame < 0 || config.reducedFrame >= config.frames.length)) ||
                (config.reducedHoldMs !== undefined &&
                    (!Number.isFinite(config.reducedHoldMs) || config.reducedHoldMs <= 0)) ||
                (name === "walk" && (!Number.isFinite(config.stridePx) || config.stridePx <= 0 || config.loop !== true))) {
                throw new Error("モーションのコマ定義が不正です。");
            }

            const sheet = new Image();
            sheet.src = new URL(config.image, manifestUrl).href;
            // 全コマをデコードしてから切り替え、読み込み途中の空白を防ぐ。
            await sheet.decode();

            if (!config.frames.every(frame => frame === null || (
                Number.isInteger(frame.x) && frame.x >= 0 &&
                Number.isInteger(frame.y) && frame.y >= 0 &&
                frame.x + config.frameWidth <= sheet.naturalWidth &&
                frame.y + config.frameHeight <= sheet.naturalHeight &&
                (frame.offsetX === undefined || (Number.isFinite(frame.offsetX) && Math.abs(frame.offsetX) <= config.frameWidth))))) {
                throw new Error("モーションのコマが画像の外にはみ出しています。");
            }

            const entry = { name, config, sheet, loop: config.loop ?? name === "idle" };
            assets.set(name, entry);
            return entry;
        })().catch(error => {
            // 読み込みに失敗しても、ブログと現在の待機・静止画表示は継続する。
            console.warn(`COCOの${name}モーションを読み込めませんでした。`, error);
            return null;
        });
        loading.set(name, pending);
        return pending;
    }

    function canShow() {
        return enabled && pageActive && !document.hidden;
    }

    function canAnimate() {
        return canShow() && !reducedMotion.matches;
    }

    function drawFrame(entry, index) {
        const { config, sheet } = entry;
        const frame = config.frames[index];
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.save();
        // 左歩きも同じ全身原画を反転し、左右で頭身やコマ数を変えない。
        const mirrored = entry.name === "walk" && actionTask?.direction === -1;
        if (mirrored) {
            context.translate(canvas.width, 0);
            context.scale(-1, 1);
        }
        // 出入りの空コマもCanvas内で処理し、画面外の画像で横スクロールを発生させない。
        if (frame !== null) context.drawImage(
            sheet, frame.x, frame.y, config.frameWidth, config.frameHeight,
            (frame.offsetX ?? 0) * canvas.width / config.frameWidth, 0, canvas.width, canvas.height
        );
        context.restore();
        canvas.hidden = false;
        still.hidden = true;
        avatar.dataset.cocoFacing = entry.name === "walk" ? (mirrored ? "left" : "right") : "front";
        avatar.dataset.cocoMotion = entry.name;
        avatar.dataset.cocoFrame = String(index + 1);
        drawnFrame = index;
    }

    function settleAction(completed) {
        const task = actionTask;
        actionTask = null;
        if (task?.homeOffset !== undefined) movement.setOffset(task.homeOffset);
        task?.resolve(completed);
    }

    function finishAction(completed = true, reason = "") {
        settleAction(completed);
        resumeIdle(reason);
    }

    function animate(timestamp) {
        requestId = null;
        if (!canAnimate()) {
            updatePlayback();
            return;
        }

        if (startedAt === null) startedAt = timestamp;
        const { config, loop } = active;
        const elapsed = actionTask?.durationMs ? Math.min(timestamp - startedAt, actionTask.durationMs) : timestamp - startedAt;
        if (active.name === "walk" && actionTask?.travel && movement.step(elapsed)) {
            finishAction(true, actionTask.direction < 0 ? "left-edge" : "right-edge");
            return;
        }
        if (!loop && elapsed >= config.periodMs) {
            finishAction();
            return;
        }
        if (actionTask?.durationMs && elapsed >= actionTask.durationMs) {
            finishAction();
            return;
        }

        const phase = loop ? elapsed % config.periodMs : elapsed;
        let boundary = 0;
        let index = config.sequence[config.sequence.length - 1];
        for (let step = 0; step < config.sequence.length; step += 1) {
            boundary += config.holdMs[step];
            if (phase < boundary) {
                index = config.sequence[step];
                break;
            }
        }

        // 部位別の変形で頭身を崩さないよう、必ず全身の絵をコマごとに描く。
        if (drawnFrame !== index) drawFrame(active, index);
        requestId = window.requestAnimationFrame(animate);
    }

    function startMotion(entry) {
        stop();
        active = entry;
        if (entry.name === "peek" && actionTask) {
            actionTask.homeOffset = movement.snapshot().offset;
            drawFrame(entry, entry.config.sequence[0]);
            movement.setOffset(0);
        }
        if (entry.name === "walk" && actionTask?.travel && !movement.start(actionTask.direction, entry.config)) {
            finishAction(false, actionTask.direction < 0 ? "left-edge" : "right-edge");
            return;
        }
        drawFrame(entry, entry.config.sequence[0]);
        avatar.dataset.cocoPlaying = "true";
        requestId = window.requestAnimationFrame(animate);
        notify();
    }

    function resumeIdle(reason) {
        stop();
        if (reason !== undefined) lastReason = reason;
        movement.refreshBounds();
        if (!canAnimate()) {
            showStill();
            notify();
            return;
        }
        if (assets.has("idle")) {
            startMotion(assets.get("idle"));
            return;
        }

        showStill();
        notify();
        const expectedRevision = revision;
        loadMotion("idle").then(entry => {
            // OFFや別アクションの後に、古い読み込み結果で再生を始めない。
            if (entry && revision === expectedRevision && canAnimate() && !actionTask) startMotion(entry);
        });
    }

    function updatePlayback() {
        settleAction(false);
        resumeIdle("");
    }

    function requestAction(name, { direction = 0, travel = false, source = "manual", durationMs = null } = {}) {
        if (!canShow() || (name === "walk" && !canAnimate()) ||
            (source === "auto" && (!canAnimate() || actionTask)) ||
            (actionTask?.name === "wave" && actionTask.source === "manual")) return Promise.resolve(false);
        // 同じ方向のキーを押し続けても、歩き始めのコマへ戻さない。
        if (actionTask?.name === name && actionTask.direction === direction && actionTask.travel === travel) return actionTask.completion;
        settleAction(false);
        resumeIdle("");

        let resolve;
        const completion = new Promise(done => { resolve = done; });
        lastSource = source;
        const task = { name, direction, travel, source, durationMs, resolve, completion };
        actionTask = task;
        notify();

        // 素材は操作された時に読み込み、中止した要求へ古い結果を適用しない。
        loadMotion(name).then(entry => {
            if (actionTask !== task) return;
            if (!entry || !canShow() || (name === "walk" && !canAnimate())) {
                finishAction(false, entry ? "" : "load-error");
                return;
            }
            if (reducedMotion.matches) {
                // 動きを減らす設定でもクリックへ応答するため、手を上げた静止ポーズだけ短く見せる。
                stop();
                drawFrame(entry, entry.config.reducedFrame ?? entry.config.sequence[0]);
                reducedTimer = window.setTimeout(() => {
                    if (actionTask === task) finishAction();
                }, entry.config.reducedHoldMs ?? 600);
                notify();
            } else {
                startMotion(entry);
            }
        });
        return completion;
    }

    document.addEventListener("visibilitychange", updatePlayback);
    reducedMotion.addEventListener("change", updatePlayback);
    window.addEventListener("pagehide", () => {
        pageActive = false;
        updatePlayback();
    });
    window.addEventListener("pageshow", () => {
        pageActive = true;
        updatePlayback();
    });

    stop();
    showStill();
    notify();

    return {
        setVisible(visible) {
            enabled = visible;
            updatePlayback();
        },
        play(name, options = {}) {
            if (!actions.has(name)) return Promise.resolve(false);
            return requestAction(name, options);
        },
        wave(options = {}) {
            return requestAction("wave", options);
        },
        walk(direction, { travel = false, source = "manual", durationMs = null } = {}) {
            if (direction !== -1 && direction !== 1) return Promise.resolve(false);
            if (durationMs !== null && (!Number.isFinite(durationMs) || durationMs <= 0)) return Promise.resolve(false);
            return requestAction("walk", { direction, travel, source, durationMs });
        },
        getPosition() {
            movement.refreshBounds();
            return movement.snapshot();
        },
        resetPosition() {
            // 会話パネルの後ろに隠れないよう、右下の基準位置へ戻す。
            movement.setOffset(0);
            notify();
        },
        stop({ source = "manual" } = {}) {
            settleAction(false);
            lastSource = source;
            resumeIdle("stopped");
        }
    };
}
