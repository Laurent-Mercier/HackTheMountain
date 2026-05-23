CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra

all: osc_receiver

osc_receiver: osc_receiver.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

.PHONY: all clean run
run: osc_receiver
	./osc_receiver

clean:
	rm -f osc_receiver
