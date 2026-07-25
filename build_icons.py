from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "assets" / "droste-icon-source.png"
STATIC = ROOT / "static"


def main():
    with Image.open(SOURCE) as source:
        icon = source.convert("RGBA")
        icon.resize((180, 180), Image.Resampling.LANCZOS).save(
            STATIC / "icon-180.png",
            optimize=True,
        )
        icon.resize((512, 512), Image.Resampling.LANCZOS).save(
            STATIC / "icon-512.png",
            optimize=True,
        )
        icon.resize((192, 192), Image.Resampling.LANCZOS).save(
            STATIC / "icon-192.png",
            optimize=True,
        )
        icon.save(
            ROOT / "droste.ico",
            format="ICO",
            sizes=[
                (16, 16),
                (24, 24),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            ],
        )


if __name__ == "__main__":
    main()
