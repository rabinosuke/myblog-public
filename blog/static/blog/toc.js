/**
 * 記事詳細ページの目次（TOC）アクティブハイライト
 *
 * IntersectionObserver で本文中の h2/h3 を監視し、
 * 現在ビューポートに入っている見出しに対応する目次リンクを強調表示する。
 */
(function () {
    'use strict';

    function init() {
        const tocLinks = document.querySelectorAll('.post-toc-nav a[data-toc-anchor]');
        if (tocLinks.length === 0) return;

        // anchor -> link 要素の対応表
        const linkMap = {};
        tocLinks.forEach(function (a) {
            linkMap[a.dataset.tocAnchor] = a;
        });

        // 本文中の対象見出しを集める
        const headings = Object.keys(linkMap)
            .map(function (id) { return document.getElementById(id); })
            .filter(function (el) { return el !== null; });

        if (headings.length === 0) return;

        // 現在表示中のうち最も上にあるものをアクティブにする
        const visibleIds = new Set();

        function updateActive() {
            // visibleIds に入っている中で、document 順に最初のものをアクティブにする
            let activeId = null;
            for (let i = 0; i < headings.length; i++) {
                if (visibleIds.has(headings[i].id)) {
                    activeId = headings[i].id;
                    break;
                }
            }
            tocLinks.forEach(function (a) {
                a.classList.remove('toc-active');
            });
            if (activeId && linkMap[activeId]) {
                linkMap[activeId].classList.add('toc-active');
            }
        }

        const observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    visibleIds.add(entry.target.id);
                } else {
                    visibleIds.delete(entry.target.id);
                }
            });
            updateActive();
        }, {
            // 画面上部 80px ～ 下部 60% で「読んでる範囲」とみなす
            rootMargin: '-80px 0px -60% 0px',
            threshold: 0,
        });

        headings.forEach(function (h) { observer.observe(h); });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
