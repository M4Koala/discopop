#!/usr/bin/env python3
"""
    Shared-memory layout (all fields little-endian uint64, 64-bit target):

    struct MetricsHeader {
        uint64_t ready;                         # 0 until fully initialized, then 1
        uint64_t num_first_queue_workers;       # number of first-queue workers
        uint64_t num_second_queue_workers;      # number of second-queue workers
        uint64_t first_queue_size;              # FirstAccessQueue length
        uint64_t second_queue_size;             # SecondAccessQueue length
        uint64_t first_queue_dependency_count;  # deps found by first-queue workers (within chunks)
        uint64_t second_queue_dependency_count; # deps found by the second-queue worker (chunk boundaries)
        uint64_t total_dependency_count;        # unique deps in the program overall
    };

    The header is immediately followed by two variable-length bitmask arrays,
    one 64-bit word per 64 workers (bit i => worker i working):
        [ceil(num_first_queue_workers  / 64) words]   first-queue worker status
        [ceil(num_second_queue_workers / 64) words]   second-queue worker status

    Both worker counts are unbounded and the two pools are tracked separately.
"""

import mmap
import os
import struct
import time

SHM_NAME = "discopop_metrics"          # producer: shm_open("/discopop_metrics")
SHM_PATH = f"/dev/shm/{SHM_NAME}"

HEADER_LAYOUT = "<8Q"
HEADER_SIZE = struct.calcsize(HEADER_LAYOUT)

INTERVAL_S = float(os.environ.get("DP_METRICS_INTERVAL", "0.01"))
LOG_PATH = os.environ.get("DP_METRICS_LOG", "metrics_log.txt")


def status_words(num_workers: int) -> int:
    return (num_workers + 63) // 64


def render_bits(words: tuple, num_workers: int) -> str:
    return "".join(str((words[i // 64] >> (i % 64)) & 1) for i in range(num_workers))


def main() -> None:

    log = open(LOG_PATH, "w", buffering=1)

    def emit(line: str) -> None:
        print(line)
        log.write(line + "\n")
        log.flush()

    emit(f"# discopop metrics log  interval={INTERVAL_S}s  segment={SHM_PATH}")

    # wait for the metrics
    while not os.path.exists(SHM_PATH) or os.path.getsize(SHM_PATH) < HEADER_SIZE:
        print(f"waiting for {SHM_PATH} ...")
        time.sleep(INTERVAL_S)

    fd = os.open(SHM_PATH, os.O_RDONLY)
    size = os.fstat(fd).st_size
    mm = mmap.mmap(fd, size, prot=mmap.PROT_READ)

    # wait for complete layout
    while True:
        mm.seek(0)
        ready, num_first, num_second = struct.unpack("<3Q", mm.read(24))
        if ready:
            break
        print(f"waiting for metrics layout at {SHM_PATH} ...")
        time.sleep(INTERVAL_S)

    first_words = status_words(num_first)
    second_words = status_words(num_second)
    status_layout = f"<{first_words + second_words}Q"

    emit(f"# first_queue_workers={num_first}  second_queue_workers={num_second}")

    start = time.monotonic()
    samples = 0
    try:
        while True:
            mm.seek(0)
            block = mm.read(size)
            (_, num_first, num_second, first_q, second_q,
             first_deps, second_deps, total_deps) = struct.unpack_from(HEADER_LAYOUT, block, 0)
            words = struct.unpack_from(status_layout, block, HEADER_SIZE) if (first_words + second_words) else ()
            first_status = words[:first_words]
            second_status = words[first_words:first_words + second_words]

            first_bits = render_bits(first_status, num_first)
            second_bits = render_bits(second_status, num_second)
            emit(f"t={time.monotonic() - start:7.3f}  "
                 f"FQW={num_first} status={first_bits}  SQW={num_second} status={second_bits}  "
                 f"FAQ={first_q}  SAQ={second_q}  "
                 f"deps[chunks(FQW)={first_deps} boundary(SQW)={second_deps} overall={total_deps}]")
            samples += 1

            if not os.path.exists(SHM_PATH):
                emit(f"# run finished after {samples} samples")
                break
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        mm.close()
        os.close(fd)
        log.close()


if __name__ == "__main__":
    main()
