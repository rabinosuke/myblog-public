// 表示設定・再生・自動行動を分け、素材や行動ルールを独立して変更できるようにする。
import { createCocoPlayer } from "./coco-player.js?v=20261101-coco-ai-text-v2";
import { createCocoAutonomy } from "./coco-autonomy.js?v=20261031-coco-cute";
import { createCocoChat } from "./coco-chat.js?v=20261103-coco-mobile";

(() => {
    "use strict";
    const avatar = document.getElementById("coco-avatar");
    const settings = document.getElementById("coco-settings");
    const waveButton = avatar?.querySelector("[data-coco-wave]");
    const toggle = document.getElementById("coco-toggle");
    const stateLabel = toggle?.querySelector("[data-coco-state]");
    const settingsButton = document.getElementById("coco-settings-button");
    const settingsPanel = document.getElementById("coco-settings-panel");
    if (![avatar, settings, waveButton, toggle, stateLabel, settingsButton, settingsPanel].every(Boolean)) return;

    function readVisibility() {
        try { return localStorage.getItem("coco.visible") !== "false"; }
        catch { return true; }
    }
    function saveVisibility() {
        try { localStorage.setItem("coco.visible", String(visible)); }
        catch { /* 保存できなくても、このページでの切り替えは続ける。 */ }
    }

    let visible = readVisibility();
    let autonomy = null;
    let chat = null;
    let latestState = null;
    let conversationMotionRevision = 0;

    function syncWaveButton() {
        const manualWave = latestState?.motion === "wave" && latestState.source === "manual";
        waveButton.setAttribute("aria-disabled", String(manualWave || Boolean(chat?.isBusy())));
    }

    const player = createCocoPlayer(avatar, { onStateChange(state) {
        latestState = state;
        syncWaveButton();
        autonomy?.update(state);
    } });
    autonomy = createCocoAutonomy(player);
    autonomy.update(latestState);

    function renderVisibility() {
        if (!visible) chat?.cancel();
        avatar.hidden = !visible;
        toggle.setAttribute("aria-checked", String(visible));
        stateLabel.textContent = visible ? "ON" : "OFF";
        // 表示ONなら自動で動く。旧coco.autonomous設定には依存せず、OFFなら予約も止める。
        autonomy.setEnabled(visible);
        player.setVisible(visible);
    }
    function setPanelOpen(open) {
        settingsPanel.hidden = !open;
        settingsButton.setAttribute("aria-expanded", String(open));
        if (open) chat?.close({ restoreFocus: false });
        syncPanelPause();
    }

    function syncPanelPause() {
        // 片方を閉じても、もう片方が開いている間は自動行動を再開しない。
        autonomy.setPanelOpen(!settingsPanel.hidden || Boolean(chat?.isOpen()));
    }

    function setConversationMotion(state) {
        const revision = ++conversationMotionRevision;
        syncWaveButton();
        if (state === "idle") {
            if (latestState?.source === "chat") player.stop({ source: "chat" });
            return;
        }
        const motion = { listening: "listen", thinking: "think", speaking: "talk" }[state];
        player.stop({ source: "chat" });
        if (!motion || !visible || !latestState?.canAnimate) return;

        async function playWhileCurrent() {
            // 素材は全身コマのまま再生。状態が変わると古いループを終了する。
            const completed = await player.play(motion, { source: "chat" });
            if (completed && revision === conversationMotionRevision && visible && latestState?.canAnimate) {
                void playWhileCurrent();
            }
        }
        void playWhileCurrent();
    }

    chat = createCocoChat({
        onOpenChange(open) {
            document.documentElement.dataset.cocoChatOpen = String(open);
            if (open) {
                setPanelOpen(false);
                autonomy.manualInteraction();
                player.stop();
                player.resetPosition();
            }
            syncPanelPause();
        },
        onConversationStateChange: setConversationMotion
    });

    waveButton.addEventListener("click", () => {
        if (!visible || waveButton.getAttribute("aria-disabled") === "true") return;
        autonomy.manualInteraction();
        void player.wave();
    });
    settingsButton.addEventListener("click", () => setPanelOpen(settingsPanel.hidden));
    toggle.addEventListener("click", () => {
        visible = !visible;
        renderVisibility();
        saveVisibility();
    });
    document.addEventListener("click", event => {
        if (!settingsPanel.hidden && !settings.contains(event.target)) setPanelOpen(false);
    });
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape" || settingsPanel.hidden) return;
        event.preventDefault();
        const returnFocus = settings.contains(document.activeElement);
        setPanelOpen(false);
        if (returnFocus) settingsButton.focus();
    });

    renderVisibility();
    settings.hidden = false;
    chat?.restore();
})();
