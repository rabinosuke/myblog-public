/** 音声は保存せず、ユーザー操作で許可されたAudioContextで順番に再生する。 */
export function createCocoSpeech() {
    const Context = window.AudioContext || window.webkitAudioContext;
    let context = null;

    async function prepare() {
        if (!Context) throw new Error("このブラウザでは読み上げを利用できません。");
        context ??= new Context();
        if (context.state !== "running") await context.resume();
    }

    async function decode(speech) {
        if (!speech || speech.mime_type !== "audio/mpeg" || typeof speech.audio_base64 !== "string" ||
            speech.audio_base64.length > 5592408) throw new Error("音声を受け取れませんでした。文章で確認してね。");
        if (!context || context.state !== "running") throw new Error("読み上げをONにし直して、もう一度話しかけてね。");
        const bytes = Uint8Array.from(atob(speech.audio_base64), char => char.charCodeAt(0));
        const buffer = await context.decodeAudioData(bytes.buffer);
        if (!Number.isFinite(buffer.duration) || buffer.duration <= 0 || buffer.duration > 240) {
            throw new Error("音声が長すぎるため、文章で確認してね。");
        }
        return buffer;
    }

    async function playBuffer(buffer, { signal, onStart }) {
        if (signal.aborted) return;
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        await new Promise((resolve, reject) => {
            let finished = false;
            const finish = error => {
                if (finished) return;
                finished = true;
                window.clearTimeout(timer);
                signal.removeEventListener("abort", abort);
                source.onended = null;
                try { source.stop(); } catch { /* 終了済みの音声は停止不要。 */ }
                source.disconnect();
                if (error) reject(error); else resolve();
            };
            const abort = () => finish();
            const timer = window.setTimeout(() => finish(new Error("読み上げを停止しました。")), (buffer.duration + 5) * 1000);
            signal.addEventListener("abort", abort, { once: true });
            source.onended = () => finish();
            try { source.start(); onStart(); } catch {
                finish(new Error("音声を再生できませんでした。文章で確認してね。"));
            }
        });
    }

    async function play(response, options) {
        if (response.speech_error) throw Object.assign(new Error(response.speech_error.message), { code: response.speech_error.code });
        if (options.signal.aborted) return;
        const buffer = await decode(response.speech);
        await playBuffer(buffer, options);
    }

    function createQueue({ signal, onStart = () => {}, onPause = () => {} }) {
        const controller = new AbortController(), pending = [];
        let closed = false, stopped = false, wake = null, count = 0, duration = 0;
        let resolveDone, rejectDone;
        const done = new Promise((resolve, reject) => { resolveDone = resolve; rejectDone = reject; });
        // 到着直後にデコードを始めるため、再生待ち中の失敗も未処理のPromiseにしない。
        void done.catch(() => {});
        const notify = () => { const callback = wake; wake = null; callback?.(); };
        const stop = error => {
            if (stopped) return;
            stopped = true;
            controller.abort();
            pending.length = 0;
            signal.removeEventListener("abort", abort);
            notify();
            if (error) rejectDone(error); else resolveDone();
        };
        const abort = () => stop();
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) stop();

        async function run() {
            while (!stopped) {
                if (!pending.length) {
                    if (closed) {
                        if (!count) throw new Error("音声を受け取れませんでした。文章で確認してね。");
                        stop();
                        return;
                    }
                    await new Promise(resolve => { wake = resolve; });
                    continue;
                }
                const result = await pending.shift();
                if (stopped) return;
                if (result.error) throw result.error;
                duration += result.buffer.duration;
                if (duration > 240) throw new Error("音声が長すぎるため、残りは文章で確認してね。");
                await playBuffer(result.buffer, { signal: controller.signal, onStart });
                if (!stopped && (!closed || pending.length)) onPause();
            }
        }
        void run().catch(stop);
        return {
            done,
            push(speech) {
                if (closed || stopped) return;
                if (++count > 4) { stop(new Error("音声が多すぎるため、残りは文章で確認してね。")); return; }
                pending.push(Promise.resolve().then(() => decode(speech)).then(buffer => ({ buffer }), error => ({ error })));
                notify();
            },
            finish() { if (!stopped) { closed = true; notify(); } },
            fail: stop,
            cancel: () => stop()
        };
    }

    return { supported: Boolean(Context), prepare, play, createQueue };
}
