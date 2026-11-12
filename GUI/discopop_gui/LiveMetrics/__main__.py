# This file is part of the DiscoPoP software (http://www.discopop.tu-darmstadt.de)
#
# Copyright (c) 2020, Technische Universitaet Darmstadt, Germany
#
# This software may be modified and distributed under the terms of
# the 3-Clause BSD License.  See the LICENSE file in the package base
# directory for details.

import argparse
import tkinter as tk

from discopop_gui.LiveMetrics.LiveMetricsFrame import LiveMetricsFrame


def main() -> None:
    parser = argparse.ArgumentParser(description="Live view of the DiscoPoP profiler metrics in /dev/shm.")
    parser.add_argument(
        "--discopop-dir",
        help="directory with FileMapping.txt and profiler/Data.xml, "
        "default: DOT_DISCOPOP or <cwd>/.discopop of the profiled process",
    )
    args = parser.parse_args()
    root = tk.Tk()
    root.title("DiscoPoP live")
    root.geometry("1400x900")
    LiveMetricsFrame(root, args.discopop_dir).pack(fill="both", expand=True)
    root.mainloop()


if __name__ == "__main__":
    main()
