#include "native.h"
#include <cmath>
#include <iomanip>
#include <stdexcept>
namespace {const char* version="mature-mpz-v1";}
int main(int argc,char** argv){
 try{
  if(argc<2)throw std::runtime_error("usage: ufnia_mature INPUT.native --seconds N --seed N --model-output PATH [--parse-only]");
  std::string input=argv[1],model;double seconds=1;unsigned seed=1;bool parse=false,uf=false;std::string interaction="joint",scoring="count";bool interaction_set=false,scoring_set=false;
  for(int i=2;i<argc;++i){std::string option=argv[i];if(option=="--parse-only"){parse=true;continue;}if(option=="--uf-observers"){uf=true;version="mature-mpz-v2";continue;}if(i+1==argc)throw std::runtime_error("missing option value");std::string value=argv[++i];size_t used=0;
   if(option=="--seconds"){seconds=std::stod(value,&used);if(used!=value.size()||!std::isfinite(seconds)||seconds<0)throw std::runtime_error("invalid seconds");}
   else if(option=="--seed"){auto x=std::stoull(value,&used);if(used!=value.size()||value.empty()||value[0]=='-'||x>UINT_MAX)throw std::runtime_error("invalid seed");seed=x;}
   else if(option=="--interaction"){interaction=value;interaction_set=true;if(value!="none"&&value!="soft"&&value!="joint")throw std::runtime_error("invalid interaction");}
   else if(option=="--scoring"){scoring=value;scoring_set=true;if(value!="count"&&value!="paws")throw std::runtime_error("invalid scoring");}
   else if(option=="--model-output")model=value;else throw std::runtime_error("unknown option: "+option);
  }
  if(!uf&&(interaction_set||scoring_set))throw std::runtime_error("interaction/scoring require --uf-observers");
  nia::NativeInput source;source.read(input,uf);nia::ls_solver solver(seed);solver.uf_enabled=uf;solver.uf_interaction=interaction;solver.uf_scoring=scoring;source.install(solver);solver._cutoff=seconds;
  if(parse){std::cout<<"{\"status\":\"parsed\",\"backend\":\"mature_mpz\",\"backend_version\":\""<<version<<"\",\"atoms\":"<<source.atoms.size()-1<<",\"vars\":"<<solver._num_vars<<",\"clauses\":"<<solver._num_clauses<<",\"max_numeral_bits\":"<<source.max_numeral_bits<<",\"uf_observers\":"<<(uf?"true":"false")<<",\"uf_applications\":"<<solver.uf_applications.size()<<"}\n";return 0;}
  bool found=seconds>0&&solver.local_search();bool valid=found&&source.validate(solver);
  if(valid&&!model.empty()){std::ofstream file(model);if(!file)throw std::runtime_error("cannot write model");file<<"{";bool first=true;for(size_t i=0;i<solver._num_vars;++i){if(!first)file<<",";first=false;file<<nia::json_string(solver._vars[i].var_name)<<":";if(solver._vars[i].is_nia)file<<nia::json_string(solver._solution[i].get_str());else file<<(solver._solution[i]>0?"true":"false");}for(const auto& [name,value]:source.restored){if(solver.name2var.count(name))continue;if(!first)file<<",";first=false;file<<nia::json_string(name)<<":"<<nia::json_string(value.get_str());}file<<"}\n";if(!file)throw std::runtime_error("model write failed");}
  std::cout<<(valid?"sat":"unknown")<<"\n";
  std::cerr<<"{\"status\":\""<<(valid?"sat":"unknown")<<"\",\"backend\":\"mature_mpz\",\"backend_version\":\""<<version<<"\",\"validated\":"<<(valid?"true":"false")<<",\"steps\":"<<solver._step<<",\"vars\":"<<solver._num_vars<<",\"int_vars\":"<<solver._num_nia_vars<<",\"bool_vars\":"<<solver.bool_var_vec.size()<<",\"clauses\":"<<solver._num_clauses<<",\"best_arithmetic_unsat\":"<<(solver.best_arithmetic_unsat==UINT64_MAX?uint64_t(solver.best_found_cost):solver.best_arithmetic_unsat)<<",\"bool_phase_steps\":"<<solver.bool_phase_steps<<",\"int_phase_steps\":"<<solver.int_phase_steps<<",\"phase_switches\":"<<solver.phase_switches<<",\"weight_updates\":"<<solver.weight_updates<<",\"tabu_rejections\":"<<solver.tabu_rejections<<",\"candidate_cache_hits\":"<<solver.candidate_cache_hits<<",\"candidate_cache_rebuilds\":"<<solver.candidate_cache_rebuilds<<",\"auxiliary_solvers\":\"disabled\",\"uf_observers\":"<<(uf?"true":"false")
           <<",\"interaction\":"<<(uf?nia::json_string(interaction):"null")<<",\"scoring\":"<<(uf?nia::json_string(scoring):"null")
           <<",\"validation_scope\":"<<nia::json_string(uf?"native_cnf_and_uf_congruence":"native_cnf")
           <<",\"uf_applications\":"<<solver.uf_applications.size()<<",\"uf_conflicts\":"<<solver.uf_conflicts
           <<",\"uf_proposals\":"<<solver.uf_proposals<<",\"uf_group_moves\":"<<solver.uf_group_moves<<",\"uf_separation_moves\":"<<solver.uf_separation_moves
           <<",\"uf_max_patch\":"<<solver.uf_max_patch<<",\"uf_trial_applications\":"<<solver.uf_trial_applications<<",\"uf_bucket_rebuilds\":"<<solver.uf_bucket_rebuilds
           <<",\"uf_joint_evaluations\":"<<solver.uf_joint_evaluations<<",\"uf_single_evaluations\":"<<solver.uf_single_evaluations<<",\"uf_escape_moves\":"<<solver.uf_escape_moves<<",\"uf_linear_rows\":"<<solver.uf_linear_rows.size()<<",\"uf_compensation_proposals\":"<<solver.uf_compensation_proposals<<",\"uf_compensated_moves\":"<<solver.uf_compensated_moves
           <<",\"uf_search_policy\":"<<nia::json_string(uf?"adaptive_tabu_v12":"legacy_v1")
           <<",\"uf_only_iterations\":"<<solver.uf_only_iterations<<",\"uf_only_weight_updates\":"<<solver.uf_only_weight_updates<<",\"uf_weight\":"<<solver.uf_weight
           <<",\"uf_tabu_rejections\":"<<solver.uf_tabu_rejections<<",\"uf_aspirations\":"<<solver.uf_aspirations<<",\"uf_tabu_overrides\":"<<solver.uf_tabu_overrides<<",\"uf_tabu_waits\":"<<solver.uf_tabu_waits
           <<",\"search_restarts\":"<<(solver.initialization_round?solver.initialization_round-1:0)
           <<",\"current_arithmetic_unsat\":"<<(solver.initialization_round?std::to_string(solver.unsat_clauses->size()):"null")
           <<",\"current_uf_conflicts\":"<<(solver.initialization_round?std::to_string(solver.uf_conflicts):"null")
           <<",\"best_total_unsat\":"<<(solver.best_total_arithmetic_unsat==UINT64_MAX?"null":std::to_string(solver.best_total_arithmetic_unsat+solver.best_total_uf_conflicts))
           <<",\"best_total_arithmetic_unsat\":"<<(solver.best_total_arithmetic_unsat==UINT64_MAX?"null":std::to_string(solver.best_total_arithmetic_unsat))
           <<",\"best_total_uf_conflicts\":"<<(solver.best_total_uf_conflicts==UINT64_MAX?"null":std::to_string(solver.best_total_uf_conflicts))<<"}\n";
  return 0;
 }catch(const std::exception& e){std::cerr<<"{\"status\":\"error\",\"backend\":\"mature_mpz\",\"backend_version\":\""<<version<<"\",\"error\":"<<nia::json_string(e.what())<<"}\n";return 2;}
}
