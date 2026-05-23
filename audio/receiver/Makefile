# Makefile — modular build for the C++ OSC receiver.
#
#   make           # produces ./osc_receiver
#   make run       # build + run, binds 127.0.0.1:9000
#   make clean     # remove all build artefacts

CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Iinclude

SRC := \
    src/OscParser.cpp \
    src/UdpListener.cpp \
    src/Dashboard.cpp \
    src/Receiver.cpp \
    src/main.cpp
OBJ := $(SRC:.cpp=.o)
DEP := $(OBJ:.o=.d)

BIN := osc_receiver

.PHONY: all run clean

all: $(BIN)

$(BIN): $(OBJ)
	$(CXX) $(CXXFLAGS) -o $@ $^

# Compile each translation unit + emit a .d dep file so header changes
# trigger recompilation of the right .o files.
src/%.o: src/%.cpp
	$(CXX) $(CXXFLAGS) -MMD -MP -c -o $@ $<

run: $(BIN)
	./$(BIN)

clean:
	rm -f $(OBJ) $(DEP) $(BIN)

-include $(DEP)
