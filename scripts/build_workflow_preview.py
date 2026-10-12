#!/usr/bin/env python3
"""Build, never launch, an isolated synthetic workflow app for root's visible lane.

Uses production views/model with one private recorder registration replacement.
The app entrypoint injects the closed temporary provider, never SMRLauncher startup.
--before-editor uses the first native editor from #225 for an honest comparison.
"""

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "5c1185bacb50bd34aa8f53a822ad303b84da8101"
FIRST_EDITOR = "5bcc6c9a7037f70a49aa52a9cfc9f919857910ca"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/workflow-preview")
    parser.add_argument("--before-editor", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local"):
        parser.error("Preview output must stay in this worktree's ignored .local directory")
    output.mkdir(parents=True, exist_ok=True)
    view = ROOT / "src/native/LauncherView.swift"
    source = (
        subprocess.check_output(
            ["git", "show", FIRST_EDITOR + ":src/native/LauncherView.swift"], cwd=ROOT
        ).decode()
        if args.before_editor
        else view.read_text()
    )
    if not args.before_editor:
        start = source.index("    private func recorder(")
        end = source.index("    private func history(", start)
        source = (
            source[:start]
            + "    private func recorder(_ map: MapItem) -> some View { NativeOutcomeView(model: model, map: map) }\n\n"
            + source[end:]
        )
        if "NativePersonalImageDrop" not in source:
            for artwork in (
                "artwork(map).frame(height: 155)",
                "artwork(map, draft: model.personal[map.id]?.imagePNG).frame(width: 88, height: 72)",
            ):
                if source.count(artwork) != 1:
                    parser.error(
                        "The private drop registration no longer matches LauncherView; review the source"
                    )
                source = source.replace(
                    artwork, artwork + ".modifier(NativePersonalImageDrop(model: model, map: map))"
                )
    # Standardize only the synthetic picker start directory. Both baseline and
    # final use the generated assets; no personal folder needs to appear in QA.
    picker = 'let panel = NSOpenPanel(); panel.title = "Choose a local map image"; panel.allowedContentTypes = [.image]'
    if source.count(picker) != 1:
        parser.error("The synthetic picker registration no longer matches LauncherView")
    source = source.replace(
        picker,
        picker
        + '; panel.directoryURL = URL(fileURLWithPath: model.tools.text("fixture_assets"), isDirectory: true)',
    )
    registered = output / "LauncherView.swift"
    registered.write_text(source)
    bundle = output / "SMR Workflow Acceptance.app"
    contents = bundle / "Contents"
    executable = contents / "MacOS/SMRWorkflowPreview"
    executable.parent.mkdir(parents=True, exist_ok=True)
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump(
            dict(
                CFBundleIdentifier="com.craigfranklin.smrlauncher.synthetic-workflow-preview",
                CFBundleName="SMR Synthetic Workflow",
                CFBundleExecutable=executable.name,
                CFBundlePackageType="APPL",
                NSHighResolutionCapable=True,
                LSMinimumSystemVersion="13.0",
            ),
            file,
        )
    sources = sorted(
        p
        for p in (ROOT / "src/native").glob("*.swift")
        if p.name not in ("SMRLauncher.swift", "LauncherView.swift")
    ) + [registered, ROOT / "scripts/NativeWorkflowPreview.swift"]
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-parse-as-library",
            "-swift-version",
            "5",
            "-target",
            "arm64-apple-macosx13.0",
            "-O",
            *(str(p) for p in sources),
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        check=True,
    )
    baseline = output / "baseline"
    baseline.mkdir(exist_ok=True)
    archive = output / "baseline.tar"
    with archive.open("wb") as file:
        subprocess.run(
            ["git", "archive", BASELINE, "src/smr_launcher"], cwd=ROOT, stdout=file, check=True
        )
    with tarfile.open(archive) as file:
        file.extractall(baseline, filter="data")
    paths = sources + [
        ROOT / "scripts/workflow_fixture.py",
        ROOT / "scripts/preview_workflow_baseline.py",
        executable,
        contents / "Info.plist",
        baseline / "src/smr_launcher/history_ui.py",
        baseline / "src/smr_launcher/play_history.py",
    ]
    manifest = dict(
        source_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
        .decode()
        .strip(),
        baseline=BASELINE,
        first_editor=FIRST_EDITOR if args.before_editor else None,
        compiled_only=True,
        sha256={
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
        },
    )
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                bundle=str(bundle),
                baseline_source_root=str(baseline),
                manifest=str(output / "manifest.json"),
                launched=False,
            )
        )
    )


if __name__ == "__main__":
    main()
