# This file is part of the DiscoPoP software (http://www.discopop.tu-darmstadt.de)
#
# Copyright (c) 2020, Technische Universitaet Darmstadt, Germany
#
# This software may be modified and distributed under the terms of
# the 3-Clause BSD License.  See the LICENSE file in the package base
# directory for details.

from __future__ import annotations

import mmap
import os
import re
import struct
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import NamedTuple

SHM_PATH = "/dev/shm/discopop_metrics"
FUNC_STACK_CAP = 64
LOOP_STACK_CAP = 64
COUNTER_STRIDE_WORDS = 8
WORD = 8
MAXLNO = 16384
HISTORY = 60


# keep in sync with MetricsHeader in profiler/rtlib/shared_metrics.hpp
class Header(NamedTuple):
    ready: int
    num_first_queue_workers: int
    num_second_queue_workers: int
    first_queue_size: int
    second_queue_size: int
    total_dependency_count: int
    control_flags: int
    func_entry_count: int
    func_exit_count: int
    func_stack_depth: int
    loop_entry_count: int
    loop_exit_count: int
    loop_iteration_count: int
    loop_stack_depth: int
    distinct_loop_count: int
    bb_visit_count: int
    distinct_bb_count: int
    total_bb_count: int


SCALARS = len(Header._fields)
HEADER_WORDS = SCALARS + FUNC_STACK_CAP + LOOP_STACK_CAP
HEADER_SIZE = HEADER_WORDS * WORD
CONTROL_FLAGS = Header._fields.index("control_flags")


def log(message: str) -> None:
    print(f"[discopop live] {message}", flush=True)


def status_words(workers: int) -> int:
    return (workers + 63) // 64


def is_set(words: tuple[int, ...], bit: int) -> bool:
    return bool((words[bit // 64] >> (bit % 64)) & 1)


@dataclass(frozen=True)
class Layout:
    num_first: int
    num_second: int
    first_status: int
    second_status: int
    first_deps: int
    second_deps: int
    words: int

    @staticmethod
    def of(num_first: int, num_second: int) -> Layout:
        first_status = HEADER_WORDS
        second_status = first_status + status_words(num_first)
        first_deps = second_status + status_words(num_second)
        second_deps = first_deps + num_first * COUNTER_STRIDE_WORDS
        words = second_deps + num_second * COUNTER_STRIDE_WORDS
        return Layout(num_first, num_second, first_status, second_status, first_deps, second_deps, words)


@dataclass(frozen=True)
class Sample:
    t: float
    header: Header
    function_stack: tuple[int, ...]
    loop_stack: tuple[int, ...]
    first_status: tuple[int, ...]
    second_status: tuple[int, ...]
    first_deps: tuple[int, ...]
    second_deps: tuple[int, ...]

    def status(self, first: bool) -> tuple[int, ...]:
        return self.first_status if first else self.second_status

    def deps(self, first: bool) -> tuple[int, ...]:
        return self.first_deps if first else self.second_deps


def parse(t: float, buf: bytes, layout: Layout) -> Sample:
    w = struct.unpack(f"={layout.words}Q", buf)
    return Sample(
        t,
        Header._make(w[:SCALARS]),
        w[SCALARS : SCALARS + FUNC_STACK_CAP],
        w[SCALARS + FUNC_STACK_CAP : HEADER_WORDS],
        w[layout.first_status : layout.second_status],
        w[layout.second_status : layout.first_deps],
        w[layout.first_deps : layout.second_deps : COUNTER_STRIDE_WORDS],
        w[layout.second_deps : layout.words : COUNTER_STRIDE_WORDS],
    )


def _pairs(samples: list[Sample]) -> list[tuple[Sample, Sample]]:
    return [(a, b) for a, b in zip(samples, samples[1:]) if b.t > a.t]


def rates(samples: list[Sample], field: str) -> list[float]:
    return [(getattr(b.header, field) - getattr(a.header, field)) / (b.t - a.t) for a, b in _pairs(samples)]


def dep_rates(samples: list[Sample], first: bool) -> list[list[float]]:
    pairs = _pairs(samples)
    workers = len(samples[-1].deps(first)) if samples else 0
    return [[(b.deps(first)[i] - a.deps(first)[i]) / (b.t - a.t) for a, b in pairs] for i in range(workers)]


def busy_fractions(samples: list[Sample], first: bool, window: float) -> list[float]:
    if not samples:
        return []
    recent = [s for s in samples if s.t >= samples[-1].t - window]
    workers = len(samples[-1].deps(first))
    return [sum(is_set(s.status(first), i) for s in recent) / len(recent) for i in range(workers)]


@dataclass
class Names:
    files: dict[int, str]
    functions: tuple[tuple[int, int, int, str], ...]
    loops: int | None

    def frame(self, word: int) -> str:
        lid = word & 0xFFFFFFFF
        file_id, line = lid >> 14, lid & (MAXLNO - 1)
        name, start = "", -1
        for function_file, first, last, function_name in self.functions:
            if function_file == file_id and first <= line <= last and first > start:
                name, start = function_name, first
        label = f"{self.files.get(file_id, file_id)}:{line}"
        return f"{label}({name})" if name else label


NODE = re.compile(r"<Node\b([^>]*)>")
ATTRIBUTE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
LINE_REF = re.compile(r"(\d+):(\d+)")


def load_names(discopop_dir: str) -> Names:
    files: dict[int, str] = {}
    path = os.path.join(discopop_dir, "FileMapping.txt")
    try:
        with open(path, errors="replace") as mapping:
            for entry in mapping:
                parts = entry.split(maxsplit=1)
                if len(parts) == 2 and parts[0].isdigit():
                    files[int(parts[0])] = os.path.basename(parts[1].strip())
        log(f"{path}: {len(files)} files")
    except OSError as error:
        log(f"no file names: {error}")

    path = os.path.join(discopop_dir, "profiler", "Data.xml")
    try:
        with open(path, errors="replace") as data:
            text = data.read()
    except OSError as error:
        log(f"no function names and no loop total: {error}")
        return Names(files, (), None)
    functions: list[tuple[int, int, int, str]] = []
    loop_ids: set[str] = set()
    for attributes in NODE.findall(text):
        node = dict(ATTRIBUTE.findall(attributes))
        if node.get("type") == "2":
            loop_ids.add(node.get("id", ""))
        elif node.get("type") == "1":
            start = LINE_REF.fullmatch(node.get("startsAtLine", ""))
            end = LINE_REF.fullmatch(node.get("endsAtLine", ""))
            if start and end:
                functions.append((int(start[1]), int(start[2]), int(end[2]), node.get("name", "")))
    log(f"{path}: {len(functions)} functions, {len(loop_ids)} loops")
    return Names(files, tuple(functions), len(loop_ids))


def find_process(ident: tuple[int, int]) -> int | None:
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/maps") as maps:
                for line in maps:
                    fields = line.split(maxsplit=5)
                    if len(fields) == 6 and fields[5].startswith(SHM_PATH) and int(fields[4]) == ident[1]:
                        major, minor = (int(part, 16) for part in fields[3].split(":"))
                        if os.makedev(major, minor) == ident[0]:
                            return int(entry)
        except (OSError, ValueError):
            continue
    return None


# the runtime uses DOT_DISCOPOP, else .discopop in its working directory
def describe_process(pid: int) -> tuple[str, str, float] | None:
    try:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        with open(f"/proc/{pid}/environ", "rb") as environ:
            variables = environ.read().split(b"\0")
        with open(f"/proc/{pid}/cmdline", "rb") as cmdline:
            command = cmdline.read().replace(b"\0", b" ").decode(errors="replace").strip()
        with open(f"/proc/{pid}/stat") as stat:
            start_ticks = int(stat.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as uptime:
            since_boot = float(uptime.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    prefix = b"DOT_DISCOPOP="
    dot_discopop = next(
        (variable[len(prefix) :].decode(errors="replace") for variable in variables if variable.startswith(prefix)),
        ".discopop",
    )
    started = time.time() - (since_boot - start_ticks / os.sysconf("SC_CLK_TCK"))
    return command, os.path.join(cwd, dot_discopop), started


class SharedMetrics:
    def __init__(self, discopop_dir: str | None = None, poll_interval: float = 0.1) -> None:
        self.discopop_dir = discopop_dir
        self.poll_interval = poll_interval
        self.lock = threading.Lock()
        self.history: deque[Sample] = deque(maxlen=HISTORY)
        self.state = "waiting"
        self.message = ""
        self.end_reason = ""
        self.samples = 0
        self.ready_time: float | None = None
        self.end_time: float | None = None
        self.names = Names({}, (), None)
        self.names_dir: str | None = None
        self.pid: int | None = None
        self.command = ""
        self._next_scan = 0.0
        self._fd: int | None = None
        self._ident: tuple[int, int] | None = None
        self._layout: Layout | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="discopop-live-metrics", daemon=True)

    def start(self) -> None:
        source = os.path.abspath(self.discopop_dir) if self.discopop_dir else "the profiled process"
        log(f"polling {SHM_PATH}, names from {source}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def toggle_profiling(self) -> int:
        with self.lock:
            if self._ident is None:
                raise RuntimeError("no active run, nothing toggled")
            fd = os.open(SHM_PATH, os.O_RDWR)
            try:
                st = os.fstat(fd)
                if (st.st_dev, st.st_ino) != self._ident:
                    raise RuntimeError(f"{SHM_PATH} belongs to another run now, nothing toggled")
                with mmap.mmap(fd, HEADER_SIZE) as mm, memoryview(mm) as raw, raw.cast("Q") as words:
                    old = int(words[CONTROL_FLAGS])
                    words[CONTROL_FLAGS] = old ^ 1
            finally:
                os.close(fd)
            log(f"control_flags 0x{old:x} -> 0x{old ^ 1:x}, dep profiling {'on' if (old ^ 1) & 1 else 'off'}")
            self._poll()
            return old ^ 1

    def _note(self, message: str) -> None:
        if message != self.message:
            self.message = message
            log(message)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    if self._fd is None:
                        self._attach()
                    else:
                        with self.lock:
                            self._poll()
                    self._track_process()
                except Exception as error:
                    message = f"error while reading {SHM_PATH}: {error!r}"
                    if message != self.message:
                        traceback.print_exc()
                    with self.lock:
                        self._detach(message)
                self._stop.wait(self.poll_interval)
        finally:
            with self.lock:
                if self._fd is not None:
                    os.close(self._fd)
                    self._fd = None

    def _check(self, fd: int, size: int) -> Layout | None:
        if size < HEADER_SIZE:
            self._note(f"{SHM_PATH} has {size} bytes, less than the {HEADER_SIZE} byte header, waiting")
            return None
        header = Header._make(struct.unpack(f"={SCALARS}Q", os.pread(fd, SCALARS * WORD, 0)))
        if header.ready != 1:
            self._note(f"{SHM_PATH} found, waiting for ready=1 (ready={header.ready})")
            return None
        layout = Layout.of(header.num_first_queue_workers, header.num_second_queue_workers)
        if size != layout.words * WORD:
            self._note(
                f"{SHM_PATH} has {size} bytes, but {layout.num_first} first / {layout.num_second} second queue "
                f"workers need {layout.words * WORD}: shared_metrics.hpp and SharedMetrics.py out of sync?"
            )
            return None
        return layout

    def _attach(self) -> None:
        try:
            fd = os.open(SHM_PATH, os.O_RDONLY)
        except FileNotFoundError:
            self._note(f"waiting for {SHM_PATH}")
            return
        published = False
        try:
            st = os.fstat(fd)
            layout = self._check(fd, st.st_size)
            if layout is None:
                return
            with self.lock:
                self._fd, self._ident, self._layout = fd, (st.st_dev, st.st_ino), layout
                published = True
                self.names = Names({}, (), None)
                self.names_dir = None
                self.pid = None
                self.command = ""
                self._next_scan = 0.0
                self.history.clear()
                self.samples = 0
                self.ready_time = None
                self.end_time = None
                self.state = "active"
                self._note(
                    f"attached {SHM_PATH}: {st.st_size} bytes, inode {st.st_ino}, "
                    f"{layout.num_first} first / {layout.num_second} second queue workers"
                )
                self._poll()
        finally:
            if not published:
                os.close(fd)

    def _poll(self) -> None:
        if self._fd is None or self._layout is None:
            return
        size = self._layout.words * WORD
        buf = os.pread(self._fd, size, 0)
        if len(buf) != size:
            self._detach(f"{SHM_PATH} shrank to {len(buf)} bytes, reused by a new run")
            return
        sample = parse(time.monotonic(), buf, self._layout)
        header = sample.header
        workers = (header.num_first_queue_workers, header.num_second_queue_workers)
        if header.ready != 1 or workers != (self._layout.num_first, self._layout.num_second):
            self._detach(f"{SHM_PATH} was reinitialized (ready={header.ready}, workers={workers})")
            return
        self.history.append(sample)
        self.samples += 1
        try:
            st = os.stat(SHM_PATH)
        except FileNotFoundError:
            self._detach(f"{SHM_PATH} removed after {self.samples} samples")
            return
        if (st.st_dev, st.st_ino) != self._ident:
            self._detach(f"{SHM_PATH} belongs to a new run after {self.samples} samples")

    def _track_process(self) -> None:
        ident = self._ident
        alive = self.pid is not None and os.path.exists(f"/proc/{self.pid}")
        if ident is None or alive or time.monotonic() < self._next_scan:
            return
        rescan = self._next_scan > 0
        self._next_scan = time.monotonic() + 1.0
        pid = find_process(ident)
        process = describe_process(pid) if pid is not None else None
        command, directory, started = process if process is not None else ("", None, None)
        directory = self.discopop_dir or directory
        names, names_dir = self.names, self.names_dir
        if directory is not None and directory != names_dir:
            names, names_dir = load_names(directory), directory
        with self.lock:
            if self._ident != ident:
                return
            if pid is None and (self.pid is not None or not rescan):
                if self.pid is not None:
                    self.end_time = time.time()
                self._note(f"no process maps {SHM_PATH}")
            elif pid is not None and pid != self.pid:
                if rescan:
                    self.history.clear()
                    self.samples = 0
                    self.end_time = None
                self.ready_time = started
                self._note(f"{'new run in the same segment, ' if rescan else ''}process {pid}: {command}")
            self.pid, self.command, self.names, self.names_dir = pid, command, names, names_dir

    def _detach(self, reason: str) -> None:
        if self._fd is not None:
            os.close(self._fd)
        self._fd = None
        self._ident = None
        self._layout = None
        if self.state == "active":
            self.state = "finished"
            self.end_time = time.time()
            self.end_reason = reason
        self._note(reason)
