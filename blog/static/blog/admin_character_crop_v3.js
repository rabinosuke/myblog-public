/**
 * キャラクター画像 円形クロップ
 *
 * 動作:
 *   1. CharacterAdmin の <input type="file" name="image"> の change を横取り
 *   2. Cropper.js モーダルを開き、1:1 固定で円形クロップ
 *   3. 確定時、クロップ後の Blob を DataTransfer 経由で input.files に書き戻す
 *   4. Django 標準 ImageField がそのまま PNG として受け取り保存
 *
 * 設計判断:
 *   - サーバー側はノータッチ（バックエンドコード変更ゼロ）
 *   - 出力は正方形 PNG（透過対応）。円形表示は表示側の border-radius:50% に任せる
 *   - DataTransfer による input.files 書き換えは Chrome/Firefox/Safari 全対応
 *
 * 参考URL:
 *   - Cropper.js: https://github.com/fengyuanchen/cropperjs
 *   - DataTransfer: https://developer.mozilla.org/en-US/docs/Web/API/DataTransfer
 *   - input.files への書き戻し技法:
 *     https://stackoverflow.com/questions/47515232/how-to-set-file-objects-and-length-property-at-filelist-object-where-the-files-a
 */

(function () {
    'use strict';

    // 高解像度の元画像を管理画面で潰さないため、1024pxで書き出す
    const OUTPUT_SIZE = 1024;

    let modal = null;
    let cropper = null;
    let targetInput = null;
    let pendingPreviewUrl = null;
    let lastConfirmedFile = null;

    /**
     * 編集フォームのプレビューを、クロップ後の保存予定画像に更新。
     */
    function updateAdminPreview(file) {
        const preview = document.querySelector('#cc-admin-preview');
        if (!preview || !file) return;

        if (pendingPreviewUrl) URL.revokeObjectURL(pendingPreviewUrl);
        pendingPreviewUrl = URL.createObjectURL(file);

        preview.querySelectorAll('.cc-preview-image').forEach(function (img) {
            img.src = pendingPreviewUrl;
        });

        const images = preview.querySelector('.cc-preview-images');
        const empty = preview.querySelector('.cc-preview-empty');
        const state = preview.querySelector('.cc-preview-state');
        if (images) images.classList.remove('is-hidden');
        if (empty) empty.classList.add('is-hidden');
        if (state) state.textContent = '保存前のプレビュー（保存ボタンで反映）';
    }

    /**
     * モーダルHTMLを構築
     */
    function buildModal() {
        const el = document.createElement('div');
        el.id = 'cc-modal';
        el.innerHTML = `
            <div class="cc-overlay"></div>
            <div class="cc-dialog">
                <div class="cc-header">
                    <h3>キャラクター画像を円形にクロップ</h3>
                    <button type="button" class="cc-close" aria-label="閉じる">×</button>
                </div>
                <div class="cc-body">
                    <div class="cc-image-area">
                        <img id="cc-source-image" alt="">
                    </div>
                    <p class="cc-hint">ドラッグで位置調整・ホイールで拡大縮小</p>
                </div>
                <div class="cc-footer">
                    <button type="button" class="cc-cancel">キャンセル</button>
                    <button type="button" class="cc-submit">この範囲で確定</button>
                </div>
            </div>
        `;
        document.body.appendChild(el);

        el.querySelector('.cc-close').addEventListener('click', cancel);
        el.querySelector('.cc-overlay').addEventListener('click', cancel);
        el.querySelector('.cc-cancel').addEventListener('click', cancel);
        el.querySelector('.cc-submit').addEventListener('click', submit);

        return el;
    }

    /**
     * クロップ確定 → input.files に書き戻す
     */
    function submit() {
        if (!cropper || !targetInput) return;

        const canvas = cropper.getCroppedCanvas({
            width: OUTPUT_SIZE,
            height: OUTPUT_SIZE,
            imageSmoothingQuality: 'high',
        });
        if (!canvas) return;

        canvas.toBlob(function (blob) {
            if (!blob) return;

            // 元ファイル名を保持しつつ拡張子を .png に
            const origName = targetInput.dataset.ccOrigName || 'character.png';
            const baseName = origName.replace(/\.[^.]+$/, '');
            const file = new File([blob], baseName + '.png', { type: 'image/png' });

            // input.files を書き換え（DataTransfer 経由）
            const dt = new DataTransfer();
            dt.items.add(file);
            targetInput.files = dt.files;
            lastConfirmedFile = file;

            updateAdminPreview(file);

            close();
        }, 'image/png');
    }

    /**
     * モーダルを閉じる
     */
    function close() {
        if (modal) modal.classList.remove('show');
        if (cropper) {
            cropper.destroy();
            cropper = null;
        }
    }

    /**
     * クロップを取り消した画像が、元のまま保存されるのを防ぐ。
     */
    function cancel() {
        if (targetInput && lastConfirmedFile) {
            const dt = new DataTransfer();
            dt.items.add(lastConfirmedFile);
            targetInput.files = dt.files;
        } else if (targetInput) {
            targetInput.value = '';
        }
        close();
    }

    /**
     * file input の change イベントハンドラ
     */
    function onFileChange(e) {
        const input = e.target;
        const file = input.files && input.files[0];
        if (!file) return;

        if (!modal) modal = buildModal();
        targetInput = input;
        targetInput.dataset.ccOrigName = file.name;

        const img = modal.querySelector('#cc-source-image');
        const reader = new FileReader();
        reader.onload = function (ev) {
            img.src = ev.target.result;
            img.onload = function () {
                if (cropper) cropper.destroy();
                cropper = new Cropper(img, {
                    aspectRatio: 1,
                    viewMode: 1,
                    autoCropArea: 1,
                    responsive: true,
                    background: false,
                    movable: true,
                    zoomable: true,
                    rotatable: false,
                    scalable: false,
                });
            };
        };
        reader.readAsDataURL(file);
        modal.classList.add('show');
    }

    /**
     * submit 前のクライアント側バリデーション
     *
     * 目的: サーバー側バリデーションで弾かれて再描画 → ファイル input が空になる
     *      事故（ブラウザのセキュリティ制約で JS から file 値復元不可）を未然に防ぐ。
     *
     * チェック内容:
     *   - name 必須
     *   - slug 必須 + 形式（半角英数・ハイフン・アンダースコアのみ）
     *   - image 必須（新規作成時のみ。編集時は既に保存済みなのでスキップ）
     *
     * 参考URL:
     *   - SlugField の許可文字: https://docs.djangoproject.com/en/5.0/ref/forms/fields/#slugfield
     *   - file input が再描画で空になる仕様:
     *     https://developer.mozilla.org/en-US/docs/Web/API/HTMLInputElement/files
     */
    function attachFormValidation() {
        const form = document.querySelector('#character_form');
        if (!form) return;

        const nameInput = form.querySelector('#id_name');
        const slugInput = form.querySelector('#id_slug');
        const imageInput = form.querySelector('#id_image');
        // 既に画像が保存済みかどうか（編集画面では「現在: xxx.png」リンクが表示される）
        const hasExistingImage = !!form.querySelector('input[name="image-clear"], a[href*="/media/characters/"]');

        const SLUG_RE = /^[-a-zA-Z0-9_]+$/;

        form.addEventListener('submit', function (e) {
            const errors = [];

            if (!nameInput.value.trim()) {
                errors.push('表示名を入力してください');
            }
            const slugVal = slugInput.value.trim();
            if (!slugVal) {
                errors.push('スラッグを入力してください（日本語名の場合は手入力が必要です）');
            } else if (!SLUG_RE.test(slugVal)) {
                errors.push('スラッグは半角英数・ハイフン・アンダースコアのみ使えます');
            }
            if (!hasExistingImage && (!imageInput.files || imageInput.files.length === 0)) {
                errors.push('アイコン画像を選択してください');
            }

            if (errors.length > 0) {
                e.preventDefault();
                alert('入力エラー:\n\n・' + errors.join('\n・'));
            }
        });
    }

    /**
     * Character フォームの image input を探して change を横取り
     */
    function init() {
        // CharacterAdmin の image フィールドは name="image"
        const input = document.querySelector('#id_image');
        if (!input) return;  // Character の編集画面以外は何もしない
        input.addEventListener('change', onFileChange);
        attachFormValidation();
        window.addEventListener('beforeunload', function () {
            if (pendingPreviewUrl) URL.revokeObjectURL(pendingPreviewUrl);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
