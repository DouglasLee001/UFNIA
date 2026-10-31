#include "nia_ls.h"
#include <set>
#include <cmath>
namespace nia {
static double log_magnitude(const Int& value){Int n=value>=0?value:Int(-value);if(n==0)return 0;long exponent=0;double mantissa=mpz_get_d_2exp(&exponent,n.get_mpz_t());return exponent<40?std::log1p(n.get_d()):std::log(mantissa)+double(exponent)*std::log(2.0);}
//random walk
void ls_solver::update_clause_weight(){
    if(uf_enabled && uf_scoring=="count")return;
    ++weight_updates;
    if(uf_enabled && uf_conflicts)++uf_weight;
    for(int i=0;i<unsat_clauses->size();i++){
        clause *unsat_cl=&(_clauses[unsat_clauses->element_at(i)]);
        unsat_cl->weight++;
        for(int l_sign_idx:unsat_cl->bool_literals){_vars[_lits[std::abs(l_sign_idx)].bool_var].score++;}
    }
    total_clause_weight+=unsat_clauses->size();
}

void ls_solver::smooth_clause_weight(){
    if(uf_enabled && uf_scoring=="count")return;
    if(uf_enabled && uf_weight>1)--uf_weight;
    for(int i=0;i<_num_clauses;i++){
        if(_clauses[i].weight>1&&!unsat_clauses->is_in_array(i)){
            _clauses[i].weight--;
            total_clause_weight--;
            if(_clauses[i].sat_count==1&&!_lits[std::abs(_clauses[i].watch_lit_idx)].is_nia_lit){
                uint64_t watch_lit_idx=_lits[std::abs(_clauses[i].watch_lit_idx)].bool_var;
                _vars[watch_lit_idx].score++;
            }
        }
    }
}

//when there is no operation, simply find a lit in a random false clause, pick a random var with coff!=0, set it to 0
void ls_solver::no_operation_random_walk(){
    clause *cp=&(_clauses[unsat_clauses->element_at(mt()%unsat_clauses->size())]);//choose a random unsat clause
    int lit_idx=cp->literals[mt()%cp->literals.size()];
    lit *l=&(_lits[std::abs(lit_idx)]);
    if(!l->is_nia_lit){
        critical_move(l->bool_var, 0);
        return;
    }//boolean lit
    if(l->var_lit_terms.empty())return;
    var_lit_term *vlt;//nia lit
    Int coffs[2]={0,0};//the coff = sum(term_coff*vlt_coff) ( 1 * x1*x2 ) + ( -1 * x1 ) + ( -1 * x3 ) for x1 the coff=(1*x2)+(-1)
    uint64_t var_idx_curr=l->var_lit_terms[0].var_idx;//the current var idx of var_lit_term
    int l_var_lit_term_num=(int)l->var_lit_terms.size();
    std::vector<uint64_t> var_idx_none_zero;//the vars with coff != 0
    //go through the var_lit_term and insert critical move for each var
    for(int vlt_idx=0;vlt_idx<l_var_lit_term_num;vlt_idx++){
        vlt=&(l->var_lit_terms[vlt_idx]);
        if(vlt->var_idx!=var_idx_curr){
            coffs[0]=coffs[1]=0;
            var_idx_curr=vlt->var_idx;
        }//enter a new var
        if(vlt->exponent<3){coffs[vlt->exponent-1]=search_coefficient(coffs[vlt->exponent-1]+vlt->coff*coff_in_term(var_idx_curr,vlt->term_idx,vlt->exponent));}//determine the coff, for vars with only coff>2, the var will not be considered
        if((vlt_idx==l_var_lit_term_num-1)||(var_idx_curr!=l->var_lit_terms[vlt_idx+1].var_idx)){
            if(coffs[0]!=0||coffs[1]!=0){var_idx_none_zero.push_back(var_idx_curr);}// if coff==0, changing the var cannot make any progress
        }//the last vlt of the var or the last vlt of the lit
    }
    if(var_idx_none_zero.size()==0){var_idx_curr=l->var_lit_terms[mt()%l_var_lit_term_num].var_idx;}
    else{var_idx_curr=var_idx_none_zero[mt()%var_idx_none_zero.size()];}
    Int future_solution=0;
    variable *var=&(_vars[var_idx_curr]);
    if(var->low_bound && *var->low_bound>0){future_solution=*var->low_bound;}
    else if(var->upper_bound && *var->upper_bound<0){future_solution=*var->upper_bound;}
    critical_move(var_idx_curr, future_solution-_solution[var_idx_curr]);//move a random var with coff!=0 to 0
}

void ls_solver::random_walk(){
    int operation_idx(0),operation_idx_bool(0),clause_idx;
    clause *cp;
    //determine the operations
    for(int i=0;i<3&&operation_idx+operation_idx_bool<5;i++){
        clause_idx=unsat_clauses->element_at(mt()%unsat_clauses->size());
        cp=&(_clauses[clause_idx]);
        for(int l_idx:cp->nia_literals){add_operation_from_false_lit(false, l_idx, operation_idx);}
        for(int l_idx:cp->bool_literals){add_bool_operation(false, l_idx, operation_idx_bool);}
    }
    //recover bool vec
    for(int i=0;i<operation_idx_bool;i++){is_chosen_bool_var[operation_var_idx_bool_vec[i]]=false;}
    //recover the false_lit
    false_lit_occur->clear();
    //if no operation, return
    if(operation_idx+operation_idx_bool==0){
        last_op_var=UINT64_MAX;//in case the random walk make no move, it will not ban the only operation
        no_operation_random_walk();
        return;
    }
    //nia mode make move
    if(operation_idx_bool==0||(operation_idx>0&&operation_idx_bool>0&&!is_in_bool_search)){
        const bool novelty_ready=random_walk_stall_steps>10000 && (config.strategy==SlsStrategy::automatic?
            ((input_gate_dense && TimeElapsed()>30.0) || (input_unit_dense && TimeElapsed()>120.0)):
            (config.strategy==SlsStrategy::corebip || config.strategy==SlsStrategy::stepnovel)?_step>100000:
            config.strategy!=SlsStrategy::plain && TimeElapsed()>30.0);
        if(novelty_ready){
            using Choice=NoveltyChoice;
            std::vector<Choice> choices;
            const int count=std::min(operation_idx,45);
            const bool collect_break=config.strategy==SlsStrategy::stepnovel && stepnovel_choice==StepnovelChoice::break_cost;
            for(int i=0;i<count;++i){
                int idx=operation_idx<=45?i:int(mt()%operation_idx);
                int var=operation_var_idx_vec[idx];auto delta=operation_change_value_vec[idx];
                uint64_t broken=0;
                const MoveScore score=critical_score(var,delta,collect_break?&broken:nullptr);
                choices.push_back({var,delta,score,std::max(last_move[2*var],last_move[2*var+1]),broken});
            }
            std::stable_sort(choices.begin(),choices.end(),[](const Choice& a,const Choice& b){
                if(a.score!=b.score)return a.score>b.score;
                return a.age<b.age;
            });
            size_t selected=0;
            if(config.strategy==SlsStrategy::stepnovel){
#ifdef NIA_NOVELTY_REFERENCE
                // Test baseline: the immediately preceding 10%-random-only rule.
                selected=select_novelty_by_score(choices,mt);
#else
                if(stepnovel_choice!=StepnovelChoice::random){
                    double scale=1.0;
                    if(stepnovel_choice==StepnovelChoice::softmax){
                        const double before=stepnovel_softmax.temperature;
                        stepnovel_softmax.prepare(_step,best_found_cost);
                        selected=select_novelty_by_softmax(choices,mt,stepnovel_softmax.temperature,&scale);
                        if(stepnovel_softmax.temperature>before && stepnovel_softmax.heat_windows>0 && stepnovel_softmax.heat_windows<=8)
                            std::cerr<<"stepnovel_softmax_heat step="<<_step<<" temperature="<<stepnovel_softmax.temperature
                                     <<" windows="<<stepnovel_softmax.heat_windows<<" best="<<best_found_cost<<"\n";
                    }else selected=stepnovel_choice==StepnovelChoice::break_cost?
                        select_novelty_by_break(choices,mt,stepnovel_beta):select_novelty_by_rank(choices,mt);
                    if(sampling_calls==0){
                        std::cerr<<"stepnovel_sampling_start choice="<<stepnovel_choice_name(stepnovel_choice);
                        if(stepnovel_choice==StepnovelChoice::softmax)
                            std::cerr<<" temperature="<<stepnovel_softmax.temperature<<" window="<<stepnovel_softmax.window
                                     <<" score_scale="<<scale;
                        else if(collect_break)std::cerr<<" beta="<<stepnovel_beta<<" selected_break="<<choices[selected].break_cost;
                        std::cerr<<" step="<<_step<<" stall="<<random_walk_stall_steps<<" candidates="<<choices.size()<<"\n";
                    }
                    ++sampling_calls;
                    sampling_nonbest+=selected!=0;
                }else selected=select_novelty_by_score(choices,mt);
#endif
            }else{
                if(mt()%100<10)selected=mt()%choices.size();
                else if(choices.size()>1 && choices[0].var==int(last_op_var) && mt()%100<50){
                    for(size_t i=1;i<choices.size();++i)if(choices[i].var!=choices[0].var){selected=i;break;}
                }
            }
            critical_move(choices[selected].var,choices[selected].delta);return;
        }

        Int best_value_nia;
        int best_var_idx_nia=-1;MoveScore best_score_nia=INT64_MIN;
        const int STEP=40000,K_MAX=12;
        const int K=std::min(K_MAX,1+random_walk_stall_steps/STEP);
        const int M=std::min(K,operation_idx);
        const bool use_window=K>1&&operation_idx>1;
        uint64_t window_var_idx[K_MAX];
        Int window_change_value[K_MAX];
        if(use_window){
            // Preserve insertion order and pairs before BMS overwrites the work vectors.
            for(int r=0;r<M;r++){
                window_var_idx[r]=operation_var_idx_vec[r];
                window_change_value[r]=operation_change_value_vec[r];
            }
        }
        select_best_operation_from_vec(operation_idx, best_score_nia, best_var_idx_nia, best_value_nia);//choose best nia operation
        if(use_window){
            const int P =
                (random_walk_stall_steps < 120000) ? 2 :
                (random_walk_stall_steps < 400000) ? 3 : 4;
            const bool allow_override=random_walk_window_keeps>=P-1;
            const double alpha=std::max(0.2,4.0-random_walk_stall_steps/50000.0);
            double weights[K_MAX],total_weight=0.0;
            int last_valid=-1;
            for(int r=0;r<M;r++){
                const double weight=std::pow(r+1,-alpha);
                weights[r]=(std::isfinite(weight)&&weight>0.0)?weight:0.0;
                if(weights[r]>0.0){
                    total_weight+=weights[r];
                    last_valid=r;
                }
            }
            if(last_valid>=0&&std::isfinite(total_weight)&&total_weight>0.0){
                const double draw=std::uniform_real_distribution<double>(0.0,1.0)(mt);
                double cumulative=0.0;
                int selected=last_valid;//rounding at the cumulative boundary
                for(int r=0;r<M;r++){
                    cumulative+=weights[r]/total_weight;
                    if(weights[r]>0.0&&draw<cumulative){selected=r;break;}
                }
                if(allow_override){
                    best_var_idx_nia=(int)window_var_idx[selected];
                    best_value_nia=window_change_value[selected];
                }
            }
            // Every eligible call consumes an opportunity, even if sampling falls back.
            // Carry accumulated keeps across stagnation stage boundaries.
            random_walk_window_keeps=allow_override?0:(random_walk_window_keeps+1);
        }
        critical_move(best_var_idx_nia, best_value_nia);
    }
    //bool mode make move
    else{
        if(config.strategy==SlsStrategy::automatic){
            if(input_gate_dense && TimeElapsed()>90.0 && operation_idx_bool>1 && mt()%100<10){
                critical_move(operation_var_idx_bool_vec[mt()%operation_idx_bool],0);return;
            }
        }
        //choose best bool operation
        int best_var_idx_bool=0;MoveScore best_score_bool=INT64_MIN;
        select_best_operation_from_bool_vec(operation_idx_bool, best_score_bool, best_var_idx_bool);
        critical_move(best_var_idx_bool, 0);
    }
}

//basic operations
bool ls_solver::update_best_solution(){
    bool improve=false;
    const uint64_t raw=raw_cost();
    best_arithmetic_unsat=std::min(best_arithmetic_unsat,uint64_t(unsat_clauses->size()));
    best_uf_conflicts=std::min(best_uf_conflicts,uf_conflicts);
    if(raw<uint64_t(best_found_this_restart)){
        improve=true;
        best_found_this_restart=raw;
        if(restart_repair_enabled)
            restart_best_assignment.assign(_solution.begin(),_solution.begin()+_num_vars);
        if(_complete_ls){
            for(uint64_t lit_idx=0;lit_idx<_num_lits;lit_idx++){
                _best_literal_assignment[lit_idx]=(_lits[lit_idx].lits_index==0)?0:_lits[lit_idx].is_true;
            }
        }
    }
    if(raw<uint64_t(best_found_cost)){
        improve=true;
        best_found_cost=raw;
        best_total_arithmetic_unsat=unsat_clauses->size();
        best_total_uf_conflicts=uf_enabled?uf_conflicts:0;
        if(config.strategy==SlsStrategy::stepnovel && stepnovel_choice==StepnovelChoice::softmax
           && stepnovel_softmax.active)
            stepnovel_softmax.observe_progress(_step,best_found_cost);
        if(config.strategy==SlsStrategy::corebip){
            if(core_jitter)core_elite.assign(_solution.begin(),_solution.begin()+_num_vars);
        }
        best_cost_time=TimeElapsed();
    }
    return improve;
}

int ls_solver::pick_critical_move_bool(){
    int best_var_idx=-1;MoveScore best_score=1;
    int operation_idx=0;
    for(int i=0;i<contain_bool_unsat_clauses->size();i++){
        int clause_idx=contain_bool_unsat_clauses->element_at(i);
        clause *cl=&(_clauses[clause_idx]);
        for(int l_sign_idx:cl->bool_literals){add_bool_operation(true, l_sign_idx, operation_idx);}
    }
    for(int i=0;i<operation_idx;i++){is_chosen_bool_var[operation_var_idx_bool_vec[i]]=false;}// recover chosen_bool_var
    select_best_operation_from_bool_vec(operation_idx, best_score, best_var_idx);
    //if there is untabu decreasing move
    if(best_var_idx!=-1){return best_var_idx;}
    if(uf_enabled && uf_conflicts && try_uf_move())return -1;
    //update weight
    if(mt()%10000>smooth_probability){update_clause_weight();}
    else {smooth_clause_weight();}
    random_walk();
    return -1;
}

void ls_solver::add_bool_operation(bool use_tabu, int lit_idx, int &operation_idx_bool){
    uint64_t bool_var_idx=_lits[std::abs(lit_idx)].bool_var;
    if(is_chosen_bool_var[bool_var_idx])return;//operations will not be inserted repeatedly
    if(!use_tabu||(use_tabu&&_outer_layer_step>tabulist[2*bool_var_idx])){//tabu mechanism
        operation_var_idx_bool_vec[operation_idx_bool++]=(int)bool_var_idx;
        is_chosen_bool_var[bool_var_idx]=true;
    }
}

//calculate the operation to be added for exp==1
void ls_solver::add_coff(uint64_t var_idx_curr,bool use_tabu,int lit_idx,int& operation_idx,Int coff_1){
    coff_1=search_coefficient(coff_1);
    lit *l=&(_lits[std::abs(lit_idx)]);
    Int lit_delta=l->delta;
    if(l->is_equal){
        if(lit_idx>0){
            if(lit_delta%coff_1==0){insert_operation(var_idx_curr,(-lit_delta/coff_1), operation_idx,use_tabu);}
        }//the delta should be 0, while it is now !=0, so the change value should be -delta/coff
        else{
            insert_operation(var_idx_curr,1, operation_idx,use_tabu);
            insert_operation(var_idx_curr,-1, operation_idx,use_tabu);
        }//the delta should be !=0, while it is now 0, so the change value should be +/- 1
    }
    else{
        if(lit_idx>0){insert_operation(var_idx_curr, devide((-lit_delta), coff_1), operation_idx, use_tabu);}//the delta should be <=0, while it is now >0, so the change value should add -delta/coff
        else{insert_operation(var_idx_curr, devide((1-lit_delta), coff_1), operation_idx, use_tabu);}//the delta should be >0, while it is now <=0, so the change value should add (1-delta)/coff
    }
}
// Exact integer critical boundaries for a*x^2+b*x+c, including negative literals.
void ls_solver::add_coff(uint64_t v,bool tabu,int li,int& count,Int a,Int b,Int c){
    if(a==0){if(b!=0)add_coff(v,tabu,li,count,b);return;}
    const lit& literal=_lits[std::abs(li)];
    if(literal.is_equal && li<0){insert_operation(v,1,count,tabu);insert_operation(v,-1,count,tabu);return;}
    Int d=b*b-4*a*c;if(d<0)return;
    Int root;mpz_sqrt(root.get_mpz_t(),d.get_mpz_t());
    Int denominator=2*a;std::set<Int> values;
    for(int sign:{-1,1}){
        Int numerator=-b+sign*root,q;mpz_fdiv_q(q.get_mpz_t(),numerator.get_mpz_t(),denominator.get_mpz_t());
        for(int offset=-2;offset<=2;++offset)values.insert(Int(q+offset));
    }
    for(const Int& x:values){Int residual=a*x*x+b*x+c;bool truth=literal.is_equal?residual==0:residual<=0;
        if(truth==(li>0))insert_operation(v,Int(x-_solution[v]),count,tabu);
    }
}

//for a falsified NIA lit, choose critical move from it
void ls_solver::add_operation_from_false_lit(bool use_tabu, int lit_idx, int &operation_idx){
    if(false_lit_occur->is_in_array(std::abs(lit_idx))){return;}//if the false lit has been considered, then the lit will not be considered repeatedly
    false_lit_occur->insert_element(std::abs(lit_idx));
#ifdef NIA_CANDIDATE_REFERENCE
    generate_operations_from_false_lit(use_tabu,lit_idx,operation_idx);
#else
    auto& cached=critical_operation_caches[2*size_t(std::abs(lit_idx))+(lit_idx<0)];
    if (!cached.valid) {
        cached.moves.clear();
        capturing_critical_operations=&cached.moves;
        int ignored=0;
        generate_operations_from_false_lit(false,lit_idx,ignored);
        capturing_critical_operations=nullptr;
        cached.valid=true;
        ++candidate_cache_rebuilds;
    } else {
        ++candidate_cache_hits;
    }
#ifdef NIA_CANDIDATE_VERIFY
    // Compare ordered raw moves, including duplicates, with fresh generation.
    std::vector<raw_critical_operation> expected;
    capturing_critical_operations=&expected;
    int ignored=0;
    generate_operations_from_false_lit(false,lit_idx,ignored);
    capturing_critical_operations=nullptr;
    if (expected!=cached.moves) {
        std::cerr << "critical operation cache mismatch at step " << _step << " literal " << lit_idx << '\n';
        std::abort();
    }
#endif
    for (const auto& move:cached.moves)
        insert_operation(move.first,move.second,operation_idx,use_tabu);
#endif
}

void ls_solver::generate_operations_from_false_lit(bool use_tabu,int lit_idx,int& operation_idx){
    Int total_term_value=0;
    lit *l=&(_lits[std::abs(lit_idx)]);
    if(l->var_lit_terms.empty())return;
    uint64_t var_idx_curr=l->var_lit_terms[0].var_idx;//the current var idx of var_lit_term
    var_lit_term *vlt;
    int l_var_lit_term_num=(int)l->var_lit_terms.size();
    bool too_high_exp=false;//if the exponent of var is larger than 2, then the var should be modified
    Int coffs[2]={0,0};
    //go through the var_lit_term and insert critical move for each var
    for(int vlt_idx=0;vlt_idx<l_var_lit_term_num;vlt_idx++){
        vlt=&(l->var_lit_terms[vlt_idx]);
        if(vlt->var_idx!=var_idx_curr){
            total_term_value=0;
            coffs[0]=coffs[1]=0;//reset coffs
            var_idx_curr=vlt->var_idx;
            too_high_exp=false;
        }//enter a new var
        if(vlt->exponent>2){too_high_exp=true;}
        if(too_high_exp){continue;}//if too high exponent, the operations will not be inserted
        coffs[vlt->exponent-1]=search_coefficient(coffs[vlt->exponent-1]+vlt->coff*coff_in_term(var_idx_curr,vlt->term_idx,vlt->exponent));//determine the coff
        total_term_value=search_coefficient(total_term_value+vlt->coff*_terms[vlt->term_idx].value);
        if((vlt_idx==l_var_lit_term_num-1)||(var_idx_curr!=l->var_lit_terms[vlt_idx+1].var_idx)){
            if(coffs[0]==0&&coffs[1]==0){
                insert_operation(var_idx_curr, 1, operation_idx, use_tabu);
                insert_operation(var_idx_curr, -1, operation_idx, use_tabu);
                continue;
            }// if coff==0, change the var by 1
            else if(coffs[1]==0){add_coff(var_idx_curr,use_tabu,lit_idx,operation_idx,coffs[0]);}//high coff is 1
            else{add_coff(var_idx_curr,use_tabu,lit_idx,operation_idx,coffs[1],coffs[0],l->delta-total_term_value);}//high coff is 2
        }//the last vlt of the var or the last vlt of the lit
    }
}
//select best bool operation from
void ls_solver::select_best_operation_from_bool_vec(int operation_idx_bool, MoveScore &best_score_bool, int &best_var_idx_bool){
    int cnt,var_idx;MoveScore score;
    uint64_t best_last_move(UINT64_MAX);
    bool BMS;
    if(operation_idx_bool>45){BMS=true;cnt=45;}
    else{BMS=false;cnt=operation_idx_bool;}
    for(int i=0;i<cnt;i++){
        if(BMS){
            int idx=mt()%(operation_idx_bool-i);
            int tmp=operation_var_idx_bool_vec[operation_idx_bool-i-1];
            var_idx=operation_var_idx_bool_vec[idx];
            operation_var_idx_bool_vec[idx]=tmp;
        }
        else{var_idx=operation_var_idx_bool_vec[i];}
        score=_vars[var_idx].score;
        if(uf_enabled && uf_interaction!="none")score+=uf_single_gain(var_idx,Int(-_solution[var_idx]));
        uint64_t last_move_step=last_move[2*var_idx];
        if(score>best_score_bool||(score==best_score_bool&&last_move_step<best_last_move)){
            best_score_bool=score;
            best_var_idx_bool=var_idx;
            best_last_move=last_move_step;
        }
    }
}
//select the best nia operation from operation_vec depending on score
void ls_solver::select_best_operation_from_vec(int operation_idx,MoveScore &best_score,int &best_var_idx,Int &best_value){
    double best_distance=-1e300;
    bool BMS;
    int cnt;MoveScore score;
    uint64_t operation_var_idx,best_last_move(UINT64_MAX);
    Int operation_change_value,best_future_abs_value=0,future_abs_value;
    if(operation_idx>45){BMS=true;cnt=45;}
    else{BMS=false;cnt=operation_idx;}
    const bool prefer_age=config.strategy==SlsStrategy::automatic?
        input_unit_dense && _step>100000 && TimeElapsed()>30.0 && TimeElapsed()<120.0:
        config.strategy==SlsStrategy::corebip?_step>100000:
        (config.distance_scoring || config.strategy==SlsStrategy::diverse) && _step>100000 && TimeElapsed()>30.0;

    for(int i=0;i<cnt;i++){
        if(BMS){
            int idx=mt()%(operation_idx-i);
            operation_change_value=operation_change_value_vec[idx];
            operation_var_idx=operation_var_idx_vec[idx];
            operation_change_value_vec[idx]=operation_change_value_vec[operation_idx-i-1];
            operation_var_idx_vec[idx]=operation_var_idx_vec[operation_idx-i-1];
        }
        else{
            operation_change_value=operation_change_value_vec[i];
            operation_var_idx=operation_var_idx_vec[i];
        }
        future_abs_value=abs_128(_solution[operation_var_idx]+operation_change_value);
        if(use_distance_scoring())distance_score_threshold=best_score;
        score=critical_score(operation_var_idx,operation_change_value);
        if(uf_enabled && uf_interaction!="none")score+=uf_single_gain(operation_var_idx,Int(_solution[operation_var_idx]+operation_change_value));
        int opposite_direction=(operation_change_value>0)?1:0;//if the change value is >0, then means it is moving forward, the opposite direction is 1(backward)
        uint64_t last_move_step=last_move[2*operation_var_idx+opposite_direction];
        const bool age_tie=prefer_age?
            (last_move_step<best_last_move || (last_move_step==best_last_move && future_abs_value<best_future_abs_value)):
            (future_abs_value<best_future_abs_value || (future_abs_value==best_future_abs_value && last_move_step<best_last_move));
        const bool tie_better=use_distance_scoring()?
            latest_distance_gain>best_distance+1e-9 || (std::abs(latest_distance_gain-best_distance)<=1e-9 && age_tie):age_tie;
        if(score>best_score || (score==best_score && (best_var_idx<0 || tie_better))){
            if(use_distance_scoring())best_distance=latest_distance_gain;

            best_score=score;
            best_var_idx=(int)operation_var_idx;
            best_value=operation_change_value;
            best_last_move=last_move_step;
            best_future_abs_value=future_abs_value;
        }
    }
}
//pick a NIA critical move
int ls_solver::pick_critical_move(Int &best_value){
    MoveScore best_score=1;int best_var_idx=-1,operation_idx=0;
    //determine the critical value from unsat sat, using tabu
    for(int i=0;i<unsat_clauses->size();i++){
        clause *cl=&(_clauses[unsat_clauses->element_at(i)]);
        for(int l_idx:cl->nia_literals){add_operation_from_false_lit(true, l_idx, operation_idx);}
    }
    //recover the false_lit
    false_lit_occur->clear();
    //go through the forward and backward move of vars, evaluate their score, pick the untabued best one
    select_best_operation_from_vec(operation_idx,best_score,best_var_idx, best_value);
    //if there is untabu decreasing move
    if(best_var_idx!=-1){return best_var_idx;}
    //choose from swap operations if there is no decreasing unsat critical
    if(!sat_clause_with_false_literal->empty()){
        operation_idx=0;
        add_swap_operation(operation_idx);
        //recover the false_lit
        false_lit_occur->clear();
        select_best_operation_from_vec(operation_idx,best_score,best_var_idx, best_value);
        if(best_var_idx!=-1){return best_var_idx;}
    }
    // A joint move is committed inside the operator, before weight/random walk.
    if(joint_operators_enabled && try_joint_move())return -1;
    if(uf_enabled && uf_conflicts && try_uf_move())return -1;
    //update weight and random walk
    if(mt()%10000>smooth_probability){update_clause_weight();}
    else {smooth_clause_weight();}
    random_walk();
    return -1;
}

void ls_solver::modify_frequency_in_unsat_clauses(){
    int l_idx_abs=0;
    int num_lits_in_unsat_clauses=0;
    //record
    for(int i=0;i<unsat_clauses->size();i++){
        for(int l:_clauses[unsat_clauses->element_at(i)].literals){
            l_idx_abs=std::abs(l);
            if(!is_in_unsat_clause[l_idx_abs]){
                is_in_unsat_clause[l_idx_abs]=true;
                lits_in_unsat_clause[num_lits_in_unsat_clauses++]=l_idx_abs;
                _frequency_in_unsat_clauses[l_idx_abs]++;
            }
        }
    }
    //revert
    for(int i=0;i<num_lits_in_unsat_clauses;i++){
        l_idx_abs=lits_in_unsat_clause[i];
        is_in_unsat_clause[l_idx_abs]=false;
    }
}
//make move
void ls_solver::critical_move(uint64_t var_idx, Int change_value){
    if(config.strategy==SlsStrategy::corebip && core_locked.size()>var_idx && core_locked[var_idx])return;
    if(config.distance_scoring)++distance_current_generation;
    int direction=(change_value>0)?0:1;
    if(_vars[var_idx].is_nia){
        last_op_value=change_value;
        last_op_var=var_idx;
        if(!_vars[var_idx].var_lit_terms.empty())move_update(var_idx, change_value);
        _solution[var_idx]+=change_value;
    }
    else{
        last_op_var=UINT64_MAX;//if last operation is boolean operation, the var should no longer be banned
        MoveScore origin_score=_vars[var_idx].score;
        if(!_vars[var_idx].literal_idxs.empty())move_update(var_idx);
        _solution[var_idx]*=-1;
        _vars[var_idx].score=-origin_score;
    }
    if(uf_enabled)commit_uf_variable(var_idx);
    if(_record_frequency){modify_frequency_in_unsat_clauses();}
    //step
    if(_vars[var_idx].is_nia){
        last_move[2*var_idx+direction]=_step;
        tabulist[var_idx*2+(direction+1)%2]=_step+3+mt()%10;
    }
    else{
        last_move[2*var_idx]=_outer_layer_step;
        tabulist[2*var_idx]=_outer_layer_step+1+mt()%3;
        _outer_layer_step++;
    }
}
//transfer the ">" to "<="
void ls_solver::invert_lit(lit &l){
    l.key=1-l.key;
    for(int i=0;i<l.coff_terms.size();i++){l.coff_terms[i].coff*=-1;}
}
//all coffs are positive, go through all terms of the literal
Int ls_solver::delta_lit(lit &l){
    Int delta=l.key;
    for(int i=0;i<l.coff_terms.size();i++){delta+=(l.coff_terms[i].coff*_terms[l.coff_terms[i].term_idx].value);}
    return delta;
}

Int ls_solver::coff_in_term(uint64_t var_idx, uint64_t term_idx,int exponent){
    if(_terms[term_idx].var_epxs.size()==1){return 1;}//the term only contains the var
    if(_solution[var_idx]!=0){
        if(exponent==1){return _terms[term_idx].value/_solution[var_idx];}
        else{return _terms[term_idx].value/(integer_power(_solution[var_idx], exponent));}
    }//if the var!=0, the coff is value/var_solution
    else{
        Int coff=1;
        for(var_exp &ve:_terms[term_idx].var_epxs){
            if(ve.var_index==var_idx){continue;}//the var itself will not be counted
            if(ve.exponent==1){coff*=_solution[ve.var_index];}
            else{coff*=integer_power(_solution[ve.var_index],ve.exponent);}
            if(coff==0){break;}
        }
        return coff;
    }//the var_solution==0, then the coff should be calculated by going through the ve of the term
}

double ls_solver::TimeElapsed(){
    std::chrono::steady_clock::time_point finish = std::chrono::steady_clock::now();
    std::chrono::duration<double> duration = finish - start;
    return duration.count();
}

//return the upper round of (a/b): (-3.5)->-4; (3.5)->4
Int ls_solver::devide(Int a, Int b){
    Int abs_a=abs_128(a);
    Int abs_b=abs_128(b);
    Int up_round=abs_a/abs_b;
    if(abs_a%abs_b>0){up_round++;}
    return (a<0)==(b<0)?up_round:Int(-up_round);
}
void ls_solver::insert_operation(uint64_t var_idx,Int change_value,int &operation_idx,bool use_tabu){
    if(config.strategy==SlsStrategy::corebip && core_locked.size()>var_idx && core_locked[var_idx])return;
    // Cache before all dynamic filters; a temporarily forbidden move must survive.
    if (capturing_critical_operations) {
        capturing_critical_operations->emplace_back(var_idx,change_value);
        return;
    }
    if(change_value==0)return;
    if(var_idx==last_op_var&&change_value==-last_op_value)return;
    uint64_t direction=change_value>0?0:1;
    if(use_tabu&&_step<tabulist[2*var_idx+direction]){++tabu_rejections;return;}
    Int future_solution=_solution[var_idx]+change_value;
    const variable& v=_vars[var_idx];
    if((v.low_bound && future_solution<*v.low_bound)||(v.upper_bound && future_solution>*v.upper_bound))return;
    if(_pre_value_1[var_idx] && future_solution!=*_pre_value_1[var_idx] &&
       (!_pre_value_2[var_idx] || future_solution!=*_pre_value_2[var_idx]))return;
    {
        // The parser's estimate omits some simple literals. Grow both arrays
        // together rather than dropping candidates or writing past their ends.
        if (size_t(operation_idx)>=operation_var_idx_vec.size()) {
            const size_t size = std::max(size_t(operation_idx)+1, operation_var_idx_vec.size()*2);
            operation_var_idx_vec.resize(size);
            operation_change_value_vec.resize(size);
        }
        operation_var_idx_vec[operation_idx]=var_idx;
        operation_change_value_vec[operation_idx++]=change_value;
    }
}

void ls_solver::add_swap_operation(int &operation_idx){
    int clause_idx;
    clause *cl;
    if(sat_clause_with_false_literal->size()<20){
        for(int i=0;i<sat_clause_with_false_literal->size();i++){
            clause_idx=sat_clause_with_false_literal->element_at(i);
            cl=&(_clauses[clause_idx]);
            for(int l_idx:cl->nia_literals){
                if((_lits[std::abs(l_idx)].is_true^l_idx)<0){add_operation_from_false_lit(true, l_idx, operation_idx);}//determine a false lit, and add operation from it
            }
        }
    }
    else{
        for(int i=0;operation_idx<20&&i<50;i++){
            clause_idx=sat_clause_with_false_literal->element_at(mt()%sat_clause_with_false_literal->size());
            cl=&(_clauses[clause_idx]);
            for(int l_idx:cl->nia_literals){
                if((_lits[std::abs(l_idx)].is_true^l_idx)<0){add_operation_from_false_lit(true, l_idx, operation_idx);}//determine a false lit, and add operation from it
            }
        }
    }
    
}

//choose a clause with small weight, then choose a random lit, select the operation with greatest score in the lit
void ls_solver::swap_from_small_weight_clause(){}


void ls_solver::initialize_scoring_index(){
    _score_literal_offsets.assign(_lits.size()+1, 0);
    for (const clause &c : _clauses)
        for (int l : c.nia_literals)
            ++_score_literal_offsets[std::abs(l)+1];
    for (size_t i=1; i<_score_literal_offsets.size(); ++i)
        _score_literal_offsets[i] += _score_literal_offsets[i-1];
    _score_clause_occurrences.resize(_score_literal_offsets.back());
    std::vector<size_t> next = _score_literal_offsets;
    for (size_t c=0; c<_clauses.size(); ++c)
        for (int l : _clauses[c].nia_literals)
            _score_clause_occurrences[next[std::abs(l)]++] = l>0 ? int(c)+1 : -int(c)-1;
    _score_clause_deltas.assign(_clauses.size(), INT32_MIN);
    _score_touched_clauses.clear();
    _score_touched_clauses.reserve(_clauses.size());
    for (variable &v : _vars) {
        v.scoring_coffs.resize(v.var_lit_terms.size());
        v.scoring_coffs_valid = false;
    }
}

// Evaluate only clauses reached by literals whose truth value changes.
MoveScore ls_solver::critical_score(uint64_t var_idx, Int change_value,uint64_t* break_cost){
    if(break_cost)*break_cost=0;
    latest_distance_gain=0;
    const bool geometric=use_distance_scoring() && _step>1000;
    if(geometric){
        if(distance_trial_deltas.size()!=_lits.size()){
            distance_trial_deltas.resize(_lits.size());distance_trial_stamps.assign(_lits.size(),0);
            distance_clause_stamps.assign(_clauses.size(),0);distance_clause_values.resize(_clauses.size());
        }
        ++distance_trial_generation;
    }

#ifdef NIA_SCORING_REFERENCE
    return critical_score_reference(var_idx, change_value,break_cost);
#else
    variable &var = _vars[var_idx];
    if (change_value==0 || var.var_lit_terms.empty()) return 0;
    if (!var.scoring_coffs_valid) {
        for (const term_exp &te : var.term_exps)
            term_coffs[te.term_idx] = coff_in_term(var_idx, te.term_idx, te.exponent);
        for (size_t i=0; i<var.var_lit_terms.size(); ++i) {
            const var_lit_term &vlt = var.var_lit_terms[i];
            var.scoring_coffs[i] = vlt.coff * term_coffs[vlt.term_idx];
        }
        var.scoring_coffs_valid = true;
    }

    const Int future_value = _solution[var_idx]+change_value;
    size_t i=0;
    while (i<var.var_lit_terms.size()) {
        const uint64_t lit_idx = var.var_lit_terms[i].lit_idx;
        const lit &l = _lits[lit_idx];
        Int delta = l.delta;
        do {
            const var_lit_term &vlt = var.var_lit_terms[i];
            const Int coff = var.scoring_coffs[i];
            // Preserve the original arithmetic order, including higher powers.
            if (vlt.exponent==1) delta += coff*change_value;
            else delta += coff*(integer_power(future_value, vlt.exponent)
                              -integer_power(_solution[var_idx], vlt.exponent));
            ++i;
        } while (i<var.var_lit_terms.size() && var.var_lit_terms[i].lit_idx==lit_idx);
        if(geometric){distance_trial_deltas[lit_idx]=delta;distance_trial_stamps[lit_idx]=distance_trial_generation;}
        const bool old_true = l.is_equal ? l.delta==0 : l.delta<=0;
        const bool new_true = l.is_equal ? delta==0 : delta<=0;
        if (old_true==new_true) continue;
        const int change = new_true ? 1 : -1;
        for (size_t p=_score_literal_offsets[lit_idx]; p<_score_literal_offsets[lit_idx+1]; ++p) {
            const int occurrence = _score_clause_occurrences[p];
            const int c = std::abs(occurrence)-1;
            int &count_delta = _score_clause_deltas[c];
            if (count_delta==INT32_MIN) {
                count_delta=0;
                _score_touched_clauses.push_back(c);
            }
            count_delta += occurrence>0 ? change : -change;
        }
    }
    MoveScore score=0;
    for (int c_idx : _score_touched_clauses) {
        const clause &c = _clauses[c_idx];
        const int new_count = c.sat_count+_score_clause_deltas[c_idx];
        if (c.sat_count>0 && new_count==0){
            score-=c.weight;
            if(break_cost)*break_cost+=uint64_t(c.weight);
        }
        else if (c.sat_count==0 && new_count>0) score+=c.weight;
    }
    if(geometric && score>=distance_score_threshold){
        auto clause_distance=[&](int index,bool future){
            double minimum=1e300;
            for(int signed_lit:_clauses[index].literals){
                unsigned id=std::abs(signed_lit);const auto& literal=_lits[id];double value=0;
                if(!literal.is_nia_lit){
                    bool truth=_solution[literal.bool_var]>0;value=truth==(signed_lit>0)?0:std::log(2.0);
                }else{
                    Int delta=(future && distance_trial_stamps[id]==distance_trial_generation)?distance_trial_deltas[id]:literal.delta;
                    if(literal.is_equal){
                        if(signed_lit>0){value=log_magnitude(delta);}
                        else value=delta==0?std::log(2.0):0;
                    }else if(signed_lit>0)value=delta>0?log_magnitude(delta):0;
                    else value=delta<=0?log_magnitude(Int(1-delta)):0;
                }
                minimum=std::min(minimum,value);if(minimum==0)break;
            }
            return minimum;
        };
        for(int index:var.clause_idxs){
            const auto& c=_clauses[index];
            const int delta=_score_clause_deltas[index]==INT32_MIN?0:_score_clause_deltas[index];
            const int after=c.sat_count+delta;
            if(c.sat_count>0 && after>0)continue;
            double old_distance=0,new_distance=0;
            if(c.sat_count==0){
                if(distance_clause_stamps[index]!=distance_current_generation){
                    distance_clause_values[index]=clause_distance(index,false);distance_clause_stamps[index]=distance_current_generation;
                }
                old_distance=distance_clause_values[index];
            }
            if(after==0)new_distance=clause_distance(index,true);
            latest_distance_gain+=c.weight*(old_distance-new_distance);
        }
    }
    for(int index:_score_touched_clauses)_score_clause_deltas[index]=INT32_MIN;
    _score_touched_clauses.clear();
#ifdef NIA_SCORING_VERIFY
    uint64_t reference_break=0;
    const MoveScore reference = critical_score_reference(var_idx, change_value,break_cost?&reference_break:nullptr);
    if (score!=reference || (break_cost && *break_cost!=reference_break)) {
        std::cerr << "incremental score mismatch at step " << _step
                  << ", variable " << var_idx << ": " << score << " != " << reference << '\n';
        std::abort();
    }
#endif
    return score;
#endif
}

#if defined(NIA_SCORING_VERIFY) || defined(NIA_SCORING_REFERENCE)
// Original full clause scan, retained for differential validation.
//calculate score for nia vars
MoveScore ls_solver::critical_score_reference(uint64_t var_idx, Int change_value,uint64_t* break_cost){
    if(break_cost)*break_cost=0;
    if (change_value==0 || _vars[var_idx].var_lit_terms.empty()) return 0;
    MoveScore critical_score=0;
    Int future_value=_solution[var_idx]+change_value;
    variable * var=&(_vars[var_idx]);
    var_lit_term *vlt;
    uint64_t curr_lit_idx=var->var_lit_terms[0].lit_idx;
    Int curr_lit_delta_new=_lits[curr_lit_idx].delta;
    Int coff;//(coff of original term) * (term_value / _solution[var]) ( 2 * x1*x2 ) x1=3, x2=4  -> coff[x2]=2*3
    for(term_exp &te:var->term_exps){term_coffs[te.term_idx]=coff_in_term(var_idx, te.term_idx,te.exponent);}//determine the term coff
    //determine the lit_make_break by going through the vlt of var
    for(int vlt_idx=0;vlt_idx<var->var_lit_terms.size();vlt_idx++){
        vlt=&(var->var_lit_terms[vlt_idx]);
        if(curr_lit_idx!=vlt->lit_idx){
            curr_lit_idx=vlt->lit_idx;
            curr_lit_delta_new=_lits[curr_lit_idx].delta;
        }//enter a new lit
        coff=vlt->coff*term_coffs[vlt->term_idx];
        if(vlt->exponent==1){curr_lit_delta_new+=coff*change_value;}
        else{curr_lit_delta_new+=coff*(integer_power(future_value,vlt->exponent)-integer_power(_solution[var_idx],vlt->exponent));}
        if((vlt_idx==var->var_lit_terms.size()-1)||(curr_lit_idx!=var->var_lit_terms[vlt_idx+1].lit_idx)){
            if(_lits[curr_lit_idx].is_equal){
                if(_lits[curr_lit_idx].delta==0&&curr_lit_delta_new!=0){_lit_make_break[curr_lit_idx]=-1;}
                else if(_lits[curr_lit_idx].delta!=0&&curr_lit_delta_new==0){_lit_make_break[curr_lit_idx]=1;}
            }//equal lit
            else{
                if(_lits[curr_lit_idx].delta<=0&&curr_lit_delta_new>0){_lit_make_break[curr_lit_idx]=-1;}
                else if(_lits[curr_lit_idx].delta>0&&curr_lit_delta_new<=0){_lit_make_break[curr_lit_idx]=1;}
            }//nia <= lit
        }//the last vlt of the var or the last vlt of current lit
    }
    //determine the score by going through the clauses of var
    clause *c;
    for(int cls_idx:var->clause_idxs){
        c=&(_clauses[cls_idx]);
        int clause_sat_count_new=c->sat_count;
        for(int v_lit:c->literals){clause_sat_count_new+=(v_lit>0)?_lit_make_break[v_lit]:(-_lit_make_break[-v_lit]);}
        if(c->sat_count>0&&clause_sat_count_new==0){critical_score-=c->weight;if(break_cost)*break_cost+=uint64_t(c->weight);}
        else if(c->sat_count==0&&clause_sat_count_new>0){critical_score+=c->weight;}
    }
    //recover the lit_make_break
    for(uint64_t l_idx:var->literal_idxs){_lit_make_break[l_idx]=0;}
    return critical_score;
}

#endif

//sat or unsat a clause, update the delta, dedicated for nia var
void ls_solver::move_update(uint64_t var_idx, Int change_value){
    variable *var=&(_vars[var_idx]);
    // Invalidate even when the literal truth value or a zero product is unchanged.
    for (uint64_t literal:var->literal_idxs) {
        critical_operation_caches[2*literal].valid=false;
        critical_operation_caches[2*literal+1].valid=false;
    }
    if(config.fast_moves){
        if(_move_new_watch.size()!=_clauses.size())_move_new_watch.resize(_clauses.size());
        if(_move_bool_dirty.size()!=_clauses.size())_move_bool_dirty.assign(_clauses.size(),0);
    }
    var_lit_term *vlt;
    uint64_t curr_lit_idx=var->var_lit_terms[0].lit_idx;
    Int curr_lit_delta_new=_lits[curr_lit_idx].delta;
    Int term_coff,lit_coff;
    Int future_value=_solution[var_idx]+change_value;
    //update term value
    for(term_exp &te:var->term_exps){
        term_coff=coff_in_term(var_idx, te.term_idx,te.exponent);
        term_coffs[te.term_idx]=term_coff;
        if(te.exponent==1){_terms[te.term_idx].value+=term_coff*change_value;}
        else{_terms[te.term_idx].value=term_coff*integer_power(future_value,te.exponent);}
        // Even when a product stays zero, another factor's coefficient can change.
        for (const var_exp &ve : _terms[te.term_idx].var_epxs)
            _vars[ve.var_index].scoring_coffs_valid=false;
    }
    //determine the lit_is_true by going through the vlt of var, and update the delta of lits
    for(int vlt_idx=0;vlt_idx<var->var_lit_terms.size();vlt_idx++){
        vlt=&(var->var_lit_terms[vlt_idx]);
        if(curr_lit_idx!=vlt->lit_idx){
            curr_lit_idx=vlt->lit_idx;
            curr_lit_delta_new=_lits[curr_lit_idx].delta;
        }//enter a new lit
        lit_coff=term_coffs[vlt->term_idx]*vlt->coff;
        if(vlt->exponent==1){curr_lit_delta_new+=lit_coff*change_value;}
        else{curr_lit_delta_new+=lit_coff*(integer_power(future_value,vlt->exponent)-integer_power(_solution[var_idx],vlt->exponent));}
        if((vlt_idx==var->var_lit_terms.size()-1)||(curr_lit_idx!=var->var_lit_terms[vlt_idx+1].lit_idx)){
            const int old_literal_truth=_lits[curr_lit_idx].is_true;
            if(_lits[curr_lit_idx].is_equal){
                if(_lits[curr_lit_idx].delta==0&&curr_lit_delta_new!=0){_lits[curr_lit_idx].is_true=-1;}
                else if(_lits[curr_lit_idx].delta!=0&&curr_lit_delta_new==0){_lits[curr_lit_idx].is_true=1;}
            }//equal lit
            else{
                if(_lits[curr_lit_idx].delta<=0&&curr_lit_delta_new>0){_lits[curr_lit_idx].is_true=-1;}
                else if(_lits[curr_lit_idx].delta>0&&curr_lit_delta_new<=0){_lits[curr_lit_idx].is_true=1;}
            }//nia <= lit
            _lits[curr_lit_idx].delta=curr_lit_delta_new;//update lit delta
            if(config.fast_moves){
                if((old_literal_truth<0)!=(_lits[curr_lit_idx].is_true<0)){
                    int change=_lits[curr_lit_idx].is_true>=0?1:-1;
                    for(size_t p=_score_literal_offsets[curr_lit_idx];p<_score_literal_offsets[curr_lit_idx+1];++p){
                        int occurrence=_score_clause_occurrences[p],clause_index=std::abs(occurrence)-1;
                        int& delta=_score_clause_deltas[clause_index];
                        if(delta==INT32_MIN){delta=0;_move_new_watch[clause_index]=0;_score_touched_clauses.push_back(clause_index);}
                        int contribution=occurrence>0?change:-change;delta+=contribution;
                        if(contribution>0)_move_new_watch[clause_index]=occurrence>0?int(curr_lit_idx):-int(curr_lit_idx);
                    }
                }

            }
        }//the last vlt of the var or the last vlt of current lit
    }
    //unsat/sat a clause by going through the lits in it
    clause *c;
    lit *l;
    for(int cls_idx:var->clause_idxs){
        c=&(_clauses[cls_idx]);
        const int change=config.fast_moves && _score_clause_deltas[cls_idx]!=INT32_MIN?_score_clause_deltas[cls_idx]:0;
        const bool dirty=config.fast_moves && _move_bool_dirty[cls_idx];
#ifdef NIA_FAST_MOVE_VERIFY
        int actual=0;
        if(config.fast_moves){
            for(int literal:c->literals)if((literal^_lits[std::abs(literal)].is_true)>=0)++actual;
            if(!dirty && actual!=c->sat_count+change){std::cerr<<"move count mismatch step="<<_step<<" clause="<<cls_idx<<"\n";std::abort();}
        }
#endif
        if(config.fast_moves && !dirty && _score_clause_deltas[cls_idx]==INT32_MIN)continue;
        const int clause_sat_count_old=c->sat_count;
        const int watch_lit_idx_old=config.fast_moves && !clause_sat_count_old?0:c->watch_lit_idx;
        if(!config.fast_moves || dirty){
            // A Boolean move can leave legacy metadata inconsistent. Recount
            // the clause when needed, preserving the original watch order.
            c->sat_count=0;
            for(int literal:c->literals)if((literal^_lits[std::abs(literal)].is_true)>=0){++c->sat_count;c->watch_lit_idx=literal;}
            if(config.fast_moves)_move_bool_dirty[cls_idx]=0;
        }else{
            c->sat_count+=change;
            if(c->sat_count>0 && (clause_sat_count_old==0 ||
               (c->watch_lit_idx^_lits[std::abs(c->watch_lit_idx)].is_true)<0)){
                const int watch=_move_new_watch[cls_idx];
                if(watch && (watch^_lits[std::abs(watch)].is_true)>=0)c->watch_lit_idx=watch;
                else for(int literal:c->literals)if((literal^_lits[std::abs(literal)].is_true)>=0){c->watch_lit_idx=literal;break;}
            }
        }
#ifdef NIA_FAST_MOVE_VERIFY
        if(config.fast_moves){
            if(c->sat_count!=actual)std::abort();
            if(c->sat_count>0 && (c->watch_lit_idx^_lits[std::abs(c->watch_lit_idx)].is_true)<0)std::abort();
        }
#endif
        //sat or unsat a clause, update the lit_in_unsat_clause
        if(c->sat_count>0&&clause_sat_count_old==0){
            sat_a_clause(cls_idx);
            _lit_in_unsat_clause_num-=c->literals.size();
            _bool_lit_in_unsat_clause_num-=c->bool_literals.size();
        }
        else if(c->sat_count==0&&clause_sat_count_old>0){
            unsat_a_clause(cls_idx);
            _lit_in_unsat_clause_num+=c->literals.size();
            _bool_lit_in_unsat_clause_num+=c->bool_literals.size();
        }
        //update the sat_clause_with_false_lit
        if(c->sat_count>0&&c->sat_count<c->literals.size()){sat_clause_with_false_literal->insert_element((int)cls_idx);}
        else{sat_clause_with_false_literal->delete_element((int)cls_idx);}
        //update the score of boolean vars
        if(clause_sat_count_old==0&&c->sat_count>0){
            for(int l_sign_idx:c->bool_literals){
                l=&(_lits[std::abs(l_sign_idx)]);
                _vars[l->bool_var].score-=c->weight;
            }
        }
        else if(clause_sat_count_old==1&&c->sat_count>1){
            l=&(_lits[std::abs(watch_lit_idx_old)]);
            if(!l->is_nia_lit){_vars[l->bool_var].score+=c->weight;}
        }
        else if(clause_sat_count_old>0&&c->sat_count==0){
            for(int l_sign_idx:c->bool_literals){
                l=&(_lits[std::abs(l_sign_idx)]);
                _vars[l->bool_var].score+=c->weight;
            }
        }
        else if(clause_sat_count_old>1&&c->sat_count==1){
            l=&(_lits[std::abs(c->watch_lit_idx)]);
            if(!l->is_nia_lit){_vars[l->bool_var].score-=c->weight;}
        }
    }
    if(config.fast_moves){
        for(int index:_score_touched_clauses)_score_clause_deltas[index]=INT32_MIN;
        _score_touched_clauses.clear();

    }
}

//dedicated for boolean var
void ls_solver::move_update(uint64_t var_idx){
    variable *var=&(_vars[var_idx]);
    if(config.fast_moves){
        if(_move_bool_dirty.size()!=_clauses.size())_move_bool_dirty.assign(_clauses.size(),0);
    }
    int v_lit_idx=(int)var->literal_idxs[0];//the lit_idx containing the var
    _lits[v_lit_idx].is_true*=-1;
    int make_break_in_clause=0;
    int cl_sign_idx;
    for(int i=0;i<var->clause_idxs.size();i++){
        cl_sign_idx=var->clause_idxs[i];
        if((_solution[var_idx]>0&&var->bool_var_in_pos_clause[i])||(_solution[var_idx]<0&&!var->bool_var_in_pos_clause[i])){make_break_in_clause=-1;}//true to false
        else{make_break_in_clause=1;}//false to true
        int clause_idx=std::abs(cl_sign_idx);
        if(config.fast_moves){
            _move_bool_dirty[clause_idx]=1;
        }
        clause *cp=&(_clauses[clause_idx]);
        //sat or unsat a clause
        if(cp->sat_count>0&&cp->sat_count+make_break_in_clause==0) {
            unsat_a_clause(clause_idx);
            _lit_in_unsat_clause_num+=cp->literals.size();
            _bool_lit_in_unsat_clause_num+=cp->bool_literals.size();
        }
        else if(cp->sat_count==0&&cp->sat_count+make_break_in_clause>0) {
            sat_a_clause(clause_idx);
            _lit_in_unsat_clause_num-=cp->literals.size();
            _bool_lit_in_unsat_clause_num-=cp->bool_literals.size();
        }
        int origin_watch_lit=cp->watch_lit_idx;
        int origin_sat_count=cp->sat_count;
        cp->sat_count+=make_break_in_clause;
        //update sat_clause_with_false_lit
        if(cp->sat_count>0&&cp->sat_count<cp->literals.size()){sat_clause_with_false_literal->insert_element(clause_idx);}
        else{sat_clause_with_false_literal->delete_element(clause_idx);}
        //update the watch lit
        if(std::abs(origin_watch_lit)==v_lit_idx&&cp->sat_count>0){//if the original watch lit exactly contains the var and the sat_count>0, set the new watch lit
            for(int l_idx:cp->literals){
                if((l_idx^_lits[std::abs(l_idx)].is_true)>=0){cp->watch_lit_idx=l_idx;break;}
            }
        }
        else if(origin_sat_count==0){cp->watch_lit_idx=var->bool_var_in_pos_clause[i]?v_lit_idx:-v_lit_idx;}//if origin sat count==0, then set new watch lit
        //update the score of boolean vars
        if(make_break_in_clause>0){
            if(origin_sat_count==0){
                for(int bl:cp->bool_literals){
                    lit *l=&(_lits[std::abs(bl)]);
                    _vars[l->bool_var].score-=cp->weight;
                }
            }
            else if(origin_sat_count==1){
                lit *l=&(_lits[std::abs(origin_watch_lit)]);
                if(!l->is_nia_lit){_vars[l->bool_var].score+=cp->weight;}
            }
        }
        else if(make_break_in_clause<0){
            if(cp->sat_count==0){
                for(int bl:cp->bool_literals){
                    lit *l=&(_lits[std::abs(bl)]);
                    _vars[l->bool_var].score+=cp->weight;
                }
            }
            else if(cp->sat_count==1){
                lit *l=&(_lits[std::abs(cp->watch_lit_idx)]);
                if(!l->is_nia_lit){_vars[l->bool_var].score-=cp->weight;}
            }
        }
    }
}

//check
bool ls_solver::check_solution(){
    clause *cp;
    int unsat_num=0;
    for(int term_idx=0;term_idx<_terms.size();term_idx++){
        if(!term_appear[term_idx]){continue;}
        term *t=&(_terms[term_idx]);
        Int new_term_value=1;
        for(var_exp &ve:t->var_epxs){
            if(ve.exponent==1){new_term_value*=_solution[ve.var_index];}
            else{new_term_value*=integer_power(_solution[ve.var_index],ve.exponent);}
            if(new_term_value==0){break;}
        }
        if(new_term_value!=_terms[term_idx].value){std::cout<<"term value wrong: "<<term_idx<<"\n";}
    }//check term value
    for(int lit_idx=0;lit_idx<_lits.size();lit_idx++){
        if(_lits[lit_idx].lits_index==0||!_lits[lit_idx].is_nia_lit){continue;}
        Int delta=delta_lit(_lits[lit_idx]);
        if(delta!=_lits[lit_idx].delta){std::cout<<"lit delta wrong: "<<lit_idx<<"\n";}
    }//check lit delta
    for(uint64_t i=0;i<_num_clauses;i++){
        int sat_count=0;
        cp=&(_clauses[i]);
        for(int lit_idx: cp->literals){
            Int delta=_lits[std::abs(lit_idx)].delta;
            bool is_equal=_lits[std::abs(lit_idx)].is_equal;
            if(!_lits[std::abs(lit_idx)].is_nia_lit){
                uint64_t var_idx=_lits[std::abs(lit_idx)].bool_var;
                if((_solution[var_idx]>0&&lit_idx>0)||(_solution[var_idx]<0&&lit_idx<0)){sat_count++;}
            }
            else if( (!is_equal&&((delta<=0&&lit_idx>0)||(delta>0&&lit_idx<0))) || (is_equal&&((delta==0&&lit_idx>0)||(delta!=0&&lit_idx<0))) ){sat_count++;}
        }
        if(sat_count!=cp->sat_count){std::cout<<"sat count wrong at clause "<<i<<"\n";}
        if(sat_count==0){unsat_num++;}
    }
    for(int var_idx=0;var_idx<_vars.size();var_idx++){if((_vars[var_idx].upper_bound && _solution[var_idx]>*_vars[var_idx].upper_bound)||(_vars[var_idx].low_bound && _solution[var_idx]<*_vars[var_idx].low_bound)){std::cout<<"var "<<var_idx<<" out of range\n";}}
    if(unsat_num==unsat_clauses->size())
        std::cout<<"right solution\n";
    else
        std::cout<<"wrong solution\n";
    return unsat_num==unsat_clauses->size();
}

bool ls_solver::update_inner_best_solution(){
    if(raw_cost()<uint64_t(_best_found_hard_cost_this_nia)){
        _best_found_hard_cost_this_nia=raw_cost();
        return true;
    }
    return false;
}

bool ls_solver::update_outer_best_solution(){
    if(raw_cost()<uint64_t(_best_found_hard_cost_this_bool)){
        _best_found_hard_cost_this_bool=raw_cost();
        return true;
    }
    return false;
}

void ls_solver::enter_nia_mode(){
    _best_found_hard_cost_this_nia=raw_cost();
    if(is_in_bool_search){++phase_switches;no_improve_cnt_nia=0;}//only when it is now in boolean mode, the no_improve_cnt_nia will be 0
    is_in_bool_search=false;
}

void ls_solver::enter_bool_mode(){
    _best_found_hard_cost_this_bool=raw_cost();
    if(!is_in_bool_search){++phase_switches;no_improve_cnt_bool=0;}//only when it is now in integer mode, the no_improve_cnt_bool will be 0
    is_in_bool_search=true;
}

//local search
bool ls_solver::local_search(){
    if(build_unsat){return false;}
    int no_improve_cnt=0;
    int flipv;
    Int change_value=0;
    start = std::chrono::steady_clock::now();
    initialize();
    native_repair_cycle_best=best_found_cost;
    random_walk_stall_steps=0;
    random_walk_window_keeps=0;
    _outer_layer_step=1;
    for(_step=1;_step<_max_step;_step++){
        if(0==unsat_clauses->size() && (!uf_enabled || uf_conflicts==0)){
#ifdef NLS_DEBUG
            std::cout<<"step:"<<_step<<"\n";
#endif
            // check_solution();
            up_bool_vars();
            return true;}
        if(_step%1000==0){
            if(TimeElapsed()>_cutoff)break;
        }

        if(uint64_t(no_improve_cnt)>restart_stall_limit){
            if(TimeElapsed()>=_cutoff)break;
            if(restart_repair_enabled){
                prepare_restart_repair();
                if(TimeElapsed()>=_cutoff)break;
            }
            initialize();
            no_improve_cnt=0;
            // A diversified initial assignment can itself satisfy the formula.
            if(unsat_clauses->empty())continue;
            // Auxiliary native-child pipeline is intentionally disabled in mature_mpz.
            if(TimeElapsed()>=_cutoff)break;
        }//restart
        // UF-only iterations must obey the same deadline and stall/restart
        // policy. Their continue must not bypass either check.
        if(uf_enabled && TimeElapsed()>=_cutoff)break;
        if(unsat_clauses->empty() && uf_enabled && uf_conflicts){
            if(uf_interaction=="none")break;
            ++uf_only_iterations;
            if(!try_uf_move(true) && !uf_move_waiting)break;
            no_improve_cnt=update_best_solution()?0:no_improve_cnt+1;
            continue;
        }
        random_walk_stall_steps=no_improve_cnt;
        if(no_improve_cnt==0){random_walk_window_keeps=0;}
        bool time_up_bool=(uint64_t(no_improve_cnt_bool)*uint64_t(_lit_in_unsat_clause_num)>5ULL*uint64_t(_bool_lit_in_unsat_clause_num));
        bool time_up_nia=(uint64_t(no_improve_cnt_nia)*uint64_t(_lit_in_unsat_clause_num)>20ULL*uint64_t(_lit_in_unsat_clause_num-_bool_lit_in_unsat_clause_num));
        if((is_in_bool_search&&_bool_lit_in_unsat_clause_num<_lit_in_unsat_clause_num&&time_up_bool)||_bool_lit_in_unsat_clause_num==0){enter_nia_mode();}
        else if((!is_in_bool_search&&_bool_lit_in_unsat_clause_num>0&&time_up_nia)||(_lit_in_unsat_clause_num==_bool_lit_in_unsat_clause_num)){enter_bool_mode();}
        if(is_in_bool_search){
            ++bool_phase_steps;
            flipv=pick_critical_move_bool();
            if(flipv!=-1){critical_move(flipv, change_value);}
            if(update_outer_best_solution()) no_improve_cnt_bool=0;
            else                              no_improve_cnt_bool++;
        }
        else{
            ++int_phase_steps;
            flipv=pick_critical_move(change_value);
            if(flipv!=-1){critical_move(flipv, change_value);}
            if(update_inner_best_solution()) no_improve_cnt_nia=0;
            else                               no_improve_cnt_nia++;
        }
        no_improve_cnt=(update_best_solution())?0:(no_improve_cnt+1);
    }
    return false;
}


}
