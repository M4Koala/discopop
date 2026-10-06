/*
 * This file is part of the DiscoPoP software
 * (http://www.discopop.tu-darmstadt.de)
 *
 * Copyright (c) 2020, Technische Universitaet Darmstadt, Germany
 *
 * This software may be modified and distributed under the terms of
 * the 3-Clause BSD License. See the LICENSE file in the package base
 * directory for details.
 *
 */

#include "shared_metrics.hpp"

#if DP_LIVE_METRICS

#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

namespace __dp {

MetricsHeader *metrics = nullptr;
std::atomic<uint64_t> *metrics_first_status = nullptr;
std::atomic<uint64_t> *metrics_second_status = nullptr;
std::atomic<uint64_t> *metrics_first_dep_counts = nullptr;
std::atomic<uint64_t> *metrics_second_dep_counts = nullptr;

// size of the mapped segment for munmap()
static size_t metrics_mapped_size = 0;

void metrics_init(uint64_t first_queue_workers, uint64_t second_queue_workers) {
  // the status arrays grow with the worker counts, so any number of workers is supported
  const uint64_t first_words = metrics_status_words(first_queue_workers);
  const uint64_t second_words = metrics_status_words(second_queue_workers);
  const uint64_t first_dep_words = first_queue_workers * DP_METRICS_COUNTER_STRIDE_WORDS;
  const uint64_t second_dep_words = second_queue_workers * DP_METRICS_COUNTER_STRIDE_WORDS;
  const size_t size =
      sizeof(MetricsHeader) + (first_words + second_words + first_dep_words + second_dep_words) * sizeof(uint64_t);

  int fd = shm_open(DP_METRICS_SHM_NAME, O_CREAT | O_RDWR, 0666);
  if (fd == -1) {
    std::cerr << "DiscoPoP: live metrics disabled, shm_open failed" << std::endl;
    return;
  }
  int truncated = ftruncate(fd, size);
  void *addr = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);

  // failure leaves metrics == nullptr
  if (truncated == -1) {
    std::cerr << "DiscoPoP: live metrics disabled, ftruncate failed" << std::endl;
    return;
  }
  if (addr == MAP_FAILED) {
    std::cerr << "DiscoPoP: live metrics disabled, mmap failed" << std::endl;
    return;
  }

  std::memset(addr, 0, size);
  MetricsHeader *header = static_cast<MetricsHeader *>(addr);
  header->num_first_queue_workers.store(first_queue_workers, std::memory_order_relaxed);
  header->num_second_queue_workers.store(second_queue_workers, std::memory_order_relaxed);
  // profiling is on unless the launcher turns it off
  // DP_CONTROL_FLAGS sets the initial flags, bit 0 = 0 starts with dep profiling off
  uint64_t control_flags = 1;
  if (const char *env = std::getenv("DP_CONTROL_FLAGS"))
    control_flags = std::strtoull(env, nullptr, 0);
  header->control_flags.store(control_flags, std::memory_order_relaxed);

  // total number of instrumented basic blocks
  if (const char *profiler_dir = std::getenv("DOT_DISCOPOP_PROFILER")) {
    std::ifstream counter_file(std::string(profiler_dir) + "/DP_BBDepCounter.txt");
    uint64_t total = 0;
    if (counter_file >> total)
      header->total_bb_count.store(total, std::memory_order_relaxed);
  }

  std::atomic<uint64_t> *status = reinterpret_cast<std::atomic<uint64_t> *>(header + 1);
  metrics_first_status = status;
  metrics_second_status = status + first_words;
  metrics_first_dep_counts = status + first_words + second_words;
  metrics_second_dep_counts = metrics_first_dep_counts + first_dep_words;
  metrics_mapped_size = size;
  metrics = header;

  // ready to read
  header->ready.store(1, std::memory_order_release);
}

void metrics_shutdown() {
  if (!metrics)
    return;
  munmap(metrics, metrics_mapped_size);
  metrics = nullptr;
  metrics_first_status = nullptr;
  metrics_second_status = nullptr;
  metrics_first_dep_counts = nullptr;
  metrics_second_dep_counts = nullptr;
  metrics_mapped_size = 0;
  shm_unlink(DP_METRICS_SHM_NAME);
}

}

#endif
