cmake_minimum_required(VERSION 3.20)
project(supcom_llm_bridge VERSION 1.0.0 LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# -------------------------------------------------------
# IMPORTANT: Must be built with MinGW GCC (not MSVC).
# The LuaAPI.h headers use GCC asm("0x...") to bind Lua
# functions to absolute addresses in SupCom.exe's process.
#
# Recommended toolchain:
#   cmake -B build_cmake -G "MinGW Makefiles" \
#         -DCMAKE_BUILD_TYPE=Release
#   cmake --build build_cmake
#
# Output: bridge/build/supcom_llm_bridge.dll
# -------------------------------------------------------

# -------------------------------------------------------
# Target: supcom_llm_bridge.dll  (x86 Windows shared lib)
# NOTE: SupCom:FA is a 32-bit process. Build flags ensure
# the DLL targets i686 so it can be loaded by the game.
# -------------------------------------------------------
add_library(supcom_llm_bridge SHARED
    src/dllmain.cpp
    src/lua_bridge.cpp
    src/pipe_client.cpp
    src/lua_injector.cpp
)

target_include_directories(supcom_llm_bridge PRIVATE
    src/
)

# Windows-only: link against kernel32 for named-pipe API
target_link_libraries(supcom_llm_bridge PRIVATE
    kernel32
)

# GCC/MinGW build settings
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(supcom_llm_bridge PRIVATE
        -Wall
        -m32          # 32-bit target (SupCom:FA is x86)
        -O2
        # Disable C++ exception handling, RTTI, and thread-safe statics.
        # These three features are the primary consumers of winpthread in libstdc++.
        # Since pipe_client.cpp no longer uses any C++ standard library, libstdc++
        # is not linked at all, which eliminates the winpthread dependency entirely.
        -fno-exceptions
        -fno-rtti
        -fno-threadsafe-statics
    )
    target_link_options(supcom_llm_bridge PRIVATE
        -m32
        -Wl,--enable-stdcall-fixup
        # Static libgcc embeds the GCC support library (integer/float helpers).
        # Without -fno-exceptions it would pull in EH code that needs winpthread,
        # but with -fno-exceptions that EH code is not generated/linked.
        # No -static-libstdc++ needed: we use zero C++ standard library features.
        -static-libgcc
    )
    set_target_properties(supcom_llm_bridge PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/build"
        LIBRARY_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/build"
        PREFIX ""   # no "lib" prefix on Windows
    )
endif()

# -------------------------------------------------------
# Target: inject.exe  (32-bit C helper — process injector)
# Used by bridge_server.py to inject llm_bridge.dll into
# ForgedAlliance.exe.  Must be 32-bit: GetProcAddress on
# kernel32!LoadLibraryA must return the WOW64 address that
# matches FA's 32-bit address space.
# -------------------------------------------------------
add_executable(inject inject.c)
target_link_libraries(inject PRIVATE kernel32)
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(inject PRIVATE -m32 -O2 -Wall)
    target_link_options(inject PRIVATE -m32)
    set_target_properties(inject PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/build"
    )
endif()
