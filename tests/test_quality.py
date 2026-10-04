import json

from PIL import Image, ImageDraw

from spine_quality import audit_preview, audit_project_assets


def _gif(path, moving):
    frames = []
    for index in range(12):
        image = Image.new("RGB", (80, 80), "#081020")
        if moving:
            ImageDraw.Draw(image).rectangle((index * 3, 25, index * 3 + 20, 45), fill="gold")
        frames.append(image)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)


def test_preview_gate_rejects_blank_and_accepts_motion(tmp_path):
    blank = tmp_path / "blank.gif"
    moving = tmp_path / "moving.gif"
    _gif(blank, False)
    _gif(moving, True)
    assert not audit_preview(str(blank))["ok"]
    assert audit_preview(str(moving))["ok"]


def test_project_assets_must_travel_with_project(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGBA", (4, 4), "white").save(images / "body.png")
    runtime = tmp_path / "hero.json"
    runtime.write_text(json.dumps({
        "skeleton": {"images": "./images/"},
        "skins": [{"attachments": {"body": {"body": {"width": 4, "height": 4}}}}],
    }))
    project = tmp_path / "hero.spine"
    project.write_bytes(b"test")
    assert audit_project_assets(str(runtime), str(project))["ok"]
    (images / "body.png").unlink()
    assert not audit_project_assets(str(runtime), str(project))["ok"]
