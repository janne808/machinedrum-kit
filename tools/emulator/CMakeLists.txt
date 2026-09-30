# Machinedrum emulator harness for the machinedrum-kit.
# Build: python3 setup.py && cmake -S . -B build -G Ninja && cmake --build build
cmake_minimum_required(VERSION 3.16)
project(machinedrum_kit_emulator LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
set(BUILD_TESTING OFF CACHE BOOL "Upstream tests" FORCE)
set(ASMJIT_STATIC TRUE)
set(ASMJIT_NO_INSTALL TRUE)
set(GEAR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/gearmulator/source")
find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)

# Refuse to build unpinned or unpatched sources.
execute_process(COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/setup.py --check
  RESULT_VARIABLE SETUP_RESULT)
if(NOT SETUP_RESULT EQUAL 0)
  message(FATAL_ERROR "Third-party sources are not pinned and patched: run python3 setup.py")
endif()

add_subdirectory(${GEAR}/dsp56300/source deps/dsp)
set(BUILD_TESTING ON)
add_subdirectory(${GEAR}/mc68k deps/mc68k)          # ColdFire timing/divide/HI08 tests
set(BUILD_TESTING OFF)
add_subdirectory(${GEAR}/baseLib deps/base)
add_subdirectory(${GEAR}/libresample deps/resample)
add_subdirectory(${GEAR}/synthLib deps/synth)
add_subdirectory(${GEAR}/hardwareLib deps/hardware)
add_subdirectory(${GEAR}/elektron/md/mdLib deps/md)

add_executable(md-harness src/harness.cpp)          # boot + MIDI + panel + audio smoke test
target_link_libraries(md-harness PRIVATE mdLib)
add_executable(md-monitor src/monitor.cpp)          # three-processor debugger, JSONL protocol
target_link_libraries(md-monitor PRIVATE mdLib)
add_executable(md-kernel src/kernel.cpp)            # machine DSP code outside the firmware
target_link_libraries(md-kernel PRIVATE dsp56kEmu)
add_executable(md-debug-hooks-test src/test_debug_hooks.cpp)
target_link_libraries(md-debug-hooks-test PRIVATE dsp56kEmu)

enable_testing()
add_test(NAME dsp-debug-hooks COMMAND md-debug-hooks-test)
set_tests_properties(dsp-debug-hooks PROPERTIES TIMEOUT 30)
add_custom_target(check
  COMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure
  DEPENDS md-debug-hooks-test mc68kColdFireTimingTest mc68kColdFireDivideTest mc68kHdi08ReceiveTest
  USES_TERMINAL)
