import ui_layout


class WindowProbe:
    def __init__(self, screen=(1280, 720), size=(1280, 720), position=(0, 0), requested=(600, 400),
                 geometry_value=None):
        self.screen = screen
        self.size = size
        self.position = position
        self.requested = requested
        self.geometry_value = geometry_value

    def update_idletasks(self):
        pass

    def winfo_screenwidth(self):
        return self.screen[0]

    def winfo_screenheight(self):
        return self.screen[1]

    def winfo_vrootx(self):
        return 0

    def winfo_vrooty(self):
        return 0

    def winfo_vrootwidth(self):
        return self.screen[0]

    def winfo_vrootheight(self):
        return self.screen[1]

    def winfo_width(self):
        return self.size[0]

    def winfo_height(self):
        return self.size[1]

    def winfo_rootx(self):
        return self.position[0]

    def winfo_rooty(self):
        return self.position[1]

    def winfo_reqwidth(self):
        return self.requested[0]

    def winfo_reqheight(self):
        return self.requested[1]

    def geometry(self, value=None):
        if value is not None:
            self.geometry_value = value
        return self.geometry_value or (
            f"{self.size[0]}x{self.size[1]}+{self.position[0]}+{self.position[1]}"
        )


parent = WindowProbe(position=(100, 20))
child = WindowProbe()
assert ui_layout.fit_window(child, 1600, 900, parent=parent) == (1256, 648)
assert child.geometry_value == "1256x648+24+56"

decorated_parent = WindowProbe(position=(48, 71), geometry_value="1280x720+40+40")
decorated_child = WindowProbe()
assert ui_layout.fit_window(decorated_child, 1040, 580, parent=decorated_parent) == (1040, 580)
assert decorated_child.geometry_value == "1040x580+160+110"

small_parent = WindowProbe(screen=(800, 600), size=(640, 480))
small_child = WindowProbe(screen=(800, 600))
assert ui_layout.fit_window(
    small_child, 950, 700, parent=small_parent, min_size=(600, 400)
) == (616, 408)
assert small_child.geometry_value == "616x408+12+36"

natural_parent = WindowProbe()
natural_child = WindowProbe(requested=(720, 500))
assert ui_layout.fit_window(natural_child, parent=natural_parent) == (720, 500)
assert natural_child.geometry_value == "720x500+280+110"

print("OK")
