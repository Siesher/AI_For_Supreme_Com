#include <windows.h>
#include <stdio.h>
int main() {
    const char* path = "C:\Work\AI_For_Supreme_Com\bridge\build\supcom_llm_bridge.dll";
    HMODULE h = LoadLibraryA(path);
    DWORD err = GetLastError();
    printf("Handle=%p  Err=%lu\n", (void*)h, (unsigned long)err);
    return h ? 0 : 1;
}
