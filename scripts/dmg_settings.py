"""Finder layout for the drag-to-Applications download (read by dmgbuild)."""
from pathlib import Path

application = Path(defines["app"]).resolve(strict=True)
if application.name != "Definitive SMR Launcher Apple Silicon.app":
    raise ValueError("Unexpected app bundle")
format = "UDZO"
files = [str(application)]
symlinks = {"Applications": "/Applications"}
icon = str(application / "Contents/Resources/AppIcon.icns")
background = "builtin-arrow"
window_rect = ((180, 160), (640, 380))
icon_locations = {application.name: (140, 120), "Applications": (500, 120)}
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
arrange_by = None
icon_size = 96
text_size = 12
