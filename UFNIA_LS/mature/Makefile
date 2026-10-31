CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra
TARGET ?= ../../build/ufnia_ls/ufnia_mature
SOURCES = main.cpp native.cpp nia_uf.cpp nia_ls.cpp nia_initialize.cpp
HEADERS = nia_ls.h nia_Array.h nia_linear_initialize.h nia_novelty_score.h native.h
all: $(TARGET)
$(TARGET): $(SOURCES) $(HEADERS)
	mkdir -p $(dir $(TARGET))
	$(CXX) $(CXXFLAGS) $(SOURCES) -o $(TARGET) -lgmpxx -lgmp
clean:
	rm -f $(TARGET)
.PHONY: all clean
