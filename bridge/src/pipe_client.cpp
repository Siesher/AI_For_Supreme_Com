// T007: Named Pipe background thread — Game (DLL) side
// Connects to \\.\pipe\supcom_llm_bridge created by the Python server.
// Pure Windows API only — no C++ standard library → no libstdc++ → no winpthread.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <string.h>
#include "pipe_client.h"

#define MAX_MSG_LEN       (128 * 1024)   // 128 KB max per message
#define RECONNECT_DELAY   3000           // ms between reconnect attempts
#define PIPE_TIMEOUT      5000           // ms WaitNamedPipe timeout

// ---------------------------------------------------------------------------
// One-slot lock-free queue (single-producer / single-consumer).
// "ready" is the atomic flag: 1 = slot has fresh data, 0 = empty.
// The producer marks not-ready, writes, then marks ready.
// The consumer atomically swaps ready→0 and reads if it was 1.
// On x86, aligned DWORD reads/writes are naturally atomic.  MemoryBarrier()
// prevents compiler/hardware reordering across the flag update.
// ---------------------------------------------------------------------------
typedef struct {
    char          buf[MAX_MSG_LEN];
    unsigned int  len;
    volatile LONG ready;
} OneSlotQueue;

static void osq_push(OneSlotQueue* q, const char* data, unsigned int len)
{
    InterlockedExchange(&q->ready, 0);
    if (len > MAX_MSG_LEN) len = MAX_MSG_LEN;
    q->len = len;
    memcpy(q->buf, data, len);
    MemoryBarrier();
    InterlockedExchange(&q->ready, 1);
}

static int osq_pop(OneSlotQueue* q, char* out, unsigned int cap, unsigned int* outlen)
{
    if (InterlockedCompareExchange(&q->ready, 0, 1) != 1) return 0;
    MemoryBarrier();
    unsigned int n = q->len;
    if (n > cap) n = cap;
    memcpy(out, q->buf, n);
    *outlen = n;
    return 1;
}

// Global state
static OneSlotQueue  g_send_q;               // Lua sim  → pipe thread → Python
static OneSlotQueue  g_recv_q;               // Python   → pipe thread → Lua sim
static volatile LONG g_running = 0;
static volatile LONG g_status  = 0;          // 0=disconnected 1=connecting 2=connected
static HANDLE        g_thread  = NULL;

// Scratch buffers for the pipe thread (one thread only, so static is safe)
static char g_tmp_send[MAX_MSG_LEN];
static char g_tmp_recv[MAX_MSG_LEN];

static const char PIPE_NAME[] = "\\\\.\\pipe\\supcom_llm_bridge";

// ---------------------------------------------------------------------------
// Write a 4-byte LE length prefix followed by payload.
// ---------------------------------------------------------------------------
static BOOL write_msg(HANDLE pipe, const char* data, DWORD len)
{
    DWORD written;
    if (!WriteFile(pipe, &len, sizeof(len), &written, NULL)) return FALSE;
    if (!WriteFile(pipe, data, len, &written, NULL))          return FALSE;
    return written == len;
}

// ---------------------------------------------------------------------------
// Read a 4-byte LE length prefix, then that many bytes.
// ---------------------------------------------------------------------------
static BOOL read_msg(HANDLE pipe, char* buf, DWORD cap, DWORD* out_len)
{
    DWORD len, read;
    if (!ReadFile(pipe, &len, sizeof(len), &read, NULL) || read != sizeof(len)) return FALSE;
    if (len == 0 || len > cap) return FALSE;
    if (!ReadFile(pipe, buf, len, &read, NULL) || read != len)                   return FALSE;
    *out_len = len;
    return TRUE;
}

// ---------------------------------------------------------------------------
// Background pipe thread — runs for the lifetime of the DLL in FA's process.
// ---------------------------------------------------------------------------
static DWORD WINAPI PipeThread(LPVOID unused)
{
    (void)unused;

    while (g_running) {
        InterlockedExchange(&g_status, 1);  // connecting

        if (!WaitNamedPipeA(PIPE_NAME, PIPE_TIMEOUT)) {
            Sleep(RECONNECT_DELAY);
            continue;
        }

        HANDLE pipe = CreateFileA(
            PIPE_NAME, GENERIC_READ | GENERIC_WRITE,
            0, NULL, OPEN_EXISTING, 0, NULL);

        if (pipe == INVALID_HANDLE_VALUE) {
            Sleep(RECONNECT_DELAY);
            continue;
        }

        DWORD mode = PIPE_READMODE_BYTE;
        SetNamedPipeHandleState(pipe, &mode, NULL, NULL);
        InterlockedExchange(&g_status, 2);  // connected

        while (g_running) {
            // Flush latest game-state snapshot
            unsigned int send_len = 0;
            if (osq_pop(&g_send_q, g_tmp_send, MAX_MSG_LEN, &send_len)) {
                if (!write_msg(pipe, g_tmp_send, send_len)) break;
            }

            // Read any available command (non-blocking peek first)
            DWORD avail = 0;
            if (PeekNamedPipe(pipe, NULL, 0, NULL, &avail, NULL) && avail > 0) {
                DWORD recv_len = 0;
                if (!read_msg(pipe, g_tmp_recv, MAX_MSG_LEN, &recv_len)) break;
                osq_push(&g_recv_q, g_tmp_recv, recv_len);
            }

            Sleep(10);
        }

        CloseHandle(pipe);
        InterlockedExchange(&g_status, 0);  // disconnected
        if (g_running) Sleep(RECONNECT_DELAY);
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void PipeClient_Start(void)
{
    // Atomically set running=1; if it was already 1, bail out
    if (InterlockedCompareExchange(&g_running, 1, 0) != 0) return;
    g_thread = CreateThread(NULL, 0, PipeThread, NULL, 0, NULL);
}

void PipeClient_Stop(void)
{
    InterlockedExchange(&g_running, 0);
    if (g_thread) {
        WaitForSingleObject(g_thread, 5000);
        CloseHandle(g_thread);
        g_thread = NULL;
    }
}

void PipeClient_Send(const char* data, unsigned int len)
{
    osq_push(&g_send_q, data, len);
}

int PipeClient_TryReceive(char* buf, unsigned int cap, unsigned int* outlen)
{
    return osq_pop(&g_recv_q, buf, cap, outlen);
}

int PipeClient_IsConnected(void)
{
    return g_status == 2;
}

const char* PipeClient_GetStatus(void)
{
    switch (g_status) {
        case 0: return "disconnected";
        case 1: return "connecting";
        case 2: return "connected";
        default: return "error";
    }
}
