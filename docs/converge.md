# Convergence correction

Generated video can finish a few pixels away from the selected final image. Replacing only the last frame can therefore create a visible jump. This module gradually aligns the final frames and then replaces the last frame with the exact final image.

The final image is fitted with a cover transform and centrally cropped. Optical flow is calculated from the target image to the last video frame. DIS uses the selected preset and two variational-refinement iterations; Farneback is used when DIS is unavailable. Flow vectors beyond the failure threshold are replaced with 5x5 median-filtered components. Each of the final frames is remapped with a smoothstep weight, `t*t*(3-2*t)`, and the final frame is replaced exactly after correction.

```python
from scene_to_hero.converge import converge, contact_sheet

result = converge("input.mov", "final.png", "output.mov", frames=12, preset="medium")
contact_sheet([("before", "input.mov"), ("after", "output.mov")], "comparison.jpg",
              final_image_path="final.png")
```

| Parameter | Meaning |
| --- | --- |
| `frames` | Number of trailing frames to converge |
| `preset` | DIS preset: `ultrafast`, `fast`, or `medium` |
| `flow_failure_px` | Flow magnitude above which a pixel is replaced |
| `crf` | Default H.264 quality value |
| `encode_args` | Complete replacement for the default encoder arguments |

This is intended for short videos and keeps all decoded frames in memory. The final image is cropped with cover fitting. Audio is discarded. No API calls or usage charges are involved. CLI invocation is planned for the future.

Numeric error and PSNR checks are useful diagnostics, but acceptance should also include visual inspection of a contact sheet.
