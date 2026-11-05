// ページが変わっても、同じタブでは一つの会話として続ける。
const CHAT_KEY = "coco.chat.v2";
const LEGACY_KEY = "coco.chat.v1";
const ARTICLE_PREFIX = "coco.chat.article.v1:";
export const UI_KEY = "coco.chat.ui.v1";
export const MAX_MESSAGES = 50;
export const MAX_LENGTH = 2000;

function cleanMessages(saved, articleSlug = "") {
    if (!Array.isArray(saved.messages)) return [];
    return saved.messages.filter(message => ["user", "assistant"].includes(message?.role) &&
        typeof message.text === "string" && message.text.trim()).slice(-MAX_MESSAGES).map(message => ({
            role: message.role, text: message.text.slice(0, MAX_LENGTH),
            mode: saved.version >= 3 && message.mode === "ai" ? "ai" : "demo",
            articleSlug: typeof message.articleSlug === "string" && /^[-a-zA-Z0-9_]{1,200}$/.test(message.articleSlug) ? message.articleSlug : articleSlug,
            articleTitle: typeof message.articleTitle === "string" ? message.articleTitle.slice(0, 200) : ""
        }));
}

function parse(storage, key) {
    const saved = JSON.parse(storage.getItem(key));
    return [1, 2, 3, 4].includes(saved?.version) ? saved : null;
}

export function loadChat(storage, currentSlug = "") {
    const empty = { draft: "", messages: [], pending: "" };
    try {
        const saved = parse(storage, CHAT_KEY);
        if (saved?.version === 4) return {
            draft: typeof saved.draft === "string" ? saved.draft.slice(0, MAX_LENGTH) : "",
            messages: cleanMessages(saved),
            pending: typeof saved.pending === "string" ? saved.pending.slice(0, 1000) : ""
        };
    } catch { /* 共有データが壊れていれば、残っている以前の保存先を確認する。 */ }

    // 旧データは削除せず、以前の一般会話と記事別の履歴を参照元付きで移行する。
    const keys = [LEGACY_KEY];
    try {
        for (let i = 0; i < storage.length; i++) {
            const key = storage.key(i);
            if (key?.startsWith(ARTICLE_PREFIX) && /^[-a-zA-Z0-9_]{1,200}$/.test(key.slice(ARTICLE_PREFIX.length))) keys.push(key);
        }
    } catch { /* 保存が使えない場合も、画面内の会話は続けられる。 */ }
    const currentKey = ARTICLE_PREFIX + currentSlug;
    // 以前の保存形式に日時はないため、現在の記事の履歴を最後に取り込む。
    const ordered = [...keys.filter(key => key !== currentKey), ...(keys.includes(currentKey) ? [currentKey] : [])];
    for (const key of ordered) {
        try {
            const saved = parse(storage, key);
            if (!saved) continue;
            const slug = key.startsWith(ARTICLE_PREFIX) ? key.slice(ARTICLE_PREFIX.length) : "";
            empty.messages.push(...cleanMessages(saved, slug));
            if (typeof saved.draft === "string" && saved.draft) empty.draft = saved.draft.slice(0, MAX_LENGTH);
        } catch { /* 一つの保存データが壊れていても、他の履歴は取り込む。 */ }
    }
    empty.messages = empty.messages.slice(-MAX_MESSAGES);
    return empty;
}

export function saveChat(storage, { draft, messages, pending }) {
    storage.setItem(CHAT_KEY, JSON.stringify({ version: 4, draft, messages, pending }));
}
