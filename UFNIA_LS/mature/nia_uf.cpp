// UF congruence feedback over native typed variables. Bucket mechanics are
// adapted from mpz_sls.h; arithmetic commits retain the mature watch/CSR loop.
#include "nia_ls.h"
#include <stdexcept>
namespace nia {
Int ls_solver::uf_variable_value(size_t var)const{return _vars[var].is_nia?_solution[var]:Int(_solution[var]>0?1:0);}
ls_solver::UfValue ls_solver::evaluate_uf_application(size_t id)const{
 const auto& app=uf_applications[id];UfValue value;value.key.function=app.function;
 for(size_t v:app.arguments)value.key.arguments.push_back(uf_variable_value(v));value.result=uf_variable_value(app.result);return value;
}
void ls_solver::insert_uf_application(size_t id,const UfValue& value){auto& b=uf_buckets[value.key];uf_conflicts-=b.conflicts();b.members.insert(id);b.members_by_result[value.result].insert(id);b.change_frequency(value.result,1);uf_conflicts+=b.conflicts();if(b.conflicts())uf_conflicting_keys.insert(value.key);else uf_conflicting_keys.erase(value.key);}
void ls_solver::remove_uf_application(size_t id,const UfValue& value){auto it=uf_buckets.find(value.key);if(it==uf_buckets.end())throw std::logic_error("missing UF bucket");auto& b=it->second;uf_conflicts-=b.conflicts();b.members.erase(id);auto result_members=b.members_by_result.find(value.result);if(result_members==b.members_by_result.end())throw std::logic_error("missing UF result class");result_members->second.erase(id);if(result_members->second.empty())b.members_by_result.erase(result_members);b.change_frequency(value.result,-1);uf_conflicts+=b.conflicts();if(b.conflicts())uf_conflicting_keys.insert(value.key);else uf_conflicting_keys.erase(value.key);if(b.members.empty())uf_buckets.erase(it);}
void ls_solver::prepare_uf(){
 variable_applications.assign(_num_vars,{});
 uf_variable_rows.assign(_num_vars,{});uf_linear_rows.clear();
 for(const auto& clause:_clauses){if(clause.literals.size()!=1||clause.literals[0]<=0)continue;const auto& l=_lits[clause.literals[0]];if(!l.is_nia_lit||!l.is_equal||l.coff_terms.size()<2||l.coff_terms.size()>16)continue;UfLinearRow row;row.key=l.key;row.literal=clause.literals[0];bool linear=true;for(const auto& ct:l.coff_terms){const auto& t=_terms[ct.term_idx];if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1){linear=false;break;}row.terms.emplace_back(t.var_epxs[0].var_index,ct.coff);}if(!linear)continue;size_t id=uf_linear_rows.size();uf_linear_rows.push_back(std::move(row));for(const auto& [v,c]:uf_linear_rows.back().terms){(void)c;uf_variable_rows[v].push_back(id);}}
 for(size_t id=0;id<uf_applications.size();++id){std::set<size_t> vars(uf_applications[id].arguments.begin(),uf_applications[id].arguments.end());vars.insert(uf_applications[id].result);for(size_t v:vars)variable_applications[v].push_back(id);}
}
void ls_solver::initialize_uf(){uf_values.clear();uf_buckets.clear();uf_conflicting_keys.clear();uf_conflicts=0;uf_weight=1;++uf_bucket_rebuilds;for(size_t id=0;id<uf_applications.size();++id){auto v=evaluate_uf_application(id);uf_values.push_back(v);insert_uf_application(id,v);}verify_uf_state();}
void ls_solver::commit_uf_variable(size_t var){for(size_t id:variable_applications[var]){auto value=evaluate_uf_application(id);remove_uf_application(id,uf_values[id]);uf_values[id]=value;insert_uf_application(id,value);}verify_uf_state();}
bool ls_solver::valid_uf_patch(const UfPatch& patch)const{
 if(patch.empty()||patch.size()>64)return false;
 for(const auto& [id,value]:patch){const auto& v=_vars[id];if(!v.is_nia){if(value!=1&&value!=-1)return false;continue;}if((v.low_bound&&value<*v.low_bound)||(v.upper_bound&&value>*v.upper_bound))return false;if(_pre_value_1[id]&&value!=*_pre_value_1[id]&&(!_pre_value_2[id]||value!=*_pre_value_2[id]))return false;}return true;
}
MoveScore ls_solver::uf_gain(const UfPatch& patch,uint64_t* future_conflicts){
 if(!uf_enabled){if(future_conflicts)*future_conflicts=0;return 0;}
 if(uf_interaction=="none"&&!future_conflicts)return 0;
 UfPatch previous;std::set<size_t> touched;for(const auto& [v,value]:patch){previous[v]=_solution[v];_solution[v]=value;touched.insert(variable_applications[v].begin(),variable_applications[v].end());}
 const uint64_t before=uf_conflicts;std::vector<std::pair<size_t,UfValue>> old;
 auto restore=[&](){for(auto it=old.rbegin();it!=old.rend();++it){remove_uf_application(it->first,uf_values[it->first]);uf_values[it->first]=it->second;insert_uf_application(it->first,it->second);}for(const auto& [v,value]:previous)_solution[v]=value;verify_uf_state();};
 try{for(size_t id:touched){auto value=evaluate_uf_application(id);old.emplace_back(id,uf_values[id]);remove_uf_application(id,uf_values[id]);uf_values[id]=value;insert_uf_application(id,value);++uf_trial_applications;}if(future_conflicts)*future_conflicts=uf_conflicts;MoveScore gain=uf_interaction=="none"?0:(MoveScore(before)-MoveScore(uf_conflicts))*MoveScore(uf_weight);restore();return gain;}catch(...){restore();throw;}
}
MoveScore ls_solver::uf_single_gain(size_t var,const Int& target){++uf_single_evaluations;
 if(affine_installed()&&affine_component[var]>=0){AffineTime timing(&affine_score_ns);UfPatch patch;if(!normalize_affine_patch({{var,target}},patch)||!preflight_affine_patch(patch))return INT64_MIN/4;return uf_gain(patch);}
 return uf_gain({{var,target}});
}
ls_solver::UfPatch ls_solver::compensate_uf_patch(const UfPatch& seed)const{
 if(affine_patch_touches(seed)){
  UfPatch patch,canonical;if(!normalize_affine_patch(seed,patch,&canonical))return seed;
  if(uf_interaction!="joint"||canonical.size()>4)return patch;
  std::vector<size_t> pending;std::set<size_t> seen;
  auto enqueue=[&](size_t v){for(size_t ri:uf_variable_rows[v]){if(pending.size()>=12)break;size_t li=uf_linear_rows[ri].literal;if(li<affine_defining_literal.size()&&affine_defining_literal[li])continue;if(seen.insert(ri).second)pending.push_back(ri);}};
  for(const auto& [v,x]:patch){(void)x;enqueue(v);}
  auto value=[&](size_t v)->const Int&{auto p=patch.find(v);return p==patch.end()?_solution[v]:p->second;};
  for(size_t pos=0;pos<pending.size()&&pos<12;++pos){const auto& row=uf_linear_rows[pending[pos]];
   bool bounded=affine::bits(row.key)<=4096;for(const auto& [v,c]:row.terms)if(affine::bits(c)>4096||affine::bits(value(v))>4096)bounded=false;if(!bounded)return patch;
   Int residual=row.key;for(const auto& [v,c]:row.terms)residual+=c*value(v);if(residual==0)continue;
   bool fixed=false;for(int pass=0;pass<2&&!fixed;++pass)for(const auto& [v,c]:row.terms){
    if(affine_component[v]>=0||canonical.count(v)||c==0||(pass==0&&c!=1&&c!=-1)||residual%c!=0)continue;
    size_t free_vars=0;for(const auto& [id,x]:patch){(void)x;if(affine_component[id]<0)++free_vars;}
    if(!patch.count(v)&&free_vars>=16)continue;Int target=value(v)-residual/c;if(!valid_uf_patch({{v,target}}))continue;
    patch[v]=target;enqueue(v);fixed=true;break;
   }
  }
  UfPatch normalized;if(!normalize_affine_patch(patch,normalized))return seed;return normalized;
 }

 if(uf_interaction!="joint"||seed.size()>4)return seed;
 UfPatch patch=seed;std::vector<size_t> pending;std::set<size_t> seen;
 auto enqueue=[&](size_t var){for(size_t row:uf_variable_rows[var]){if(pending.size()>=12)break;if(seen.insert(row).second)pending.push_back(row);}};
 for(const auto& [var,value]:seed){(void)value;enqueue(var);}
 auto value=[&](size_t var)->const Int&{auto found=patch.find(var);return found==patch.end()?_solution[var]:found->second;};
 for(size_t pos=0;pos<pending.size()&&pos<12;++pos){const auto& row=uf_linear_rows[pending[pos]];Int residual=row.key;for(const auto& [var,coff]:row.terms)residual+=coff*value(var);if(residual==0)continue;
  bool repaired=false;
  for(int pass=0;pass<2&&!repaired;++pass)for(const auto& [var,coff]:row.terms){
   if(seed.count(var)||coff==0||(pass==0&&coff!=1&&coff!=-1)||residual%coff!=0)continue;
   if(!patch.count(var)&&patch.size()>=16)continue;
   Int desired=value(var)-residual/coff;UfPatch component{{var,desired}};if(!valid_uf_patch(component))continue;
   patch[var]=desired;enqueue(var);repaired=true;break;
  }
 }
 return patch;
}
std::vector<ls_solver::UfCandidate> ls_solver::uf_candidates(){
 AffineTime timing(affine_installed()?&affine_candidate_ns:nullptr);
 std::vector<UfCandidate> groups,singles,separations;
 if(uf_interaction=="none"||uf_conflicting_keys.empty())return {};
 auto add_raw=[&](std::vector<UfCandidate>& pool,size_t limit,UfPatch p,bool separation,bool compensated){
  if(affine_patch_touches(p)){
   UfPatch normalized;if(!normalize_affine_patch(p,normalized)){++affine_rejections;return;}
   bool view=false;for(const auto& [v,x]:normalized){(void)x;if(affine_component[v]>=0){view=true;break;}}
   if(view&&!preflight_affine_patch(normalized)){++affine_rejections;return;}p=std::move(normalized);
  }
  for(auto it=p.begin();it!=p.end();)if(_solution[it->first]==it->second)it=p.erase(it);else ++it;
  if(!valid_uf_patch(p)||pool.size()>=limit)return;
  for(const auto& prior:pool)if(prior.patch==p)return;
  pool.push_back({std::move(p),separation,compensated});++uf_proposals;if(compensated)++uf_compensation_proposals;
 };
 auto add=[&](std::vector<UfCandidate>& pool,size_t limit,const UfPatch& seed,bool separation){
  if(affine_patch_touches(seed)){
   if(pool.size()>=limit)return;UfPatch base;if(!normalize_affine_patch(seed,base)){++affine_rejections;return;}
   UfPatch compensated=compensate_uf_patch(base);if(compensated!=base)add_raw(pool,limit,std::move(compensated),separation,true);add_raw(pool,limit,base,separation,false);
  }else{UfPatch compensated=compensate_uf_patch(seed);if(compensated!=seed)add_raw(pool,limit,std::move(compensated),separation,true);add_raw(pool,limit,seed,separation,false);}
 };
 std::vector<UfKey> keys(uf_conflicting_keys.begin(),uf_conflicting_keys.end());size_t start=mt()%keys.size();
 for(size_t group=0;group<std::min(size_t(3),keys.size());++group){
  const auto& bucket=uf_buckets.at(keys[(start+group)%keys.size()]);
  std::vector<std::pair<unsigned,Int>> targets;for(const auto& [value,n]:bucket.frequencies)targets.emplace_back(n,value);
  std::stable_sort(targets.begin(),targets.end(),[](const auto& a,const auto& b){return a.first>b.first;});if(targets.size()>3)targets.resize(3);
  for(const auto& [n,target]:targets){
   (void)n;UfPatch patch;bool complete=true;size_t single_count=0;
   // Skip already-correct result classes; a late minority is never hidden by
   // an arbitrary prefix of the dominant class. The bound limits changes.
   for(const auto& [value,members]:bucket.members_by_result){
    if(value==target)continue;
    for(size_t id:members){size_t var=uf_applications[id].result;Int desired=_vars[var].is_nia?target:Int(target!=0?1:-1);
     if(++single_count<=2)add(singles,4,{{var,desired}},false);
     patch[var]=desired;if(patch.size()>64){complete=false;break;}
    }
    if(!complete)break;
   }
   if(complete&&uf_interaction=="joint")add(groups,6,std::move(patch),false);
  }
  // Distinct result classes receive representatives, minority classes first.
  // Separate pool quotas prevent result proposals consuming these slots.
  std::vector<std::pair<size_t,const std::set<size_t>*>> classes;
  for(const auto& [value,members]:bucket.members_by_result){(void)value;classes.emplace_back(members.size(),&members);}
  std::stable_sort(classes.begin(),classes.end(),[](const auto& a,const auto& b){return a.first<b.first;});
  for(size_t ci=0;ci<std::min(size_t(3),classes.size());++ci){size_t sampled=0;
   for(size_t id:*classes[ci].second){if(++sampled>4)break;const auto& app=uf_applications[id];
    for(size_t ai=0;ai<std::min(size_t(4),app.arguments.size());++ai){size_t var=app.arguments[ai];
     if(_vars[var].is_nia){
      if(affine_installed()&&affine_component[var]>=0){const auto& view=affine_views[affine_component[var]];size_t coordinate=view.root;
       if(view.domain.values){for(const auto& value:*view.domain.values)if(value!=_solution[coordinate])add(separations,8,{{coordinate,value}},true);}
       else for(int sign:{-1,1}){auto value=affine::project(view.domain,Int(_solution[coordinate]+sign));if(value&&*value!=_solution[coordinate])add(separations,8,{{coordinate,*value}},true);}
      }else{add(separations,8,{{var,Int(_solution[var]-1)}},true);add(separations,8,{{var,Int(_solution[var]+1)}},true);}
     }else add(separations,8,{{var,Int(-_solution[var])}},true);
     // A materialized p=2*x argument has no feasible p+/-1 reconstruction.
     // Move a small number of source variables, then repair the unit output.
     if(uf_interaction=="joint"&&_vars[var].is_nia&&(!affine_installed()||affine_component[var]<0)){size_t rows=0;
      for(size_t ri:uf_variable_rows[var]){if(++rows>2)break;const auto& row=uf_linear_rows[ri];bool output=false;for(const auto& [v,c]:row.terms)if(v==var&&(c==1||c==-1))output=true;if(!output)continue;size_t sources=0;
       for(const auto& [source,c]:row.terms){(void)c;if(source==var)continue;if(++sources>2)break;add(separations,8,{{source,Int(_solution[source]-1)}},true);add(separations,8,{{source,Int(_solution[source]+1)}},true);}
      }
     }
    }
   }
  }
 }
 groups.insert(groups.end(),separations.begin(),separations.end());groups.insert(groups.end(),singles.begin(),singles.end());return groups;
}
MoveScore ls_solver::score_uf_patch(const UfPatch& requested,uint64_t* future_cnf,uint64_t* future_uf,MoveScore* uf_component,uint64_t* weighted_break){
 if(uf_component)*uf_component=0;if(weighted_break)*weighted_break=0;
 UfPatch expanded;const bool view_trial=affine_patch_touches(requested);
 AffineTime timing(view_trial?&affine_score_ns:nullptr);if(view_trial)++affine_scores;
 if(view_trial){bool same=true;for(const auto& [v,x]:requested)if(x!=_solution[v]){same=false;break;}if(same){if(future_cnf)*future_cnf=unsat_clauses->size();if(future_uf)*future_uf=uf_conflicts;return 0;}}
 if(view_trial&&(!normalize_affine_patch(requested,expanded)||!preflight_affine_patch(expanded))){++affine_rejections;if(future_cnf)*future_cnf=unsat_clauses->size();if(future_uf)*future_uf=uf_conflicts;return INT64_MIN/4;}
 const UfPatch& patch=view_trial?expanded:requested;

 ++uf_joint_evaluations;const MoveScore uf_score=uf_gain(patch,future_uf);if(uf_component)*uf_component=uf_score;MoveScore score=uf_score;uint64_t raw_cnf=unsat_clauses->size();UfPatch old;std::set<int> clauses;
 for(const auto& [v,value]:patch){old[v]=_solution[v];_solution[v]=value;clauses.insert(_vars[v].clause_idxs.begin(),_vars[v].clause_idxs.end());}
 auto restore=[&](){for(const auto& [v,value]:old)_solution[v]=value;};
 try{std::unordered_map<size_t,Int> values;
  std::set<size_t> changed_literals;std::map<size_t,bool> literal_truth;
  if(view_trial)for(const auto& [v,x]:patch){(void)x;changed_literals.insert(_vars[v].literal_idxs.begin(),_vars[v].literal_idxs.end());}
  auto fresh_truth=[&](int li){const auto& l=_lits[std::abs(li)];bool yes;if(!l.is_nia_lit)yes=_solution[l.bool_var]>0;else{Int residual=l.key;for(const auto& ct:l.coff_terms){auto found=values.find(ct.term_idx);if(found==values.end()){Int value=1;for(const auto& ve:_terms[ct.term_idx].var_epxs)value*=integer_power(_solution[ve.var_index],ve.exponent);found=values.emplace(ct.term_idx,std::move(value)).first;}residual+=ct.coff*found->second;}yes=l.is_equal?residual==0:residual<=0;}return yes==(li>0);};
  auto truth=[&](int li){size_t id=std::abs(li);if(!view_trial)return fresh_truth(li);if(!changed_literals.count(id))return (_lits[id].is_true>0)==(li>0);auto found=literal_truth.find(id);if(found==literal_truth.end())found=literal_truth.emplace(id,fresh_truth(int(id))).first;return found->second==(li>0);};
  auto score_clauses=[&](const auto& affected){
   for(int id:affected){const auto& c=_clauses[id];bool sat=false;for(int li:c.literals)if(truth(li)){sat=true;break;}if(c.sat_count==0&&sat){score+=c.weight;--raw_cnf;}else if(c.sat_count>0&&!sat){score-=c.weight;++raw_cnf;if(weighted_break)*weighted_break+=uint64_t(c.weight);}}
  };
  score_clauses(clauses);
#if defined(NIA_SCORING_VERIFY) || defined(NIA_FAST_MOVE_VERIFY)
  MoveScore full_score=uf_score;uint64_t full_unsat=0;
  for(const auto& c:_clauses){bool sat=false;for(int li:c.literals)if(fresh_truth(li)){sat=true;break;}if(!sat)++full_unsat;if(c.sat_count==0&&sat)full_score+=c.weight;else if(c.sat_count>0&&!sat)full_score-=c.weight;}
  if(score!=full_score||raw_cnf!=full_unsat)throw std::logic_error("UF joint CNF union score mismatch");
#endif
  if(future_cnf)*future_cnf=raw_cnf;restore();return score;
 }catch(...){restore();throw;}
}
bool ls_solver::uf_patch_tabu(const UfPatch& patch)const{
 UfPatch expanded,canonical;const UfPatch* tested=&patch;
 if(affine_patch_touches(patch)){if(!normalize_affine_patch(patch,expanded,&canonical))return true;tested=&canonical;}
 for(const auto& [var,target]:*tested){
  if(target==_solution[var])continue;
  if(!_vars[var].is_nia){if(_outer_layer_step<=tabulist[2*var])return true;}
  else{Int delta=target-_solution[var];size_t direction=delta>0?0:1;
   if(_step<tabulist[2*var+direction] || (var==last_op_var&&delta==-last_op_value))return true;
  }
 }
 return false;
}
bool ls_solver::try_uf_move(bool allow_escape){
 uf_move_waiting=false;
 if(uf_interaction=="none")return false;
 auto candidates=uf_candidates();if(candidates.empty())return false;
 struct Trial {MoveScore gain;uint64_t cnf,uf;bool tabu,aspire;};
 std::vector<Trial> trials;std::vector<size_t> admissible,blocked;
 size_t best=0;MoveScore gain=INT64_MIN;uint64_t ties=0;
 auto evaluate=[&](){
  trials.clear();admissible.clear();blocked.clear();gain=INT64_MIN;ties=0;
  for(size_t i=0;i<candidates.size();++i){Trial trial{};
   trial.gain=score_uf_patch(candidates[i].patch,&trial.cnf,&trial.uf);
   trial.tabu=uf_patch_tabu(candidates[i].patch);
   trial.aspire=(trial.cnf==0&&trial.uf==0)||trial.cnf+trial.uf<uint64_t(best_found_cost);
   trials.push_back(trial);
   if(trial.tabu&&!trial.aspire){++uf_tabu_rejections;blocked.push_back(i);continue;}
   admissible.push_back(i);
   if(trial.gain>gain){gain=trial.gain;best=i;ties=1;}
   else if(trial.gain==gain&&mt()%++ties==0)best=i;
  }
 };
 evaluate();
 // A pure UF plateau has no false CNF clause to trigger ordinary PAWS. Raise
 // real UF pressure here, then recompute every winner/aspiration at that weight.
 if(allow_escape&&unsat_clauses->empty()&&(admissible.empty()||gain<=0)){
  uint64_t previous=uf_weight;update_clause_weight();
  if(uf_weight!=previous){++uf_only_weight_updates;evaluate();}
 }
 bool override_tabu=false;
 if(admissible.empty()){
  if(!allow_escape)return false;
  // Boolean tabu uses its own clock. Waiting still ages that clock, and an
  // explicit bounded override prevents starvation with unusually long tenures.
  ++uf_tabu_waits;++uf_consecutive_tabu_waits;++_outer_layer_step;
  if(uf_consecutive_tabu_waits<8){uf_move_waiting=true;return false;}
  uf_consecutive_tabu_waits=0;++uf_tabu_overrides;override_tabu=true;
  best=blocked[mt()%blocked.size()];gain=trials[best].gain;
 }else{
  uf_consecutive_tabu_waits=0;
  if(gain<=0){if(!allow_escape)return false;best=admissible[mt()%admissible.size()];gain=trials[best].gain;}
 }
 const auto& chosen=candidates[best];
 if(trials[best].tabu&&trials[best].aspire&&!override_tabu)++uf_aspirations;
 uf_max_patch=std::max(uf_max_patch,uint64_t(chosen.patch.size()));
 if(chosen.compensated)++uf_compensated_moves;
 if(chosen.separation)++uf_separation_moves;else if(chosen.patch.size()>1)++uf_group_moves;
 if(gain<=0||override_tabu)++uf_escape_moves;
 if(affine_patch_touches(chosen.patch)){if(!commit_affine_patch(chosen.patch))return false;}
 else for(const auto& [var,value]:chosen.patch){if(value==_solution[var])continue;critical_move(var,_vars[var].is_nia?Int(value-_solution[var]):Int(0));}
 return true;
}
void ls_solver::verify_uf_state()const{
#if defined(NIA_SCORING_VERIFY) || defined(NIA_CANDIDATE_VERIFY) || defined(NIA_FAST_MOVE_VERIFY)
 std::map<UfKey,std::map<Int,std::set<size_t>>> expected;uint64_t conflicts=0;
 for(size_t id=0;id<uf_applications.size();++id){auto value=evaluate_uf_application(id);const auto& cached=uf_values[id];if(value.key<cached.key||cached.key<value.key||value.result!=cached.result)throw std::logic_error("UF value cache mismatch");expected[value.key][value.result].insert(id);}
 if(expected.size()!=uf_buckets.size())throw std::logic_error("UF bucket count mismatch");
 for(const auto& [key,classes]:expected){const auto& bucket=uf_buckets.at(key);size_t total=0,largest=0;for(const auto& [value,members]:classes){total+=members.size();largest=std::max(largest,members.size());if(bucket.frequencies.at(value)!=members.size())throw std::logic_error("UF frequency mismatch");}if(classes!=bucket.members_by_result||bucket.members.size()!=total||bucket.conflicts()!=total-largest)throw std::logic_error("UF membership mismatch");conflicts+=total-largest;}
 if(conflicts!=uf_conflicts)throw std::logic_error("UF conflict delta mismatch");
#endif
}
bool ls_solver::validate_uf()const{std::map<UfKey,Int> representatives;for(size_t id=0;id<uf_applications.size();++id){auto v=evaluate_uf_application(id);auto inserted=representatives.emplace(v.key,v.result);if(!inserted.second&&inserted.first->second!=v.result)return false;}return true;}
}
