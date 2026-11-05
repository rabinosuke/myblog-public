/**
 * 画像アップロード用モーダル
 *
 * 機能:
 *   1. ファイル選択 → モーダルで Cropper.js のクロップUI表示
 *   2. 縦横サイズ数値入力 or ドラッグで範囲指定
 *   3. Alt text 必須入力（SEO・アクセシビリティ）
 *   4. JPEG品質スライダー
 *   5. サーバーにアップロード → URLとaltを返却
 *
 * 使い方:
 *   ImageUploader.open(file).then(result => {
 *       // result = { url, alt }
 *   });
 *
 * 拡張ポイント:
 *   - DEFAULTS: 既定値の調整
 *   - buildModal(): モーダルHTMLの追加
 *   - uploadToServer(): POSTパラメータの追加
 */
window.ImageUploader = (function () {
    'use strict';

    const DEFAULTS = {
        aspectRatio: NaN,  // NaN = フリー切り抜き
        quality: 85,
        maxWidth: 1600,
        uploadUrl: '/admin-image-upload/',
    };

    let modal = null;
    let cropper = null;
    let resolveFn = null;

    /**
     * モーダルのHTMLを構築して body に追加する
     */
    function buildModal() {
        const el = document.createElement('div');
        el.id = 'image-uploader-modal';
        el.innerHTML = `
            <div class="iu-overlay"></div>
            <div class="iu-dialog">
                <div class="iu-header">
                    <h3>画像を挿入</h3>
                    <button type="button" class="iu-close">×</button>
                </div>
                <div class="iu-body">
                    <div class="iu-image-area">
                        <img id="iu-source-image" alt="">
                    </div>
                    <div class="iu-settings">
                        <div class="iu-section">
                            <label>切り抜きサイズ（px）</label>
                            <div class="iu-row">
                                <input type="number" id="iu-crop-width" min="1" placeholder="幅">
                                <span>×</span>
                                <input type="number" id="iu-crop-height" min="1" placeholder="高さ">
                                <button type="button" id="iu-apply-size">適用</button>
                            </div>
                            <p class="iu-hint">ドラッグで範囲を動かす・拡大縮小もOK</p>
                        </div>

                        <div class="iu-section">
                            <label>プリセット比率</label>
                            <div class="iu-row">
                                <button type="button" data-ratio="NaN" class="iu-ratio active">フリー</button>
                                <button type="button" data-ratio="1" class="iu-ratio">1:1</button>
                                <button type="button" data-ratio="1.7777" class="iu-ratio">16:9</button>
                                <button type="button" data-ratio="1.3333" class="iu-ratio">4:3</button>
                            </div>
                        </div>

                        <div class="iu-section">
                            <label>最大幅: <span id="iu-max-width-label">1600</span> px</label>
                            <input type="range" id="iu-max-width" min="200" max="2400" step="100" value="1600">
                        </div>

                        <div class="iu-section">
                            <label>JPEG品質: <span id="iu-quality-label">85</span></label>
                            <input type="range" id="iu-quality" min="50" max="100" step="5" value="85">
                        </div>

                        <div class="iu-section">
                            <label>代替テキスト（alt）<span class="iu-required">*</span></label>
                            <input type="text" id="iu-alt" placeholder="例：DjangoのトップページのスクリーンショットでSEOに使われます">
                            <p class="iu-hint">画像の内容を簡潔に。SEO・アクセシビリティのため必須。</p>
                        </div>
                    </div>
                </div>
                <div class="iu-footer">
                    <button type="button" id="iu-cancel-btn">キャンセル</button>
                    <button type="button" id="iu-submit-btn" disabled>挿入する</button>
                </div>
            </div>
        `;
        document.body.appendChild(el);
        return el;
    }

    /**
     * イベントハンドラを登録
     */
    function bindEvents() {
        modal.querySelector('.iu-close').addEventListener('click', close);
        modal.querySelector('.iu-overlay').addEventListener('click', close);
        modal.querySelector('#iu-cancel-btn').addEventListener('click', close);

        // プリセット比率
        modal.querySelectorAll('.iu-ratio').forEach(function (btn) {
            btn.addEventListener('click', function () {
                modal.querySelectorAll('.iu-ratio').forEach(function (b) { b.classList.remove('active'); });
                btn.classList.add('active');
                const ratio = parseFloat(btn.dataset.ratio);
                if (cropper) cropper.setAspectRatio(ratio || NaN);
            });
        });

        // 数値入力でクロップサイズ指定
        modal.querySelector('#iu-apply-size').addEventListener('click', function () {
            const w = parseInt(modal.querySelector('#iu-crop-width').value);
            const h = parseInt(modal.querySelector('#iu-crop-height').value);
            if (cropper && w > 0 && h > 0) {
                cropper.setData({ width: w, height: h });
            }
        });

        // 最大幅スライダー
        const maxWidthInput = modal.querySelector('#iu-max-width');
        maxWidthInput.addEventListener('input', function () {
            modal.querySelector('#iu-max-width-label').textContent = this.value;
        });

        // 品質スライダー
        const qualityInput = modal.querySelector('#iu-quality');
        qualityInput.addEventListener('input', function () {
            modal.querySelector('#iu-quality-label').textContent = this.value;
        });

        // Alt 入力で挿入ボタン有効化
        const altInput = modal.querySelector('#iu-alt');
        altInput.addEventListener('input', function () {
            modal.querySelector('#iu-submit-btn').disabled = !altInput.value.trim();
        });

        // 挿入ボタン
        modal.querySelector('#iu-submit-btn').addEventListener('click', submit);
    }

    /**
     * クロップ後の画像をサーバーにアップロード
     */
    function submit() {
        if (!cropper) return;
        const submitBtn = modal.querySelector('#iu-submit-btn');
        submitBtn.disabled = true;
        submitBtn.textContent = 'アップロード中...';

        // Cropper.js でクロップした canvas を取得
        const canvas = cropper.getCroppedCanvas();
        if (!canvas) {
            submitBtn.disabled = false;
            submitBtn.textContent = '挿入する';
            return;
        }

        canvas.toBlob(function (blob) {
            uploadToServer(blob)
                .then(function (result) {
                    if (resolveFn) resolveFn(result);
                    close();
                })
                .catch(function (err) {
                    console.error('画像アップロード失敗:', err);
                    alert('画像のアップロードに失敗しました: ' + err.message);
                    submitBtn.disabled = false;
                    submitBtn.textContent = '挿入する';
                });
        }, 'image/jpeg', 0.95);
    }

    /**
     * サーバーへPOST
     */
    function uploadToServer(blob) {
        const formData = new FormData();
        formData.append('image', blob, 'image.jpg');
        formData.append('alt', modal.querySelector('#iu-alt').value.trim());
        formData.append('quality', modal.querySelector('#iu-quality').value);
        formData.append('max_width', modal.querySelector('#iu-max-width').value);

        // admin_editor.js が登録した CSRF対応 fetch を使う
        // なければ素の fetch にフォールバック（admin外での利用想定）
        const doFetch = window.AdminCsrfFetch || fetch;
        return doFetch(DEFAULTS.uploadUrl, {
            method: 'POST',
            body: formData,
            credentials: 'same-origin',
        })
            .then(function (res) {
                if (!res.ok) throw new Error('HTTP ' + res.status);
                return res.json();
            });
    }

    /**
     * モーダルを開く
     * @param {File} file アップロードする画像ファイル
     * @returns {Promise} 挿入時に { url, alt } を resolve
     */
    function open(file) {
        return new Promise(function (resolve) {
            resolveFn = resolve;

            if (!modal) {
                modal = buildModal();
                bindEvents();
            }

            // 既存のcropperを破棄
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }

            // 画像をプレビューに設定
            const img = modal.querySelector('#iu-source-image');
            const reader = new FileReader();
            reader.onload = function (e) {
                img.src = e.target.result;
                img.onload = function () {
                    cropper = new Cropper(img, {
                        aspectRatio: DEFAULTS.aspectRatio,
                        viewMode: 1,
                        autoCropArea: 1,
                        responsive: true,
                        background: false,
                        movable: true,
                        zoomable: true,
                        rotatable: false,
                        scalable: false,
                        crop: function (event) {
                            modal.querySelector('#iu-crop-width').value = Math.round(event.detail.width);
                            modal.querySelector('#iu-crop-height').value = Math.round(event.detail.height);
                        },
                    });
                };
            };
            reader.readAsDataURL(file);

            // フォーム初期化
            modal.querySelector('#iu-alt').value = '';
            modal.querySelector('#iu-submit-btn').disabled = true;
            modal.querySelector('#iu-submit-btn').textContent = '挿入する';
            modal.classList.add('show');
        });
    }

    function close() {
        if (modal) modal.classList.remove('show');
        if (cropper) {
            cropper.destroy();
            cropper = null;
        }
        if (resolveFn) {
            resolveFn(null);  // キャンセル時は null
            resolveFn = null;
        }
    }

    return { open: open, close: close };
})();
