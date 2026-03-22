#pragma once
// Public API for the named pipe client (background thread).
// Pure Windows API — no C++ standard library types in this header.

#ifdef __cplusplus
extern "C" {
#endif

void        PipeClient_Start(void);
void        PipeClient_Stop(void);

// Non-blocking send: copies data into the send slot (overwrites old if not consumed).
void        PipeClient_Send(const char* data, unsigned int len);

// Non-blocking receive: fills buf if a message is waiting. Returns 1 if data was read.
int         PipeClient_TryReceive(char* buf, unsigned int cap, unsigned int* out_len);

int         PipeClient_IsConnected(void);
const char* PipeClient_GetStatus(void);

#ifdef __cplusplus
}
#endif
