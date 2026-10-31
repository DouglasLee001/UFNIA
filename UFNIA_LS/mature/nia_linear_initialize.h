#pragma once
#include <cstdint>
namespace nia {
enum class SlsStrategy { automatic, corewarm2, distancewarm, corebip, diverse, plain, novel, stepnovel };
struct SlsConfig { SlsStrategy strategy; bool wide_arithmetic,gate_input,distance_scoring,fast_moves; uint64_t restart_stall_limit; };
inline SlsConfig sls_config(SlsStrategy strategy){return {SlsStrategy::plain,true,true,false,true,500000};}
}
