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

#include "DPTypes.hpp"

#include <atomic>
#include <cstddef>
#include <cstdint>

// 0 removes the live metrics entirely
#if !defined(DP_LIVE_METRICS)
#define DP_LIVE_METRICS 1
#endif

#if !defined(DP_METRICS_BB)
#define DP_METRICS_BB DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_FUNC_ENTRY)
#define DP_METRICS_FUNC_ENTRY DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_FUNC_EXIT)
#define DP_METRICS_FUNC_EXIT DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_LOOP_ENTRY)
#define DP_METRICS_LOOP_ENTRY DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_LOOP_ITERATION)
#define DP_METRICS_LOOP_ITERATION DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_LOOP_EXIT)
#define DP_METRICS_LOOP_EXIT DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_DEP_COUNTS)
#define DP_METRICS_DEP_COUNTS DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_TOTAL_DEPS)
#define DP_METRICS_TOTAL_DEPS DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_QUEUE_SIZES)
#define DP_METRICS_QUEUE_SIZES DP_LIVE_METRICS
#endif
#if !defined(DP_METRICS_WORKER_STATUS)
#define DP_METRICS_WORKER_STATUS DP_LIVE_METRICS
#endif

namespace __dp {

// shared-memory name
#define DP_METRICS_SHM_NAME "/discopop_metrics"

// only the first 64 frames of the function and loop stack are published
#define DP_METRICS_FUNC_STACK_CAP 64
#define DP_METRICS_LOOP_STACK_CAP 64

// number of 64-bit words needed to store one status bit per worker
inline uint64_t metrics_status_words(uint64_t workers) { return (workers + 63) / 64; }

// one dep counter per worker, 64 B apart so every worker gets its own cache line
#define DP_METRICS_COUNTER_STRIDE_WORDS 8

// Fixed metrics header + array of 64-bit words for FAQ worker status + array of 64-bit words for SAQ worker status
// + one dep counter per worker in both queues
struct MetricsHeader {
  std::atomic<uint64_t> ready;                          // 1 when fully initialized
  std::atomic<uint64_t> num_first_queue_workers;        // number of first-queue workers
  std::atomic<uint64_t> num_second_queue_workers;       // number of second-queue workers
  std::atomic<uint64_t> first_queue_size;               // FirstAccessQueue length
  std::atomic<uint64_t> second_queue_size;              // SecondAccessQueue length
  std::atomic<uint64_t> total_dependency_count;         // unique deps in the program overall
  // progress of the main thread through the profiled program
  std::atomic<uint64_t> control_flags;                  // bit 0 turns dep profiling on/off
  std::atomic<uint64_t> func_entry_count;
  std::atomic<uint64_t> func_exit_count;
  std::atomic<uint64_t> func_stack_depth;
  std::atomic<uint64_t> loop_entry_count;
  std::atomic<uint64_t> loop_exit_count;
  std::atomic<uint64_t> loop_iteration_count;
  std::atomic<uint64_t> loop_stack_depth;               // open loops
  std::atomic<uint64_t> distinct_loop_count;            // static loops seen so far
  std::atomic<uint64_t> bb_visit_count;                 // basic blocks visited
  std::atomic<uint64_t> distinct_bb_count;              // basic blocks visited for the first time
  std::atomic<uint64_t> total_bb_count;                 // from DP_BBDepCounter.txt, 0 if unknown
  std::atomic<uint64_t> function_stack[DP_METRICS_FUNC_STACK_CAP];
  std::atomic<uint64_t> loop_stack[DP_METRICS_LOOP_STACK_CAP];
};

static_assert(sizeof(MetricsHeader) == (18 + DP_METRICS_FUNC_STACK_CAP + DP_METRICS_LOOP_STACK_CAP) * sizeof(uint64_t),
              "MetricsHeader layout must stay in sync with metrics_consumer.py");

extern MetricsHeader *metrics;
extern std::atomic<uint64_t> *metrics_first_status;  // first-queue worker status words
extern std::atomic<uint64_t> *metrics_second_status; // second-queue worker status words
extern std::atomic<uint64_t> *metrics_first_dep_counts;  // per-worker, stride DP_METRICS_COUNTER_STRIDE_WORDS
extern std::atomic<uint64_t> *metrics_second_dep_counts;

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

inline std::atomic<uint64_t> *metrics_first_dep_counter(int64_t id) {
  return metrics_first_dep_counts ? metrics_first_dep_counts + id * DP_METRICS_COUNTER_STRIDE_WORDS : nullptr;
}

inline std::atomic<uint64_t> *metrics_second_dep_counter(int64_t id) {
  return metrics_second_dep_counts ? metrics_second_dep_counts + id * DP_METRICS_COUNTER_STRIDE_WORDS : nullptr;
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

// pack count and begin LID of an open loop into one word
inline uint64_t metrics_pack_loop_slot(LID lid, int32_t count) {
  return (uint64_t(uint32_t(count)) << 32) | (uint64_t(lid) & 0xFFFFFFFFu);
}

// frame_level is the FuncStackLevel of the new frame, main is 0
inline void metrics_func_entry(LID lid, int32_t frame_level) {
  if (!metrics)
    return;
  metrics->func_entry_count.fetch_add(1, std::memory_order_relaxed);
  if (frame_level >= 0 && frame_level < DP_METRICS_FUNC_STACK_CAP)
    metrics->function_stack[frame_level].store(uint64_t(lid) & 0xFFFFFFFFu, std::memory_order_relaxed);
  metrics->func_stack_depth.store(uint64_t(frame_level + 1), std::memory_order_relaxed);
}

// level_after is the FuncStackLevel left by the exit, -1 after main
inline void metrics_func_exit(int32_t level_after) {
  if (!metrics)
    return;
  metrics->func_exit_count.fetch_add(1, std::memory_order_relaxed);
  metrics->func_stack_depth.store(level_after >= 0 ? uint64_t(level_after + 1) : 0, std::memory_order_relaxed);
}

inline void metrics_loop_entry(LID begin_lid, std::size_t depth_after, bool is_new_static_loop) {
  if (!metrics)
    return;
  metrics->loop_entry_count.fetch_add(1, std::memory_order_relaxed);
  if (is_new_static_loop)
    metrics->distinct_loop_count.fetch_add(1, std::memory_order_relaxed);
  if (depth_after >= 1 && depth_after <= DP_METRICS_LOOP_STACK_CAP)
    metrics->loop_stack[depth_after - 1].store(metrics_pack_loop_slot(begin_lid, 0), std::memory_order_relaxed);
  metrics->loop_stack_depth.store(depth_after, std::memory_order_relaxed);
}

inline void metrics_loop_iteration(LID begin_lid, int32_t count, std::size_t depth) {
  if (!metrics)
    return;
  metrics->loop_iteration_count.fetch_add(1, std::memory_order_relaxed);
  if (depth >= 1 && depth <= DP_METRICS_LOOP_STACK_CAP)
    metrics->loop_stack[depth - 1].store(metrics_pack_loop_slot(begin_lid, count), std::memory_order_relaxed);
}

inline void metrics_loop_exit(std::size_t depth_after) {
  if (!metrics)
    return;
  metrics->loop_exit_count.fetch_add(1, std::memory_order_relaxed);
  metrics->loop_stack_depth.store(depth_after, std::memory_order_relaxed);
}

inline void metrics_report_bb(bool is_new) {
  if (!metrics)
    return;
  metrics->bb_visit_count.fetch_add(1, std::memory_order_relaxed);
  if (is_new)
    metrics->distinct_bb_count.fetch_add(1, std::memory_order_relaxed);
}

}
