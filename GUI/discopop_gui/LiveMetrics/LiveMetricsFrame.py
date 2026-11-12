# This file is part of the DiscoPoP software (http://www.discopop.tu-darmstadt.de)
#
# Copyright (c) 2020, Technische Universitaet Darmstadt, Germany
#
# This software may be modified and distributed under the terms of
# the 3-Clause BSD License.  See the LICENSE file in the package base
# directory for details.

from __future__ import annotations

import os
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Any, Callable

from discopop_gui.LiveMetrics.SharedMetrics import (
    HISTORY,
    SHM_PATH,
    Header,
    Names,
    Sample,
    SharedMetrics,
    busy_fractions,
    dep_rates,
    is_set,
    log,
    rates,
)

BG = "#101215"
PANEL = "#171a1f"
GRID = "#2b3038"
SELECT = "#262c36"
FG = "#c8ccd4"
DIM = "#4f5662"
TITLE = "#56b6c2"
GREEN = "#62c462"
RED = "#e06c75"
YELLOW = "#e5c07b"
FIRST = "#61afef"
SECOND = "#c678dd"
FILL = {FIRST: "#1d3347", SECOND: "#36274a"}
BUSY_WINDOW_S = 2.0
TOP = 4


def fmt(value: float) -> str:
    for unit, size in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(value) >= size:
            return f"{value / size:.1f}{unit}"
    return f"{value:.1f}" if abs(value) < 10 else f"{value:.0f}"


def last(values: list[float]) -> str:
    return fmt(values[-1]) if values else "-"


def sparkline(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    values: list[float],
    colour: str,
    fill: str | None = None,
) -> None:
    values = values[-HISTORY:]
    if len(values) < 2 or x1 <= x0:
        return
    top = max(values)
    scale = (y1 - y0) / top if top > 0 else 0.0
    step = (x1 - x0) / (HISTORY - 1)
    points: list[float] = []
    for i, value in enumerate(values):
        points += [x1 - (len(values) - 1 - i) * step, y1 - max(value, 0.0) * scale]
    if fill is not None:
        canvas.create_polygon(points + [points[-2], y1, points[0], y1], fill=fill, outline="")
    canvas.create_line(points, fill=colour)


@dataclass
class Row:
    parts: list[tuple[str, str]]
    key: str | None = None
    meter: float | None = None
    spark: list[float] | None = None
    colour: str = FG


class _RowList(tk.Frame):
    def __init__(self, parent: tk.Misc, font: tkfont.Font, changed: Callable[[], None]) -> None:
        super().__init__(parent, bg=BG)
        self.open: set[str] = set()
        self._font = font
        self._lh = font.metrics("linespace") + 4
        self._changed = changed
        self._keys: list[str | None] = []
        self._selected: str | None = None
        self._height = 0
        self.canvas = tk.Canvas(
            self, bg=BG, highlightthickness=1, highlightbackground=BG, highlightcolor=GRID, takefocus=1
        )
        bar = tk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            bg=PANEL,
            troughcolor=BG,
            activebackground=GRID,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Key>", self._key)
        self.canvas.bind("<Button-4>", lambda _: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _: self.canvas.yview_scroll(3, "units"))

    def show(self, rows: list[Row]) -> None:
        c = self.canvas
        c.delete("all")
        width = max(c.winfo_width(), 100)
        self._keys = [row.key for row in rows]
        if self._selected is None or self._selected not in self._keys:
            self._selected = next((key for key in self._keys if key is not None), None)
        y = TOP
        for row in rows:
            x = 6.0
            if row.key is not None:
                if row.key == self._selected:
                    c.create_rectangle(0, y, width, y + self._lh, fill=SELECT, outline="")
                x = self._text(x, y, "▾ " if row.key in self.open else "▸ ", TITLE)
            for text, colour in row.parts:
                x = self._text(x, y, text, colour)
            if row.meter is not None and width - 10 > x + 20:
                c.create_rectangle(x + 10, y + 3, width - 10, y + self._lh - 3, outline=GRID)
                share = min(max(row.meter, 0.0), 1.0)
                c.create_rectangle(
                    x + 10, y + 3, x + 10 + (width - 20 - x) * share, y + self._lh - 3, fill=row.colour, outline=""
                )
            if row.spark is not None:
                sparkline(c, x + 10, y + 3, min(x + 170, width - 10), y + self._lh - 3, row.spark, row.colour)
            y += self._lh
        self._height = y + TOP
        c.configure(scrollregion=(0, 0, width, self._height))

    def _text(self, x: float, y: float, text: str, colour: str) -> float:
        self.canvas.create_text(x, y + 2, text=text, fill=colour, font=self._font, anchor="nw")
        return x + self._font.measure(text)

    def _click(self, event: tk.Event[tk.Canvas]) -> None:
        self.canvas.focus_set()
        index = int((self.canvas.canvasy(event.y) - TOP) // self._lh)
        key = self._keys[index] if 0 <= index < len(self._keys) else None
        if key is not None:
            self._selected = key
            self.open ^= {key}
            self._changed()

    def _key(self, event: tk.Event[tk.Canvas]) -> None:
        keys = [key for key in self._keys if key is not None]
        if self._selected is None or self._selected not in keys:
            return
        position = keys.index(self._selected)
        if event.keysym == "Up":
            self._selected = keys[max(position - 1, 0)]
        elif event.keysym == "Down":
            self._selected = keys[min(position + 1, len(keys) - 1)]
        elif event.keysym == "Right":
            self.open.add(self._selected)
        elif event.keysym == "Left":
            self.open.discard(self._selected)
        elif event.keysym in ("Return", "KP_Enter", "space"):
            self.open ^= {self._selected}
        else:
            return
        self._changed()
        self._see()

    def _see(self) -> None:
        if self._selected not in self._keys or self._height <= 0:
            return
        y = TOP + self._keys.index(self._selected) * self._lh
        view = self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        if y < top or y + self._lh > top + view:
            self.canvas.yview_moveto(max(y - view / 2, 0.0) / self._height)


class LiveMetricsFrame(tk.Frame):
    def __init__(self, parent: tk.Misc, discopop_dir: str | None = None, **kwargs: Any) -> None:
        super().__init__(parent, bg=BG, **kwargs)
        self._metrics = SharedMetrics(discopop_dir)
        self._discopop_dir = discopop_dir
        self._redraw_s = 1.0
        self._after: str | None = None
        self._toggle_error = ""
        self._font = tkfont.nametofont("TkFixedFont").copy()
        self._font.configure(size=10)
        self._bold = self._font.copy()
        self._bold.configure(weight="bold")
        self._big = self._font.copy()
        self._big.configure(size=14)
        self._lh = self._font.metrics("linespace") + 4

        for column in range(3):
            self.grid_columnconfigure(column, weight=1, uniform="column")
        self.grid_rowconfigure(2, weight=1)

        header = tk.Frame(self, bg=BG)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=8, pady=(6, 0))
        self._label(header, "DiscoPoP live", TITLE, self._bold)
        self._uptime = self._label(header, "up -", FG)
        self._count = self._label(header, "0 samples", FG)
        self._label(header, "  poll", DIM)
        self._spinbox(header, self._metrics.poll_interval, 0.001, 0.01, 3, self._set_poll)
        self._label(header, "s  redraw", DIM)
        self._spinbox(header, self._redraw_s, 0.01, 0.1, 2, self._set_redraw)
        self._label(header, "s", DIM)
        self._flag_light = self._light(header)
        self._label(header, "dep profiling", FG)
        self._toggle_button = tk.Button(
            header,
            text="-",
            width=3,
            command=self._toggle,
            bg=PANEL,
            fg=FG,
            activebackground=GRID,
            activeforeground=FG,
            disabledforeground=DIM,
            relief="flat",
            highlightthickness=0,
            font=self._font,
        )
        self._toggle_button.pack(side="left", padx=4)
        self._run_light = self._light(header)
        self._run_text = self._label(header, "", FG)

        self._note = tk.Label(self, anchor="w", bg=BG, fg=FG, font=self._font)
        self._note.grid(row=1, column=0, columnspan=3, sticky="ew", padx=8, pady=(2, 4))
        self._left = _RowList(self, self._font, self._render)
        self._middle = _RowList(self, self._font, self._render)
        self._right = _RowList(self, self._font, self._render)
        for column, rows in enumerate((self._left, self._middle, self._right)):
            rows.grid(row=2, column=column, sticky="nsew", padx=4)
        self._graphs = tk.Canvas(self, bg=BG, height=190, highlightthickness=0)
        self._graphs.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4, pady=4)
        self._waiting = tk.Label(self, bg=BG, fg=FG, font=self._big, justify="center")
        self._waiting.grid(row=2, column=0, rowspan=2, columnspan=3, sticky="nsew")

        self.bind("<Destroy>", self._on_destroy)
        self._metrics.start()
        self._tick()

    def _label(self, parent: tk.Frame, text: str, colour: str, font: tkfont.Font | None = None) -> tk.Label:
        label = tk.Label(parent, text=text, bg=BG, fg=colour, font=font or self._font)
        label.pack(side="left", padx=4)
        return label

    def _light(self, parent: tk.Frame) -> tk.Canvas:
        light = tk.Canvas(parent, width=14, height=14, bg=BG, highlightthickness=0)
        light.create_oval(2, 2, 12, 12, fill=DIM, outline="", tags="light")
        light.pack(side="left", padx=(16, 0))
        return light

    def _spinbox(
        self, parent: tk.Frame, value: float, minimum: float, step: float, digits: int, apply: Callable[[float], None]
    ) -> None:
        box = tk.Spinbox(
            parent,
            from_=minimum,
            to=10.0,
            increment=step,
            format=f"%.{digits}f",
            width=digits + 3,
            bg=PANEL,
            fg=FG,
            buttonbackground=PANEL,
            insertbackground=FG,
            relief="flat",
            highlightthickness=0,
            font=self._font,
        )
        box.delete(0, "end")
        box.insert(0, f"{value:.{digits}f}")

        def commit(_: object = None) -> None:
            nonlocal value
            try:
                value = max(float(box.get()), minimum)
            except ValueError:
                pass
            box.delete(0, "end")
            box.insert(0, f"{value:.{digits}f}")
            apply(value)

        box.configure(command=commit)
        box.bind("<Return>", commit)
        box.bind("<FocusOut>", commit)
        box.pack(side="left")

    def _set_poll(self, seconds: float) -> None:
        self._metrics.poll_interval = seconds

    def _set_redraw(self, seconds: float) -> None:
        self._redraw_s = seconds

    def _toggle(self) -> None:
        try:
            self._metrics.toggle_profiling()
            self._toggle_error = ""
        except (OSError, RuntimeError) as error:
            self._toggle_error = f"toggle failed: {error}"
            log(self._toggle_error)
        self._render()

    def _on_destroy(self, event: tk.Event[tk.Misc]) -> None:
        if event.widget is self:
            if self._after is not None:
                self.after_cancel(self._after)
            self._metrics.stop()

    def _tick(self) -> None:
        self._after = self.after(max(int(self._redraw_s * 1000), 10), self._tick)
        self._render()

    def _render(self) -> None:
        m = self._metrics
        with m.lock:
            samples = list(m.history)
            state, message, end_reason = m.state, m.message, m.end_reason
            ready_time, end_time, count, names = m.ready_time, m.end_time, m.samples, m.names
            pid, command, names_dir = m.pid, m.command, m.names_dir

        if ready_time is not None:
            up = int((end_time if end_time is not None else time.time()) - ready_time)
            self._uptime.configure(text=f"up {up // 3600}:{up // 60 % 60:02d}:{up % 60:02d}")
        else:
            self._uptime.configure(text="up -")
        self._count.configure(text=f"{count} samples")
        if samples:
            on = bool(samples[-1].header.control_flags & 1)
            flag_colour = GREEN if on else RED
            self._flag_light.itemconfigure("light", fill=flag_colour)
            self._toggle_button.configure(text="on" if on else "off", fg=flag_colour, activeforeground=flag_colour)
        else:
            self._flag_light.itemconfigure("light", fill=DIM)
            self._toggle_button.configure(text="-")
        if state == "active" and pid is not None:
            run_colour, run_text = GREEN, f"run active, pid {pid}"
        elif state == "active":
            run_colour, run_text = RED, "no process maps the segment"
        elif state == "finished":
            run_colour, run_text = RED, "run finished"
        else:
            run_colour, run_text = DIM, "no run"
        self._run_light.itemconfigure("light", fill=run_colour)
        self._run_text.configure(text=run_text)
        self._toggle_button.configure(state="normal" if state == "active" else "disabled")

        if not samples:
            text = message or f"waiting for {SHM_PATH}"
            if self._discopop_dir:
                source = os.path.abspath(self._discopop_dir)
            else:
                source = "DOT_DISCOPOP or <cwd>/.discopop of the profiled process"
            self._waiting.configure(text=f"{text}\n\nnames from {source}")
            self._waiting.grid()
            self._waiting.lift()
            self._note.configure(text=self._toggle_error, fg=RED)
            return
        self._waiting.grid_remove()

        h = samples[-1].header
        if state == "finished" and end_time is not None:
            when = time.strftime("%H:%M:%S", time.localtime(end_time))
            note, colour = f"run finished {when} ({end_reason}), last state kept", YELLOW
        else:
            if names_dir is None:
                source = "no names: no process maps the segment, see --discopop-dir"
            else:
                loops = "Data.xml missing" if names.loops is None else f"{names.loops} loops"
                functions = f"{len(names.functions)} functions"
                source = f"names from {names_dir}: {len(names.files)} files, {functions}, {loops}"
            workers = f"{h.num_first_queue_workers} first / {h.num_second_queue_workers} second queue workers"
            note, colour = f"{command or SHM_PATH}   {workers}   {source}", FG
        if self._toggle_error:
            note, colour = f"{self._toggle_error}   {note}", RED
        self._note.configure(text=note, fg=colour)

        first, second = dep_rates(samples, True), dep_rates(samples, False)
        self._left.show(self._left_rows(samples, first, second))
        self._middle.show(self._middle_rows(samples, names, first, second))
        self._right.show(self._right_rows(samples[-1], names))
        self._draw_graphs(samples, first, second)

    def _left_rows(self, samples: list[Sample], first: list[list[float]], second: list[list[float]]) -> list[Row]:
        latest = samples[-1]
        before = samples[-2].header if len(samples) > 1 else None
        h = latest.header
        window = min(BUSY_WINDOW_S, latest.t - samples[0].t)
        usage = {pool: busy_fractions(samples, pool, window) for pool in (True, False)}
        rows = [Row([("QUEUES", TITLE), (f"   chunks waiting, worker usage over the last {window:.1f}s", DIM)])]
        for name, is_first, size, previous in (
            ("FAQ", True, h.first_queue_size, before.first_queue_size if before else None),
            ("SAQ", False, h.second_queue_size, before.second_queue_size if before else None),
        ):
            arrow = (" ", DIM)
            if previous is not None:
                arrow = ("↑", RED) if size > previous else ("↓", GREEN) if size < previous else ("=", DIM)
            pool = usage[is_first]
            plural = "s" if len(pool) != 1 else ""
            workers = (
                f"{len(pool)} worker{plural}, avg usage {100 * sum(pool) / len(pool):3.0f}%" if pool else "no workers"
            )
            rows.append(Row([(f" {name} {size:>12} ", FG), arrow, (f"   {workers}", FG)]))
        rows.append(Row([]))
        rows.append(Row([("WORKERS", TITLE), ("   usage = share of polls with the busy bit set", DIM)]))
        for prefix, is_first, series, colour in (("F", True, first, FIRST), ("S", False, second, SECOND)):
            for i, deps in enumerate(latest.deps(is_first)):
                key = f"{prefix}{i}"
                busy_now = is_set(latest.status(is_first), i)
                dot = ("●", GREEN) if busy_now else ("○", DIM)
                text = f"  deps {deps:>12} {last(series[i]):>8} dep/s   usage {100 * usage[is_first][i]:3.0f}%"
                rows.append(Row([(f"{key:<4}", colour), dot, (text, FG)], key=key))
                if key in self._left.open:
                    rows.append(Row([("         dep/s history", DIM)], spark=series[i], colour=colour))
        return rows

    def _middle_rows(
        self, samples: list[Sample], names: Names, first: list[list[float]], second: list[list[float]]
    ) -> list[Row]:
        h = samples[-1].header
        rows = [Row([("PROGRESS", TITLE)])]
        if h.total_bb_count:
            share = h.distinct_bb_count / h.total_bb_count
            text = f" basic blocks {h.distinct_bb_count}/{h.total_bb_count} {100 * share:5.1f}%"
            rows.append(Row([(text, FG)], meter=share, colour=GREEN))
        else:
            rows.append(Row([(f" basic blocks {h.distinct_bb_count}, total_bb_count is 0", FG)]))
        if names.loops:
            share = h.distinct_loop_count / names.loops
            text = f" loops        {h.distinct_loop_count}/{names.loops} {100 * share:5.1f}%"
            rows.append(Row([(text, FG)], meter=share, colour=GREEN))
        else:
            missing = "Data.xml not read" if names.loops is None else "Data.xml lists no loops"
            rows.append(Row([(f" loops        {h.distinct_loop_count}, {missing}", FG)]))
        rows.append(Row([]))
        rows.append(Row([("RATES", TITLE)]))
        for label, series in (
            ("deps/s (workers)", [sum(column) for column in zip(*(first + second))]),
            ("deps/s (unique)", rates(samples, "total_dependency_count")),
            ("bb visits/s", rates(samples, "bb_visit_count")),
            ("loop iterations/s", rates(samples, "loop_iteration_count")),
            ("func entries/s", rates(samples, "func_entry_count")),
        ):
            rows.append(Row([(f" {label:<20}{last(series):>10}", FG)]))
        rows.append(Row([]))
        rows.append(Row([("MetricsHeader", TITLE)]))
        for name, value in zip(Header._fields, h):
            text = f"0x{value:x}" if name == "control_flags" else str(value)
            rows.append(Row([(f" {name:<26}{text:>18}", FG)]))
        return rows

    def _right_rows(self, latest: Sample, names: Names) -> list[Row]:
        h = latest.header
        rows: list[Row] = []
        for title, key, depth, stack, loop in (
            ("FUNCTION STACK", "functions", h.func_stack_depth, latest.function_stack, False),
            ("LOOP STACK", "loops", h.loop_stack_depth, latest.loop_stack, True),
        ):
            used = min(depth, len(stack))
            cut = depth > len(stack)
            top = self._frame(names, stack[used - 1], loop, cut) if used else "-"
            rows.append(Row([(f"{title:<15}", TITLE), (f"depth {depth:<5}top ", FG), (top, FG)], key=key))
            if key in self._right.open:
                for i, word in enumerate(stack):
                    if i < used:
                        frame = self._frame(names, word, loop, cut and i == used - 1)
                        rows.append(Row([(f"   {i:>3}  ", DIM), (frame, FG)]))
                    else:
                        rows.append(Row([(f"   {i:>3}  -", DIM)]))
            rows.append(Row([]))
        return rows

    @staticmethod
    def _frame(names: Names, word: int, loop: bool, cut: bool) -> str:
        text = names.frame(word) + ("+" if cut else "")
        return f"{text} it={word >> 32}" if loop else text

    def _draw_graphs(self, samples: list[Sample], first: list[list[float]], second: list[list[float]]) -> None:
        c = self._graphs
        c.delete("all")
        width, height = c.winfo_width(), c.winfo_height()
        unit = width / 6
        if unit < 40 or height < 4 * self._lh:
            return
        y0, y1 = 2.0, height - 2.0
        faq = [float(s.header.first_queue_size) for s in samples]
        saq = [float(s.header.second_queue_size) for s in samples]
        self._spark_panel(0, unit, y0, y1, "FAQ size", faq, FIRST, True)
        self._spark_panel(unit, 2 * unit, y0, y1, "SAQ size", saq, SECOND, True)
        self._bar_panel(2 * unit, 4 * unit, y0, y1, first, second)
        self._spark_panel(4 * unit, 5 * unit, y0, y1, "bb visits/s", rates(samples, "bb_visit_count"), GREEN, False)
        loop_rates = rates(samples, "loop_iteration_count")
        self._spark_panel(5 * unit, width, y0, y1, "loop iterations/s", loop_rates, YELLOW, False)

    def _spark_panel(
        self, x0: float, x1: float, y0: float, y1: float, title: str, values: list[float], colour: str, filled: bool
    ) -> None:
        c = self._graphs
        c.create_rectangle(x0 + 3, y0, x1 - 3, y1, fill=PANEL, outline=GRID)
        c.create_text(x0 + 9, y0 + 4, anchor="nw", text=title, fill=colour, font=self._font)
        top = fmt(max(values)) if values else "-"
        c.create_text(
            x0 + 9, y0 + 4 + self._lh, anchor="nw", text=f"{last(values)}  max {top}", fill=FG, font=self._font
        )
        fill = FILL[colour] if filled else None
        sparkline(c, x0 + 9, y0 + 2 * self._lh + 10, x1 - 9, y1 - 5, values, colour, fill)

    def _bar_panel(
        self, x0: float, x1: float, y0: float, y1: float, first: list[list[float]], second: list[list[float]]
    ) -> None:
        c = self._graphs
        c.create_rectangle(x0 + 3, y0, x1 - 3, y1, fill=PANEL, outline=GRID)
        scale = max((max(series) for series in first + second if series), default=0.0)
        c.create_text(x0 + 9, y0 + 4, anchor="nw", text="deps/s per worker", fill=FG, font=self._font)
        scale_text = f"full bar {fmt(scale)}, max of the last {HISTORY} polls"
        c.create_text(x0 + 9, y0 + 4 + self._lh, anchor="nw", text=scale_text, fill=DIM, font=self._font)
        top = y0 + 2 * self._lh + 10
        half = (x1 - x0) / 2
        for column, (prefix, workers, colour) in enumerate((("F", first, FIRST), ("S", second, SECOND))):
            if not workers:
                continue
            row = min(float(self._lh), (y1 - 5 - top) / len(workers))
            labelled = row >= self._lh - 4
            left = x0 + column * half + 9
            bar0 = left + (self._font.measure("F000 ") if labelled else 0)
            bar1 = x0 + (column + 1) * half - 9
            for i, series in enumerate(workers):
                y = top + i * row
                c.create_rectangle(bar0, y + 1, bar1, y + row - 1, outline=GRID)
                if series and scale > 0:
                    end = bar0 + (bar1 - bar0) * max(series[-1], 0.0) / scale
                    c.create_rectangle(bar0, y + 1, end, y + row - 1, fill=colour, outline="")
                if labelled:
                    c.create_text(left, y, anchor="nw", text=f"{prefix}{i}", fill=colour, font=self._font)
                    c.create_text(bar1 - 4, y, anchor="ne", text=last(series), fill=FG, font=self._font)
