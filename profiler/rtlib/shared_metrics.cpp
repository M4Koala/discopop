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

#include <cstddef>
#include <cstring>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

namespace __dp {

MetricsHeader *metrics = nullptr;
std::atomic<uint64_t> *metrics_first_status = nullptr;
std::atomic<uint64_t> *metrics_second_status = nullptr;

// size of the mapped segment for munmap()
static size_t metrics_mapped_size = 0;

void metrics_init(uint64_t first_queue_workers, uint64_t second_queue_workers) {
  // the status arrays grow with the worker counts, so any number of workers is supported
  const uint64_t first_words = metrics_status_words(first_queue_workers);
  const uint64_t second_words = metrics_status_words(second_queue_workers);
  const size_t size = sizeof(MetricsHeader) + (first_words + second_words) * sizeof(uint64_t);

  int fd = shm_open(DP_METRICS_SHM_NAME, O_CREAT | O_RDWR, 0666);
  if (fd == -1)
    return;
  int truncated = ftruncate(fd, size);
  void *addr = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);

  // failure leaves metrics == nullptr
  if (truncated == -1 || addr == MAP_FAILED)
    return;

  std::memset(addr, 0, size);
  MetricsHeader *header = static_cast<MetricsHeader *>(addr);
  header->num_first_queue_workers.store(first_queue_workers, std::memory_order_relaxed);
  header->num_second_queue_workers.store(second_queue_workers, std::memory_order_relaxed);

  std::atomic<uint64_t> *status = reinterpret_cast<std::atomic<uint64_t> *>(header + 1);
  metrics_first_status = status;
  metrics_second_status = status + first_words;
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
  metrics_mapped_size = 0;
  shm_unlink(DP_METRICS_SHM_NAME);
}

}
