// main.cpp — entry point. Parses port from argv, hands off to Receiver.
//
// Usage:
//     ./osc_receiver           # binds 127.0.0.1:9000
//     ./osc_receiver 9001      # binds 127.0.0.1:9001
#include "Receiver.hpp"

#include <cstdio>
#include <cstdlib>
#include <exception>

int main(int argc, char** argv) {
    int port = (argc > 1) ? std::atoi(argv[1]) : 9000;

    try {
        audio_brain::Receiver receiver(port);
        receiver.run();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "[osc-receiver] fatal: %s\n", e.what());
        return 1;
    }
    return 0;
}
