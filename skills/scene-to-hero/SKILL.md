---
name: scene-to-hero
description: Create project knowledge from scene stills through a short interview. Save the knowledge in project.json so ordering and generation can use it.
---

# Scene-to-hero interview

## Flow

Use `init` to create a project, interview the user here, use the UI or `order` to set order, importance, and the final still, then run `generate`. The same questions are available as `scene-to-hero interview <name>`.

## Subject questions

`shape` — What shape is the subject? (silhouette, proportions)
`color` — What color is it?
`material` — What material and surface texture does it have?
`size` — How large does it look in the frame?
`marking` — Is there printed text or a logo on it? Give the exact characters. They are drawn during video generation, never added afterward. Leave blank if there is none.
`orientation` — Any orientation constraint? (for example always facing the camera, must not rotate)
`fragile` — Any fragile parts that tend to break in generation? (thin, transparent, small)
`differences` — If there are several objects, how do they differ from each other?

## Scenery questions

`background` — What kind of background is it?
`light` — Which direction does the light come from, and what color temperature?
`motion` — Which elements move? (cloth, smoke, water)
`foreground` — Any blurred foreground elements?
`avoid` — Which effects must be avoided? (for example lens flares)
`color_treatment` — What color treatment is intended?

## Save and prompt policy

Write a concise answer to `project.json` under `knowledge.subject.description` and `knowledge.scenery.description`; other keys are free-form. Include `has_marking` under subject when useful. `generate` turns these descriptions into stable subject, scene, label, and motion prompt parts.

Printed markings and text must be generated with the image-to-video request, never added afterward. Treat intermediate scene stills as atmosphere references and fix only the final still. Do not accept an asset on numeric QC alone; make a contact sheet for visual review. Keep the final still free of effects. Keep API keys in environment variables and show the estimate before generation.
