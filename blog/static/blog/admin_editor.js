/**
 * Toast UI Editor + iframe による「本物そっくり」プレビュー
 *
 * 機能:
 *   - 左：Toast UI Editor（Markdown編集）
 *   - 右：iframe（本物の detail.html でレンダリング）
 *   - リアルタイム更新（500ms debounce）
 *   - デバイスサイズ切替（Desktop / Tablet / Mobile）
 *   - プレビュー全画面表示
 */
(function () {
    'use strict';

    // ============================================================
    // CSRF対応ヘルパー
    // Django の CSRF 防御を維持したまま fetch で POST するため、
    // クッキー `csrftoken` を読んで X-CSRFToken ヘッダーに付ける。
    // これがあれば views.py 側で @csrf_exempt を外せる（推奨）。
    // ============================================================
    function getCsrfToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function csrfFetch(url, options) {
        options = options || {};
        const headers = Object.assign({}, options.headers || {});
        headers['X-CSRFToken'] = getCsrfToken();
        return fetch(url, Object.assign({}, options, {
            headers: headers,
            credentials: 'same-origin',
        }));
    }
    // window 経由で他のスクリプト（admin_image_uploader.js）からも使えるようにする
    window.AdminCsrfFetch = csrfFetch;

    // デバイスサイズの定義
    const DEVICE_SIZES = {
        desktop: { width: '100%', label: '🖥 Desktop' },
        tablet: { width: '768px', label: '📱 Tablet' },
        mobile: { width: '375px', label: '📱 Mobile' },
    };

    function getTitleValue() {
        const el = document.getElementById('id_title');
        return el ? el.value : 'プレビュー';
    }

    function getSummaryValue() {
        const el = document.getElementById('id_summary');
        return el ? el.value : '';
    }

    async function initEditor() {
        const textarea = document.getElementById('id_body');
        if (!textarea) return;
        if (typeof toastui === 'undefined') {
            console.warn('Toast UI Editor が読み込まれていません');
            return;
        }

        // ラッパー（左右分割の親）
        const wrapper = document.createElement('div');
        wrapper.id = 'editor-preview-wrapper';
        textarea.parentNode.insertBefore(wrapper, textarea);

        // 左：エディタ
        const editorContainer = document.createElement('div');
        editorContainer.id = 'toastui-editor-container';
        editorContainer.className = 'editor-pane';
        wrapper.appendChild(editorContainer);

        // 右：プレビュー（コントロール + iframe）
        const previewContainer = document.createElement('div');
        previewContainer.className = 'preview-pane';
        previewContainer.innerHTML = `
            <div class="preview-header">
                <div class="preview-header-left">
                    <span id="async-save-status"></span>
                </div>
                <div class="preview-header-right">
                    <div class="device-buttons">
                        <button type="button" data-device="desktop" class="device-btn active">🖥</button>
                        <button type="button" data-device="tablet" class="device-btn">📱T</button>
                        <button type="button" data-device="mobile" class="device-btn">📱M</button>
                    </div>
                    <button type="button" id="preview-reload-btn" title="プレビュー更新">🔄</button>
                    <button type="button" id="preview-fullscreen-btn" title="プレビュー拡大/縮小">⛶</button>
                    <button type="button" id="async-save-btn" title="非同期で保存（ページ遷移なし）">
                        💾 保存
                    </button>
                </div>
            </div>
            <div class="preview-frame-area">
                <!--
                    allow-same-origin: 親画面から本文DOMを差分更新するため必要。
                    allow-scripts: サーバーでサニタイズ済みの本文にMathJaxを適用するため必要。
                -->
                <iframe id="preview-iframe" sandbox="allow-same-origin allow-scripts"></iframe>
            </div>
        `;
        wrapper.appendChild(previewContainer);

        // textarea は wrapper の外側に移動して非表示
        textarea.style.display = 'none';
        wrapper.parentNode.appendChild(textarea);

        // コードハイライト プラグイン
        const codeSyntaxHighlight = toastui.Editor.plugin.codeSyntaxHighlight;

        // カスタムボタン：左寄せ・中央寄せ・右寄せ
        // Markdown標準には寄せ指定がないので、HTMLの <div align="..."> で代替する
        // クリックハンドラは editor 初期化後にバインドする（chicken-and-egg回避）
        function createAlignButton(align, label, title) {
            const button = document.createElement('button');
            button.type = 'button';
            // toastui-editor-toolbar-icons クラスを付けるとデフォルトの背景画像が出てしまうので付けない
            button.className = 'toastui-custom-align-btn';
            button.innerHTML = label;
            button.title = title;
            button.dataset.align = align;
            return button;
        }
        const alignLeftButton = createAlignButton('left', '⇤', '左寄せ');
        const alignCenterButton = createAlignButton('center', '⇔', '中央寄せ');
        const alignRightButton = createAlignButton('right', '⇥', '右寄せ');

        // カスタムボタン：キャラ吹き出し（ドロップダウン方式）
        //
        // ツールバーには 1 ボタンだけ置き、クリックでパネル展開して全キャラを表示する。
        // パネル内ではキャラを slug の `-` 前部分でグルーピングして見やすく並べる。
        // 例: coco / coco-smile / coco-shock → すべて「coco」グループ配下
        //
        // キャラ一覧は管理画面の Character モデル（is_active=True）から動的取得。
        // 新しいキャラを増やすときは /admin/blog/character/add/ から追加するだけで、
        // エディタをリロードすれば自動的に挿入候補に出る。
        let allCharacters = [];  // [{slug, name, image_url}, ...]

        async function loadCharacters() {
            try {
                const res = await fetch('/admin-character-list/', { credentials: 'same-origin' });
                if (!res.ok) throw new Error('HTTP ' + res.status);
                const data = await res.json();
                allCharacters = data.characters || [];
            } catch (e) {
                console.warn('キャラ一覧の取得に失敗:', e);
                allCharacters = [];
            }
        }
        await loadCharacters();

        // ドロップダウンを開くトリガーボタン
        const bubbleTriggerButton = document.createElement('button');
        bubbleTriggerButton.type = 'button';
        bubbleTriggerButton.className = 'toastui-custom-align-btn toastui-bubble-trigger';
        bubbleTriggerButton.innerHTML = '🎭キャラ▾';
        bubbleTriggerButton.title = 'キャラクター吹き出しを挿入';

        // ドロップダウンパネル本体（最初は非表示）
        const bubblePanel = document.createElement('div');
        bubblePanel.className = 'toastui-bubble-panel';
        bubblePanel.style.display = 'none';

        // パネル内コンテンツ生成（キャラを slug 先頭でグルーピング）
        function buildBubblePanel() {
            bubblePanel.replaceChildren();
            if (allCharacters.length === 0) {
                const empty = document.createElement('div');
                empty.className = 'bp-empty';
                empty.textContent = 'キャラ未登録。';
                bubblePanel.appendChild(empty);
                return;
            }
            // slug の先頭セグメント（`-` の前）でグルーピング
            const groups = Object.create(null);
            allCharacters.forEach(function (c) {
                const groupKey = c.slug.split('-', 1)[0];
                if (!groups[groupKey]) groups[groupKey] = [];
                groups[groupKey].push(c);
            });

            Object.keys(groups).forEach(function (groupKey) {
                const groupEl = document.createElement('div');
                groupEl.className = 'bp-group';
                const title = document.createElement('div');
                title.className = 'bp-group-title';
                title.textContent = groupKey;
                groupEl.appendChild(title);
                groups[groupKey].forEach(function (c) {
                    const row = document.createElement('div');
                    row.className = 'bp-row';
                    let imageUrl = null;
                    try {
                        const candidate = new URL(c.image_url, window.location.origin);
                        if (c.image_url && candidate.origin === window.location.origin &&
                            ['http:', 'https:'].includes(candidate.protocol)) imageUrl = candidate.href;
                    } catch (_) { /* 不正なURLは画像の代わりに表示名を使う。 */ }
                    const thumb = document.createElement(imageUrl ? 'img' : 'span');
                    thumb.className = imageUrl ? 'bp-thumb' : 'bp-thumb bp-thumb-fallback';
                    if (imageUrl) {
                        thumb.src = imageUrl;
                        thumb.alt = '';
                    } else {
                        thumb.textContent = c.name.charAt(0);
                    }
                    row.appendChild(thumb);
                    // キャラ名は管理者の入力。HTMLに連結せず必ず文字として描画する。
                    const name = document.createElement('span');
                    name.className = 'bp-name';
                    name.textContent = c.name;
                    row.appendChild(name);
                    const slug = document.createElement('span');
                    slug.className = 'bp-slug';
                    slug.textContent = c.slug;
                    row.appendChild(slug);
                    ['', '-r'].forEach(function (pos) {
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.className = 'bp-insert';
                        button.dataset.slug = c.slug;
                        button.dataset.pos = pos;
                        button.textContent = pos ? 'R' : 'L';
                        row.appendChild(button);
                    });
                    groupEl.appendChild(row);
                });
                bubblePanel.appendChild(groupEl);
            });
        }
        buildBubblePanel();
        document.body.appendChild(bubblePanel);  // body 直下に置いてポジショニング自由

        // トリガークリックでパネル開閉
        bubbleTriggerButton.addEventListener('click', function (e) {
            e.stopPropagation();
            if (bubblePanel.style.display === 'none') {
                const rect = bubbleTriggerButton.getBoundingClientRect();
                bubblePanel.style.top = (rect.bottom + window.scrollY + 4) + 'px';
                bubblePanel.style.left = (rect.left + window.scrollX) + 'px';
                bubblePanel.style.display = 'block';
            } else {
                bubblePanel.style.display = 'none';
            }
        });

        // 外側クリックで閉じる
        document.addEventListener('click', function (e) {
            if (!bubblePanel.contains(e.target) && e.target !== bubbleTriggerButton) {
                bubblePanel.style.display = 'none';
            }
        });

        // Toast UI Editor 初期化
        const editor = new toastui.Editor({
            el: editorContainer,
            height: '700px',
            initialEditType: 'markdown',
            previewStyle: 'tab',
            initialValue: textarea.value || '',
            language: 'ja-JP',
            usageStatistics: false,
            theme: 'dark',
            plugins: [codeSyntaxHighlight],
            toolbarItems: [
                ['heading', 'bold', 'italic', 'strike'],
                ['hr', 'quote'],
                ['ul', 'ol', 'task'],
                ['table', 'image', 'link'],
                ['code', 'codeblock'],
                [
                    {
                        name: 'alignLeft',
                        tooltip: '左寄せ',
                        el: alignLeftButton,
                    },
                    {
                        name: 'alignCenter',
                        tooltip: '中央寄せ',
                        el: alignCenterButton,
                    },
                    {
                        name: 'alignRight',
                        tooltip: '右寄せ',
                        el: alignRightButton,
                    },
                    {
                        name: 'bubbleDropdown',
                        tooltip: 'キャラクター吹き出しを挿入',
                        el: bubbleTriggerButton,
                    },
                ],
            ],
            // 画像挿入時：base64ではなくモーダルでクロップ・代替テキスト指定してからアップロード
            hooks: {
                addImageBlobHook: function (blob, callback) {
                    if (!window.ImageUploader) {
                        // フォールバック：モーダルが読み込まれていなければ素のアップロード
                        const formData = new FormData();
                        formData.append('image', blob);
                        csrfFetch('/admin-image-upload/', {
                            method: 'POST',
                            body: formData,
                        })
                            .then(function (res) { return res.json(); })
                            .then(function (data) { callback(data.url, 'image'); });
                        return;
                    }

                    // クロップ・alt入力モーダルを開く
                    window.ImageUploader.open(blob).then(function (result) {
                        if (!result) return;  // キャンセル時
                        callback(result.url, result.alt);
                    });
                },
            },
        });

        // 寄せボタンのクリックハンドラ
        // editor 初期化後にバインドしないと editor が undefined になる
        [alignLeftButton, alignCenterButton, alignRightButton].forEach(function (btn) {
            btn.addEventListener('click', function () {
                const align = btn.dataset.align;
                const selected = editor.getSelectedText() || (align + '寄せテキスト');
                // 前後に空行を入れないと Markdown が中身をブロックとして解釈しない
                // markdown="1" を付けないと中身（画像など）が Markdown として解釈されない
                editor.replaceSelection('\n<div align="' + align + '" markdown="1">\n\n' + selected + '\n\n</div>\n');
            });
        });

        // キャラ吹き出し挿入（ドロップダウンパネル内 L/R ボタンに委譲）
        // パネルは body 直下に置いてあるので、編集中に作り直しても残る前提でここで一度だけ登録
        bubblePanel.addEventListener('click', function (e) {
            const btn = e.target.closest('.bp-insert');
            if (!btn) return;
            const slug = btn.dataset.slug;
            const pos = btn.dataset.pos;  // '' or '-r'
            const tag = slug + pos;
            const selected = editor.getSelectedText() || 'セリフをここに書く';
            editor.replaceSelection('\n:::' + tag + '\n' + selected + '\n:::\n');
            bubblePanel.style.display = 'none';
        });

        const iframe = document.getElementById('preview-iframe');
        let debounceTimer = null;

        // iframe更新
        // 初回だけ srcdoc で全ページをロードし、以降は本文・タイトル・要約のDOMだけ
        // 差分置換する。これでリロードが発生せずスクロール位置・チラつきもゼロ。
        let isFirstLoad = true;
        let previewRequestId = 0;
        let pendingPartialHtml = null;
        let partialUpdateRunning = false;
        // setupScrollSync() が、DOM差し替え後の再描画・再同期処理をここに登録する。
        let onPreviewContentReady = function () {};
        let alignPreviewToEditor = function () {};

        function updatePreview() {
            const requestId = ++previewRequestId;
            const markdown = editor.getMarkdown();
            const formData = new URLSearchParams();
            formData.append('content', markdown);
            formData.append('title', getTitleValue());
            formData.append('summary', getSummaryValue());

            csrfFetch('/admin-preview/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body: formData.toString(),
            })
                .then(function (response) {
                    if (!response.ok) throw new Error('Preview fetch failed');
                    return response.text();
                })
                .then(function (html) {
                    // 連続入力時に古い応答が後から到着して、
                    // 新しい本文と行番号マーカーを巻き戻すのを防ぐ。
                    if (requestId !== previewRequestId) return;
                    if (isFirstLoad) {
                        // 初回はフルロード（base.html・CSS・JSを取得するため必須）
                        iframe.srcdoc = html;
                        isFirstLoad = false;
                        return;
                    }
                    queuePartialUpdate(html);
                })
                .catch(function (err) {
                    console.error('プレビュー更新失敗:', err);
                });
        }

        // 取得HTMLから本文DOMだけ抜き出して iframe 内のノードを差し替える
        // ページ全体は再読み込みしないのでスクロール位置は維持される
        function queuePartialUpdate(html) {
            // MathJaxの組版中にDOMを破壊しないよう、更新を1本ずつ直列化する。
            // 待ちが発生した場合は最新HTMLだけを残し、中間状態は捨てる。
            pendingPartialHtml = html;
            drainPartialUpdates();
        }

        async function drainPartialUpdates() {
            if (partialUpdateRunning) return;
            partialUpdateRunning = true;
            try {
                while (pendingPartialHtml !== null) {
                    const html = pendingPartialHtml;
                    pendingPartialHtml = null;
                    await applyPartialUpdate(html);
                }
            } catch (err) {
                console.error('プレビューの差分更新に失敗:', err);
            } finally {
                partialUpdateRunning = false;
                // finally直前に次の入力が来た競合時も取りこぼさない。
                if (pendingPartialHtml !== null) drainPartialUpdates();
            }
        }

        async function applyPartialUpdate(html) {
            const doc = iframe.contentDocument;
            if (!doc) {
                // contentDocument が取れない異常時はフルロードにフォールバック
                iframe.srcdoc = html;
                return;
            }
            const parser = new DOMParser();
            const newDoc = parser.parseFromString(html, 'text/html');

            // 本文と目次を差し替える。数式入り見出しを変更した場合も、
            // 右側の目次だけ古いまま残らないよう同じ応答から更新する。
            const newBody = newDoc.querySelector('.blog-post-body');
            const curBody = doc.querySelector('.blog-post-body');
            const newToc = newDoc.querySelector('.post-toc');
            const curToc = doc.querySelector('.post-toc');
            if ((!newBody || !curBody) || Boolean(newToc) !== Boolean(curToc)) {
                // 本文や目次の有無が変わる場合は親構造も変わるため、
                // 部分置換ではなく完成HTMLを読み直す。
                isFirstLoad = true;
                iframe.srcdoc = html;
                return;
            }

            const mathJax = iframe.contentWindow && iframe.contentWindow.MathJax;
            if (mathJax && mathJax.startup && mathJax.startup.promise) {
                // 初回の自動組版中なら、完了後に安全に差し替える。
                await mathJax.startup.promise;
            }
            const oldMathTargets = [curBody];
            if (curToc) oldMathTargets.push(curToc);
            if (mathJax && typeof mathJax.typesetClear === 'function') {
                // 削除前に古いMathItemを破棄しないと、入力のたびにメモリが増える。
                mathJax.typesetClear(oldMathTargets);
            }

            curBody.innerHTML = newBody.innerHTML;
            if (newToc && curToc) {
                curToc.innerHTML = newToc.innerHTML;
            }

            if (mathJax && typeof mathJax.texReset === 'function') {
                // 本文と目次を一組として再描画し、式番号・ラベルの累積を防ぐ。
                mathJax.texReset();
            }
            if (mathJax && typeof mathJax.typesetPromise === 'function') {
                const newMathTargets = [curBody];
                if (curToc) newMathTargets.push(curToc);
                await mathJax.typesetPromise(newMathTargets);
            }

            // タイトル（h1.display-5）
            const newTitle = newDoc.querySelector('.blog-post .display-5');
            const curTitle = doc.querySelector('.blog-post .display-5');
            if (newTitle && curTitle) {
                curTitle.innerHTML = newTitle.innerHTML;
            }

            // 要約（.lead.mb-4）。空のときに DOM がそもそも存在しないケースに備える
            const newSummary = newDoc.querySelector('.blog-post .lead.mb-4');
            const curSummary = doc.querySelector('.blog-post .lead.mb-4');
            if (newSummary && curSummary) {
                curSummary.innerHTML = newSummary.innerHTML;
            } else if (newSummary || curSummary) {
                // 片方しかない場合は構造が変わったのでフルロードし直す
                isFirstLoad = true;
                iframe.srcdoc = html;
                return;
            }

            // コードハイライト用の <pre><code> が新規追加された場合に
            // ハイライト適用が必要なら iframe 内の Prism / hljs を呼び直す
            // （現状サーバー側で codehilite 済みなので追加処理は不要）
            const previewWindow = iframe.contentWindow;
            if (previewWindow && typeof previewWindow.AttachCodeCopyButtons === 'function') {
                previewWindow.AttachCodeCopyButtons(curBody || doc);
            }
            onPreviewContentReady();
        }

        // 編集変更で500ms後に自動更新
        editor.on('change', function () {
            textarea.value = editor.getMarkdown();
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(updatePreview, 500);
        });

        // 手動更新ボタンは「CSSや関連記事も含めて完全に作り直したい」時に使うので
        // フルロードに戻す
        document.getElementById('preview-reload-btn').addEventListener('click', function () {
            isFirstLoad = true;
            updatePreview();
        });

        // ============================================================
        // ソース行アンカーによる双方向同期
        //
        // Markdownと完成HTMLは見出し・画像・数式の高さが異なるため、
        // スクロール率だけでは後半ほどずれる。data-source-line で同じ
        // 原稿位置を探し、それを左右の同じ画面内Y座標へ合わせる。
        // ============================================================
        setupScrollSync();

        function setupScrollSync() {
            let editorElements = {};
            if (typeof editor.getEditorElements === 'function') {
                editorElements = editor.getEditorElements() || {};
            }

            // 公式APIの要素を優先し、バージョン差だけDOM候補で吸収する。
            // 短い記事でも初期化するため、現在スクロール可能かは条件にしない。
            const officialEditor = editorElements.mdEditor || null;
            const candidates = [
                officialEditor && officialEditor.matches('.ProseMirror') ? officialEditor : null,
                officialEditor ? officialEditor.querySelector('.ProseMirror') : null,
                document.querySelector('.toastui-editor-md-container .ProseMirror'),
                officialEditor,
                document.querySelector('.toastui-editor-md-container'),
            ].filter(Boolean);
            let editorScrollEl = candidates.find(function (el) {
                const overflowY = window.getComputedStyle(el).overflowY;
                return overflowY === 'auto' || overflowY === 'scroll';
            }) || candidates[0] || null;

            if (!editorScrollEl) {
                console.warn('[ScrollSync] エディタ容器が見つかりませんでした');
                return;
            }

            const SOURCE_LINE_SELECTOR = '[data-source-line]';
            const SCROLL_SYNC_RATIO = 0.25;
            let jumpScheduled = false;
            let ignoreEditorJumpUntil = 0;
            let ignoreEditorScrollUntil = 0;
            let ignorePreviewScrollUntil = 0;
            let editorScrollScheduled = false;
            let previewScrollScheduled = false;
            let previewScrollDocument = null;

            function clamp(value, min, max) {
                return Math.min(max, Math.max(min, value));
            }

            function isEditorFocused() {
                return editorScrollEl === document.activeElement ||
                    editorScrollEl.contains(document.activeElement);
            }

            function scheduleJump(force) {
                if (force !== true && performance.now() < ignoreEditorJumpUntil) return;
                if (jumpScheduled) return;
                jumpScheduled = true;
                requestAnimationFrame(function () {
                    jumpScheduled = false;
                    jumpToCursorLine();
                });
            }

            // 公式イベントでクリック・矢印キー・選択変更をまとめて取る。
            if (typeof editor.on === 'function') {
                editor.on('caretChange', function () { scheduleJump(false); });
            }
            // CDN版の差でcaretChangeが漏れる場合の軽量フォールバック。
            editorScrollEl.addEventListener('click', function () { scheduleJump(false); });
            editorScrollEl.addEventListener('keyup', function () { scheduleJump(false); });

            function getEditorCaretRect() {
                const selection = window.getSelection();
                if (!selection || selection.rangeCount === 0) return null;
                const range = selection.getRangeAt(0);
                const node = range.startContainer.nodeType === 3
                    ? range.startContainer.parentElement
                    : range.startContainer;
                if (!node || !editorScrollEl.contains(node)) return null;

                let rect = range.getBoundingClientRect();
                if (!rect || (rect.width === 0 && rect.height === 0)) {
                    rect = node.getBoundingClientRect();
                }
                return rect;
            }

            function getEditorViewportRatio() {
                const editorRect = editorScrollEl.getBoundingClientRect();
                const caretRect = getEditorCaretRect();
                if (!caretRect || editorRect.height <= 0) return 0.2;
                return clamp((caretRect.top - editorRect.top) / editorRect.height, 0.05, 0.95);
            }

            function ensurePreviewBottomSpacer() {
                const iframeWin = iframe.contentWindow;
                const iframeDoc = iframe.contentDocument;
                if (!iframeWin || !iframeDoc || !iframeDoc.body) return;
                // 公開ページのsmooth指定を残すと、同期スクロール同士が追いかけ合い
                // 一時的に行がずれて見えるため、管理画面内だけ即時移動にする。
                iframeDoc.documentElement.style.scrollBehavior = 'auto';
                let spacer = iframeDoc.getElementById('admin-preview-sync-spacer');
                if (!spacer) {
                    spacer = iframeDoc.createElement('div');
                    spacer.id = 'admin-preview-sync-spacer';
                    spacer.setAttribute('aria-hidden', 'true');
                    spacer.style.pointerEvents = 'none';
                    iframeDoc.body.appendChild(spacer);
                }
                // 末尾の行もエディタと同じY座標まで上げられる余白を作る。
                spacer.style.height = Math.max(400, iframeWin.innerHeight) + 'px';
            }

            function getSourceAnchors(iframeDoc, iframeWin) {
                const anchors = [];
                const seenLines = new Set();
                iframeDoc.querySelectorAll(SOURCE_LINE_SELECTOR).forEach(function (el) {
                    const line = parseInt(el.dataset.sourceLine, 10);
                    if (!Number.isFinite(line) || seenLines.has(line)) return;
                    seenLines.add(line);
                    anchors.push({
                        line: line,
                        y: el.getBoundingClientRect().top + iframeWin.scrollY,
                    });
                });
                anchors.sort(function (a, b) { return a.line - b.line; });

                const body = iframeDoc.querySelector('.blog-post-body');
                if (!body) return anchors;
                const bodyTop = body.getBoundingClientRect().top + iframeWin.scrollY;
                const bodyBottom = bodyTop + body.offsetHeight;
                const totalLines = Math.max(1, editor.getMarkdown().split('\n').length);

                if (anchors.length === 0 || anchors[0].line > 1) {
                    anchors.unshift({line: 1, y: bodyTop});
                }
                if (anchors[anchors.length - 1].line <= totalLines) {
                    anchors.push({line: totalLines + 1, y: bodyBottom});
                }
                return anchors;
            }

            function sourceLineToPreviewY(line, iframeDoc, iframeWin) {
                const anchors = getSourceAnchors(iframeDoc, iframeWin);
                if (anchors.length === 0) return null;
                if (line <= anchors[0].line) return anchors[0].y;

                for (let i = 1; i < anchors.length; i++) {
                    const prev = anchors[i - 1];
                    const next = anchors[i];
                    if (line <= next.line) {
                        const ratio = (line - prev.line) / Math.max(1, next.line - prev.line);
                        return prev.y + ratio * (next.y - prev.y);
                    }
                }
                return anchors[anchors.length - 1].y;
            }

            function previewYToSourceLine(y, iframeDoc, iframeWin) {
                const anchors = getSourceAnchors(iframeDoc, iframeWin);
                if (anchors.length === 0) return null;
                if (y <= anchors[0].y) return anchors[0].line;

                for (let i = 1; i < anchors.length; i++) {
                    const prev = anchors[i - 1];
                    const next = anchors[i];
                    if (y <= next.y) {
                        const ratio = (y - prev.y) / Math.max(1, next.y - prev.y);
                        return prev.line + ratio * (next.line - prev.line);
                    }
                }
                return anchors[anchors.length - 1].line;
            }

            function editorLineToContentY(line) {
                const children = editorScrollEl.children;
                const totalLines = Math.max(1, editor.getMarkdown().split('\n').length);
                if (children.length !== totalLines || children.length === 0) {
                    return (clamp(line, 1, totalLines) - 1) /
                        Math.max(1, totalLines - 1) * editorScrollEl.scrollHeight;
                }

                const safeLine = clamp(line, 1, totalLines);
                const lowerLine = Math.floor(safeLine);
                const upperLine = Math.ceil(safeLine);
                const editorRect = editorScrollEl.getBoundingClientRect();
                const contentY = function (lineNumber) {
                    const rect = children[lineNumber - 1].getBoundingClientRect();
                    return rect.top - editorRect.top + editorScrollEl.scrollTop;
                };
                const lowerY = contentY(lowerLine);
                const upperY = contentY(upperLine);
                return lowerY + (safeLine - lowerLine) * (upperY - lowerY);
            }

            function editorViewportRatioToLine(viewportRatio) {
                const children = editorScrollEl.children;
                const totalLines = Math.max(1, editor.getMarkdown().split('\n').length);
                if (children.length !== totalLines || children.length === 0) {
                    const contentY = editorScrollEl.scrollTop +
                        viewportRatio * editorScrollEl.clientHeight;
                    return 1 + contentY / Math.max(1, editorScrollEl.scrollHeight) *
                        Math.max(0, totalLines - 1);
                }

                const editorRect = editorScrollEl.getBoundingClientRect();
                const targetY = editorRect.top + viewportRatio * editorRect.height;
                let low = 0;
                let high = children.length - 1;
                while (low < high) {
                    const middle = Math.floor((low + high) / 2);
                    if (children[middle].getBoundingClientRect().bottom < targetY) {
                        low = middle + 1;
                    } else {
                        high = middle;
                    }
                }
                return low + 1;
            }

            function syncPreviewFromEditorScroll() {
                const iframeWin = iframe.contentWindow;
                const iframeDoc = iframe.contentDocument;
                if (!iframeWin || !iframeDoc) return;
                const sourceLine = editorViewportRatioToLine(SCROLL_SYNC_RATIO);
                const previewY = sourceLineToPreviewY(sourceLine, iframeDoc, iframeWin);
                if (!Number.isFinite(previewY)) return;

                ignorePreviewScrollUntil = performance.now() + 100;
                iframeWin.scrollTo({
                    top: Math.max(0, previewY - SCROLL_SYNC_RATIO * iframeWin.innerHeight),
                    behavior: 'auto',
                });
            }

            function syncEditorFromPreviewScroll() {
                const iframeWin = iframe.contentWindow;
                const iframeDoc = iframe.contentDocument;
                if (!iframeWin || !iframeDoc) return;
                const previewY = iframeWin.scrollY + SCROLL_SYNC_RATIO * iframeWin.innerHeight;
                const sourceLine = previewYToSourceLine(previewY, iframeDoc, iframeWin);
                if (!Number.isFinite(sourceLine)) return;

                ignoreEditorScrollUntil = performance.now() + 100;
                editorScrollEl.scrollTop = Math.max(
                    0,
                    editorLineToContentY(sourceLine) -
                        SCROLL_SYNC_RATIO * editorScrollEl.clientHeight,
                );
            }

            editorScrollEl.addEventListener('scroll', function () {
                if (performance.now() < ignoreEditorScrollUntil || editorScrollScheduled) return;
                editorScrollScheduled = true;
                requestAnimationFrame(function () {
                    editorScrollScheduled = false;
                    syncPreviewFromEditorScroll();
                });
            }, {passive: true});

            function attachPreviewScroll() {
                const iframeWin = iframe.contentWindow;
                const iframeDoc = iframe.contentDocument;
                // iframeの再読込ではWindowProxyが再利用される一方、Documentと
                // そのWindow上のリスナーは作り直される。Document単位で登録する。
                if (!iframeWin || !iframeDoc || iframeDoc === previewScrollDocument) return;
                previewScrollDocument = iframeDoc;
                iframeWin.addEventListener('scroll', function () {
                    if (performance.now() < ignorePreviewScrollUntil || previewScrollScheduled) return;
                    previewScrollScheduled = true;
                    requestAnimationFrame(function () {
                        previewScrollScheduled = false;
                        syncEditorFromPreviewScroll();
                    });
                }, {passive: true});
            }

            // ----------------------------------------
            // プレビュー → エディタ：クリックで data-source-line を読んで対応行へ
            // ----------------------------------------
            function attachPreviewBlockClick() {
                const iframeDoc = iframe.contentDocument;
                if (!iframeDoc) return;
                const blocks = iframeDoc.querySelectorAll(SOURCE_LINE_SELECTOR);
                blocks.forEach(function (el) {
                    if (el.dataset.jumpAttached) return;
                    el.dataset.jumpAttached = '1';
                    el.style.cursor = 'pointer';
                    el.title = 'クリックでエディタの該当行へジャンプ';
                    el.addEventListener('click', function (e) {
                        // リンク・画像クリックは元の動作を優先
                        if (e.target.closest && e.target.closest('a, img')) return;
                        e.preventDefault();
                        e.stopPropagation();
                        const line = parseInt(el.dataset.sourceLine, 10);
                        if (!isNaN(line) && line > 0) {
                            const iframeWin = iframe.contentWindow;
                            const previewRatio = iframeWin && iframeWin.innerHeight > 0
                                ? clamp(el.getBoundingClientRect().top / iframeWin.innerHeight, 0.05, 0.95)
                                : 0.2;
                            jumpEditorToLine(line, previewRatio);
                        }
                    });
                });
            }

            // Toast UI Editorの正確な選択DOMを使い、クリック元と同じ画面内Y座標へ合わせる。
            function jumpEditorToLine(line, viewportRatio) {
                try {
                    const totalLines = Math.max(1, editor.getMarkdown().split('\n').length);
                    const safeLine = clamp(line, 1, totalLines);
                    ignoreEditorJumpUntil = performance.now() + 250;
                    editor.setSelection([safeLine, 1], [safeLine, 1]);
                    editor.focus();
                    requestAnimationFrame(function () {
                        requestAnimationFrame(function () {
                            const alignSelectedLine = function () {
                                ignoreEditorScrollUntil = performance.now() + 100;
                                editorScrollEl.scrollTop = Math.max(
                                    0,
                                    editorLineToContentY(safeLine) -
                                        viewportRatio * editorScrollEl.clientHeight,
                                );
                                ignoreEditorJumpUntil = performance.now() + 100;
                            };
                            alignSelectedLine();
                            // Toast UI自身の選択表示が後からスクロールしても最終位置を戻す。
                            setTimeout(alignSelectedLine, 60);
                        });
                    });
                } catch (e) {
                    console.warn('[Jump] エディタへのジャンプに失敗:', e);
                }
            }

            function jumpToCursorLine() {
                const iframeWin = iframe.contentWindow;
                const iframeDoc = iframe.contentDocument;
                if (!iframeWin || !iframeDoc) return;

                const cursorLine = getCurrentEditorLine();
                if (!cursorLine) return;
                const targetY = sourceLineToPreviewY(cursorLine, iframeDoc, iframeWin);
                if (typeof targetY === 'number' && Number.isFinite(targetY)) {
                    const targetOffset = getEditorViewportRatio() * iframeWin.innerHeight;
                    ignorePreviewScrollUntil = performance.now() + 100;
                    iframeWin.scrollTo({
                        top: Math.max(0, targetY - targetOffset),
                        behavior: 'auto',
                    });
                }
            }

            function getCurrentEditorLine() {
                try {
                    // 公式 API：getSelection は [[行,列], [行,列]] を返す（markdownモード）
                    const sel = editor.getSelection();
                    if (Array.isArray(sel) && sel.length > 0) {
                        const from = sel[0];
                        if (Array.isArray(from) && typeof from[0] === 'number') {
                            return from[0];
                        }
                    }
                } catch (e) {
                    // フォールバックへ
                }

                // 公式APIが取れない旧版のみ、DOM座標から近似する。
                try {
                    const selection = window.getSelection();
                    if (!selection || selection.rangeCount === 0) return null;
                    const range = selection.getRangeAt(0);
                    const cursorEl = range.startContainer.nodeType === 3
                        ? range.startContainer.parentElement
                        : range.startContainer;
                    if (!cursorEl || !editorScrollEl.contains(cursorEl)) return null;

                    // Markdown ソース全体に対する位置から行を計算
                    const markdown = editor.getMarkdown();
                    const totalLines = markdown.split('\n').length;
                    // DOM 上の相対位置から行を推定
                    const editorRect = editorScrollEl.getBoundingClientRect();
                    const cursorRect = cursorEl.getBoundingClientRect();
                    const ratio = (cursorRect.top - editorRect.top + editorScrollEl.scrollTop) /
                                  editorScrollEl.scrollHeight;
                    return Math.max(1, Math.round(ratio * totalLines));
                } catch (e) {
                    return null;
                }
            }

            function refreshPreviewBindings() {
                ensurePreviewBottomSpacer();
                attachPreviewBlockClick();
                attachPreviewScroll();
                if (isEditorFocused()) scheduleJump(true);
            }

            onPreviewContentReady = refreshPreviewBindings;
            alignPreviewToEditor = function () { scheduleJump(true); };

            iframe.addEventListener('load', function () {
                const previewMathJax = iframe.contentWindow && iframe.contentWindow.MathJax;
                if (previewMathJax && previewMathJax.startup && previewMathJax.startup.promise) {
                    previewMathJax.startup.promise.then(refreshPreviewBindings, refreshPreviewBindings);
                } else {
                    refreshPreviewBindings();
                }
            });
            setTimeout(refreshPreviewBindings, 100);
        }

        // フォーム送信前にMarkdownを書き戻し
        const form = textarea.closest('form');
        if (form) {
            form.addEventListener('submit', function () {
                textarea.value = editor.getMarkdown();
            });
        }

        // ============================================================
        // デバイスサイズ切替
        // ============================================================
        const deviceBtns = previewContainer.querySelectorAll('.device-btn');
        deviceBtns.forEach(function (btn) {
            btn.addEventListener('click', function () {
                const device = btn.dataset.device;
                const size = DEVICE_SIZES[device];
                iframe.style.width = size.width;
                iframe.style.maxWidth = size.width;
                // ボタンの active 切替
                deviceBtns.forEach(function (b) {
                    b.classList.remove('active');
                });
                btn.classList.add('active');
                // 折り返し位置が変わるので、幅のアニメーション完了後に再計算する。
                setTimeout(function () {
                    onPreviewContentReady();
                    alignPreviewToEditor();
                }, 320);
            });
        });

        // ============================================================
        // プレビュー全画面切替
        // ============================================================
        document.getElementById('preview-fullscreen-btn').addEventListener('click', function () {
            wrapper.classList.toggle('preview-fullscreen');
            requestAnimationFrame(function () {
                onPreviewContentReady();
                alignPreviewToEditor();
            });
        });

        // ============================================================
        // 非同期保存（ページ遷移なし）
        // 通常の Django admin 保存は一覧に戻ってしまうが、
        // これなら編集を続けながら段階的に保存できる
        // ============================================================
        document.getElementById('async-save-btn').addEventListener('click', function () {
            const status = document.getElementById('async-save-status');
            const saveBtn = document.getElementById('async-save-btn');

            // 編集中の Markdown を textarea に同期
            textarea.value = editor.getMarkdown();

            if (!form) {
                status.textContent = '❌ フォームが見つかりません';
                return;
            }

            // FormData にフォーム全体を入れて送信（画像等もまるごと送れる）
            const formData = new FormData(form);
            // Django admin に「保存して編集を続ける」と伝えるためのフラグ
            formData.append('_continue', '');

            saveBtn.disabled = true;
            status.textContent = '⏳ 保存中...';
            status.className = 'saving';

            csrfFetch(window.location.href, {
                method: 'POST',
                body: formData,
            })
                .then(function (response) {
                    if (response.ok || response.redirected) {
                        return response;
                    }
                    throw new Error('HTTP ' + response.status);
                })
                .then(function () {
                    status.textContent = '✅ 保存しました';
                    status.className = 'saved';
                    saveBtn.disabled = false;
                    // 保存後にプレビュー更新
                    updatePreview();
                    // 3秒後にメッセージを消す
                    setTimeout(function () {
                        status.textContent = '';
                        status.className = '';
                    }, 3000);
                })
                .catch(function (err) {
                    status.textContent = '❌ 保存失敗: ' + err.message;
                    status.className = 'error';
                    saveBtn.disabled = false;
                });
        });

        // 初回プレビュー
        updatePreview();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEditor);
    } else {
        initEditor();
    }
})();
