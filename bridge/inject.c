/*
 * inject.c — 32-bit DLL injector for Supreme Commander: FA.
 *
 * Usage: inject.exe <pid> <dll_path>
 *
 * Injects dll_path into the process with the given PID using the classic
 * CreateRemoteThread + LoadLibraryA technique.
 *
 * MUST be compiled as 32-bit so that GetProcAddress("LoadLibraryA") returns
 * the WOW64 kernel32 address that matches FA's 32-bit address space:
 *
 *   i686-w64-mingw32-gcc -O2 -o inject.exe inject.c -lkernel32
 *
 * Exit codes: 0 = success, 1 = error (message on stderr).
 */

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char* argv[]) {
    if (argc < 3) {
        fprintf(stderr, "Usage: inject.exe <pid> <dll_path>\n");
        return 1;
    }

    DWORD pid        = (DWORD)atoi(argv[1]);
    const char* path = argv[2];
    SIZE_T path_len  = strlen(path) + 1;

    HANDLE hproc = OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);
    if (!hproc) {
        fprintf(stderr, "OpenProcess(%lu) failed: %lu\n",
                (unsigned long)pid, (unsigned long)GetLastError());
        return 1;
    }

    /* Allocate memory in target process for the DLL path string */
    LPVOID remote = VirtualAllocEx(
        hproc, NULL, path_len, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remote) {
        fprintf(stderr, "VirtualAllocEx failed: %lu\n",
                (unsigned long)GetLastError());
        CloseHandle(hproc);
        return 1;
    }

    /* Write the path into the target process */
    SIZE_T written = 0;
    if (!WriteProcessMemory(hproc, remote, path, path_len, &written)) {
        fprintf(stderr, "WriteProcessMemory failed: %lu\n",
                (unsigned long)GetLastError());
        VirtualFreeEx(hproc, remote, 0, MEM_RELEASE);
        CloseHandle(hproc);
        return 1;
    }

    /* Get LoadLibraryA address — same in all 32-bit processes per boot */
    LPVOID loadlib = (LPVOID)GetProcAddress(
        GetModuleHandleA("kernel32.dll"), "LoadLibraryA");
    if (!loadlib) {
        fprintf(stderr, "GetProcAddress(LoadLibraryA) failed: %lu\n",
                (unsigned long)GetLastError());
        VirtualFreeEx(hproc, remote, 0, MEM_RELEASE);
        CloseHandle(hproc);
        return 1;
    }

    /* Create remote thread that calls LoadLibraryA(dll_path) */
    HANDLE hthread = CreateRemoteThread(
        hproc, NULL, 0,
        (LPTHREAD_START_ROUTINE)loadlib,
        remote, 0, NULL);
    if (!hthread) {
        fprintf(stderr, "CreateRemoteThread failed: %lu\n",
                (unsigned long)GetLastError());
        VirtualFreeEx(hproc, remote, 0, MEM_RELEASE);
        CloseHandle(hproc);
        return 1;
    }

    /* Wait for LoadLibraryA to return (DllMain finishes) */
    DWORD wait_result = WaitForSingleObject(hthread, 15000);
    if (wait_result != WAIT_OBJECT_0) {
        fprintf(stderr, "WaitForSingleObject timeout or error: %lu\n",
                (unsigned long)GetLastError());
    }

    /* The remote thread's exit code IS the HMODULE from LoadLibraryA.
       A zero exit code means LoadLibraryA returned NULL — DLL not loaded. */
    DWORD exit_code = 0;
    GetExitCodeThread(hthread, &exit_code);
    CloseHandle(hthread);
    VirtualFreeEx(hproc, remote, 0, MEM_RELEASE);
    CloseHandle(hproc);

    if (exit_code == 0) {
        fprintf(stderr, "LoadLibraryA returned NULL — DLL failed to load (path=%s)\n", path);
        return 1;
    }

    printf("OK hmod=0x%lX\n", (unsigned long)exit_code);
    return 0;
}
