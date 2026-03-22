#pragma once
// T006: Lock-free Single-Producer Single-Consumer ring buffer (2-slot)
// Used for send/receive queues between Lua sim thread and pipe background thread.
// SPSC guarantee: only one writer (producer) and one reader (consumer) at a time.

#include <atomic>
#include <optional>
#include <string>

template<typename T, size_t Capacity = 2>
class SPSCRingBuffer {
    static_assert((Capacity & (Capacity - 1)) == 0, "Capacity must be power of 2");

public:
    SPSCRingBuffer() : head_(0), tail_(0) {}

    // Producer: try to push an item. Returns false if full.
    bool push(T item) {
        const size_t head = head_.load(std::memory_order_relaxed);
        const size_t next_head = (head + 1) & (Capacity - 1);
        if (next_head == tail_.load(std::memory_order_acquire)) {
            // Buffer full — drop oldest to make room (game state snapshots go stale)
            tail_.store((tail_.load(std::memory_order_relaxed) + 1) & (Capacity - 1),
                        std::memory_order_release);
        }
        buffer_[head] = std::move(item);
        head_.store(next_head, std::memory_order_release);
        return true;
    }

    // Consumer: try to pop an item. Returns nullopt if empty.
    std::optional<T> pop() {
        const size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail == head_.load(std::memory_order_acquire)) {
            return std::nullopt; // Empty
        }
        T item = std::move(buffer_[tail]);
        tail_.store((tail + 1) & (Capacity - 1), std::memory_order_release);
        return item;
    }

    bool empty() const {
        return head_.load(std::memory_order_acquire) ==
               tail_.load(std::memory_order_acquire);
    }

private:
    T buffer_[Capacity];
    alignas(64) std::atomic<size_t> head_; // Written by producer
    alignas(64) std::atomic<size_t> tail_; // Written by consumer
};

// Convenience aliases used by the bridge
using SendQueue    = SPSCRingBuffer<std::string, 2>; // Lua → Python (game state snapshots)
using ReceiveQueue = SPSCRingBuffer<std::string, 2>; // Python → Lua (strategic commands)
