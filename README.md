# spine-mcp — Spine 2D animation MCP server

Make **production** Spine 2D animations from a cut-up character — straight from a
`.psd` or a PhotoshopToSpine export folder. Inspired by
[`ampersante/spine2d-animation-mcp`](https://github.com/ampersante/spine2d-animation-mcp),
but it ships **real runtime rigs that play in spine-pixi / pixi games** *and*
**editable `.spine` projects** (via the licensed Spine 4.3 CLI), with hand-crafted
motion instead of canned templates.

```
cut parts (you, in Photoshop)            ──▶  rig_and_animate
  PSD  or  PhotoshopToSpine export             │
                                               ├─▶ <name>.json   runtime skeleton (Spine 4.2)
                                               ├─▶ <name>.atlas + .png   packed atlas
                                               └─▶ <name>.spine  EDITABLE project (open in your Spine)
                                          ──▶  preview (montage PNG)  /  wire into the game
```

## Why it beats the original
| | ampersante MCP | spine-mcp (this) |
|---|---|---|
| Rig | auto by layer name | bones placed at joints, head/body hierarchy |
| Motion | predefined templates | hand-authored idle/win/blink/pop, squash-stretch, face-swap |
| Atlas | Pillow | **real Spine CLI packer** |
| Output | JSON / PNG / GIF preview | **runtime rig that runs in the game + editable `.spine`** |
| Head states | drawn stacked | collapsed to one slot, swapped in win/blink |

## Tools
- `spine_doctor` — check Spine CLI + Python deps
- `inspect_source` — list parts / detected head-state families before rigging
- `rig_and_animate(source, out_dir, name?, kind?, anims?, make_editable?)` — the main one
- `pack_atlas(images_dir, out_dir, name)` — Spine-CLI atlas pack
- `make_project(runtime_json, out_spine)` — runtime json → editable `.spine`
- `export_project(project, out_dir, fmt)` — `.spine` → runtime json+atlas
- `project_info(project_or_json)` — bones/slots/animations
- `preview(rig_dir, images_dir?, out_png?, maxpx?)` — keyframe-montage PNG
- `batch(roster_dir, out_root, kind?, make_editable?)` — rig every export subfolder

## Input conventions
- **Head-state family:** name the alt faces `<base>_win` / `<base>_blink`
  (e.g. `head`, `head_win`, `head_blink`, or `face`, `face_win`, `face_blink`).
  They auto-collapse into one slot and swap inside `win` / `blink`.
- **Glow layer** named `Layer 2` → additive blend + pulse. Parts named `*fire*`
  → flicker. Parts named `*rot*` → spinning accent bone.
- Leave a small **overlap tab** on each part under its joint so bends don't tear.

## Install

The easy way, alongside the brain that knows how to use it:

```bash
/plugin marketplace add egorfedorov/mozg-plugin
/plugin install mozg-spine@mozg
```

Or wire it up directly — needs [uv](https://docs.astral.sh/uv/):

```bash
claude mcp add spine -- uvx --from git+https://github.com/egorfedorov/spine-mcp spine-mcp
```

From a checkout, for hacking on it:

```bash
uv venv && uv pip install -e .
./.venv/bin/spine-mcp          # stdio MCP server
```

## Your Spine licence stays yours

This project contains no part of Spine and grants no licence to it. Spine is
commercial software from [Esoteric Software](https://esotericsoftware.com), and
everyone runs their own installation under their own licence. The server looks
for it at `/Applications/Spine.app/Contents/MacOS/Spine`, or wherever `SPINE_BIN`
points.

**It works without one.** Rigging and animating never start a Spine process —
they are plain Python over Pillow and psd-tools. A licence adds three tools:

| | needs Spine |
|---|---|
| `rig_and_animate` — skeleton, animations, atlas | no |
| `inspect_source`, `preview` | no |
| `pack_atlas` — the Spine packer | yes |
| `make_project` / `export_project` — editable `.spine` | yes |
| `project_info` | yes |

`spine_doctor` reports what it found. One person's licence never reaches
another, and nothing degrades because somebody else has a different one.

## Naming conventions it assumes

`_classify` decides what is a head from a word list tuned on one studio's art —
`head, face, golova, crown, tooth`. A layer called `kopf` or `cabeza` lands in
the body instead. Override it rather than renaming your art:

```bash
export SPINE_HEAD_WORDS="kopf,gesicht,krone"
```

## Files
- `server.py` — FastMCP server (tool surface)
- `spine_rig.py` — rig + animation builder (pure, importable, runnable standalone)
- `spine_cli.py` — Spine 4.3 CLI wrappers (pack / import / export / info)
- `spine_preview.py` — keyframe-montage renderer

## Licence

MIT — see [LICENSE](LICENSE). Written for, and used by,
[mozg](https://mozg.sh)'s [Spine 2D Animation brain](https://mozg.sh/b/mozg/spine-2d-animation),
which is the half that knows *how* the rigs should move.

