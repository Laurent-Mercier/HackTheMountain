cmake_minimum_required(VERSION 3.25)
project(visual CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

find_package(SDL3 REQUIRED CONFIG)
find_program(GLSLC glslc REQUIRED)

# ---------------------------------------------------------------------------
# Shader compilation: .vert/.frag → .spv
# ---------------------------------------------------------------------------
set(SHADER_SRC_DIR ${CMAKE_CURRENT_SOURCE_DIR}/shaders)
set(SHADER_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/shaders)
file(MAKE_DIRECTORY ${SHADER_OUT_DIR})

set(SHADER_SOURCES
    ${SHADER_SRC_DIR}/fullscreen.vert
    ${SHADER_SRC_DIR}/default.frag
)

foreach(SHADER ${SHADER_SOURCES})
    get_filename_component(SHADER_NAME ${SHADER} NAME)
    set(SPV_FILE ${SHADER_OUT_DIR}/${SHADER_NAME}.spv)
    add_custom_command(
        OUTPUT  ${SPV_FILE}
        COMMAND ${GLSLC} --target-env=vulkan1.0 -o ${SPV_FILE} ${SHADER}
        DEPENDS ${SHADER}
        COMMENT "Compiling shader ${SHADER_NAME}"
        VERBATIM
    )
    list(APPEND SPV_FILES ${SPV_FILE})
endforeach()

add_custom_target(shaders ALL DEPENDS ${SPV_FILES})

# ---------------------------------------------------------------------------
# Main executable
# ---------------------------------------------------------------------------
add_executable(visual src/main.cpp)
add_dependencies(visual shaders)

target_link_libraries(visual PRIVATE SDL3::SDL3)

target_compile_options(visual PRIVATE
    -Wall
    -Wextra
    -Wpedantic
)
