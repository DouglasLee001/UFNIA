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
    uf_move_waiting=false;uf_consecutive_tabu_waits=0;
    if(initialization_round==0){last_op_var=UINT64_MAX;last_op_value=0;}
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
    if(uf_enabled)initialize_uf();
    if(initialization_round==1)initial_arithmetic_unsat=unsat_clauses->size();
    best_found_this_restart=raw_cost();
    if(restart_repair_enabled)
        restart_best_assignment.assign(_solution.begin(),_solution.begin()+_num_vars);
    update_best_solution();
}
void ls_solver::clear_prev_data(){
    if(initialization_round>0){
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
    ++initialization_round;
    for(size_t i=0;i<_num_vars;++i){
        const variable& v=_vars[i];
        _solution[i]=v.is_nia?0:-1;
        if(v.is_nia){
            if(v.low_bound && _solution[i]<*v.low_bound)_solution[i]=*v.low_bound;
            if(v.upper_bound && _solution[i]>*v.upper_bound)_solution[i]=*v.upper_bound;
            if(_pre_value_1[i])_solution[i]=*_pre_value_1[i];
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
            else{l.is_true=(_solution[l.bool_var]>0)?1:-1;}//boolean lit
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
            for(int l_sign_idx:cl->bool_literals){_vars[_lits[std::abs(l_sign_idx)].bool_var].score++;}
        }
        else{sat_a_clause(c);}
        if(cl->sat_count>0&&cl->sat_count<cl->literals.size()){sat_clause_with_false_literal->insert_element((int)c);}
        //TODO::else{sat_clause_with_false_literal->delete_element((int)c);}
        if(cl->sat_count==1){
            lit *l=&(_lits[std::abs(cl->watch_lit_idx)]);
            if(!l->is_nia_lit){_vars[l->bool_var].score--;}
        }
    }
    total_clause_weight=_num_clauses;
}


}
