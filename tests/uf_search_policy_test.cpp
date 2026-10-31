#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>
namespace ufnia {
struct UfScoringTest {
    static void initialize(NiaSolver& solver){
        solver.clauses=solver.original;solver.prepare_applications();solver.build_dependencies();solver.initialize_applications();
        solver.clause_weights.assign(solver.clauses.size(),1);solver.initialize_scores();solver.best=solver.assignment;solver.has_best=true;
    }
    static void resumed_greedy_after_escape(){
        z3::context c;auto x=c.int_const("x");NiaSolver solver(c);solver.set_search_policy("greedy");solver.add(x*x==2);initialize(solver);
        size_t v=solver.indices.at(x.id());auto current=solver.native_score(solver.score()),best=current;
        std::vector<NiaSolver::Operation> plateau{{{v,mpz_class(2)}},{{v,mpz_class(-2)}}};
        auto escape=solver.choose_native_operation(plateau,current,best,80,80);
        if(!escape.found || !escape.escape || solver.greedy_evaluations_!=2)throw std::runtime_error("stall>64 disabled greedy evaluation");
        solver.commit_operation(plateau[escape.index],escape.trial,escape.escape);current=escape.trial.score;
        std::vector<NiaSolver::Operation> improving{{{v,mpz_class(3)}},{{v,mpz_class(1)}}};
        auto choice=solver.choose_native_operation(improving,current,best,81,81);
        if(!choice.found || choice.escape || choice.index!=1 || solver.greedy_evaluations_!=4)
            throw std::runtime_error("random escape did not resume greedy search on the next step");
        solver.commit_operation(improving[choice.index],choice.trial,choice.escape);
        if(solver.random_escape_moves_!=1)throw std::runtime_error("random escape move count is wrong");
    }
    static void paws_reselects_winner(){
        z3::context c;auto x=c.int_const("x");NiaSolver solver(c);solver.set_search_policy("greedy");solver.set_scoring("paws");
        solver.add(x>=1);solver.add(x>=2);solver.add(x*x>=4);solver.add(!(x==1));solver.add(!(x==2));initialize(solver);
        solver.clause_weights={1,1,1,2,5};solver.rng.seed(0);size_t v=solver.indices.at(x.id());
        auto current=solver.native_score(solver.score()),best=current;
        std::vector<NiaSolver::Operation> operations{{{v,mpz_class(1)}},{{v,mpz_class(2)}}};
        auto choice=solver.choose_native_operation(operations,current,best,15,80);
        if(!choice.found || choice.escape || choice.index!=1 || choice.trial.score.weighted_unsat!=5)
            throw std::runtime_error("PAWS committed the winner scored before changing weights");
        if(current.weighted_unsat!=6 || best.weighted_unsat!=6 || solver.weight_updates_!=1 ||
           solver.greedy_evaluations_!=4 || solver.paws_reselections_!=1)
            throw std::runtime_error("PAWS did not rebuild current/best and re-evaluate all candidates");
        solver.commit_operation(operations[choice.index],choice.trial,choice.escape);solver.verify_scores();
        if(solver.native_score(solver.score()).weighted_unsat!=choice.trial.score.weighted_unsat)
            throw std::runtime_error("selected weighted trial differs from committed cache");
    }
    static void legacy_is_available(){
        z3::context c;auto x=c.int_const("x");NiaSolver solver(c);solver.add(x*x==1);initialize(solver);
        size_t v=solver.indices.at(x.id());auto current=solver.native_score(solver.score()),best=current;
        std::vector<NiaSolver::Operation> operations{{{v,mpz_class(2)}},{{v,mpz_class(1)}}};
        auto choice=solver.choose_native_operation(operations,current,best,80,80);
        if(!choice.escape || solver.greedy_evaluations_!=0)throw std::runtime_error("legacy permanent-walk control was changed");
    }
    static void run(){resumed_greedy_after_escape();paws_reselects_winner();legacy_is_available();}
};
}
int main(){try{ufnia::UfScoringTest::run();std::cout<<"search policy traces passed\n";}
catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
