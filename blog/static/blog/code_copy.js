/**
 * コードブロックにコピーボタンを付与する
 *
 * 対象：
 *   - .codehilite-wrap（Qiita風ファイル名付き）
 *   - .codehilite（標準コードブロック）
 *
 * クリックで <pre><code> 内のテキストをクリップボードへ。
 * 成功時は「Copied!」を一瞬表示する。
 */
(function () {
    'use strict';

    function attachCopyButtons(root) {
        // root が指定されてれば root 配下、なければ document 全体
        const scope = root || document;
        // ファイル名付きコードブロックと標準コードブロック両方が対象
        const blocks = scope.querySelectorAll('.codehilite-wrap, .codehilite');

        blocks.forEach(function (block) {
            if (block.dataset.copyAttached) return;
            // .codehilite が .codehilite-wrap の中にある場合は、wrap 側にボタンを付けるので飛ばす。
            // 行属性処理などで間に <p> 等が挟まり親子関係が崩れても、
            // closest() で先祖を辿るので確実に重複付与を防げる。
            if (block.classList.contains('codehilite') && block.closest('.codehilite-wrap')) {
                return;
            }
            // 既にコピーボタンがある要素には二度付けしない（多重実行への保険）
            if (block.querySelector(':scope > .codehilite-copy-btn')) return;
            block.dataset.copyAttached = '1';

            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'codehilite-copy-btn';
            button.title = 'コードをコピー';
            button.setAttribute('aria-label', 'コードをコピー');
            // Claude UI 風の四角いコピーアイコン（2つの重なった四角）
            button.innerHTML = (
                '<svg class="codehilite-copy-icon" xmlns="http://www.w3.org/2000/svg" ' +
                'width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
                'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
                'stroke-linejoin="round" aria-hidden="true">' +
                '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>' +
                '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>' +
                '</svg>' +
                '<span class="codehilite-copy-label">Copy</span>'
            );

            button.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                const codeEl = block.querySelector('pre');
                if (!codeEl) return;
                const text = codeEl.textContent;

                // Clipboard API（モダンブラウザ）
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(function () {
                        showFeedback(button, true);
                    }, function () {
                        fallbackCopy(text, button);
                    });
                } else {
                    fallbackCopy(text, button);
                }
            });

            block.appendChild(button);
        });
    }

    // 古いブラウザ用のフォールバック
    function fallbackCopy(text, button) {
        try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(textarea);
            showFeedback(button, ok);
        } catch (e) {
            showFeedback(button, false);
        }
    }

    function showFeedback(button, success) {
        const label = button.querySelector('.codehilite-copy-label');
        const originalText = 'Copy';
        if (success) {
            label.textContent = 'Copied!';
            button.classList.add('codehilite-copy-success');
        } else {
            label.textContent = 'Failed';
            button.classList.add('codehilite-copy-failed');
        }
        setTimeout(function () {
            label.textContent = originalText;
            button.classList.remove('codehilite-copy-success', 'codehilite-copy-failed');
        }, 1500);
    }

    // 初回ロード時
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { attachCopyButtons(); });
    } else {
        attachCopyButtons();
    }

    // 動的にコンテンツが差し替わる場合（管理画面プレビュー等）に再実行できるよう公開
    window.AttachCodeCopyButtons = attachCopyButtons;
})();
