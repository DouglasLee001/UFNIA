#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>
namespace ufnia {
struct UfScoringTest {
    static void initialize(NiaSolver& s){
        s.clauses=s.original;for(const auto& e:s.clauses)s.bounds(e);
        s.prepare_applications();s.build_dependencies();s.initialize_applications();s.initialize_scores();
        s.clause_weights.assign(s.clauses.size(),1);s.last_variable_move.assign(s.variables.size(),0);
    }
    static void exact_signed_case(unsigned kind){
        z3::context c;auto x=c.int_const("x");NiaSolver s(c);s.set_candidate_policy("critical");
        auto clause=kind==0?!(x*x==4):kind==1?(x*x>4):kind==2?!(x*x>=4):(-3*x==6);
        if(kind<3)s.add(x>=0);
        s.add(clause);s.assignment[s.indices.at(x.id())]=kind<3?2:0;initialize(s);
        auto original=s.assignment;std::vector<NiaSolver::Operation> operations;s.arithmetic_operations(clause,operations);
        if(operations.empty() || s.assignment!=original)throw std::runtime_error("signed candidate ranking changed assignment or lost all moves");
        {NiaSolver::Repair patch(s);for(const auto& change:operations.front())patch.put(change.first,change.second);
         if(s.eval(clause)==0)throw std::runtime_error("NOT/strict/signed equality put a non-satisfying value first");}
        if(!s.critical_candidates_)throw std::runtime_error("signed exact critical value was not counted");
    }
    static void indivisible_equality_keeps_fallback(){
        z3::context c;auto x=c.int_const("x");NiaSolver s(c);s.set_candidate_policy("critical");auto clause=3*x==1;
        s.add(clause);initialize(s);std::vector<NiaSolver::Operation> operations;s.arithmetic_operations(clause,operations);
        if(operations.empty() || s.critical_candidates_ || !s.fallback_candidates_)throw std::runtime_error("nondivisible equality was mislabeled critical or lost fallback");
        for(const auto& operation:operations){NiaSolver::Repair patch(s);for(const auto& change:operation)patch.put(change.first,change.second);
            if(s.eval(clause)!=0)throw std::runtime_error("an impossible integer equality was accepted");}
    }
    static void conjunctive_distance_precedes_small_step(){
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y");NiaSolver s(c);s.set_candidate_policy("critical");
        auto clause=(x*x==4)&&(y*y==4);s.add(x>=0);s.add(y>=0);s.add(clause);initialize(s);
        std::vector<NiaSolver::Operation> operations;s.arithmetic_operations(clause,operations,true,2);
        if(operations.size()!=2 || s.critical_candidates_)throw std::runtime_error("conjunction candidate setup is wrong");
        for(const auto& operation:operations)for(const auto& change:operation)if(change.second!=2)
            throw std::runtime_error("whole-clause-false fallback ignored exact subgoal distance and crowded out root 2");
    }
    static void zero_factors_keep_both_unit_steps(){
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");NiaSolver s(c);s.set_candidate_policy("critical");
        auto clause=x*y*z==1;s.add(clause);initialize(s);std::vector<NiaSolver::Operation> operations;s.arithmetic_operations(clause,operations);
        if(operations.empty() || s.critical_candidates_ || !s.fallback_candidates_)throw std::runtime_error("zero-factor plateau lost all multistep fallbacks");
        for(size_t v=0;v<s.variables.size();++v){bool negative=false,positive=false;
            for(const auto& operation:operations){auto found=operation.find(v);if(found!=operation.end()){negative|=found->second==-1;positive|=found->second==1;}}
            if(!negative || !positive)throw std::runtime_error("zero-factor activation lost a legal +/-1 fallback");
        }
    }
    static void cap_case(){
        z3::context c;NiaSolver s(c);z3::expr_vector atoms(c);
        for(unsigned i=0;i<32;++i){auto x=c.int_const(("x"+std::to_string(i)).c_str());s.add(x>=0);atoms.push_back(x*x==4);}
        auto clause=z3::mk_or(atoms);s.add(clause);initialize(s);
        std::vector<NiaSolver::Operation> operations;s.arithmetic_operations(clause,operations);
        bool has_two=false,all_one=true;
        for(const auto& operation:operations)for(const auto& change:operation){has_two|=change.second==2;all_one&=change.second==1;}
        if(operations.size()!=32 || has_two || !all_one)throw std::runtime_error("broad candidate ordering changed");
        auto original=s.assignment;s.set_candidate_policy("critical");operations.clear();s.arithmetic_operations(clause,operations);
        if(s.assignment!=original)throw std::runtime_error("critical ranking leaked a tentative assignment");
        if(operations.size()!=32)throw std::runtime_error("critical changed the fair candidate capacity");
        for(const auto& operation:operations)for(const auto& change:operation)
            if(change.second!=2)throw std::runtime_error("critical cap still omits a satisfying xi=2 representative");
        if(!s.critical_candidates_ || !s.candidate_probes_)throw std::runtime_error("critical candidate diagnostics are missing");
    }
};
}
int main(){try{ufnia::UfScoringTest::cap_case();for(unsigned kind=0;kind<4;++kind)ufnia::UfScoringTest::exact_signed_case(kind);
ufnia::UfScoringTest::indivisible_equality_keeps_fallback();ufnia::UfScoringTest::conjunctive_distance_precedes_small_step();ufnia::UfScoringTest::zero_factors_keep_both_unit_steps();std::cout<<"critical candidate policies passed\n";}
catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
