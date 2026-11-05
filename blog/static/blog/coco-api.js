/** 成功したAI会話の直近6往復を、文字数の上限内で選ぶ。デモ・中断・失敗は混ぜない。 */
export function selectHistory(messages, currentMessage) {
    const pairs = [];
    for (let index = 0; index < messages.length - 1; index += 1) {
        const user = messages[index], reply = messages[index + 1];
        if (user.role === "user" && reply.role === "assistant" && reply.mode === "ai") {
            pairs.push([
                { role: "user", content: user.text },
                { role: "assistant", content: reply.articleSlug
                    ? `[参照記事: /posts/${reply.articleSlug}/]\n${reply.text}`.slice(0, 2000)
                    : reply.text }
            ]);
        }
    }
    const recent = pairs.slice(-6);
    let count = currentMessage.length + recent.flat().reduce((sum, entry) => sum + entry.content.length, 0);
    while (count > 8000 && recent.length) {
        count -= recent.shift().reduce((sum, entry) => sum + entry.content.length, 0);
    }
    return recent.flat();
}

export class CocoApiError extends Error {
    constructor(code, message, status = 0) {
        super(message);
        this.name = "CocoApiError";
        this.code = code;
        this.status = status;
    }
}

function validateReply(payload) {
    if (payload?.mode !== "ai" || typeof payload.reply !== "string" || !payload.reply.trim() || payload.reply.length > 2000) {
        throw new CocoApiError("invalid_reply", "返事を受け取れませんでした。もう一度お試しください。");
    }
    return payload;
}

/** UTF-8やイベントが通信の区切りで分割されても、文字が欠けないように復元する。 */
async function readEvents(response, { signal, onDelta, onTextComplete, readAloud, onSpeechStreamStart, onSpeechChunk, onSpeechStreamError }) {
    if (!response.body) throw new CocoApiError("unsupported_stream", "返事を受け取れませんでした。ページを再読み込みしてください。");
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = "", data = [], eventLength = 0, text = "", reply = null, streamingSpeech = false, speechCount = 0, speechFailed = false, started = false;
    const invalid = () => new CocoApiError("invalid_stream", "返信の通信が途切れました。もう一度送信してください。");
    const abort = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener("abort", abort, { once: true });
    try {
        while (true) {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            const { value, done } = await reader.read();
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            buffer += decoder.decode(value, { stream: !done });
            let end;
            while ((end = buffer.indexOf("\n")) !== -1) {
                const line = buffer.slice(0, end).replace(/\r$/, "");
                buffer = buffer.slice(end + 1);
                eventLength += line.length;
                // 完成時のMP3（最大4MB）のbase64を含めた上限。生のデータは保存しない。
                if (eventLength > 6000000) throw invalid();
                if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
                if (line !== "") continue;
                const encoded = data.join("\n");
                data = []; eventLength = 0;
                if (!encoded) continue;
                let event;
                try { event = JSON.parse(encoded); } catch { throw invalid(); }
                if (event?.type === "start") {
                    if (started) throw invalid();
                    started = true;
                    if (event.speech_stream === true) {
                        if (!readAloud) throw invalid();
                        streamingSpeech = true;
                        onSpeechStreamStart();
                    }
                    continue;
                }
                if (event?.type === "speech") {
                    if (!streamingSpeech || speechFailed || event.index !== speechCount || speechCount >= 4 ||
                        event.speech?.mime_type !== "audio/mpeg" || typeof event.speech.audio_base64 !== "string" ||
                        event.speech.audio_base64.length > 5592408) throw invalid();
                    speechCount++;
                    onSpeechChunk(event.speech);
                    continue;
                }
                if (event?.type === "speech_error") {
                    if (!streamingSpeech || speechFailed) throw invalid();
                    speechFailed = true;
                    const message = typeof event.error?.message === "string" ? event.error.message.slice(0, 200) : "読み上げできませんでした。文章で確認してね。";
                    onSpeechStreamError(new CocoApiError(event.error?.code ?? "speech_unavailable", message));
                    continue;
                }
                if (event?.type === "error") {
                    const message = typeof event.error?.message === "string" ? event.error.message.slice(0, 200) : invalid().message;
                    throw new CocoApiError(event.error?.code ?? "stream_error", message);
                }
                if (event?.type === "delta") {
                    if (reply || typeof event.delta !== "string" || text.length + event.delta.length > 2000) throw invalid();
                    text += event.delta;
                    if (event.delta) onDelta(text);
                } else if (event?.type === "reply") {
                    if (reply) throw invalid();
                    reply = validateReply(event).reply;
                    // 完成した文章は、音声の生成を待たず一度だけ履歴へ確定する。
                    onTextComplete(reply);
                } else if (event?.type === "complete") {
                    validateReply(event);
                    if (!reply || event.reply !== reply) throw invalid();
                    if (streamingSpeech && (event.speech_stream !== true || event.speech_chunks !== speechCount)) throw invalid();
                    if (!streamingSpeech && event.speech_stream === true) throw invalid();
                    return event;
                } else throw invalid();
            }
            if (buffer.length + eventLength > 6000000) throw invalid();
            if (done) throw invalid(); // 完了イベントのない切断を成功扱いしない。
        }
    } finally {
        signal.removeEventListener("abort", abort);
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}

/** ブラウザは同一サイトのDjangoだけに通信し、APIキーを受け取らない。 */
export async function requestCocoResponse({ endpoint, csrfToken, message, history, articleSlug = "", readAloud = false, signal,
    onDelta = () => {}, onTextComplete = () => {}, onSpeechStreamStart = () => {}, onSpeechChunk = () => {}, onSpeechStreamError = () => {} }) {
    const url = new URL(endpoint, window.location.href);
    if (url.origin !== window.location.origin || !csrfToken) {
        throw new CocoApiError("invalid_session", "ページを再読み込みして、もう一度お試しください。");
    }
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) abort();
    signal?.addEventListener("abort", abort, { once: true });
    let timedOut = false;
    const timer = window.setTimeout(() => { timedOut = true; controller.abort(); }, readAloud ? 90000 : 25000);
    try {
        const response = await fetch(url.href, {
            method: "POST", credentials: "same-origin", mode: "same-origin", cache: "no-store", redirect: "error",
            headers: { "Content-Type": "application/json", "Accept": "text/event-stream", "X-CSRFToken": csrfToken, ...(readAloud ? { "X-Coco-Audio-Stream": "sentences-v1" } : {}) },
            body: JSON.stringify({ message, history, ...(articleSlug ? { article_slug: articleSlug } : {}), ...(readAloud ? { read_aloud: true } : {}) }), signal: controller.signal
        });
        if (response.ok && response.headers?.get("Content-Type")?.startsWith("text/event-stream")) {
            return await readEvents(response, { signal: controller.signal, onDelta, onTextComplete, readAloud, onSpeechStreamStart, onSpeechChunk, onSpeechStreamError });
        }
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
            const fallback = response.status === 403
                ? "送信の確認に失敗しました。ページを再読み込みしてください。"
                : "返事を受け取れませんでした。時間をおいてもう一度お試しください。";
            const errorMessage = typeof payload?.error?.message === "string" ? payload.error.message.slice(0, 200) : fallback;
            throw new CocoApiError(payload?.error?.code ?? "http_error", errorMessage, response.status);
        }
        // 更新前のサーバーからJSONが届く場合も、完成文として安全に受け取る。
        validateReply(payload);
        onTextComplete(payload.reply);
        return payload;
    } catch (error) {
        if (signal?.aborted) throw error;
        if (timedOut) throw new CocoApiError("timeout", "返事に時間がかかっています。時間をおいてもう一度お試しください。");
        if (error instanceof CocoApiError) throw error;
        throw new CocoApiError("network", "通信できませんでした。接続を確認して、もう一度お試しください。");
    } finally {
        controller.abort();
        window.clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
    }
}

// 従来の文字だけの呼び出しは、返事の文字列を返すインターフェースを保つ。
export async function requestCocoReply(options) {
    return (await requestCocoResponse(options)).reply;
}
