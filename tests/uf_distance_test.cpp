#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>
namespace ufnia {
struct UfScoringTest {
    static void run(){
        z3::context c;NiaSolver solver(c);auto p=c.bool_const("p"),q=c.bool_const("q");
        solver.add(p || q);auto shared=p;
        for(unsigned depth=0;depth<40;++depth)shared=shared && shared;
        solver.budget_active=true;solver.deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(80);
        double result;
        try{result=solver.distance(shared);}
        catch(const NiaSolver::BudgetExpired&){throw std::runtime_error("distance expanded the shared depth-40 Boolean DAG");}
        solver.budget_active=false;
        if(result!=std::ldexp(1.0,40))throw std::runtime_error("shared-DAG distance changed its exact sum");
        auto guard=c.bool_const("guard");solver.collect(guard);solver.set_distance("structural");
        auto equality=c.bool_val(true)==(p && q);
        if(solver.distance(equality)!=2)throw std::runtime_error("Bool equality lost the cost of two required flips");
        solver.assignment[solver.indices.at(p.id())]=1;
        if(solver.distance(equality)!=1)throw std::runtime_error("Bool equality provides no progress after the first flip");
        solver.assignment[solver.indices.at(p.id())]=0;
        auto choice=z3::ite(guard,c.bool_val(true),p && q);
        if(solver.distance(choice)!=1)throw std::runtime_error("ITE distance ignores a cheaper guard switch");
        if(solver.distance(equality,false)!=0)throw std::runtime_error("Bool equality desired-false distance is inconsistent");
        solver.assignment[solver.indices.at(p.id())]=1;solver.assignment[solver.indices.at(q.id())]=1;
        if(solver.distance(equality,false)!=1)throw std::runtime_error("Bool disequality does not use crossed truth values");
        if(solver.distance(shared)!=0)throw std::runtime_error("distance cache escaped its assignment");
    }
};
}
int main(){try{ufnia::UfScoringTest::run();std::cout<<"structural and shared-DAG distances passed\n";}
catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
