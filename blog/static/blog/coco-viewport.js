/** キーボードやアドレスバーを除いた表示領域。ブラウザー差分はここへ集める。 */
export function measureChatViewport(innerHeight, viewport) {
    const layout = Number.isFinite(innerHeight) && innerHeight > 0 ? innerHeight : 800;
    const height = Number.isFinite(viewport?.height) && viewport.height > 0
        ? Math.min(layout, viewport.height) : layout;
    const top = Number.isFinite(viewport?.offsetTop) ? Math.max(0, viewport.offsetTop) : 0;
    return {
        height: Math.round(height),
        bottom: Math.round(Math.max(0, layout - height - top)),
        compact: height < 500,
        tight: height < 300
    };
}
