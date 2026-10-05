# scene-to-hero

scene-to-hero turns a set of scene stills into a short hero video that converges on one final still. The first version provides a Python 3.12+ CLI, a static ordering mock, a budget ledger, and a reverse-playback generation path.

## Flow

Prepare stills, create knowledge through the interview skill, set order and importance in the UI, choose the final still, and generate.

Typical flow: `init` → `order` (or UI) → `generate --dry-run` → `generate` → `converge` → review the contact sheet. See [docs/converge.md](docs/converge.md).

Input images may be png, jpg, jpeg, or webp. Use the final image's aspect ratio as the target. The final image should be an effects-free version; file names are arbitrary.

## Install

```sh
uv venv
uv pip install -e '.[dev]'
```

ffmpeg and ffprobe must be available. Set `FAL_KEY` in the environment when making a real generation request; never commit its value.

## Usage

```sh
scene-to-hero init demo --scenes ./stills
scene-to-hero order demo --final scene-02
scene-to-hero generate demo --prompt "A calm studio scene" --dry-run
scene-to-hero generate demo --prompt-file prompt.txt --yes
```

The default output is `<root>/<project>/` (including when `--root` is specified). Set `SCENE_TO_HERO_HOME` to change the default root. A budget-over-limit generation stops with exit code 3 even when `FAL_KEY` is not set. The dry run prints billed seconds, price, estimate, existing total, and limit without requiring `FAL_KEY` or writing a ledger.

The CLI and UI preserve unknown project and scene keys.

The UI can be used by opening `ui/index.html` with `file://` and selecting a project.json and its images, or by running `python3 -m http.server` at the repository root and opening `ui/index.html?project=<relative project.json path>`. Use drag-and-drop or Up/Down, edit importance, final, and note, then validate and download; save the downloaded `project.json` over the original. Test it with `node --test ui/` (not needed where Node is unavailable).

Exit codes: 0 success, 1 runtime error, 2 unimplemented subcommand, 3 budget exceeded.

The default estimate is ceil(4.2) seconds × $0.096 = $0.48; a failed retry can reserve up to $0.96. These are estimates, and the fal.ai dashboard is authoritative for actual charges. Unit prices can change; the model page is the source to re-check.

## Design notes

Intermediate stills remain atmosphere references; only the final still is fixed. Reverse playback makes the final image the last frame. Printed text belongs in generation, and numeric QC should be paired with a contact sheet for visual review. Upscaling adds fidelity to size, not new detail.

## Roadmap

| Feature | Status |
|---|---|
| converge | implemented |
| finish | not implemented yet |
| upscale | not implemented yet |
| label_erase | not implemented yet |
| review | not implemented yet |
| export | not implemented yet |
| interview automation | not implemented yet |

## License

MIT.

## 日本語

シーン画像を用意し、対話でナレッジを作り、UI または CLI で順番・重要度・最終画像を決めてから生成します。中間画像は雰囲気の参考にし、最終画像だけを固定して逆再生で収束させます。後続工程は未実装です。

UI は `ui/index.html` を開き、project.json と画像を選んで編集・保存します。
