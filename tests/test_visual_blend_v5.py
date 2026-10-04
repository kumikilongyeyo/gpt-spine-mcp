from PIL import Image

import spine_blend
import spine_preview


def test_psd_blend_translation():
    screen = spine_blend.translate_psd_blend("screen", layer_name="Glow")
    assert screen["spine"] == "screen"
    assert screen["exact"] is True

    add = spine_blend.translate_psd_blend("linear_dodge", layer_name="FX_Glow")
    assert add["spine"] == "additive"
    assert add["exact"] is True

    multiply = spine_blend.translate_psd_blend("multiply", layer_name="Shadow")
    assert multiply["spine"] == "multiply"

    overlay = spine_blend.translate_psd_blend("overlay", layer_name="FX_Glow")
    assert overlay["bake_required"] is True
    assert overlay["spine"] == "screen"


def test_transparent_rgb_is_blend_neutral():
    image = Image.new("RGBA", (2, 1), (255, 0, 255, 0))
    image.putpixel((1, 0), (200, 100, 20, 255))
    cleaned = spine_blend.sanitize_transparent_rgb(image, "additive")
    assert cleaned.getpixel((0, 0)) == (0, 0, 0, 0)
    assert cleaned.getpixel((1, 0)) == (200, 100, 20, 255)

    cleaned_mul = spine_blend.sanitize_transparent_rgb(image, "multiply")
    assert cleaned_mul.getpixel((0, 0)) == (255, 255, 255, 0)


def test_preview_screen_blend_does_not_create_black_box(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    fx = Image.new("RGBA", (8, 8), (0, 0, 0, 255))
    fx.putpixel((4, 4), (255, 255, 255, 255))
    fx.save(images / "glow.png")

    data = {
        "skeleton": {"width": 16, "height": 16},
        "bones": [{"name": "root"}],
        "slots": [{"name": "glow", "bone": "root", "attachment": "glow", "blend": "screen"}],
        "skins": [{"name": "default", "attachments": {"glow": {"glow": {"width": 8, "height": 8}}}}],
        "animations": {"idle": {}},
    }
    frame = spine_preview.render_frame(data, str(images), "idle", 0, transparent=False)
    pixels = list(frame.getdata())
    # Black is neutral under screen: most affected pixels stay equal to the background,
    # while the white source pixel must brighten at least one output pixel.
    assert (24, 20, 32, 255) in pixels
    assert max(pixel[0] for pixel in pixels) > 24
