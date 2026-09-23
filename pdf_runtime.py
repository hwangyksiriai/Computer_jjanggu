"""One process-wide guard for every MuPDF document, page and pixmap operation.

MuPDF callers in indexing, thumbnails and both preview windows must share this
guard. Keep OCR subprocesses and Tk updates outside it. Document context managers
close files before releasing the guard; only detached Pillow images may escape.
"""
import threading


PDF_LOCK = threading.RLock()
