#include "nia_linear_initialize.h"
#include "nia_ls.h"
#include <set>
namespace nia{

//initialize
ls_solver::ls_solver()
:_swt_p(0.3),
_swt_threshold(50),
smooth_probability(3),
_cutoff(1200),
_additional_len(10),
_max_step(UINT64_MAX),
_complete_ls(false),
_record_frequency(false),
config(sls_config(SlsStrategy::automatic)),restart_stall_limit(config.restart_stall_limit)
{mt.seed(1);}

ls_solver::ls_solver(int random_seed,uint64_t max_step,bool complete_ls,bool record_frequency,SlsStrategy strategy)
:_swt_p(0.3),
_swt_threshold(50),
smooth_probability(3),
_cutoff(1200),
_additional_len(10),
_max_step(max_step),
_complete_ls(complete_ls),
_record_frequency(record_frequency),
config(sls_config(strategy)),restart_stall_limit(config.restart_stall_limit)
{mt.seed(random_seed);}



void ls_solver::initialize(){
    if(joint_operators_enabled && initialization_round==0){last_op_var=UINT64_MAX;last_op_value=0;}
    if(config.fast_moves)std::fill(_move_bool_dirty.begin(),_move_bool_dirty.end(),0);
    if(config.distance_scoring)++distance_current_generation;
    critical_operation_caches.clear();
    critical_operation_caches.resize(2*_lits.size());
    capturing_critical_operations=nullptr;
    clear_prev_data();
    construct_slution_score();
    initialize_term_datas();
    initialize_lit_datas();
    initialize_clause_datas();
    initialize_variable_datas();
    best_found_this_restart=unsat_clauses->size();
    if(restart_repair_enabled)
        restart_best_assignment.assign(_solution.begin(),_solution.begin()+_num_vars);
    update_best_solution();
}
void ls_solver::clear_prev_data(){
    if(restart_repair_enabled && initialization_round>0){
        std::fill(tabulist.begin(),tabulist.end(),0);
        std::fill(last_move.begin(),last_move.end(),0);
        last_op_var=UINT64_MAX;
        last_op_value=0;
        is_in_bool_search=false;
        sat_clause_with_false_literal->clear();
    }
    for(int v:bool_var_vec){_vars[v].score=0;}
    _best_found_hard_cost_this_bool=INT32_MAX;
    _best_found_hard_cost_this_nia=INT32_MAX;
    no_improve_cnt_bool=0;
    no_improve_cnt_nia=0;
}

//construction
void ls_solver::construct_slution_score(){
    const uint64_t round=initialization_round++;
    if(config.strategy==SlsStrategy::corebip)core_locked.assign(_num_vars,false);
    for(int i=0;i<_num_vars;++i){
        const variable& v=_vars[i];
        if(!v.is_nia){_solution[i]=-1;continue;}
        _solution[i]=std::max(v.low_bound,std::min(v.upper_bound,__int128_t(0)));
    }
    const bool reuse_core=(config.strategy==SlsStrategy::corewarm2 || config.strategy==SlsStrategy::corebip) && core_jitter;
    if((round==0 || reuse_core) && !progressive_values.empty()){
        std::vector<__int128_t> candidate(_solution.begin(),_solution.begin()+_num_vars);
        unsigned mapped=0;
        for(size_t i=0;i<_num_vars;++i){
            auto found=progressive_values.find(_vars[i].var_name);
            if(found==progressive_values.end())continue;
            const std::string& text=found->second;
            if(!_vars[i].is_nia){
                if(text!="true" && text!="false")continue;
                candidate[i]=text=="true"?1:-1;++mapped;
            }else{
                try{
                    std::string number=text;
                    if(number.rfind("(- ",0)==0 && number.back()==')')number="-"+number.substr(3,number.size()-4);
                    size_t used=0;__int128_t value=std::stoll(number,&used);
                    if(used!=number.size())continue;
                    candidate[i]=std::max(_vars[i].low_bound,std::min(_vars[i].upper_bound,value));++mapped;
                }catch(const std::exception&){} // Keep the baseline value if not representable.
            }
        }
        auto cost=[&](const std::vector<__int128_t>& assignment)->uint64_t{
            std::vector<__int128_t> terms(_terms.size(),0);
            for(size_t i=0;i<_terms.size();++i){
                if(!term_appear[i])continue;
                __int128_t value=1;
                for(const auto& ve:_terms[i].var_epxs)
                    for(int k=0;k<ve.exponent;++k)
                        if(__builtin_mul_overflow(value,assignment[ve.var_index],&value))return UINT64_MAX;
                terms[i]=value;
            }
            uint64_t unsat=0;
            for(const auto& clause:_clauses){
                bool satisfied=false;
                for(int signed_index:clause.literals){
                    const auto& l=_lits[std::abs(signed_index)];bool truth;
                    if(l.is_nia_lit){
                        __int128_t delta=l.key;
                        for(const auto& ct:l.coff_terms){
                            __int128_t product;
                            if(__builtin_mul_overflow(ct.coff,terms[ct.term_idx],&product) ||
                               __builtin_add_overflow(delta,product,&delta))return UINT64_MAX;
                        }
                        truth=l.is_equal?delta==0:delta<=0;
                    }else truth=assignment[l.delta]>0;
                    if(truth==(signed_index>0)){satisfied=true;break;}
                }
                if(!satisfied)++unsat;
            }
            return unsat;
        };
        std::vector<__int128_t> original(_solution.begin(),_solution.begin()+_num_vars);
        uint64_t before=cost(original),after=cost(candidate);
        const bool installed=mapped>0 && (config.gate_input?
            after!=UINT64_MAX && (force_progressive || after<before):after<before);
        if(installed)std::copy(candidate.begin(),candidate.end(),_solution.begin());
        std::cerr<<"progressive_handoff mapped="<<mapped<<" installed="<<installed
                 <<" before="<<before<<" after="<<after<<"\n";

    }
    if(round>0 && restart_seed.size()==_num_vars){
        std::copy(restart_seed.begin(),restart_seed.end(),_solution.begin());
        restart_seed.clear();
    }
    const bool bipartite_core=config.strategy==SlsStrategy::corebip;
    const bool core_restart=bipartite_core || config.strategy==SlsStrategy::corewarm2;
    if(core_restart && core_jitter && !core_ranked){
        core_lower.resize(_num_vars);core_upper.resize(_num_vars);
        for(size_t v=0;v<_num_vars;++v){core_lower[v]=_vars[v].low_bound;core_upper[v]=_vars[v].upper_bound;}
        auto floor_div=[](__int128_t n,__int128_t d){__int128_t q=n/d;if(n<0 && n%d)--q;return q;};
        auto ceil_div=[](__int128_t n,__int128_t d){__int128_t q=n/d;if(n>0 && n%d)++q;return q;};
        for(const auto& clause:_clauses){
            if(clause.literals.size()!=1)continue;
            int index=clause.literals[0];const auto& literal=_lits[std::abs(index)];
            if(!literal.is_nia_lit || literal.coff_terms.size()!=1)continue;
            const auto& term=_terms[literal.coff_terms[0].term_idx];
            if(term.var_epxs.size()!=1 || term.var_epxs[0].exponent!=1)continue;
            size_t v=term.var_epxs[0].var_index;__int128_t a=literal.coff_terms[0].coff,k=literal.key;if(a==0)continue;
            if(literal.is_equal){
                if(index>0 && k%a==0){core_lower[v]=std::max(core_lower[v],-k/a);core_upper[v]=std::min(core_upper[v],-k/a);}
            }else{
                if(index<0){a=-a;k=1-k;}
                if(a>0)core_upper[v]=std::min(core_upper[v],floor_div(-k,a));
                else core_lower[v]=std::max(core_lower[v],ceil_div(k,-a));
            }
        }
        if(bipartite_core){
            std::vector<std::vector<size_t>> graph(_num_vars);
            for(size_t t=0;t<_terms.size();++t){
                if(!term_appear[t])continue;const auto& factors=_terms[t].var_epxs;
                if(factors.size()!=2 || factors[0].exponent!=1 || factors[1].exponent!=1)continue;
                size_t x=factors[0].var_index,y=factors[1].var_index;
                if(core_lower[x]>=core_upper[x] || core_lower[y]>=core_upper[y])continue;
                graph[x].push_back(y);graph[y].push_back(x);
            }
            std::vector<int> color(_num_vars,-1);
            for(size_t start=0;start<_num_vars;++start){
                if(color[start]>=0 || graph[start].empty())continue;
                std::vector<size_t> pending{start},sides[2];color[start]=0;bool bipartite=true;
                for(size_t k=0;k<pending.size();++k){
                    size_t v=pending[k];sides[color[v]].push_back(v);
                    for(size_t other:graph[v]){
                        if(color[other]<0){color[other]=1-color[v];pending.push_back(other);}
                        else if(color[other]==color[v])bipartite=false;
                    }
                }
                if(!bipartite || pending.size()<4)continue;
                const auto& selected=sides[sides[0].size()<=sides[1].size()?0:1];
                nonlinear_core.insert(nonlinear_core.end(),selected.begin(),selected.end());
            }
        }else{
            std::vector<std::pair<unsigned,size_t>> ranked;
            for(size_t v=0;v<_num_vars;++v){
                if(!_vars[v].is_nia || core_lower[v]>=core_upper[v])continue;
                bool small=core_upper[v]-core_lower[v]<=8;
                bool free_sign=core_lower[v]<0 && core_upper[v]>0;
                bool discrete=_pre_value_1[v]!=INT32_MAX;
                if(!small && !free_sign && !discrete)continue;
                std::set<size_t> partners;
                for(const auto& occurrence:_vars[v].term_exps){
                    for(const auto& factor:_terms[occurrence.term_idx].var_epxs)if(size_t(factor.var_index)!=v)partners.insert(factor.var_index);
                    if(partners.size()>=64)break;
                }
                if(partners.size()>=4)ranked.emplace_back(unsigned(partners.size()),v);
            }
            std::stable_sort(ranked.begin(),ranked.end(),[](const auto& a,const auto& b){return a.first>b.first;});
            for(size_t i=0;i<std::min(size_t(64),ranked.size());++i)nonlinear_core.push_back(ranked[i].second);
        }
        core_ranked=true;std::cerr<<"nonlinear_core="<<nonlinear_core.size()<<"\n";
    }
    if(bipartite_core && core_jitter && round>0 && !core_elite.empty() && round%4!=0)
        std::copy(core_elite.begin(),core_elite.end(),_solution.begin());
    const bool core_restart_ready=core_restart && core_jitter && round>0 && !nonlinear_core.empty();
    // Cleanup has the same eligibility as before, even in the no-perturbation experiment.
    if(core_restart_ready){
        std::fill(tabulist.begin(),tabulist.end(),0);std::fill(last_move.begin(),last_move.end(),0);last_op_var=UINT64_MAX;last_op_value=0;
    }
    if(core_restart_ready && core_perturb_enabled){
        std::vector<__int128_t> saved(_solution.begin(),_solution.begin()+_num_vars);
        __int128_t scale=1;
        for(const auto& literal:_lits){
            scale=std::max(scale,abs_128(literal.key));
            for(const auto& term:literal.coff_terms)scale=std::max(scale,abs_128(term.coff));
        }
        scale=std::min(scale,__int128_t(INT32_MAX));
        const unsigned count=1+mt()%std::min(core_jitter,unsigned(nonlinear_core.size()));
        std::set<size_t> selected;
        while(selected.size()<count)selected.insert(nonlinear_core[mt()%nonlinear_core.size()]);
        for(size_t v:selected){
            __int128_t value;
            if(_pre_value_1[v]!=INT32_MAX)value=(_pre_value_2[v]!=INT32_MAX && mt()%2)?_pre_value_2[v]:_pre_value_1[v];
            else if(core_upper[v]>=core_lower[v] && core_upper[v]-core_lower[v]<=8)
                value=core_lower[v]+mt()%unsigned(core_upper[v]-core_lower[v]+1);
            else{
                unsigned choice=mt()%10;
                if(choice<5)value=__int128_t(int(mt()%3)-1);
                else if(choice<8)value=_solution[v]+__int128_t(int(mt()%5)-2);
                else value=(mt()%2?1:-1)*scale*(mt()%2?1:2);
            }
            _solution[v]=std::max(core_lower[v],std::min(core_upper[v],value));
        }
        if(bipartite_core && (round%4==1 || round%4==2)){
            for(size_t v:nonlinear_core){
                __int128_t value=round%4==1?1:__int128_t(mt()%2);
                _solution[v]=std::max(core_lower[v],std::min(core_upper[v],value));
            }
        }
        std::vector<__int128_t> current(_solution.begin(),_solution.begin()+_num_vars);
        if(assignment_cost(current)==UINT64_MAX)std::copy(saved.begin(),saved.end(),_solution.begin());
    }
    if(bipartite_core && core_jitter && round>0 && round%4!=0)
        for(size_t v:nonlinear_core)core_locked[v]=true;
    if(config.strategy==SlsStrategy::diverse && round>0){
        if(round%4==0){
            for(size_t v=0;v<_num_vars;++v)if(_vars[v].is_nia)_solution[v]=std::max(_vars[v].low_bound,std::min(_vars[v].upper_bound,__int128_t(0)));
        }
        const unsigned count=1+mt()%std::max(1u,std::min(16u,unsigned(_num_vars/16+1)));
        for(unsigned n=0;n<count;++n){
            size_t v=mt()%_num_vars;
            if(!unsat_clauses->empty() && n%2==0){
                auto& cl=_clauses[unsat_clauses->element_at(mt()%unsat_clauses->size())];
                if(!cl.nia_literals.empty()){
                    auto& l=_lits[std::abs(cl.nia_literals[mt()%cl.nia_literals.size()])];
                    if(!l.var_lit_terms.empty())v=l.var_lit_terms[mt()%l.var_lit_terms.size()].var_idx;
                }
            }
            if(!_vars[v].is_nia){_solution[v]=(_solution[v]>0)?-1:1;continue;}
            if(_pre_value_1[v]!=INT32_MAX){_solution[v]=(_pre_value_2[v]!=INT32_MAX && mt()%2)?_pre_value_2[v]:_pre_value_1[v];continue;}
            const int offsets[]={-4,-2,-1,1,2,4};
            __int128_t value=mt()%2?__int128_t(int(mt()%5)-2):_solution[v]+offsets[mt()%6];
            _solution[v]=std::max(_vars[v].low_bound,std::min(_vars[v].upper_bound,value));
        }
    }
//    read_model();
}

void ls_solver::read_model(){
    int size;
    std::cin>>size;
    std::string in_string,in_string_2;
    for(int i=0;i<size;i++){
        std::cin>>in_string;
        std::cin>>in_string_2;
        if(name2var.find(in_string)!=name2var.end()){
            if(in_string_2=="false"){_solution[name2var[in_string]]=-1;}
            else if(in_string_2=="true"){_solution[name2var[in_string]]=1;}
            else{_solution[name2var[in_string]]=atoi(in_string_2.c_str());}
        }
    }
}

void ls_solver::initialize_variable_datas(){
    
}

//initialize the value of each term
void ls_solver::initialize_term_datas(){
    for (variable &v : _vars) v.scoring_coffs_valid = false;
    for(term &t:_terms){
        t.value=1;
        for(var_exp &ve:t.var_epxs){
            if(ve.exponent==1){t.value*=_solution[ve.var_index];}
            else if(!config.wide_arithmetic){t.value*=std::pow((long long)_solution[ve.var_index],ve.exponent);}
            else{t.value*=integer_power(_solution[ve.var_index],ve.exponent);}

            if(t.value==0)break;
        }
    }
}

//initialize the delta of each literal by delta_lit operation
void ls_solver::initialize_lit_datas(){
    for(lit &l:_lits){
        if(l.lits_index!=0){
            if(l.is_nia_lit){
                l.delta=delta_lit(l);
                if(l.is_equal){l.is_true=(l.delta==0)?1:-1;}
                else{l.is_true=(l.delta<=0)?1:-1;}
            }//nia lit
            else{l.is_true=(_solution[l.delta]>0)?1:-1;}//boolean lit
        }
    }
}
//set the sat num of each clause, and sat/unsat a clause
void ls_solver::initialize_clause_datas(){
    _lit_in_unsat_clause_num=0;
    _bool_lit_in_unsat_clause_num=0;
    for(uint64_t c=0;c<_num_clauses;c++){
        clause *cl=&(_clauses[c]);
        cl->sat_count=0;
        cl->weight=1;
        for(int l_idx:cl->literals){
            if(l_idx*_lits[std::abs(l_idx)].is_true>0){cl->sat_count++;cl->watch_lit_idx=l_idx;}//determine the sat count and watch lit
        }
        if(cl->sat_count==0){
            unsat_a_clause(c);
            _lit_in_unsat_clause_num+=_clauses[c].literals.size();
            _bool_lit_in_unsat_clause_num+=_clauses[c].bool_literals.size();
            for(int l_sign_idx:cl->bool_literals){_vars[_lits[std::abs(l_sign_idx)].delta].score++;}
        }
        else{sat_a_clause(c);}
        if(cl->sat_count>0&&cl->sat_count<cl->literals.size()){sat_clause_with_false_literal->insert_element((int)c);}
        //TODO::else{sat_clause_with_false_literal->delete_element((int)c);}
        if(cl->sat_count==1){
            lit *l=&(_lits[std::abs(cl->watch_lit_idx)]);
            if(!l->is_nia_lit){_vars[l->delta].score--;}
        }
    }
    total_clause_weight=_num_clauses;
}


}
