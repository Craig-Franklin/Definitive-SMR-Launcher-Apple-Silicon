"""Clickable and keyboard reachable HTTP(S) links in read-only native text."""

import re
import webbrowser

from .map_metadata import safe_source_url


def url_ranges(content):
    for match in re.finditer(r"https?://[^\s<>\"']+", content, re.IGNORECASE):
        url = match.group().rstrip(".,;:!?")
        for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        if safe_source_url(url):
            yield match.start(), match.start() + len(url), url


def render_linked_text(widget, content, *, opener=None):
    """Replace text, with links working even while the widget is disabled."""
    opener = opener or webbrowser.open
    widget.configure(state="normal", takefocus=True)
    for tag in getattr(widget, "_url_tags", ()):
        widget.tag_delete(tag)
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    ranges = tuple(url_ranges(content))
    widget._url_tags = tuple(f"url_{i}" for i in range(len(ranges)))
    cursor = widget.cget("cursor")
    red, green, blue = widget.winfo_rgb(widget.cget("background"))
    color = "#1557b0" if red + green + blue > 98302 else "#8cbcff"
    for tag, (start, end, url) in zip(widget._url_tags, ranges):
        widget.tag_add(tag, f"1.0 + {start} chars", f"1.0 + {end} chars")
        widget.tag_configure(tag, foreground=color, underline=True)
        widget.tag_bind(tag, "<Button-1>", lambda _, target=url: (opener(target), "break")[1])
        widget.tag_bind(tag, "<Enter>", lambda _: widget.configure(cursor="pointinghand"))
        widget.tag_bind(tag, "<Leave>", lambda _: widget.configure(cursor=cursor))
    position = [-1]

    def cycle(event):
        reverse = event.keysym == "ISO_Left_Tab" or bool(event.state & 1)
        next_index = position[0] + (-1 if reverse else 1)
        if reverse and position[0] == -1:
            next_index = len(ranges) - 1
        if not 0 <= next_index < len(ranges):
            position[0] = -1
            target = widget.tk.call("tk_focusPrev" if reverse else "tk_focusNext", widget._w)
            if target:
                widget.tk.call("focus", target)
            return "break"  # Text's class binding inserts tabs instead of traversing.
        position[0] = next_index
        start, end, _ = ranges[next_index]
        widget.tag_remove("sel", "1.0", "end")
        widget.tag_add("sel", f"1.0 + {start} chars", f"1.0 + {end} chars")
        widget.see(f"1.0 + {start} chars")
        return "break"

    def activate(_):
        if 0 <= position[0] < len(ranges):
            opener(ranges[position[0]][2])
            return "break"

    widget.bind("<Tab>", cycle)
    widget.bind("<Shift-Tab>", cycle)
    widget.bind("<ISO_Left_Tab>", cycle)
    widget.bind("<Return>", activate)
    widget.bind("<FocusOut>", lambda _: position.__setitem__(0, -1))
    widget.configure(state="disabled")
