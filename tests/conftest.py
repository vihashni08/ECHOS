import os

os.environ.setdefault("MPLBACKEND", "Agg")  # report charts are drawn from a background thread: never use a GUI backend
