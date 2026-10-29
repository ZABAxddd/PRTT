"""Helpers for keeping Tk windows within the visible area and centered on their owner."""

import re


def _read_int(widget, method, fallback):
    try:
        value = getattr(widget, method)()
    except (AttributeError, TypeError, ValueError):
        return fallback
    return value if isinstance(value, int) else fallback


def _outer_position(widget, fallback_x, fallback_y):
    try:
        geometry = widget.geometry()
    except (AttributeError, TypeError):
        return fallback_x, fallback_y
    if not isinstance(geometry, str):
        return fallback_x, fallback_y
    match = re.search(r"([+-]\d+)([+-]\d+)$", geometry)
    if match is None:
        return fallback_x, fallback_y
    x, y = int(match.group(1)), int(match.group(2))
    if x < 0 or y < 0:
        return fallback_x, fallback_y
    return x, y


def fit_window(window, width=None, height=None, *, parent=None, min_size=(1, 1),
               margin=(12, 36)):
    """Clamp a window to the available display area and center it on its parent."""
    window.update_idletasks()
    if parent is not None:
        parent.update_idletasks()

    screen_width = _read_int(window, "winfo_screenwidth", 1280)
    screen_height = _read_int(window, "winfo_screenheight", 720)
    margin_x, margin_y = margin
    max_width = max(1, screen_width - 2 * margin_x)
    max_height = max(1, screen_height - 2 * margin_y)

    if parent is not None:
        parent_width = _read_int(parent, "winfo_width", screen_width)
        parent_height = _read_int(parent, "winfo_height", screen_height)
        if parent_width > 1:
            max_width = min(max_width, max(1, parent_width - 2 * margin_x))
        if parent_height > 1:
            max_height = min(max_height, max(1, parent_height - 2 * margin_y))

    requested_width = width if width is not None else _read_int(
        window, "winfo_reqwidth", min_size[0])
    requested_height = height if height is not None else _read_int(
        window, "winfo_reqheight", min_size[1])
    min_width = min(min_size[0], max_width)
    min_height = min(min_size[1], max_height)
    width = max(min_width, min(int(requested_width), max_width))
    height = max(min_height, min(int(requested_height), max_height))

    if parent is not None:
        base_x = _read_int(parent, "winfo_rootx", 0)
        base_y = _read_int(parent, "winfo_rooty", 0)
        center_width = _read_int(parent, "winfo_width", screen_width)
        center_height = _read_int(parent, "winfo_height", screen_height)
        base_x, base_y = _outer_position(parent, base_x, base_y)
    else:
        base_x = _read_int(window, "winfo_vrootx", 0)
        base_y = _read_int(window, "winfo_vrooty", 0)
        center_width = screen_width
        center_height = screen_height

    x = base_x + (center_width - width) // 2
    y = base_y + (center_height - height) // 2
    virtual_x = _read_int(window, "winfo_vrootx", 0)
    virtual_y = _read_int(window, "winfo_vrooty", 0)
    virtual_width = _read_int(window, "winfo_vrootwidth", screen_width)
    virtual_height = _read_int(window, "winfo_vrootheight", screen_height)
    x = min(max(x, virtual_x), virtual_x + max(0, virtual_width - width))
    y = min(max(y, virtual_y), virtual_y + max(0, virtual_height - height))

    window.geometry(f"{width}x{height}+{x}+{y}")
    return width, height
