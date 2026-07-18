from PIL import Image, ImageDraw
import os

def create_icon(size, filename):
    # 暗い背景にシアンのグラデーション、中央にインジケーターを表す丸いディスプレイのシンボルを描く
    img = Image.new('RGB', (size, size), color='#0b0f19')

    # 同心円状のグロー効果
    center = size // 2
    glow_radius = int(size * 0.45)

    # Pillowでアルファブレンドを行うためのベース
    base = img.convert('RGBA')

    for r in range(glow_radius, 0, -2):
        alpha = int(45 * (1 - r / glow_radius))
        overlay = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.ellipse(
            [center - r, center - r, center + r, center + r],
            fill=(0, 240, 255, alpha)
        )
        base = Image.alpha_composite(base, overlay)

    # 中央のリングとドットの描画
    draw = ImageDraw.Draw(base)
    ring_radius = int(size * 0.2)
    draw.ellipse(
        [center - ring_radius, center - ring_radius, center + ring_radius, center + ring_radius],
        outline='#00f0ff',
        width=max(2, size // 50)
    )

    dot_radius = int(size * 0.07)
    draw.ellipse(
        [center - dot_radius, center - dot_radius, center + dot_radius, center + dot_radius],
        fill='#00f0ff'
    )

    # 保存先フォルダの作成
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    base.convert('RGB').save(filename, 'PNG')
    print(f"Created {filename} ({size}x{size})")

if __name__ == '__main__':
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    create_icon(192, os.path.join(static_dir, 'icon-192.png'))
    create_icon(512, os.path.join(static_dir, 'icon-512.png'))
