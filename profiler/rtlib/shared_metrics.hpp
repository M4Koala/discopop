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

#pragma once

#include <atomic>
#include <cstdint>

namespace __dp {

// shared-memory name
#define DP_METRICS_SHM_NAME "/discopop_metrics"

// number of 64-bit words needed to store one status bit per worker
inline uint64_t metrics_status_words(uint64_t workers) { return (workers + 63) / 64; }

// Fixed metrics header + array of 64-bit words for FAQ worker status + array of 64-bit words for SAQ worker status
struct MetricsHeader {
  std::atomic<uint64_t> ready;                          // 1 when fully initialized
  std::atomic<uint64_t> num_first_queue_workers;        // number of first-queue workers
  std::atomic<uint64_t> num_second_queue_workers;       // number of second-queue workers
  std::atomic<uint64_t> first_queue_size;               // FirstAccessQueue length
  std::atomic<uint64_t> second_queue_size;              // SecondAccessQueue length
  std::atomic<uint64_t> first_queue_dependency_count;   // deps found by first-queue workers (within chunks)
  std::atomic<uint64_t> second_queue_dependency_count;  // deps found by the second-queue worker (chunk boundaries)
  std::atomic<uint64_t> total_dependency_count;         // unique deps in the program overall
};

extern MetricsHeader *metrics;
extern std::atomic<uint64_t> *metrics_first_status;  // first-queue worker status words
extern std::atomic<uint64_t> *metrics_second_status; // second-queue worker status words

void metrics_init(uint64_t first_queue_workers, uint64_t second_queue_workers);
void metrics_shutdown();

inline void metrics_set_first_queue_size(uint64_t n) {
  if (metrics)
    metrics->first_queue_size.store(n, std::memory_order_relaxed);
}

inline void metrics_set_second_queue_size(uint64_t n) {
  if (metrics)
    metrics->second_queue_size.store(n, std::memory_order_relaxed);
}

inline void metrics_inc_first_queue_dependency_count() {
  if (metrics)
    metrics->first_queue_dependency_count.fetch_add(1, std::memory_order_relaxed);
}

inline void metrics_inc_second_queue_dependency_count() {
  if (metrics)
    metrics->second_queue_dependency_count.fetch_add(1, std::memory_order_relaxed);
}

inline void metrics_inc_total_dependency_count() {
  if (metrics)
    metrics->total_dependency_count.fetch_add(1, std::memory_order_relaxed);
}

inline void metrics_first_queue_worker_busy(int64_t id) {
  if (metrics_first_status)
    metrics_first_status[id / 64].fetch_or(uint64_t(1) << (id % 64), std::memory_order_relaxed);
}
inline void metrics_first_queue_worker_idle(int64_t id) {
  if (metrics_first_status)
    metrics_first_status[id / 64].fetch_and(~(uint64_t(1) << (id % 64)), std::memory_order_relaxed);
}

inline void metrics_second_queue_worker_busy(int64_t id) {
  if (metrics_second_status)
    metrics_second_status[id / 64].fetch_or(uint64_t(1) << (id % 64), std::memory_order_relaxed);
}
inline void metrics_second_queue_worker_idle(int64_t id) {
  if (metrics_second_status)
    metrics_second_status[id / 64].fetch_and(~(uint64_t(1) << (id % 64)), std::memory_order_relaxed);
}

}
