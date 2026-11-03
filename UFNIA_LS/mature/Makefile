CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra
TARGET ?= ../../build/ufnia_ls/ufnia_mature
GMP_DIR ?= ../../build/ufnia_ls/deps/gmp
SOURCES = main.cpp native.cpp nia_uf.cpp nia_ls.cpp nia_initialize.cpp nia_affine.cpp
HEADERS = nia_ls.h nia_Array.h nia_linear_initialize.h nia_novelty_score.h native.h affine_math.h

ifneq ($(wildcard $(GMP_DIR)),)
GMP_CPPFLAGS := -I$(GMP_DIR)
GMP_LIBS := $(GMP_DIR)/libgmpxx.a $(GMP_DIR)/libgmp.a
else
GMP_CPPFLAGS :=
GMP_LIBS := -lgmpxx -lgmp
endif
LDLIBS ?= $(GMP_LIBS)

all: $(TARGET)
$(TARGET): $(SOURCES) $(HEADERS)
	mkdir -p $(dir $(TARGET))
	$(CXX) $(CPPFLAGS) $(GMP_CPPFLAGS) $(CXXFLAGS) $(SOURCES) -o $(TARGET) $(LDFLAGS) $(LDLIBS)
clean:
	rm -f $(TARGET)
.PHONY: all clean
