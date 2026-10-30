CXX ?= c++
CXXFLAGS ?= -O2 -std=c++17 -Wall -Wextra -pedantic
TARGET := ../build/ufnia_ls/ufnia_core
PYTHON ?= python3
PY_Z3_DIR := $(shell $(PYTHON) -c 'import pathlib,z3; print(pathlib.Path(z3.__file__).resolve().parent)')
FRONTEND := ../build/ufnia_ls/native_frontend.so

.PHONY: all test native-frontend
all: $(TARGET) $(FRONTEND)

$(TARGET): core.cpp mpz_sls.h mpz_preprocess.h
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) core.cpp -o $@ $(LDFLAGS) -lz3 -lgmpxx -lgmp

test: all
	cd .. && $(PYTHON) -m unittest discover -s tests -p 'test_*.py' -v

# Passing a Python Z3_context requires the exact bundled Z3 ABI. The standalone
# GMP solver above stays a separate process using the system development Z3.
native-frontend: $(FRONTEND)

$(FRONTEND): native_frontend.cpp
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) -fPIC -shared -I"$(PY_Z3_DIR)/include" $< -o $@.tmp "$(PY_Z3_DIR)/lib/libz3.so" -Wl,-rpath,"$(PY_Z3_DIR)/lib" $(LDFLAGS)
	mv $@.tmp $@
