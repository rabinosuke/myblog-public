import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../static/blog/admin_editor.js', import.meta.url), 'utf8');
const start = source.indexOf('function buildBubblePanel()');
const end = source.indexOf('buildBubblePanel();', start);
assert.ok(start >= 0 && end > start);
const panelCode = source.slice(start, end) + '\nbuildBubblePanel();';

function build(characters) {
    const nodes = [];
    function createElement(tag) {
        const node = {
            tag, dataset: {}, children: [], textContent: '',
            appendChild(child) { this.children.push(child); },
            replaceChildren() { this.children = []; },
            // DOMへHTMLを戻した変更は、危険な入力を使うこのテストで必ず落とす。
            set innerHTML(value) { throw new Error('HTML interpolation is forbidden'); },
        };
        nodes.push(node);
        return node;
    }
    const panel = createElement('div');
    vm.runInNewContext(panelCode, {
        allCharacters: characters, bubblePanel: panel, document: { createElement },
        window: { location: { origin: 'https://coco-lab.io' } }, URL,
    }, { timeout: 500 });
    return { panel, nodes };
}

test('キャラ名・グループ名・スラッグをHTMLとして解釈しない', () => {
    const payload = '<img src=x onerror=alert(1)>';
    const { nodes } = build([{ name: payload, slug: '<svg onload=alert(1)>', image_url: '' }]);
    assert.equal(nodes.find(node => node.className === 'bp-name').textContent, payload);
    assert.equal(nodes.find(node => node.className === 'bp-slug').textContent, '<svg onload=alert(1)>');
    assert.equal(nodes.filter(node => node.tag === 'img').length, 0);
    const buttons = nodes.filter(node => node.tag === 'button');
    assert.equal(buttons.length, 2);
    assert.equal(buttons[0].dataset.slug, '<svg onload=alert(1)>');
    assert.equal(buttons[1].dataset.pos, '-r');
});

test('同一オリジンの画像URLは属性文字列へ連結せずsrcへ設定する', () => {
    const { nodes } = build([{ name: 'COCO', slug: 'coco', image_url: '/media/characters/coco.png' }]);
    assert.equal(nodes.find(node => node.tag === 'img').src, 'https://coco-lab.io/media/characters/coco.png');
});

for (const image_url of ['javascript:alert(1)', 'data:image/svg+xml,<svg onload=alert(1)>', 'https://example.invalid/track']) {
    test(`危険・外部の画像URLを使わない: ${image_url}`, () => {
        const { nodes } = build([{ name: 'COCO', slug: 'coco', image_url }]);
        assert.equal(nodes.filter(node => node.tag === 'img').length, 0);
    });
}

test('特殊なスラッグでもプロトタイプを変更せずパネルを作る', () => {
    const { nodes } = build([{ name: 'COCO', slug: '__proto__', image_url: '' }]);
    assert.equal(nodes.filter(node => node.className === 'bp-row').length, 1);
});

test('未登録でもHTMLを挿入しない', () => {
    const { nodes } = build([]);
    assert.equal(nodes.find(node => node.className === 'bp-empty').textContent, 'キャラ未登録。');
});
