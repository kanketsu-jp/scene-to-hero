---
name: scene-to-hero
description: Create project knowledge from scene stills through a short interview. Save the knowledge in project.json so ordering and generation can use it.
---

# Scene-to-hero interview

## Flow

Use `init` to create a project, interview the user here, use the UI or `order` to set order, importance, and the final still, then run `generate`.

## Subject questions

Ask about shape, color, material and texture, size impression, printed surface or text and its exact content, orientation constraints such as facing front or not rotating, fragile parts such as thin or transparent pieces, and differences between individual objects when there are several.

## Scenery questions

Ask about background type, light direction and color temperature, moving elements such as cloth, smoke, or water, blurred foreground elements, effects to avoid such as flares, and the intended color treatment.

## Save and prompt policy

Write a concise answer to `project.json` under `knowledge.subject.description` and `knowledge.scenery.description`; other keys are free-form. Include `has_marking` under subject when useful. `generate` turns these descriptions into stable subject, scene, label, and motion prompt parts.

Printed markings and text must be generated with the image-to-video request, never added afterward. Treat intermediate scene stills as atmosphere references and fix only the final still. Do not accept an asset on numeric QC alone; make a contact sheet for visual review. Keep the final still free of effects. Keep API keys in environment variables and show the estimate before generation.
