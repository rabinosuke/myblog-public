/** 論理上の会話状態だけを管理し、画面表示やモーション再生は呼び出し側に任せる。 */
export function createCocoConversation({
    respond,
    onStateChange = () => {},
    onReply = () => {},
    onDelta = () => {},
    onError = () => {},
    playReply,
    createSpeechQueue,
    onSpeechError = () => {}
} = {}) {
    if (typeof respond !== "function") throw new Error("返信処理を指定してください");
    let state = "idle";
    let current = null;

    function changeState(next) {
        state = next;
        onStateChange(next);
    }

    function wait(ms, task) {
        return new Promise(resolve => {
            const timer = window.setTimeout(() => {
                task.timers.delete(timer);
                resolve();
            }, ms);
            task.timers.set(timer, resolve);
        });
    }

    function cancel() {
        if (!current) return false;
        const task = current;
        // 古い非同期処理が戻っても、次の会話の状態・返信を書き換えない。
        current = null;
        task.controller.abort();
        for (const [timer, resolve] of task.timers) {
            window.clearTimeout(timer);
            resolve();
        }
        task.timers.clear();
        task.resolveCancelled(null);
        changeState("idle");
        return true;
    }

    async function submit(text) {
        if (current || !text.trim()) return false;
        const task = { controller: new AbortController(), timers: new Map() };
        task.cancelled = new Promise(resolve => { task.resolveCancelled = resolve; });
        current = task;
        try {
            changeState("listening");
            // 受付モーションと並行して通信する。早い応答を見せかけの待ち時間で遅らせない。
            const deliver = reply => {
                if (current !== task || task.delivered) return;
                if (typeof reply !== "string" || !reply.trim()) throw new Error("返信が空です");
                task.delivered = true;
                onReply(reply.trim().slice(0, 2000));
            };
            const delta = text => {
                if (current !== task || task.delivered) return;
                if (state === "listening") changeState("thinking");
                onDelta(text);
            };
            const speechStart = () => {
                if (current !== task || task.speechQueue) return;
                if (typeof createSpeechQueue !== "function") throw new Error("読み上げを開始できませんでした。");
                task.speechQueue = createSpeechQueue({
                    signal: task.controller.signal,
                    onStart: () => { if (current === task) changeState("speaking"); },
                    onPause: () => { if (current === task) changeState("thinking"); }
                });
                task.speechDone = task.speechQueue.done.then(() => null, error => error);
            };
            const response = Promise.resolve()
                .then(() => respond(text, { signal: task.controller.signal, onDelta: delta, onTextComplete: deliver,
                    onSpeechStreamStart: speechStart,
                    onSpeechChunk: chunk => { if (current === task) task.speechQueue?.push(chunk); },
                    onSpeechStreamError: error => { if (current === task) task.speechQueue?.fail(error); }
                }))
                .then(reply => ({ reply }), error => ({ error }));
            const first = await Promise.race([response, wait(1800, task), task.cancelled]);
            if (current !== task) return false;
            if (state === "listening") changeState("thinking");
            const result = first ?? await Promise.race([response, task.cancelled]);
            if (current !== task) return false;
            for (const timer of task.timers.keys()) window.clearTimeout(timer);
            task.timers.clear();
            if (result.error) throw result.error;
            const payload = result.reply;
            const reply = typeof payload === "string" ? payload : payload?.reply;
            if (typeof reply !== "string" || !reply.trim()) throw new Error("返信が空です");

            const message = reply.trim().slice(0, 2000);
            deliver(message);
            if (task.speechQueue) {
                task.speechQueue.finish();
                const error = await Promise.race([task.speechDone, task.cancelled]);
                if (current !== task) return false;
                if (error) throw error;
            } else if (typeof payload === "object" && typeof playReply === "function") {
                // 再生が実際に始まった時に口ぱくを開始し、終了まで同じ状態を保つ。
                await playReply(payload, { signal: task.controller.signal, onStart: () => {
                    if (current === task) changeState("speaking");
                } });
            } else {
                changeState("speaking");
                await wait(Math.min(4800, Math.max(1800, Array.from(message).length * 65)), task);
            }
            if (current !== task) return false;
            current = null;
            changeState("idle");
            return true;
        } catch (error) {
            task.speechQueue?.cancel();
            if (current !== task) return false;
            for (const timer of task.timers.keys()) window.clearTimeout(timer);
            task.timers.clear();
            current = null;
            changeState("idle");
            if (task.delivered) {
                onSpeechError(error);
                return true; // 音声が失敗しても、届いた返事を再送用の入力へ戻さない。
            }
            onError(error, text);
            return false;
        }
    }

    return { submit, cancel, isBusy: () => current !== null, getState: () => state };
}
