cmake_minimum_required(VERSION 3.22.1)
project(guard C)

add_library(guard SHARED guard.c)

# JNI 链接
target_link_libraries(guard android log)

set_target_properties(guard PROPERTIES
    C_STANDARD 99
    C_STANDARD_REQUIRED ON
)
