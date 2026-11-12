#include "nia_ls.h"
#include <stdexcept>
namespace nia {
namespace {
struct Footprint {
 std::set<size_t> terms,literals,clauses,apps,score_literals,score_terms;
 size_t raw_slots=0,repeat_coeff_slots=0,all_coeff_slots=0;
};
constexpr size_t max_variables=262144,max_input_clauses=1048576,max_unit_edges=131072,max_mapped=65536;
constexpr size_t max_terms=16,max_literals=128,max_clauses=256,max_apps=64,max_raw_slots=8192;
constexpr size_t max_score_literals=8192,max_score_terms=4096,max_factor_slots=65536,max_coeff_slots=65536;
constexpr size_t max_macro_components=4,max_native_patch=64,max_uf_table=131072;
constexpr size_t input_bits=4096,intermediate_bits=16384,max_exponent=64;
struct Limit {bool numeric=false;};

size_t nbits(const Int& x){return affine::bits(x);}
Footprint footprint(const ls_solver& s,const std::vector<size_t>& vars,size_t multiplier=1){
 Footprint f;
 for(size_t v:vars){
  const auto& a=s._vars[v];
  if(a.term_exps.size()>max_terms*multiplier||a.literal_idxs.size()>max_literals*multiplier||a.clause_idxs.size()>max_clauses*multiplier)throw Limit{};
  for(const auto& te:a.term_exps){f.terms.insert(te.term_idx);f.raw_slots+=s._terms[te.term_idx].var_epxs.size();}
  f.raw_slots+=a.var_lit_terms.size();
  f.literals.insert(a.literal_idxs.begin(),a.literal_idxs.end());
  for(int c:a.clause_idxs){f.clauses.insert(size_t(c));f.raw_slots+=s._clauses[c].literals.size();}
  if(s.uf_enabled){
   if(s.variable_applications[v].size()>max_apps*multiplier)throw Limit{};
   for(size_t id:s.variable_applications[v]){f.apps.insert(id);f.raw_slots+=s.uf_applications[id].arguments.size()+1;}
  }
  if(f.raw_slots>max_raw_slots*multiplier)throw Limit{};
 }
 if(f.terms.size()>max_terms*multiplier||f.literals.size()>max_literals*multiplier||f.clauses.size()>max_clauses*multiplier||f.apps.size()>max_apps*multiplier)throw Limit{};
 f.score_literals=f.literals;f.score_terms=f.terms;
 for(size_t c:f.clauses)for(int li:s._clauses[c].literals){size_t id=std::abs(li);f.score_literals.insert(id);f.repeat_coeff_slots+=s._lits[id].coff_terms.size();if(f.repeat_coeff_slots>max_coeff_slots*multiplier)throw Limit{};}
 if(f.score_literals.size()>max_score_literals*multiplier)throw Limit{};
 for(size_t li:f.score_literals){const auto& l=s._lits[li];
  if(l.coff_terms.size()>max_coeff_slots*multiplier-f.all_coeff_slots)throw Limit{};
  f.all_coeff_slots+=l.coff_terms.size();
  for(const auto& ct:l.coff_terms){f.score_terms.insert(ct.term_idx);if(f.score_terms.size()>max_score_terms*multiplier)throw Limit{};}
 }
 size_t factors=0;for(size_t t:f.score_terms){factors+=s._terms[t].var_epxs.size();if(factors>max_factor_slots*multiplier)throw Limit{};}
 return f;
}
void numeric_preflight(const ls_solver& s,const Footprint& f,const ls_solver::UfPatch& p,const std::vector<size_t>* extra=nullptr){
 std::map<size_t,size_t> term_bounds;
 for(size_t t:f.score_terms){
  size_t bound=1;const auto& term=s._terms[t];
  if(s.initialization_round&&nbits(term.value)>intermediate_bits)throw Limit{true};
  for(const auto& e:term.var_epxs){
   if(e.exponent<1||size_t(e.exponent)>max_exponent)throw Limit{true};
   size_t v=e.var_index,old_bits=nbits(s._solution[v]);auto target=p.find(v);
   size_t future=target==p.end()?old_bits:nbits(target->second);
   size_t magnitude=std::max(old_bits,future);if(extra)magnitude=std::max(magnitude,extra->at(v));
   if(magnitude>input_bits)throw Limit{true};
   size_t factor=std::max(size_t(1),magnitude)*size_t(e.exponent);
   if(factor>intermediate_bits||bound>intermediate_bits-factor)throw Limit{true};
   bound+=factor;
  }
  term_bounds[t]=bound;
 }
 for(size_t li:f.score_literals){
  const auto& l=s._lits[li];if(!l.is_nia_lit)continue;
  if(nbits(l.key)>input_bits||(s.initialization_round&&nbits(l.delta)>intermediate_bits))throw Limit{true};
  size_t maximum=nbits(l.key);
  for(const auto& ct:l.coff_terms){
   if(nbits(ct.coff)>input_bits)throw Limit{true};
   size_t a=term_bounds.at(ct.term_idx),b=nbits(ct.coff);if(a>intermediate_bits-b)throw Limit{true};maximum=std::max(maximum,a+b);
  }
  size_t plus=0,n=l.coff_terms.size()+1;while(n>1){n=(n+1)/2;++plus;}if(maximum>intermediate_bits-plus)throw Limit{true};
 }
 if(s.uf_enabled){
  if(s.uf_applications.size()>max_uf_table)throw Limit{true};
  for(size_t id:f.apps){const auto& app=s.uf_applications[id];if(app.arguments.size()+1>max_raw_slots)throw Limit{true};
   auto check=[&](size_t v){auto t=p.find(v);if(nbits(s._solution[v])>input_bits||(t!=p.end()&&nbits(t->second)>input_bits)||(extra&&extra->at(v)>input_bits))throw Limit{true};};
   for(size_t v:app.arguments)check(v);check(app.result);
  }
 }
}
std::vector<size_t> variables(const ls_solver::UfPatch& p){std::vector<size_t> v;for(const auto& [id,x]:p){(void)x;v.push_back(id);}return v;}
bool scalar_domain(const ls_solver& s,size_t v,const Int& x){
 const auto& a=s._vars[v];if(!a.is_nia)return x==1||x==-1;
 if((a.low_bound&&x<*a.low_bound)||(a.upper_bound&&x>*a.upper_bound))return false;
 return !s._pre_value_1[v]||x==*s._pre_value_1[v]||(s._pre_value_2[v]&&x==*s._pre_value_2[v]);
}
affine::Domain search_domain(const ls_solver& s,size_t v){
 affine::Domain domain;
 if(s.affine_component[v]>=0)domain=s.affine_views[s.affine_component[v]].domain;
 else{domain.lower=s._vars[v].low_bound;domain.upper=s._vars[v].upper_bound;if(s._pre_value_1[v]){domain.values=std::vector<Int>{*s._pre_value_1[v]};if(s._pre_value_2[v])domain.values->push_back(*s._pre_value_2[v]);}}
 return domain;
}
bool coordinate_has_alternative(const ls_solver& s,size_t v){
 const auto domain=search_domain(s,v);
 const Int& current=s._solution[v];if(domain.values){for(const auto& x:*domain.values)if(x!=current&&affine::contains(domain,x))return true;return false;}
 for(int direction:{-1,1}){auto x=affine::project(domain,Int(current+direction));if(x&&*x!=current)return true;}return false;
}
bool multiply_add(const Int& a,const Int& x,const Int& b,Int& out){
 if(nbits(a)>input_bits||nbits(x)>input_bits||nbits(b)>input_bits||nbits(a)+nbits(x)>intermediate_bits)return false;
 Int product=a*x;if(std::max(nbits(product),nbits(b))+1>intermediate_bits)return false;out=product+b;return nbits(out)<=input_bits;
}
}
void ls_solver::prepare_affine_views(){
 if(!affine_enabled)return;
 AffineTime timing(&affine_build_ns);
 if(_num_vars>max_variables||_clauses.size()>max_input_clauses||(uf_enabled&&uf_applications.size()>max_uf_table)){++affine_skip_footprint;return;}
 std::vector<affine::Domain> domains(_num_vars);
 std::vector<Int> plain(_num_vars);
 for(size_t v=0;v<_num_vars;++v){
  const auto& a=_vars[v];domains[v].lower=a.low_bound;domains[v].upper=a.upper_bound;
  if(_pre_value_1[v]){domains[v].values=std::vector<Int>{*_pre_value_1[v]};if(_pre_value_2[v])domains[v].values->push_back(*_pre_value_2[v]);}
  Int x=a.is_nia?Int(0):Int(-1);if(a.is_nia){if(a.low_bound&&x<*a.low_bound)x=*a.low_bound;if(a.upper_bound&&x>*a.upper_bound)x=*a.upper_bound;if(_pre_value_1[v])x=*_pre_value_1[v];}plain[v]=x;
 }
 std::vector<affine::Row> rows;std::set<size_t> seen;
 for(const auto& c:_clauses){
  if(c.literals.size()!=1||c.literals[0]<=0)continue;size_t id=c.literals[0];const auto& l=_lits[id];
  if(!seen.insert(id).second||!l.is_nia_lit||!l.is_equal||l.coff_terms.size()!=2)continue;
  bool good=true;size_t v[2];Int a[2];size_t i=0;
  for(const auto& ct:l.coff_terms){const auto& t=_terms[ct.term_idx];if(t.var_epxs.size()!=1||t.var_epxs[0].exponent!=1||ct.coff==0){good=false;break;}v[i]=t.var_epxs[0].var_index;a[i++]=ct.coff;}
  if(!good||v[0]==v[1]||(a[0]!=1&&a[0]!=-1&&a[1]!=1&&a[1]!=-1))continue;
  rows.push_back({v[0],v[1],a[0],a[1],l.key,id});if(rows.size()>max_unit_edges){++affine_skip_footprint;return;}
 }
 auto built=affine::build_views(rows,domains);affine_build_components=built.components;affine_skip_size=built.skip_size;affine_skip_no_root=built.skip_no_root;affine_skip_domain=built.skip_domain;affine_skip_bits=built.skip_bits;
 affine_skip_math=built.skip_size+built.skip_no_root+built.skip_bits+built.skip_domain+built.skip_unsupported;
 std::vector<size_t> initial_bits(_num_vars);for(size_t v=0;v<_num_vars;++v)initial_bits[v]=nbits(plain[v]);
 std::vector<bool> candidate(built.views.size(),true);
 size_t proposed=0;
 for(size_t i=0;i<built.views.size();++i){
  const auto& view=built.views[i];if(proposed+view.members.size()>max_mapped){candidate[i]=false;++affine_skip_footprint;continue;}proposed+=view.members.size();
  auto root=affine::project(view.domain,plain[view.root]);if(!root){candidate[i]=false;++affine_skip_math;continue;}
  for(const auto& m:view.members){Int x;if(!multiply_add(m.a,*root,m.b,x)){candidate[i]=false;break;}initial_bits[m.variable]=std::max(initial_bits[m.variable],nbits(x));}
  if(!candidate[i])++affine_skip_numeric;
 }
 for(size_t i=0;i<built.views.size();++i)if(candidate[i]){
  const auto& view=built.views[i];std::vector<size_t> ids;for(const auto& m:view.members)ids.push_back(m.variable);
  try{auto f=footprint(*this,ids);numeric_preflight(*this,f,{},&initial_bits);affine_views.push_back(view);}catch(const Limit& failure){if(failure.numeric)++affine_skip_numeric;else ++affine_skip_footprint;}
 }
 if(affine_views.empty())return;
 affine_component.assign(_num_vars,-1);affine_member.resize(_num_vars);affine_literal.assign(_lits.size(),0);affine_defining_literal.assign(_lits.size(),0);
 for(const auto& view:affine_views)for(size_t li:view.selected_rows)affine_defining_literal[li]=1;
 for(size_t i=0;i<affine_views.size();++i)for(const auto& m:affine_views[i].members){affine_component[m.variable]=int(i);affine_member[m.variable]=m;for(size_t li:_vars[m.variable].literal_idxs)affine_literal[li]=1;if(m.variable!=affine_views[i].root)++affine_derived;}
}
void ls_solver::initialize_affine_values(){
 if(!affine_installed())return;
 for(const auto& view:affine_views){auto r=affine::project(view.domain,_solution[view.root]);if(!r)throw std::logic_error("installed affine domain unexpectedly empty");
  for(const auto& m:view.members){Int x;if(!multiply_add(m.a,*r,m.b,x))throw std::logic_error("installed affine initial value exceeds budget");_solution[m.variable]=x;}}
 if(!verify_affine())throw std::logic_error("affine initialization invariant");
}
bool ls_solver::normalize_affine_patch(const UfPatch& requests,UfPatch& materialized,UfPatch* canonical)const{
 materialized.clear();if(canonical)canonical->clear();if(requests.empty()||requests.size()>max_native_patch)return false;
 const bool view_request=affine_patch_touches(requests);
 UfPatch free;std::set<int> components;
 for(const auto& [v,target]:requests){
  if(v>=_num_vars||(view_request&&nbits(target)>input_bits))return false;size_t root=v;Int root_target=target;
  if(affine_installed()&&affine_component[v]>=0){int c=affine_component[v];components.insert(c);root=affine_views[c].root;
   auto inv=affine::inverse(affine_member[v],target);if(!inv||nbits(*inv)>input_bits||!affine::contains(affine_views[c].domain,*inv))return false;root_target=*inv;
  }else if(!scalar_domain(*this,v,target))return false;
  auto same=free.emplace(root,root_target);if(!same.second&&same.first->second!=root_target)return false;
 }
 if(components.size()>max_macro_components)return false;
 for(const auto& [v,target]:free){
  if(affine_installed()&&affine_component[v]>=0){const auto& view=affine_views[affine_component[v]];
   for(const auto& m:view.members){Int x;if(!multiply_add(m.a,target,m.b,x)||!scalar_domain(*this,m.variable,x))return false;materialized[m.variable]=x;}
  }else materialized[v]=target;
 }
 if(materialized.size()>max_native_patch)return false;
 for(const auto& [v,x]:requests){auto f=materialized.find(v);if(f==materialized.end()||f->second!=x)return false;}
 for(auto i=materialized.begin();i!=materialized.end();)if(i->second==_solution[i->first])i=materialized.erase(i);else ++i;
 for(auto i=free.begin();i!=free.end();)if(i->second==_solution[i->first])i=free.erase(i);else ++i;
 if(canonical)*canonical=std::move(free);
 return !materialized.empty();
}
bool ls_solver::preflight_affine_patch(const UfPatch& patch)const{
 if(patch.empty()||patch.size()>max_native_patch)return false;
 try{
  for(const auto& [v,x]:patch){
   if(v>=_num_vars||!scalar_domain(*this,v,x)||nbits(x)>input_bits)return false;
   if(config.strategy==SlsStrategy::corebip&&core_locked.size()>v&&core_locked[v])return false;
  }
  auto f=footprint(*this,variables(patch),max_macro_components);
  numeric_preflight(*this,f,patch);return true;
 }catch(const Limit&){return false;}
}
bool ls_solver::verify_affine()const{
 if(!affine_installed())return true;
 for(const auto& view:affine_views){if(!affine::contains(view.domain,_solution[view.root]))return false;
  for(const auto& m:view.members)if(_solution[m.variable]!=m.a*_solution[view.root]+m.b)return false;}
 return true;
}
affine::PolyResult ls_solver::effective_affine_polynomial(int literal,size_t root,size_t& work)const{
 const auto& l=_lits[std::abs(literal)];affine::Limits limits;
 if(!l.is_nia_lit||l.coff_terms.size()>128)return {affine::Status::unsupported,{}};
 std::vector<Int> sum{l.key};
 for(const auto& ct:l.coff_terms){
  if(++work>limits.work)return {affine::Status::budget,{}};
  std::vector<affine::Factor> factors;
  for(const auto& e:_terms[ct.term_idx].var_epxs){
   if(++work>limits.work)return {affine::Status::budget,{}};
   size_t v=e.var_index;
   if(affine_component[v]>=0&&affine_views[affine_component[v]].root==root){const auto& m=affine_member[v];factors.push_back({m.a,m.b,unsigned(e.exponent)});}
   else if(v==root)factors.push_back({1,0,unsigned(e.exponent)});
   else factors.push_back({0,_solution[v],unsigned(e.exponent)});
  }
  auto p=affine::expand_product(ct.coff,factors,limits,work);
  if(p.status!=affine::Status::ok)return p;
  if(sum.size()<p.coefficients.size())sum.resize(p.coefficients.size());
  for(size_t i=0;i<p.coefficients.size();++i){
   if(++work>limits.work||std::max(nbits(sum[i]),nbits(p.coefficients[i]))+1>intermediate_bits)return {affine::Status::budget,{}};
   sum[i]+=p.coefficients[i];
  }
 }
 while(sum.size()>1&&sum.back()==0)sum.pop_back();
 return {affine::Status::ok,std::move(sum)};
}

void ls_solver::generate_affine_operations(bool tabu,int signed_literal,int& count){
 AffineTime timing(&affine_generate_ns);
 const auto& l=_lits[std::abs(signed_literal)];if(!l.is_nia_lit)return;
 affine::Limits limits;size_t work=0,fallbacks=0;
 std::set<size_t> roots;
 auto fallback=[&](size_t root){
  if(fallbacks++>=8)return;
  auto insert=[&](const Int& x){insert_operation(root,Int(x-_solution[root]),count,tabu);++affine_proposals;};
  const auto d=search_domain(*this,root);
  if(d.values)for(const auto& x:*d.values)insert(x);
  else for(int sign:{-1,1}){auto x=affine::project(d,Int(_solution[root]+sign));if(x)insert(*x);}
 };
 for(const auto& term:l.var_lit_terms){
  if(++work>limits.work){++affine_poly_budget;for(size_t root:roots)fallback(root);return;}
  size_t v=term.var_idx;roots.insert(affine_component[v]>=0?affine_views[affine_component[v]].root:v);
 }
 for(size_t root:roots){
  auto polynomial=effective_affine_polynomial(signed_literal,root,work);
  if(polynomial.status==affine::Status::budget){++affine_poly_budget;fallback(root);break;}
  if(polynomial.status!=affine::Status::ok){++affine_poly_unsupported;fallback(root);continue;}
  std::vector<Int> sum=std::move(polynomial.coefficients);
  auto insert=[&](const Int& x){insert_operation(root,Int(x-_solution[root]),count,tabu);++affine_proposals;};
  if(sum.size()<=3){
   Int a=sum.size()>2?sum[2]:Int(0),b=sum.size()>1?sum[1]:Int(0),c=sum[0];
   if(a!=0){
    if(nbits(b)*2+2>intermediate_bits||nbits(a)+nbits(c)+3>intermediate_bits){++affine_poly_budget;fallback(root);continue;}
    Int d=b*b-4*a*c;if(d>=0){Int square;mpz_sqrt(square.get_mpz_t(),d.get_mpz_t());Int den=2*a;std::set<Int> values;
     for(int sign:{-1,1}){Int n=-b+sign*square,q;mpz_fdiv_q(q.get_mpz_t(),n.get_mpz_t(),den.get_mpz_t());for(int offset=-2;offset<=2;++offset)values.insert(Int(q+offset));}
     for(const auto& x:values){if(2*nbits(x)+nbits(a)+2>intermediate_bits||nbits(b)+nbits(x)+2>intermediate_bits)continue;Int r=a*x*x+b*x+c;bool truth=l.is_equal?r==0:r<=0;if(truth==(signed_literal>0))insert(x);}
    }
    if(l.is_equal&&signed_literal<0){insert(Int(_solution[root]+1));insert(Int(_solution[root]-1));}
   }else if(b!=0){
    if(l.is_equal){if(signed_literal>0){if(c%b==0)insert(Int(-c/b));}else{insert(Int(_solution[root]+1));insert(Int(_solution[root]-1));}}
    else{Int aa=signed_literal>0?b:Int(-b),cc=signed_literal>0?c:Int(1-c),x,n=-cc;
     if(aa>0)mpz_fdiv_q(x.get_mpz_t(),n.get_mpz_t(),aa.get_mpz_t());else mpz_cdiv_q(x.get_mpz_t(),n.get_mpz_t(),aa.get_mpz_t());insert(x);}
   }else{
    // Preserve v12's bounded zero-coefficient escape: a frozen zero in a
    // product can make every exact one-coordinate slice constant for now.
    fallback(root);
   }
  }else{++affine_poly_unsupported;fallback(root);}
  // A legal finite endpoint can satisfy an inequality beyond its first
  // boundary. Never leave {0,100} reachable only through forbidden +/-1.
  if(affine_component[root]>=0){const auto& d=affine_views[affine_component[root]].domain;if(d.values)for(const auto& x:*d.values)insert(x);}
 }
}

MoveScore ls_solver::evaluate_search_move(size_t v,const Int& delta,uint64_t* broken){
 if(!affine_installed()||affine_component[v]<0){MoveScore score=critical_score(v,delta,broken);if(uf_enabled&&uf_interaction!="none")score+=uf_single_gain(v,Int(_solution[v]+delta));return score;}
 latest_distance_gain=0;MoveScore uf_part=0;
 return score_uf_patch({{v,Int(_solution[v]+delta)}},nullptr,nullptr,&uf_part,broken);
}
void ls_solver::record_affine_search_move(size_t v,const Int& delta){
 if(_vars[v].is_nia){last_op_var=v;last_op_value=delta;size_t direction=delta>0?0:1;last_move[2*v+direction]=_step;tabulist[2*v+(direction+1)%2]=_step+3+mt()%10;}
 else{last_op_var=UINT64_MAX;last_move[2*v]=_outer_layer_step;tabulist[2*v]=_outer_layer_step+1+mt()%3;++_outer_layer_step;}
}
bool ls_solver::commit_affine_patch(const UfPatch& requests){
 if(!affine_patch_touches(requests)){bool changed=false;for(const auto& [v,target]:requests)if(target!=_solution[v]){critical_move(v,_vars[v].is_nia?Int(target-_solution[v]):Int(0));changed=true;}return changed;}
 AffineTime timing(&affine_commit_ns);
 UfPatch patch,canonical;if(!normalize_affine_patch(requests,patch,&canonical)||!preflight_affine_patch(patch)){++affine_rejections;return false;}
 UfPatch deltas;
 for(const auto& [v,target]:canonical)deltas[v]=target-_solution[v];
 for(const auto& [v,target]:patch)raw_critical_move(v,_vars[v].is_nia?Int(target-_solution[v]):Int(0),false);
 if(config.distance_scoring)++distance_current_generation;
 for(const auto& [v,delta]:deltas)record_affine_search_move(v,delta);
 if(_record_frequency)modify_frequency_in_unsat_clauses();
 if(!verify_affine())throw std::logic_error("affine macro broke installed view");
 ++affine_commits;return true;
}
bool ls_solver::affine_no_operation_random_walk(int signed_literal){
 if(!affine_installed())return false;AffineTime timing(&affine_generate_ns);
 const auto& l=_lits[std::abs(signed_literal)];
 if(!l.is_nia_lit){timing.stop();critical_move(l.bool_var,0);return true;}
 std::set<size_t> roots;std::vector<size_t> preferred,unknown;size_t work=0;
 for(const auto& t:l.var_lit_terms){
  if(++work>8192){++affine_poly_budget;break;}
  size_t v=t.var_idx;roots.insert(affine_component[v]>=0?affine_views[affine_component[v]].root:v);
 }
 for(size_t root:roots){
  if(!coordinate_has_alternative(*this,root))continue;
  auto p=effective_affine_polynomial(signed_literal,root,work);
  if(p.status==affine::Status::ok){if(p.coefficients.size()>1)preferred.push_back(root);}
  else{if(unknown.size()<8)unknown.push_back(root);if(p.status==affine::Status::budget){++affine_poly_budget;break;}++affine_poly_unsupported;}
 }
 // Known constant polynomials are excluded. Unsupported/budget fallbacks have
 // an independent fixed quota and are used only without a proven active root.
 const auto& choices=preferred.empty()?unknown:preferred;if(choices.empty())return true;
 size_t root=choices[mt()%choices.size()];Int target=0;
 const auto domain=search_domain(*this,root);auto value=affine::project(domain,target);if(!value)return true;target=*value;
 if(target==_solution[root]){if(domain.values){for(const auto& x:*domain.values)if(x!=target&&affine::contains(domain,x)){target=x;break;}}else{auto next=affine::project(domain,Int(target+1));if(next&&*next!=target)target=*next;else{next=affine::project(domain,Int(target-1));if(next)target=*next;}}}
 timing.stop();critical_move(root,Int(target-_solution[root]));return true;
}

void ls_solver::maybe_write_affine_progress(double elapsed,bool final){
 if(!affine_progress_enabled||affine_progress_path.empty())return;
 if(!final&&elapsed-affine_progress_last_seconds<30.0)return;
 affine_progress_last_seconds=elapsed;
 const std::string temporary=affine_progress_path+".tmp."+std::to_string(getpid());
 try{
  std::ofstream file(temporary,std::ios::trunc);if(!file){++affine_progress_errors;return;}
  file<<"{\"analysis_only\":true,\"is_sat_claim\":false,\"final\":"<<(final?"true":"false")
      <<",\"elapsed_seconds\":"<<elapsed<<",\"steps\":"<<_step
      <<",\"current_arithmetic_unsat\":"<<(initialization_round?std::to_string(unsat_clauses->size()):"null")
      <<",\"current_uf_conflicts\":"<<(initialization_round?std::to_string(uf_enabled?uf_conflicts:0):"null")
      <<",\"best_total_arithmetic_unsat\":"<<(best_total_arithmetic_unsat==UINT64_MAX?"null":std::to_string(best_total_arithmetic_unsat))
      <<",\"best_total_uf_conflicts\":"<<(best_total_uf_conflicts==UINT64_MAX?"null":std::to_string(best_total_uf_conflicts))
      <<",\"affine_views_enabled\":"<<(affine_enabled?"true":"false")
      <<",\"affine_views_policy\":\""<<(affine_enabled?"persistent_native_tree_v1":"v12_scalar")<<"\""
      <<",\"affine_applicable\":"<<(affine_installed()?"true":"false")
      <<",\"affine_components\":"<<affine_views.size()<<",\"affine_derived\":"<<affine_derived
      <<",\"affine_build_components\":"<<affine_build_components<<",\"affine_skip_math\":"<<affine_skip_math
      <<",\"affine_skip_size\":"<<affine_skip_size<<",\"affine_skip_no_root\":"<<affine_skip_no_root
      <<",\"affine_skip_domain\":"<<affine_skip_domain<<",\"affine_skip_bits\":"<<affine_skip_bits
      <<",\"affine_skip_footprint\":"<<affine_skip_footprint<<",\"affine_skip_numeric\":"<<affine_skip_numeric
      <<",\"affine_proposals\":"<<affine_proposals<<",\"affine_scores\":"<<affine_scores<<",\"affine_commits\":"<<affine_commits
      <<",\"affine_rejections\":"<<affine_rejections<<",\"affine_poly_unsupported\":"<<affine_poly_unsupported
      <<",\"affine_poly_budget\":"<<affine_poly_budget
      <<",\"affine_build_nanoseconds\":"<<affine_build_ns<<",\"affine_generate_nanoseconds\":"<<affine_generate_ns
      <<",\"affine_score_nanoseconds\":"<<affine_score_ns<<",\"affine_candidate_nanoseconds\":"<<affine_candidate_ns
      <<",\"affine_commit_nanoseconds\":"<<affine_commit_ns
      <<",\"affine_progress_writes\":"<<(affine_progress_writes+1)<<",\"affine_progress_errors\":"<<affine_progress_errors
      <<",\"affine_config\":{\"members\":32,\"direct_terms\":16,\"direct_literals\":128,\"clauses\":256,\"uf_apps\":64,\"raw_slots\":8192,\"macro_components\":4,\"native_patch\":64,\"generation_work\":8192,\"input_bits\":4096,\"intermediate_bits\":16384}}\n";
  file.close();
  if(!file||std::rename(temporary.c_str(),affine_progress_path.c_str())!=0){++affine_progress_errors;std::remove(temporary.c_str());return;}
  ++affine_progress_writes;
 }catch(...){++affine_progress_errors;std::remove(temporary.c_str());}
}


}

