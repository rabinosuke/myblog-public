import { measureChatViewport } from "./coco-viewport.js?v=20261103-coco-mobile";
import { createCocoSpeech } from "./coco-speech.js?v=20261102-coco-speech-stream";
import { createCocoConversation } from "./coco-conversation.js?v=20261102-coco-speech-stream";
import { requestCocoResponse, selectHistory } from "./coco-api.js?v=20261102-coco-speech-stream";
import { loadChat, saveChat, UI_KEY, MAX_MESSAGES, MAX_LENGTH } from "./coco-chat-storage.js?v=20261101-coco-continuity";

/** 会話画面と保存を担当し、返信の進行は会話コントローラーに任せる。 */
export function createCocoChat({ onOpenChange = () => {}, onConversationStateChange = () => {} } = {}) {
    const button = document.getElementById("coco-chat-button");
    const panel = document.getElementById("coco-chat-panel");
    const closeButton = document.getElementById("coco-chat-close");
    const form = document.getElementById("coco-chat-form");
    const input = document.getElementById("coco-chat-input");
    const send = document.getElementById("coco-chat-send");
    const list = document.getElementById("coco-chat-messages");
    const empty = document.getElementById("coco-chat-empty");
    const scroll = document.getElementById("coco-chat-scroll");
    const status = document.getElementById("coco-chat-status");
    const voiceButton = document.getElementById("coco-chat-voice");
    const stopButton = document.getElementById("coco-chat-stop");
    const speech = createCocoSpeech();
    let speechAvailable = panel?.dataset.speechEnabled === "true";
    let voiceEnabled = false;
    try { voiceEnabled = speechAvailable && localStorage.getItem("coco.voice.enabled") === "true" && speech.supported; } catch { /* 初期値はOFF。 */ }
    function renderVoice() {
        if (!voiceButton) return;
        voiceButton.textContent = `読み上げ：${voiceEnabled ? "ON" : "OFF"}`;
        voiceButton.setAttribute("aria-pressed", String(voiceEnabled));
        voiceButton.hidden = !speechAvailable;
        voiceButton.disabled = !speechAvailable || !speech.supported;
    }
    renderVoice();
    if (![button, panel, closeButton, form, input, send, list, empty, scroll, status].every(Boolean)) return null;

    const articleSlug = form.dataset.articleSlug || "";
    const articleTitle = form.dataset.articleTitle || "";
    let saved = { draft: "", messages: [], pending: "" };
    try { saved = loadChat(sessionStorage, articleSlug); } catch { /* 保存禁止でも会話は使える。 */ }
    let { draft, messages, pending } = saved;
    let partialItem = null;
    const mobileInput = window.matchMedia?.("(max-width: 640px), (pointer: coarse)");
    const usesMobileInput = () => mobileInput?.matches === true;
    let composing = false;
    let restoreOpen = false;
    let scrollTop = null;
    function readUi() {
        restoreOpen = false;
        scrollTop = null;
        try {
            const ui = JSON.parse(sessionStorage.getItem(UI_KEY));
            restoreOpen = ui?.open === true;
            if (Number.isFinite(ui?.scrollTop) && ui.scrollTop >= 0) scrollTop = ui.scrollTop;
        } catch { /* 初回・保存禁止・壊れたデータでは閉じた状態で始める。 */ }
    }
    readUi();
    const interrupted = Boolean(pending);
    if (pending && !draft) draft = pending;
    pending = "";

    function persist() {
        draft = input.value.slice(0, MAX_LENGTH);
        try { saveChat(sessionStorage, { draft, messages, pending }); }
        catch { /* 保存できなくても、このページのDOMとメモリには会話を保持する。 */ }
    }

    function persistUi() {
        if (!panel.hidden) scrollTop = scroll.scrollTop;
        try { sessionStorage.setItem(UI_KEY, JSON.stringify({ open: !panel.hidden, scrollTop })); }
        catch { /* 保存禁止でも開閉は続けられる。 */ }
    }

    function appendMessage(message) {
        const item = document.createElement("li");
        item.className = `coco-chat-message coco-chat-message-${message.role}`;
        const author = document.createElement("span");
        author.className = message.role === "user" ? "visually-hidden" : "coco-chat-message-author";
        author.textContent = message.role === "user" ? "あなた：" : message.mode === "ai" ? "COCO" : "COCO（以前のデモ）";
        const text = document.createElement("p");
        // 入力をHTMLとして扱わず、タグやスクリプトも文字列として表示する。
        text.textContent = message.text;
        item.append(author, text);
        if (message.role === "assistant" && message.articleSlug) {
            const source = document.createElement("a");
            source.className = "coco-chat-message-source";
            source.href = `/posts/${message.articleSlug}/`;
            source.textContent = `参照記事：${message.articleTitle || message.articleSlug}`;
            item.append(source);
        }
        list.append(item);
        return item;
    }

    function clearPartial() {
        partialItem?.remove();
        partialItem = null;
    }

    function addMessage(message) {
        messages.push(message);
        if (messages.length > MAX_MESSAGES) {
            messages.shift();
            list.firstElementChild?.remove();
        }
        appendMessage(message);
        empty.hidden = true;
        persist();
        scrollToLatest();
    }

    function scrollToLatest() {
        scroll.scrollTop = scroll.scrollHeight;
        persistUi();
    }

    function updateSend() {
        send.disabled = conversation.isBusy() || !input.value.trim();
    }

    const conversation = createCocoConversation({
        respond(text, { signal, onDelta, onTextComplete, onSpeechStreamStart, onSpeechChunk, onSpeechStreamError }) {
            const readAloud = voiceEnabled;
            return requestCocoResponse({
                endpoint: form.dataset.apiUrl,
                csrfToken: form.querySelector('[name="csrfmiddlewaretoken"]')?.value,
                message: text, history: selectHistory(messages, text), articleSlug, readAloud, signal, onDelta, onTextComplete, onSpeechStreamStart, onSpeechChunk, onSpeechStreamError
            }).then(response => readAloud ? response : response.reply);
        },
        playReply: speech.play,
        createSpeechQueue: speech.createQueue,
        onSpeechError(error) {
            if (error.code === "speech_disabled") {
                // 開いたままのページでも、管理者が停止した後は音声を要求しない。
                speechAvailable = false;
                voiceEnabled = false;
                renderVoice();
            }
            status.textContent = error.message || "読み上げできませんでした。文章で確認してね。";
        },
        onStateChange(state) {
            panel.dataset.conversationState = state;
            if (stopButton) stopButton.hidden = state === "idle";
            list.setAttribute("aria-busy", String(Boolean(pending) && (state === "listening" || state === "thinking")));
            const labels = { idle: "話しかけてね", listening: "聞いているよ…", thinking: pending ? "考え中…" : "音声を準備しているよ…", speaking: "お話し中…" };
            status.textContent = labels[state];
            send.textContent = state === "idle" ? "送信" : "返信中…";
            updateSend();
            onConversationStateChange(state);
        },
        onDelta(text) {
            // 未完成文は表示だけに留め、保存・次のAPI履歴には入れない。
            partialItem ??= appendMessage({ role: "assistant", text: "", mode: "ai" });
            partialItem.querySelector("p").textContent = text;
            partialItem.querySelector(".coco-chat-message-author").textContent = "COCO（返信中）";
            empty.hidden = true;
            status.textContent = panel.dataset.conversationState === "speaking" ? "お話し中…" : "返信を書いているよ…";
            scrollToLatest();
        },
        onReply(text) {
            clearPartial();
            list.setAttribute("aria-busy", "false");
            if (voiceEnabled && panel.dataset.conversationState !== "speaking") status.textContent = "音声を準備しているよ…";
            pending = "";
            addMessage({ role: "assistant", text, mode: "ai", articleSlug, articleTitle });
        },
        onError(error, text) {
            clearPartial();
            pending = "";
            status.textContent = error.message || "返信を作れませんでした。もう一度送信してください。";
            // 次の下書きを書いている場合は上書きせず、空の時だけ再送できるよう戻す。
            if (!input.value) input.value = text;
            updateSend();
            persist();
            console.warn("COCO chat failed:", error.code ?? "unknown");
        }
    });

    function cancel() {
        const waitingForReply = Boolean(pending);
        const readingAloud = voiceEnabled && !pending;
        if (!conversation.cancel()) return;
        clearPartial();
        // ページ移動・非表示・閉じる操作で未回答の質問が消えないよう、再送できる入力へ戻す。
        if (pending && !input.value) input.value = pending;
        pending = "";
        status.textContent = waitingForReply ? "返信を中断しました。送信でもう一度質問できます。"
            : readingAloud ? "読み上げを停止しました。" : "会話を中断しました。";
        updateSend();
        persist();
    }

    function updateViewport() {
        if (panel.hidden) return;
        const atBottom = scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 32;
        const viewport = measureChatViewport(window.innerHeight, window.visualViewport);
        // 表示領域が狭い時は入力と送信を横並びにして、閉じる・停止・入力を収める。
        panel.dataset.compact = String(viewport.compact);
        panel.dataset.tight = String(viewport.tight);
        panel.style.setProperty("--coco-chat-view-height", `${viewport.height}px`);
        panel.style.setProperty("--coco-chat-keyboard-bottom", `${viewport.bottom}px`);
        // 過去の返信を読んでいる時の位置は維持し、末尾にいた場合だけ追従する。
        if (atBottom) scrollToLatest();
    }

    function close({ restoreFocus = true } = {}) {
        if (panel.hidden) return;
        cancel();
        persist();
        scrollTop = scroll.scrollTop;
        panel.hidden = true;
        persistUi();
        button.setAttribute("aria-expanded", "false");
        onOpenChange(false);
        if (restoreFocus) button.focus({ preventScroll: true });
    }

    function open({ focusInput = true } = {}) {
        if (!panel.hidden) return;
        panel.hidden = false;
        button.setAttribute("aria-expanded", "true");
        onOpenChange(true);
        updateViewport();
        if (focusInput && !usesMobileInput()) input.focus({ preventScroll: true });
        scroll.scrollTop = scrollTop ?? scroll.scrollHeight;
        persistUi();
    }

    voiceButton?.addEventListener("click", () => {
        if (!speechAvailable || !speech.supported) return;
        voiceEnabled = !voiceEnabled;
        if (voiceEnabled) void speech.prepare().catch(error => { status.textContent = error.message; });
        else cancel();
        try { localStorage.setItem("coco.voice.enabled", String(voiceEnabled)); } catch { /* このページでは切替を維持。 */ }
        renderVoice();
    });
    stopButton?.addEventListener("click", cancel);
    button.addEventListener("click", () => panel.hidden ? open() : close());
    closeButton.addEventListener("click", () => close());
    document.addEventListener("keydown", event => {
        if (event.key !== "Escape" || event.defaultPrevented || panel.hidden || event.isComposing || composing) return;
        event.preventDefault();
        close();
    });
    input.addEventListener("input", () => { updateSend(); persist(); });
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; updateSend(); persist(); });
    input.addEventListener("keydown", event => {
        // スマホは改行を使い、送信ボタンで送る。PCでも日本語変換の確定Enterでは送らない。
        if (usesMobileInput() || event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey ||
            event.isComposing || composing || event.keyCode === 229) return;
        event.preventDefault();
        form.requestSubmit();
    });
    form.addEventListener("submit", event => {
        event.preventDefault();
        if (composing || conversation.isBusy() || panel.hidden) return;
        const text = input.value.slice(0, MAX_LENGTH).trim();
        if (!text) return;
        if (text.length > 1000) {
            status.textContent = "メッセージは1000文字以内で入力してください。";
            return;
        }
        // モバイルでも再生できるよう、送信操作の中で音声出力を許可する。
        if (voiceEnabled) void speech.prepare().catch(error => { status.textContent = error.message; });
        input.value = "";
        pending = text;
        addMessage({ role: "user", text, mode: "ai", articleSlug, articleTitle });
        void conversation.submit(text);
        if (!usesMobileInput()) input.focus({ preventScroll: true });
    });
    scroll.addEventListener("scroll", persistUi);
    window.addEventListener("pagehide", () => { cancel(); persist(); persistUi(); });
    // bfcacheから戻ったページも、別ページで更新した開閉状態・履歴を読み直す。
    window.addEventListener("pageshow", event => {
        if (!event.persisted) return;
        try { saved = loadChat(sessionStorage, articleSlug); } catch { return; }
        messages = saved.messages;
        try { voiceEnabled = speechAvailable && localStorage.getItem("coco.voice.enabled") === "true" && speech.supported; } catch { /* 現在の値を維持。 */ }
        renderVoice();
        pending = "";
        input.value = saved.draft || saved.pending;
        clearPartial();
        list.replaceChildren();
        messages.forEach(appendMessage);
        empty.hidden = messages.length > 0;
        readUi();
        const restoredScrollTop = scrollTop;
        if (restoreOpen) open({ focusInput: false });
        else close({ restoreFocus: false });
        scrollTop = restoredScrollTop;
        if (!panel.hidden) scroll.scrollTop = scrollTop ?? scroll.scrollHeight;
        updateSend();
        persistUi();
    });
    document.addEventListener("visibilitychange", () => { if (document.hidden) cancel(); });
    window.addEventListener("resize", updateViewport);
    mobileInput?.addEventListener?.("change", updateViewport);
    window.visualViewport?.addEventListener("resize", updateViewport);
    window.visualViewport?.addEventListener("scroll", updateViewport);

    input.value = draft;
    messages.forEach(appendMessage);
    empty.hidden = messages.length > 0;
    panel.dataset.conversationState = "idle";
    updateSend();
    if (interrupted) status.textContent = "前の返信を中断しました。送信でもう一度質問できます。";
    persist();
    button.hidden = false;
    // 呼び出し側の初期化が終わってから復元し、自動行動の休止も正しく反映する。
    return { restore: () => { if (restoreOpen) open({ focusInput: false }); }, isOpen: () => !panel.hidden, isBusy: conversation.isBusy, open, close, cancel };
}
