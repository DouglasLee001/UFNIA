
CXX = g++
CXXFLAGS = -Wall -g -w -O3
LDLIBS = -lz3 -lgmpxx -lgmp
LDFLAGS = -Wl,-rpath,$(abspath .lib)

TARGET = nia_ls
SRCS =   nia_build_instance.cpp  nia_initialize.cpp nia_linear_initialize.cpp nia_restart_repair.cpp nia_joint_moves.cpp nia_ls.cpp    nia_print.cpp call.cpp
OBJS = $(SRCS:.cpp=.o)
HEADERS = nia_Array.h nia_ls.h call.h nia_linear_initialize.h nia_linearize.h nia_lia_sls.h nia_lia_preprocess.h nia_repair_selection.h nia_novelty_score.h

# Strategies are per-instance runtime settings; each source is compiled once.

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ $^ $(LDLIBS)

%.o: %.cpp $(HEADERS)
	$(CXX) $(CXXFLAGS) -c $<

clean:
	rm -f $(TARGET) $(OBJS) $(foreach strategy,corewarm2 distancewarm corebip diverse plain novel stepnovel,$(SRCS:.cpp=.$(strategy).o))

# Verification builds use separate outputs so they cannot contaminate release objects.
SCORING_TEST_DIR = ../tests/.build
SCORING_SRCS = $(SRCS)

.PHONY: test-scoring scoring-drivers

test-scoring: scoring-drivers
	python3 ../tests/run_incremental_scoring.py

scoring-drivers:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-scoring-new ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -DNIA_SCORING_REFERENCE -DNIA_CANDIDATE_REFERENCE -I. -o $(SCORING_TEST_DIR)/nia-scoring-reference ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -DNIA_SCORING_VERIFY -DNIA_CANDIDATE_VERIFY -I. -o $(SCORING_TEST_DIR)/nia-scoring-verify ../tests/solver_driver.cpp $(SCORING_SRCS) $(LDFLAGS) $(LDLIBS)

.PHONY: test-linear-initialization
test-linear-initialization:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-linear-initialization ../tests/linear_initialization_driver.cpp $(SRCS) $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_linear_initialization.py

.PHONY: linear-model-validator
linear-model-validator: $(SCORING_TEST_DIR)/validate-initial-model

$(SCORING_TEST_DIR)/validate-initial-model: ../tests/validate_initial_model.cpp
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

.PHONY: test-restart-repair
test-restart-repair: $(TARGET) test-repair-selection
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-restart-repair ../tests/restart_repair_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-restart-repair

.PHONY: test-lia-sls
test-lia-sls:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/nia-lia-sls ../tests/lia_sls_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(SCORING_TEST_DIR)/nia-lia-sls

.PHONY: test-lia-scoring lia-scoring-drivers

test-lia-scoring: lia-scoring-drivers
	python3 ../tests/run_lia_incremental_scoring.py

lia-scoring-drivers:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-scoring-reference ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -I. -o $(SCORING_TEST_DIR)/lia-scoring-incremental ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-scoring-verify ../tests/lia_incremental_scoring_driver.cpp $(LDFLAGS) $(LDLIBS)

.PHONY: test-lia-variable

test-lia-variable:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_VARIABLE_REFERENCE -I. -o $(SCORING_TEST_DIR)/lia-variable-reference ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_LIA_SCORING_TEST -DNIA_LIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/lia-variable-heuristic ../tests/lia_variable_heuristic_driver.cpp $(LDFLAGS) $(LDLIBS)
	python3 ../tests/run_lia_variable_heuristic.py

.PHONY: test-repair-selection
test-repair-selection:
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/repair-selection ../tests/repair_selection_driver.cpp
	$(SCORING_TEST_DIR)/repair-selection

.PHONY: sat-model-validator
sat-model-validator: $(SCORING_TEST_DIR)/validate-novelty-model

$(SCORING_TEST_DIR)/validate-novelty-model: ../tests/validate_novelty_model.cpp ../tests/nia_original_sls.h ../tests/nia_original_preprocess.h nia_lia_preprocess.h
	mkdir -p $(@D)
	$(CXX) $(CXXFLAGS) -o $@ $< $(LDFLAGS) $(LDLIBS)

.PHONY: auxiliary-dispatch-driver test-auxiliary-dispatch
auxiliary-dispatch-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-auxiliary-dispatch ../tests/auxiliary_dispatch_driver.cpp $(filter-out nia_ls.o call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-auxiliary-dispatch: auxiliary-dispatch-driver
	$(SCORING_TEST_DIR)/nia-auxiliary-dispatch

.PHONY: core-perturbation-driver test-core-perturbation search-stage-driver
core-perturbation-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-core-perturbation ../tests/core_perturbation_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

search-stage-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-search-stage ../tests/search_stage_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-core-perturbation: core-perturbation-driver search-stage-driver test-auxiliary-dispatch
	$(SCORING_TEST_DIR)/nia-core-perturbation
	python3 ../tests/run_search_strategies.py

.PHONY: novelty-scoring-drivers test-novelty-scoring
novelty-scoring-drivers: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -I. -o $(SCORING_TEST_DIR)/novelty-scoring ../tests/novelty_scoring_driver.cpp
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -DNIA_NOVELTY_TRACE -I. -o $(SCORING_TEST_DIR)/nia-novelty-score ../tests/runtime_strategy_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -DNIA_NOVELTY_TRACE -DNIA_NOVELTY_REFERENCE -I. -o $(SCORING_TEST_DIR)/nia-novelty-reference ../tests/runtime_strategy_driver.cpp nia_ls.cpp $(filter-out nia_ls.o call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-novelty-scoring: novelty-scoring-drivers
	$(SCORING_TEST_DIR)/novelty-scoring
	python3 ../tests/run_novelty_scoring.py

.PHONY: break-sampling-driver test-break-sampling
.PHONY: softmax-driver test-softmax
softmax-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-softmax-integration ../tests/softmax_integration_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-softmax: softmax-driver test-novelty-scoring
	$(SCORING_TEST_DIR)/nia-softmax-integration

break-sampling-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-break-sampling ../tests/break_sampling_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -DNIA_SCORING_VERIFY -I. -o $(SCORING_TEST_DIR)/nia-break-verify ../tests/break_sampling_driver.cpp nia_ls.cpp $(filter-out nia_ls.o call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-break-sampling: break-sampling-driver test-novelty-scoring
	$(SCORING_TEST_DIR)/nia-break-sampling
	$(SCORING_TEST_DIR)/nia-break-verify

.PHONY: joint-operators-driver test-joint-operators
joint-operators-driver: $(TARGET)
	mkdir -p $(SCORING_TEST_DIR)
	$(CXX) $(CXXFLAGS) -DNIA_NO_MAIN -I. -o $(SCORING_TEST_DIR)/nia-joint-operators ../tests/joint_operators_driver.cpp $(filter-out call.o,$(OBJS)) call.cpp $(LDFLAGS) $(LDLIBS)

test-joint-operators: joint-operators-driver test-core-perturbation
	$(SCORING_TEST_DIR)/nia-joint-operators
