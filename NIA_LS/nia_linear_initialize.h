#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>
namespace nia {
enum class SlsStrategy { automatic, corewarm2, distancewarm, corebip, diverse, plain, novel, stepnovel };

// Each search owns its configuration. All strategies use the same solver class
// and object code; starting a repair never changes the parent's configuration.
struct SlsConfig {
    SlsStrategy strategy;
    bool wide_arithmetic, gate_input, distance_scoring, fast_moves;
    uint64_t restart_stall_limit;
};
inline SlsConfig sls_config(SlsStrategy strategy){
    const bool distance=strategy==SlsStrategy::corewarm2 || strategy==SlsStrategy::distancewarm || strategy==SlsStrategy::corebip;
    return {strategy,distance || strategy==SlsStrategy::diverse,distance,distance,
            strategy==SlsStrategy::automatic || strategy==SlsStrategy::plain,
            strategy==SlsStrategy::diverse?50000u:500000u};
}
const char* strategy_name(SlsStrategy strategy);
bool parse_strategy(const std::string& name,SlsStrategy& strategy);

struct initial_model {
    std::map<std::string,std::string> values;
    double seconds=0.0;
    unsigned bounded_variables=0;
};
// Returns only a complete assignment checked against the original converted CNF.
// Failure/timeout of a bounded subproblem is not a proof of unsatisfiability.
bool make_linear_initial_model(const std::string& path,double budget_seconds,initial_model& result,SlsStrategy strategy=SlsStrategy::automatic);
}

namespace nia {
// The last/best SAT prefix survives later UNSAT, UNKNOWN or budget exhaustion.
// A true return still requires every original CNF clause to hold.
bool make_progressive_initial_model(const std::string& path,double budget_seconds,initial_model& best,SlsStrategy strategy=SlsStrategy::automatic);
}

namespace nia_sls_internal {
// Internal stages are ordinary linked C++ functions, invoked in a bounded fork.
int run_stage(const std::vector<std::string>& arguments);
bool validate_model(const std::string& input,const std::string& model);
}
namespace call { int run_sls_entry(int argc,char** argv); }
