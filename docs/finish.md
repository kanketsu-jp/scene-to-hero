# Finishing a hero video

`finish` applies three independent touches: an intro focus pull, a hold of the final image, and an optional hard switch to a supplied image.

Processing happens in this order: intro blur is applied to source frames, hold frames are appended, and the sequence is encoded. The finish step adds no procedural effect such as a flare or glow; supply a switch image that already contains any desired effect.

```python
from scene_to_hero.finish import finish, IntroBlur

finish("input.mp4", "out.mp4", hold_frames=24, switch_image="final.png", intro_blur=IntroBlur(sigma=12, hold_frames=6, ramp_frames=18))
```

| Parameter | Meaning |
| --- | --- |
| `hold_frames` | Number of frames appended after the source. |
| `switch_image` | Optional image used for every appended frame; it is cover-fitted and center-cropped. |
| `intro_blur.sigma` | Full-blur Gaussian sigma in pixels. |
| `intro_blur.hold_frames` | Source frames kept at full blur. |
| `intro_blur.ramp_frames` | Frames used to pull from full blur to focus. |
| `intro_blur.curve` | `smoothstep` or `linear`. |
| `crf` | Default video encoder quality setting. |
| `encode_args` | Optional encoder arguments replacing the defaults. |
| `overwrite` | Allow an existing output to be replaced. |

The blur weight is full during the hold and then follows smoothstep `1 - u*u*(3 - 2*u)` or linear `1 - u`, where `u` runs from zero to one.

Videos must have even width and height. Audio is discarded. Frames are processed one at a time, while all decoded PNGs are kept temporarily on disk. A switch image is cover-fitted and center-cropped, and the focus pull must fit inside the source video. Numeric checks do not replace viewing the result: inspect the first and last frames.

CLI invocation is planned for the future.
