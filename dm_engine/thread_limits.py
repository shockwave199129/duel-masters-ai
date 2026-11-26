"""Cap CPU threads per process so self-play and training leave the desktop usable.

Import this before anything that imports torch. It only sets environment
variables, so it stays cheap, and worker processes inherit them. Values already
set in the environment win.
"""

import os

DEFAULT_THREADS = "2"

for _name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, DEFAULT_THREADS)
