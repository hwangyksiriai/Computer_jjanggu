"""Native coordinate round-trip on every connected display; no file operations."""
import tkinter as tk
from monitors import DesktopSpace, enable_dpi_awareness, fit_position

enable_dpi_awareness()
root=tk.Tk(); root.withdraw()
window=tk.Toplevel(root); window.overrideredirect(True)
window.geometry('270x294'); window.attributes('-alpha',0.0)
window.update_idletasks()
space=DesktopSpace(window)
screens=space.screens()
try:
    for screen in screens:
        x,y=fit_position(screen[0]+80,screen[1]+80,270,294,[screen])
        space.move(x,y); root.update()
        actual=space.position()
        assert actual==(x,y),(screen,(x,y),actual)
        print('Native monitor round-trip OK:',screen,actual)
    print('Connected displays:',len(screens))
finally:
    root.destroy()
