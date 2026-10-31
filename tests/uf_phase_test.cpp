#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>
namespace ufnia {
struct UfScoringTest {
    static NiaSolver::NativeScore initialize(NiaSolver& s){
        s.set_search_policy("greedy");s.set_search_phase("alternating");s.clauses=s.original;
        s.prepare_applications();s.build_dependencies();s.initialize_applications();s.build_arithmetic_repairs();
        s.clause_weights.assign(s.clauses.size(),1);s.last_variable_move.assign(s.variables.size(),0);auto current=s.native_score(s.initialize_scores());s.best=s.assignment;s.has_best=true;return current;
    }
    static std::vector<size_t> violated(const NiaSolver& s){
        std::vector<size_t> result;for(size_t i=0;i<s.clauses.size();++i)if(s.clause_scores[i].unsat)result.push_back(i);return result;
    }
    static void integer_finish(NiaSolver& s,NiaSolver::NativeScore& current,NiaSolver::NativeScore& best,unsigned max_steps){
        for(unsigned step=0;step<max_steps && !current.solved();++step){
            std::vector<NiaSolver::Operation> operations,uf;std::set<size_t> repaired;
            s.phase_operations(false,current,violated(s),uf,operations,repaired);
            if(operations.empty())throw std::runtime_error("integer phase produced no repair");
            for(const auto& operation:operations)for(const auto& change:operation)
                if(s.variables[change.first].is_bool())throw std::runtime_error("integer phase changed a frozen Boolean");
            auto choice=s.choose_native_operation(operations,current,best,step,0);
            if(!choice.found)throw std::runtime_error("integer phase failed to choose a repair");
            s.commit_operation(operations[choice.index],choice.trial,choice.escape);current=choice.trial.score;
        }
        if(!current.solved())throw std::runtime_error("integer phase did not finish the Boolean decision");
    }
    static void accepts_temporary_global_regression(){
        z3::context c;auto b=c.bool_const("b");auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");NiaSolver s(c);
        s.add(b);s.add(b==(x>=100));s.add(b==(y>=100));s.add(b==(z>=100));auto current=initialize(s),best=current;
        if(current.arithmetic.unsat!=1 || current.boolean_unsat!=1)throw std::runtime_error("wrong initial platform");
        std::vector<NiaSolver::Operation> operations,uf;std::set<size_t> repaired;
        s.phase_operations(true,current,violated(s),uf,operations,repaired);
        auto choice=s.choose_boolean_operation(operations,current,best,0,0);
        if(!choice.found || choice.trial.score.boolean_unsat!=0 || choice.trial.score.arithmetic.unsat!=3)
            throw std::runtime_error("Boolean phase refused the necessary global 1 -> 3 regression");
        s.commit_operation(operations[choice.index],choice.trial,choice.escape);current=choice.trial.score;
        if(s.assignment[s.indices.at(b.id())]!=1)throw std::runtime_error("Boolean decision was not installed");
        NiaSolver::PhaseState phase;s.enter_phase(phase,false,current);phase.stalled=4;
        s.set_scoring("paws");s.rng.seed(0);s.paws_reweight(current,best);s.observe_phase(phase,current);
        if(phase.stalled!=5)throw std::runtime_error("PAWS reweighting was counted as raw phase progress");
        integer_finish(s,current,best,3);
        if(s.assignment[s.indices.at(b.id())]!=1)throw std::runtime_error("integer repair undid the Boolean choice");
    }
    static void tseitin_gate_closure(){
        z3::context c;auto t=c.bool_const("t"),p=c.bool_const("p"),q=c.bool_const("q");auto x=c.int_const("x"),y=c.int_const("y");NiaSolver s(c);
        s.add(t);s.add(p||t);s.add(q||t);s.add(!t||!p||!q);s.add(p==(x>=0));s.add(q==(y>=0));
        s.assignment[s.indices.at(p.id())]=1;s.assignment[s.indices.at(q.id())]=1;auto current=initialize(s),best=current;
        std::vector<NiaSolver::Operation> operations,uf;std::set<size_t> repaired;
        s.phase_operations(true,current,violated(s),uf,operations,repaired);
        auto choice=s.choose_boolean_operation(operations,current,best,0,0);
        if(!choice.found || operations[choice.index].size()<2 || choice.trial.score.boolean_unsat!=0)
            throw std::runtime_error("Tseitin output was flipped without repairing its Boolean gate inputs");
        s.commit_operation(operations[choice.index],choice.trial,choice.escape);current=choice.trial.score;
        integer_finish(s,current,best,2);
    }
    static void run(){accepts_temporary_global_regression();tseitin_gate_closure();}
};
}
int main(){try{ufnia::UfScoringTest::run();std::cout<<"Boolean integer phase traces passed\n";}
catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
