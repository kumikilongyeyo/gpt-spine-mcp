# GPT Spine MCP

OpenAI/Codex tooling for turning a layered PSD or PhotoshopToSpine export into a
validated Spine 2D rig, animation set, atlas, preview, and—when a licensed Spine
CLI is installed—an editable `.spine` project and runtime export.

This project is based on
[`egorfedorov/spine-mcp`](https://github.com/egorfedorov/spine-mcp) and keeps the
original MIT licence and server tools. It removes the Claude-only setup path,
adds a production CLI, exposes a complete MCP workflow, and includes an OpenAI
Agents SDK client.

## What it produces

```text
mega_win.spine       # when the licensed Spine CLI is available
mega_win.atlas
mega_win.png
mega_win.json
rig_report.json
preview/
  montage.png
export/              # optional Spine CLI export
```

The pure-Python rig, atlas, runtime JSON, validation report, and preview work
without Spine. Spine is commercial software and is not bundled or licensed by
this project.

## One-command local install

From a checkout on macOS or Linux:

```bash
./scripts/install.sh
```

That installs `gpt-spine`, `gpt-spine-mcp`, and the optional OpenAI Agents SDK
with `uv`. Pass `--codex` to also register the local stdio MCP server:

```bash
./scripts/install.sh --codex
```

On Windows PowerShell:

```powershell
.\scripts\install.ps1
.\scripts\install.ps1 -Codex  # also register the stdio server with Codex
```

For an immutable release, replace the URL below with the published fork and pin
the tag:

```bash
uv tool install "gpt-spine-mcp[agents] @ git+https://github.com/kumikilongyeyo/gpt-spine-mcp@v0.7.1"
codex mcp add gpt-spine -- gpt-spine-mcp
```

## Command line workflow

```bash
gpt-spine rig mega_win.psd \
  --source-group "mega win" \
  --clean-mesh \
  --auto-weight \
  --ik \
  --clipping \
  --slot-presets pulse,flash,flicker \
  --fx-presets coin_splash,glow_flash,particle_explosion,bomb_explosion,fire,splash,clipped_shine \
  --animations intro,steering,wave,mega_win,celebration
```

Useful modes:

```bash
# Skeleton, atlas, and validation only—no animation timelines.
gpt-spine rig character.psd --rig-only

# Keep runtime output independent of the licensed editor.
gpt-spine rig character.psd --no-editable --no-export

# Check which Spine executable was auto-detected.
gpt-spine doctor

# Reject a blank/static preview and compare it with an art-direction reference.
gpt-spine audit-preview build/mega_win.gif --reference art-direction.gif
```

`SPINE_BIN` has priority when set. Otherwise macOS detection checks the normal
app bundle, user Applications, Setapp, versioned bundles, and `PATH`. Windows
checks `Spine.com` first (the console executable recommended for CLI use), then
`Spine.exe`, under Program Files, Program Files (x86), LocalAppData, and `PATH`.
Linux checks `~/Spine/Spine.sh`, `/opt/Spine/Spine.sh`, and `PATH`.

## OpenAI Agents SDK client

Set `OPENAI_API_KEY`, then give the agent a natural-language production request:

```bash
gpt-spine agent "Inspect ./mega_win.psd, build a clean weighted rig with IK, generate intro and celebration, and validate it"
```

The client launches this repository's MCP server over stdio with
`MCPServerStdio`, attaches it to an OpenAI `Agent`, and runs the request through
the Agents SDK. Use only trusted MCP servers and review actions that touch
licensed projects or production assets.

## MCP tools

- `build_workflow` — complete rig → animation → save/export → preview → validate flow
- `spine_doctor` — dependency and Spine CLI diagnosis
- `inspect_source` — layer, part, and head-state inspection
- `rig_and_animate` — lower-level rig builder with mesh/weight/IK/clipping options
- `validate_output` — semantic runtime/atlas validation
- `pack_atlas` — licensed Spine CLI texture packing
- `make_project` — runtime JSON to editable `.spine`
- `export_project` — `.spine` to runtime output
- `project_info` — licensed CLI project inspection
- `preview` — fast keyframe montage
- `audit_preview` — objective blank/static/duration/motion/occupancy delivery gate
- `apply_motion_spec` — compile model-authored numeric clips with named easing curves
- `validate_motion` — frame-grid, intro→loop handoff, and loop-seam audit
- `batch` — process a roster of PhotoshopToSpine exports

## Rig and animation behavior

- `--clean-mesh` converts regions to non-degenerate four-corner meshes.
- `--auto-weight` writes single-bone weighted vertices as an editable baseline.
- `--ik` adds a target and constraint for the head, or body when no head exists.
- `--clipping` adds a full-rig bounds clip that can be reshaped in Spine.
- Known state names (`intro`, `steering`, `wave`, `mega_win`, `celebration`) get
  tailored starter timelines. Any other state gets a safe neutral motion instead
  of being dropped.
- Slot presets (`pulse`, `flash`, `flicker`) are separate animations suitable for
  mixing on another track.
- Head variants named `<base>_win` and `<base>_blink` collapse into one slot and
  use attachment timelines.

These are deterministic production starters, not an art-director replacement.
Mesh topology, weights, constraint targets, and clipping shapes should still be
reviewed on hero assets.

## Authored motion workflow

The MCP does not pretend a preset name can understand art direction. For hero
animation, the agent first inspects the actual bones and slots, translates the
description into a numeric JSON motion spec, and calls `apply_motion_spec`.
Supported named curves are `out`, `in`, `inout`, `sine`, `outback`, and `expo`.
`validate_motion` then checks frame alignment, exact intro→loop handoffs, and
loop seams before the editor import/render step.

FX packs are mixable independent animations: `fx_coin_splash`,
`fx_glow_flash`, `fx_particle_explosion`, `fx_bomb_explosion`, `fx_fire`, and
`fx_splash`, plus `fx_clipped_shine`. They use additive white-on-alpha light assets, staggered bones,
overshoot/follow-through, source coin artwork when available, and authored
Bezier curves. FX are invisible in the setup pose, alpha is snapped to the
runtime's 8-bit precision, and clipped shine derives its mask from the target
art's alpha silhouette. Procedural fire/smoke are blocking FX; painterly hero effects
should still be supplied as art and driven by the same motion-spec compiler.

Editable `.spine` files do not embed bitmaps. Always deliver the project beside
its `images/` folder. GPT Spine copies every source and generated image into the
final output before import and fails portability validation if any are missing.

## Input conventions

A PhotoshopToSpine directory needs a layout JSON containing `skins` and an
`images/` directory. PSD import walks visible nested leaf layers, assigns unique
attachment names, and reports semantic role guesses. Use `--source-group` when
the intended asset set is inside a named folder; this prevents unrelated PSD
concepts from being flattened into the rig. The
default head classifier recognizes `head,face,golova,crown,tooth`; override it:

```bash
export SPINE_HEAD_WORDS="kopf,gesicht,krone"
```

## Development and verification

```bash
uv sync --extra dev --extra agents
uv run pytest
uv run gpt-spine doctor
```

The macOS, Windows, and Linux CI jobs build a synthetic character end to end,
assert rig-only behavior,
verify mesh weights, IK, clipping, state generation, slot presets, reports, and
preview output. Tests do not require a Spine licence or OpenAI API key.

## Release line

| Version | Scope |
|---|---|
| `v0.1.0` | upstream MCP rig/animation foundation |
| `v0.2.0` | PSD import and rig workflow |
| `v0.3.0` | mesh, weights, and constraints |
| `v0.4.0` | multi-state animation generation |
| `v0.5.0` | slot FX, clipping, validation, OpenAI/Codex client |
| `v0.5.1` | macOS/Windows/Linux installers and cross-platform CI |
| `v0.6.0` | nested PSD groups, semantic inspection, reference preview QA, portable image checks |
| `v0.6.1` | package QA module and install PSD composite dependencies |
| `v0.7.0` | numeric motion-spec compiler, curve/seam validation, and mixable FX packs |
| `v0.7.1` | setup-hidden FX, byte-snapped alpha, and alpha-silhouette clipped shine |
| `v1.0.0` | planned stable workflow after real-asset compatibility testing |

Never move a published tag. Patch a release and add a new tag so a known-good
production build remains reproducible.

## Licence

MIT. See [`LICENSE`](LICENSE). Original work copyright remains with its author;
new contributions remain under the same licence.
