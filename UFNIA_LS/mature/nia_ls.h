#pragma once
#include <gmpxx.h>
#include <optional>
#include <set>
#include <unordered_map>
#include <climits>
#include "nia_linear_initialize.h"
#include "nia_novelty_score.h"
#include <cstdio>
#include <chrono>
#include <string.h>
#include<stack>
#include<random>
#include<map>
#include <fstream>
#include <iostream>
#include <algorithm>
#include <cstdlib>
#include<exception>
#include<time.h>
#include<signal.h>
#include<unistd.h>
#include<sys/types.h>
#include<string>
#include "nia_Array.h"
#include <algorithm>

namespace nia {
using Int=mpz_class;
using MoveScore=int64_t;
inline Int integer_power(const Int& value,int exponent){Int result;mpz_pow_ui(result.get_mpz_t(),value.get_mpz_t(),exponent);return result;}


//the var_index=1(_vars[1]=x), exponent=2: x^2
struct var_exp{
    int var_index;
    int exponent=1;
    var_exp(){};
    var_exp(int index):var_index(index){};
};

//the term are in the form of x^3*y^2*z, stored in var_exps, and var_exps are sorted according to its exponents
struct term{
    std::vector<var_exp>        var_epxs;
    Int                   value;
};

//term_in_lit means the term containing the var_exp in the corresponding lit, with the coff coffient
struct var_lit_term{
    uint64_t var_idx;
    int exponent;
    uint64_t term_idx;
    uint64_t lit_idx;
    Int coff;
    var_lit_term(var_exp &_ve,uint64_t _term_idx,int _lit_idx,Int _coff):var_idx(_ve.var_index),exponent(_ve.exponent),term_idx(_term_idx),lit_idx(_lit_idx),coff(_coff){};

};

struct coff_term{
    int term_idx;
    Int coff;
    coff_term(){};
    coff_term(int _term_idx,Int _coff):term_idx(_term_idx),coff(_coff){};
};
//one arith lit is in the form of a_1*term_1+...+a_n*term_n+k<=0, the cofficient are divided into positive ones and negative ones, the coff are positive.
//if coff =1 , -1 term=xy, ab means xy-ab
//if is_nia_lit: \sum coff*term<=key
//else:_vars[delta]
struct lit{
    std::vector<coff_term>          coff_terms;//sort by term
    std::vector<var_lit_term>      var_lit_terms;//sort by var
    Int                     key;
    int                         lits_index;
    Int                     delta=0;//arithmetic residual only
    uint64_t bool_var=0;
    bool                        is_equal=false;//true means a-b-k==0, else a-b-k<=0
    bool                        is_nia_lit=false;
    int                         is_true;//1 means the lit is true, -1 otherwise
};

struct term_exp{
    uint64_t term_idx;
    int exponent;
    term_exp(uint64_t _term_idx,int _exponent):term_idx(_term_idx),exponent(_exponent){};
};
//given a var v with a term_exp(t,e), means v is in term t, with exponent e

struct variable{
    std::vector<int>            clause_idxs;
    std::vector<bool>            bool_var_in_pos_clause;//true means the boolean var is the pos form in corresponding clause
    std::vector<var_lit_term>   var_lit_terms;//sort by lit
    std::vector<uint64_t>         literal_idxs;
    std::vector<term_exp>       term_exps;//(no repeating)
    // Coefficients aligned with var_lit_terms, valid until a shared term changes.
    std::vector<Int>     scoring_coffs;
    bool                       scoring_coffs_valid=false;
    std::string                 var_name;
    std::optional<Int> low_bound;
    std::optional<Int> upper_bound;
    bool                        is_nia;
    bool                        is_delete=false;
    MoveScore                   score;//if it is a bool var, then the score is calculated beforehand
    int                         up_bool=0;//the bool value of variables deleted(1 true -1 false)
};

struct clause{
    std::vector<int>            literals;//literals[i]=l means the ith literal of the clause if the pos(neg) of the _lits, it can be negative
    std::vector<int>            nia_literals;
    std::vector<int>             bool_literals;
    MoveScore                    weight=1;
    int                          sat_count;
    int                          watch_lit_idx;//the lit_idx of sat literal in the clause
    bool                         is_delete=false;
};

class ls_solver{
public:
    
    struct UfApplication {unsigned function;std::vector<size_t> arguments;size_t result;};
    struct UfKey {unsigned function=0;std::vector<Int> arguments;bool operator<(const UfKey& b)const{return function!=b.function?function<b.function:arguments<b.arguments;}};
    struct UfValue {UfKey key;Int result;};
    struct UfBucket {
        std::map<Int,unsigned> frequencies;std::map<Int,std::set<size_t>> members_by_result;std::set<size_t> members;std::map<unsigned,unsigned> frequency_counts;
        void change_frequency(const Int& result,int delta){auto found=frequencies.find(result);unsigned old=found==frequencies.end()?0:found->second;if(old){auto count=frequency_counts.find(old);if(!--count->second)frequency_counts.erase(count);}unsigned next=unsigned(int64_t(old)+delta);if(next){frequencies[result]=next;++frequency_counts[next];}else if(found!=frequencies.end())frequencies.erase(found);}
        unsigned conflicts()const{return unsigned(members.size())-(frequency_counts.empty()?0:frequency_counts.rbegin()->first);}
    };
    using UfPatch=std::map<size_t,Int>; // Absolute mature values; Bool=-1/+1.
    struct UfCandidate {UfPatch patch;bool separation=false,compensated=false;};
    struct UfLinearRow {Int key;std::vector<std::pair<size_t,Int>> terms;};
    std::vector<UfLinearRow> uf_linear_rows;
    std::vector<std::vector<size_t>> uf_variable_rows;
    uint64_t uf_compensation_proposals=0,uf_compensated_moves=0;
    bool uf_enabled=false;
    std::string uf_interaction="joint",uf_scoring="count";
    std::vector<UfApplication> uf_applications;
    std::vector<UfValue> uf_values;
    std::map<UfKey,UfBucket> uf_buckets;
    std::set<UfKey> uf_conflicting_keys;
    std::vector<std::vector<size_t>> variable_applications;
    uint64_t uf_conflicts=0,uf_weight=1,uf_proposals=0,uf_group_moves=0,uf_separation_moves=0,uf_max_patch=0;
    uint64_t uf_trial_applications=0,uf_bucket_rebuilds=0,uf_joint_evaluations=0,uf_single_evaluations=0,uf_escape_moves=0;
    uint64_t initial_arithmetic_unsat=0,best_arithmetic_unsat=UINT64_MAX,best_uf_conflicts=UINT64_MAX;
    uint64_t uf_only_iterations=0,uf_only_weight_updates=0,uf_tabu_rejections=0,uf_aspirations=0,uf_tabu_overrides=0,uf_tabu_waits=0;
    uint64_t uf_consecutive_tabu_waits=0;
    bool uf_move_waiting=false;
    uint64_t best_total_arithmetic_unsat=UINT64_MAX,best_total_uf_conflicts=UINT64_MAX;
    Int uf_variable_value(size_t var)const;
    UfValue evaluate_uf_application(size_t id)const;
    void insert_uf_application(size_t id,const UfValue& value);
    void remove_uf_application(size_t id,const UfValue& value);
    void prepare_uf();
    void initialize_uf();
    void commit_uf_variable(size_t var);
    MoveScore uf_gain(const UfPatch& patch,uint64_t* future_conflicts=nullptr);
    MoveScore uf_single_gain(size_t var,const Int& target);
    bool valid_uf_patch(const UfPatch& patch)const;
    std::vector<UfCandidate> uf_candidates();
    UfPatch compensate_uf_patch(const UfPatch& seed)const;
    MoveScore score_uf_patch(const UfPatch& patch,uint64_t* future_cnf=nullptr,uint64_t* future_uf=nullptr);
    bool uf_patch_tabu(const UfPatch& patch)const;
    bool try_uf_move(bool allow_escape=false);
    bool validate_uf()const;
    void verify_uf_state()const;
    uint64_t raw_cost()const{return uint64_t(unsat_clauses->size())+(uf_enabled?uf_conflicts:0);}

    // Raw critical operations shared by clauses containing the same signed literal.
    using raw_critical_operation = std::pair<uint64_t,Int>;
    struct critical_operation_cache {
        bool valid=false;
        std::vector<raw_critical_operation> moves;
    };
    std::vector<critical_operation_cache> critical_operation_caches;
    std::vector<raw_critical_operation>* capturing_critical_operations=nullptr;
    uint64_t candidate_cache_hits=0, candidate_cache_rebuilds=0;
    void generate_operations_from_false_lit(bool use_tabu,int lit_idx,int& operation_idx);

    //basic information
    uint64_t                    _num_vars;
    uint64_t                    _num_nia_vars;
    uint64_t                    _num_lits;
    int                         _num_nia_lits;
    int                         _num_bool_lits;
    uint64_t                    _num_clauses;
    uint64_t                    _num_opt=0;//the number of vars in all literals, which is the max number of operations
    std::vector<variable>       _vars;
    std::vector<variable>       _tmp_vars;
    std::vector<variable>        _resolution_vars;
    std::vector<int>            nia_var_vec;
    std::vector<int>            bool_var_vec;
    std::vector<Int>     term_coffs;//given a current var, the term_coff[t] means the term_coff of var in term t
    std::vector<term>           _terms;
    std::vector<lit>            _lits;
    std::vector<int>            _lit_make_break;//making a move will make or break the lit itself (1:make, -1:break, 0:no change)
    std::vector<int>            _bound_lits;//record the index of bounded lits
    std::vector<clause>         _clauses;
    // CSR literal occurrences: signed (clause index + 1), preserving multiplicity.
    std::vector<size_t>         _score_literal_offsets;
    std::vector<int>            _score_clause_occurrences;
    std::vector<int>            _move_new_watch;
    std::vector<unsigned char> _move_bool_dirty;

    std::vector<int>            _score_clause_deltas; // INT32_MIN means untouched.
    std::vector<int>            _score_touched_clauses;
    std::stack<clause>          _reconstruct_stack;
    std::vector<uint64_t>       _frequency_in_unsat_clauses;
    std::vector<bool>           is_in_unsat_clause;
    std::vector<int>            lits_in_unsat_clause;
    Array                       *unsat_clauses=NULL;
    Array                       *sat_clause_with_false_literal=NULL;//clauses with 0<sat_num<literal_num, from which swap operation are choosen
    Array                       *contain_bool_unsat_clauses=NULL;//unsat clause with at least one boolean var
    Array                       *false_lit_occur=NULL;//the false lits for choosing critical move
    Array                       *var_in_long_term=NULL;//the var in terms with length>2
    int                         nia_var_idx_with_most_lits;
    bool                        use_pbs=false;
    bool                        is_idl=true;//if it is the IDL mode
    uint64_t                    last_op_var=UINT64_MAX;
    Int                  last_op_value;//the last value and last var, x +1, at least at next step, x -1 is forbidden
    bool                        has_high_coff=false;//if there exists x*x
    const bool                  _record_frequency;
    //solution
    std::vector<Int>       _solution;
    std::vector<Int>       _best_solutin;
    std::vector<int>            _best_literal_assignment;
    int                         best_found_cost=INT32_MAX;
    int                         best_found_this_restart;
    uint64_t                    initialization_round=0;
    std::map<std::string,std::string> progressive_values;
    const SlsConfig config;
    bool input_unit_dense=false,input_gate_dense=false,input_large=false,input_binary=false,input_small=false,input_sparse=false,input_wide_clause=false;
    std::vector<Int> first_repair_snapshot;
    std::string native_repair_input;
    std::map<std::string,std::string> external_sat_values;
    double native_repair_spent=0;
    int native_repair_cycle_best=INT32_MAX; // Global best at the start of this restart cycle.
    unsigned native_repair_calls=0;
    bool force_progressive=false;
    unsigned core_jitter=0;
    bool core_perturb_enabled=true; // Keep restart cleanup when only perturbation is disabled.
    // Auto forwards these two options only to its first auxiliary child.
    bool first_stage_distance_scoring=false, first_stage_joint_operators=true;
    bool second_stage_enabled=true; // Auto: stepnovel is enabled unless explicitly disabled.
    StepnovelChoice stepnovel_choice=StepnovelChoice::softmax; // Only forwarded to the second child.
    double stepnovel_beta=2.0;
    AdaptiveSoftmax stepnovel_softmax;
    uint64_t sampling_calls=0,sampling_nonbest=0;
    bool distance_scoring_enabled=true, joint_operators_enabled=false;
    bool use_distance_scoring() const {return config.distance_scoring && distance_scoring_enabled;}
    enum class JointKind { divisibility, preserve_equality };
    struct JointOperation {
        size_t x,y;
        Int x_value,y_value;
        JointKind kind;
    };
    uint64_t joint_probes=0,joint_attempts=0,joint_candidates=0,joint_evaluations=0;
    uint64_t joint_divisibility_moves=0,joint_preserving_moves=0;
    std::vector<JointOperation> generate_joint_operations();
    bool score_joint_operation(const JointOperation& op,int64_t& score) const;
    bool apply_joint_operation(const JointOperation& op);
    bool try_joint_move();
    std::vector<size_t> nonlinear_core;
    bool core_ranked=false;
    std::vector<bool> core_locked;
    std::vector<Int> core_elite,core_lower,core_upper;
    double latest_distance_gain=0;
    MoveScore distance_score_threshold=INT64_MIN;
    uint64_t distance_trial_generation=0,distance_current_generation=1;
    std::vector<Int> distance_trial_deltas;
    std::vector<uint64_t> distance_trial_stamps,distance_clause_stamps;
    std::vector<double> distance_clause_values;
    // Match each existing strategy's coefficient width without compiling a
    // second solver. Wide strategies keep exact 128-bit integer coefficients.
    Int search_coefficient(Int value) const { return value; }
    // Temporary repair equalities never become local-search variable bounds.
    bool                        restart_repair_enabled=false;
    uint64_t                    restart_stall_limit;
    double                      restart_repair_seconds=0.5;
    double                      restart_repair_budget=30.0;
    double                      restart_repair_spent=0.0;
    uint64_t                    restart_repair_calls=0;
    uint64_t                    restart_repair_installed=0;
    unsigned                    restart_repair_failures=0;
    unsigned                    restart_repair_last_fixed=0;
    unsigned                    restart_repair_widened=0;
    unsigned                    restart_repair_sparse=0;
    std::vector<size_t>         restart_repair_fixed_variables;
    std::vector<unsigned>       restart_repair_domain_edges;
    std::vector<Int>      restart_best_assignment;
    std::vector<Int>      restart_seed;
    int                         no_improve_cnt_bool=0;
    int                         no_improve_cnt_nia=0;
    int                         random_walk_stall_steps=0;//read-only mirror of local_search no_improve_cnt
    int                         random_walk_window_keeps=0;//0-3 eligible keeps since the last allowed window override
    uint64_t bool_phase_steps=0,int_phase_steps=0,phase_switches=0,weight_updates=0,tabu_rejections=0;
    bool                        is_in_bool_search=false;
    int                         _best_found_hard_cost_this_bool;
    int                         _best_found_hard_cost_this_nia;
    //control
    uint64_t                    _step;
    uint64_t                    _outer_layer_step;
    const uint64_t              _max_step;
    std::vector<uint64_t>       tabulist;//tabulist[2*var] and [2*var+1] means that var are forbidden to move forward or backward until then
    std::vector<bool>           is_chosen_bool_var;
    std::vector<uint64_t>       last_move;
    std::vector<uint64_t>            operation_var_idx_vec;
    std::vector<Int>        operation_change_value_vec;
    std::vector<int>             operation_var_idx_bool_vec;
    std::chrono::steady_clock::time_point start;
    double                      best_cost_time;
    double                      _cutoff;
    std::mt19937                mt;
    const uint64_t              _additional_len;
    std::map<std::string,uint64_t> name2var;//map the name of a variable to its index
    std::map<std::string,uint64_t> name2tmp_var;
    std::map<std::string,uint64_t> name2resolution_var;
    std::map<std::string,uint64_t>  str2termidx;//map the term string to its index, how to hash the term? etc: x^3*y^2 the idx of x and y is 1 and 2 then sort x,y first to get "_1^3_2^2"
    std::vector<std::optional<Int>> _pre_value_1;//the 1st pre-set value of a var, if the var is in the form of (a==0 OR a==1)
    std::vector<std::optional<Int>> _pre_value_2;
    bool                         use_swap_from_from_small_weight;
    std::vector<bool>           term_appear;//true means the term exists
    std::vector<bool>           lit_appear;//true means the lit exists in the formula
    const bool                  _complete_ls;
    // data structure for clause weighting
    const uint64_t              smooth_probability;
    uint64_t                    _swt_threshold;
    float                       _swt_p;//w=w*p+ave_w*(1-p)
    uint64_t                    total_clause_weight;
    int                         _lit_in_unsat_clause_num;
    int                         _bool_lit_in_unsat_clause_num;
    //data structure for equality
    std::vector<int>            fa;//fa[x]=f means x=coff*f
    std::vector<Int>            fa_coff;//fa_coff[x]=c means x==c*fa[x]

    
    //input transformation
    bool                        has_unidentified_lits=false;//if the literals contains unidentified lits
    void                        split_string(std::string &in_string, std::vector<std::string> &str_vec,std::string pattern);
    void                        build_lits(std::string &in_string);
    void                        build_instance(std::vector<std::vector<int> > clause_vec);
    void                        equal_vars(const std::vector<std::vector<int> >& clause_vec);//find out the equalilty among vars, and reset the terms and lits
    void                        delete_redundant_clauses(std::vector<std::vector<int> >& clause_vec);
    int                        find(int var_idx);//return the fa of var_idx
    bool                        merge(int var_idx_1,int var_idx_2,Int coff_1,Int coff_2);//var_1*coff_1=var_2*coff_2

    void                        update_lit_equal(int lit_idx);
    uint64_t                    transfer_name_to_reduced_var(std::string & name,bool is_nia);//after the resolution vars are inserted into _vars
    uint64_t                    transfer_name_to_resolution_var(std::string &name,bool is_nia);//bool var is inserted into _resolution_var when build lit
    uint64_t                    transfer_name_to_tmp_var(std::string &name);//nia var is first inserted into _tmp_var when build lit, then inserted into _resolution_var when reduce var(x-y->z)
    uint64_t                    transfer_term_to_idx(term t);
    void                        transfer_term_to_str(term &t,std::string &str);
    void                        reduce_vars();//reduce the x-y in all lits to new var z
    void                        modify_term_exp(term &t);
    bool                        is_high_coff();
    
    
    //initialize
    ls_solver();
    ls_solver(int random_seed,uint64_t max_step=UINT64_MAX,bool complete_ls=false,bool record_frequency=false,SlsStrategy strategy=SlsStrategy::plain);
    void                        make_space();
    void                        make_lits_space(uint64_t num_lits){_num_lits=num_lits;_lits.resize(num_lits+_additional_len);};
    void                        initialize();
    void                        initialize_variable_datas();
    void                        initialize_term_datas();
    void                        initialize_lit_datas();
    void                        initialize_clause_datas();
    void                        unit_prop();
    void                        resolution();
    Int                  hash_lits_to_num(std::vector<int> &lits);
    void                        reduce_clause();
    void                        set_pre_value();
    void                        read_model();
    bool                        is_same_lits(std::vector<int> &lits_1,std::vector<int> &lits_2);
    bool                        build_unsat=false;
    
    //random walk
    void                        update_clause_weight();
    void                        smooth_clause_weight();
    void                        random_walk();
    void                        no_operation_random_walk();//when there is no operation, simply find a lit in a random false clause, pick a random var with coff!=0, set it to 0
    
    //construction
    void                        construct_slution_score();//construct the solution based on score
    
    //basic operations
    inline void                 sat_a_clause(uint64_t clause_idx){unsat_clauses->delete_element((int)clause_idx);contain_bool_unsat_clauses->delete_element((int)clause_idx);};
    inline void                 unsat_a_clause(uint64_t clause_idx){unsat_clauses->insert_element((int)clause_idx);
                                 if(_clauses[clause_idx].bool_literals.size()>0)contain_bool_unsat_clauses->insert_element((int)clause_idx);};
    bool                        update_best_solution();
    uint64_t                    assignment_cost(const std::vector<Int>& assignment,
                                                std::vector<unsigned>* conflicts=nullptr,
                                                std::vector<std::vector<size_t>>* violated_variables=nullptr) const;
    void                        prepare_restart_repair();
    int                         pick_critical_move(Int &best_value);
    int                         pick_critical_move_bool();
    void                        critical_move(uint64_t var_idx,Int change_value);
    void                        move_update(uint64_t var_idx,Int change_value);
    void                        move_update(uint64_t var_idx);//dedicated for boolean var
    void                        invert_lit(lit &l);
    Int                     delta_lit(lit &l);
    Int                  coff_in_term(uint64_t var_idx,uint64_t term_idx,int exponent);
    void                        add_coff(uint64_t var_idx_curr, bool use_tabu, int lit_idx, int &operation_idx,Int coff_1);
    void                        add_coff(uint64_t var_idx_curr, bool use_tabu, int lit_idx, int &operation_idx,Int coff_1,Int coff_2,Int coff_0);

    double                      TimeElapsed();
    void                        clear_prev_data();
    Int                     devide(Int a, Int b);
    void                        insert_operation(uint64_t var_idx,Int change_value,int &operation_idx,bool use_tabu);
    void                        add_bool_operation(bool use_tabu,int lit_idx,int &operation_idx_bool);//given a false lit, add the corresponding var to the operation_vec_bool
    void                        add_operation_from_false_lit(bool use_tabu,int lit_idx,int &operation_idx);//given a false lit(lit_idx<0 means it is the neg form)
    void                        select_best_operation_from_vec(int operation_idx,MoveScore &best_score,int &best_var_idx,Int &best_value);
    void                        select_best_operation_from_bool_vec(int operation_idx_bool,MoveScore &best_score_bool,int &best_var_idx_bool);
    void                        add_swap_operation(int &operation_idx);
    void                        swap_from_small_weight_clause();
    void                        enter_nia_mode();
    void                        enter_bool_mode();
    bool                        update_inner_best_solution();
    bool                        update_outer_best_solution();
    void                        modify_frequency_in_unsat_clauses();
    //print
    void                        print_formula();
    void                        print_literal(lit &l);
    void                        print_formula_pbs();
    void                        print_lit_pbs(lit &l);
    void                        print_formula_smt();
    void                        print_lit_smt(int lit_idx);
    void                        print_mv();
    void                        print_mv_vars(uint64_t var_idx);
    void                        print_var_solution(std::string &var_name,std::string &var_value);
    void                        print_term(term &t);
    void                        up_bool_vars();
    bool                        get_lits_value(uint64_t lit_idx,const std::string & str);
    //calculate score
    MoveScore                   critical_score(uint64_t var_idx,Int change_value,uint64_t* break_cost=nullptr);
    void                        initialize_scoring_index();
#if defined(NIA_SCORING_VERIFY) || defined(NIA_SCORING_REFERENCE)
    MoveScore                   critical_score_reference(uint64_t var_idx,Int change_value,uint64_t* break_cost=nullptr);
#endif
    Int                     critical_subscore(uint64_t var_idx,Int change_value);
    //check
    bool                        check_solution();
    inline bool                 is_single_var_term(term &t){return t.var_epxs.size()==1&&t.var_epxs[0].exponent==1;}//if the term is in the form of X (single var)
    //handle 128
    inline Int abs_128(const Int& n){return n>=0?n:Int(-n);}
    std::string                 print_128(Int n);
    //local search
    bool                        local_search();
    //delete
    ~ls_solver();
};
}
