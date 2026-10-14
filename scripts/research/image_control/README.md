# Exact image correspondence

This private-research helper locates one exact decoded pixel region in an equally
sized current image. It returns current top-left pixel coordinates and a declared
relative point. It refuses missing or duplicate matches, flat references, changed
canvas dimensions, invalid geometry and changed or unowned input files. It does
not rescale, use fuzzy matching, identify a control or authorize input.

Compile `owned_image.swift` and `match_control.swift` together with Swift 6,
`-parse-as-library -warnings-as-errors -O`, targeting `arm64-apple-macosx15.0`.
Production builds must not define `TESTING`. Arguments are:

```
image-control-match reference.png reference_sha256 current.png current_sha256 x y width height dx dy
```

Both paths must be absolute, owned plain single-link files beneath plain directory
components. Hashes are lowercase SHA-256. The reference rectangle is 4 to 128
pixels wide and high. The relative point must lie inside it. Output contains only
fixed schema/claim fields, hashes and geometry; failures contain fixed codes.
Core Graphics converts both images to the same sRGB premultiplied RGBA buffer.
Exact comparison refers to those decoded buffers, not original PNG byte identity.

`image_correspondence.point_for_frame` validates the closed result and converts the
fresh matched point through the caller's actual screenshot scale. The caller must
validate PNG structure, bind its capture to the current process/window/provider,
establish the scene and permitted effect, enforce freshness and original operation
deadlines, and recheck owner/cancellation immediately before one-use dispatch.
Image correspondence alone supplies none of that authority. Game assets and
reference screenshots remain private; tests use original synthetic PNGs.
