#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>
namespace ufnia {
struct UfScoringTest {
    static void initialize(NiaSolver& solver){
        solver.clauses=solver.original;for(const auto& e:solver.clauses)solver.bounds(e);
        solver.prepare_applications();solver.build_dependencies();solver.initialize_scores();solver.initialize_applications();
        solver.clause_weights.assign(solver.clauses.size(),1);solver.build_arithmetic_repairs();
    }
    static void quotient_case(int start,int oldq,int oldr,int target,int low,int high,int divisor,
                              bool feasible=true,bool fixed_q=false,const std::string& interaction="joint"){
        z3::context c;auto x=c.int_const("x"),q=c.int_const("q"),r=c.int_const("r");NiaSolver solver(c);
        solver.set_arithmetic_repair("quotient");solver.set_interaction(interaction);
        solver.add(x==divisor*q+r);solver.add(r>=low);solver.add(r<=high);solver.add(x*x==target*target);
        if(fixed_q)solver.add(q==oldq);
        solver.assignment[solver.indices.at(x.id())]=start;solver.assignment[solver.indices.at(q.id())]=oldq;
        solver.assignment[solver.indices.at(r.id())]=oldr;initialize(solver);
        if(solver.quotient_relations.empty())throw std::runtime_error("bounded remainder relation not recognized");
        std::vector<NiaSolver::Operation> operations{{{solver.indices.at(x.id()),mpz_class(target)}}};
        auto original=solver.assignment;std::set<size_t> repaired;solver.repair_arithmetic_operations(operations,repaired);
        if(solver.assignment!=original)throw std::runtime_error("candidate generation changed the assignment");
        bool solved=false;auto current=solver.native_score(solver.score());
        for(const auto& operation:operations){
            for(const auto& change:operation)if(solver.clamp(change.first,change.second)!=change.second)
                throw std::runtime_error("generated quotient patch violates a variable bound");
            auto trial=solver.trial_operation(operation,current);solved|=trial.score.solved();
            if(trial.score.solved()){
                auto found=operation.find(solver.indices.at(x.id()));
                if(found==operation.end() || found->second!=target)throw std::runtime_error("repair undid the protected primary move");
            }
        }
        if(solved!=feasible)throw std::runtime_error("incorrect feasibility of bounded quotient/remainder reconstruction");
        if(feasible && (repaired.empty() || !solver.arith_repair_proposals_))throw std::runtime_error("repair was not counted");
    }
    static void linear_closure(){
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");NiaSolver solver(c);
        solver.set_arithmetic_repair("linear");solver.add(x+y==10);solver.add(y+z==7);solver.add(x*x==16);
        solver.assignment[solver.indices.at(x.id())]=3;solver.assignment[solver.indices.at(y.id())]=7;
        initialize(solver);std::vector<NiaSolver::Operation> operations{{{solver.indices.at(x.id()),mpz_class(4)}}};
        std::set<size_t> repaired;solver.repair_arithmetic_operations(operations,repaired);bool solved=false;
        for(const auto& operation:operations)solved|=solver.trial_operation(operation,solver.native_score(solver.score())).score.solved();
        if(!solved)throw std::runtime_error("two-row unit-linear compensation did not reach y=6,z=1");
    }
    static void active_row_platform(){
        z3::context c;auto x=c.int_const("x"),q=c.int_const("q"),r=c.int_const("r"),g=c.bool_const("g");NiaSolver solver(c);
        solver.set_arithmetic_repair("quotient");auto guarded=g || (x==32*q+r);
        solver.add(guarded);solver.add(g==(x<0));solver.add(r>=0);solver.add(r<32);solver.add(x*x==1024);
        solver.assignment[solver.indices.at(x.id())]=32;solver.assignment[solver.indices.at(r.id())]=31;initialize(solver);
        std::vector<NiaSolver::Operation> operations{{{solver.indices.at(q.id()),mpz_class(1)}}};
        std::set<size_t> repaired;solver.repair_arithmetic_operations(operations,repaired,&guarded);
        if(operations.size()!=1 || solver.arith_active_rows_)throw std::runtime_error("disabled active rows changed candidates");
        solver.set_active_arithmetic_rows(true);
        solver.repair_arithmetic_operations(operations,repaired,&guarded);
        if(!solver.arithmetic_rows.empty() || !solver.quotient_relations.empty())throw std::runtime_error("temporary guarded rows leaked into hard unit relations");
        for(const auto& edges:solver.variable_arithmetic_rows)if(!edges.empty())throw std::runtime_error("temporary guarded dependencies leaked");
        bool solved=false;for(const auto& operation:operations)solved|=solver.trial_operation(operation,solver.native_score(solver.score())).score.solved();
        if(!solved)throw std::runtime_error("currently active guarded arithmetic row cannot repair q=1,r=0");
        solver.assignment[solver.indices.at(g.id())]=1;auto extracted=solver.arith_active_rows_;
        operations={{{solver.indices.at(q.id()),mpz_class(1)}}};repaired.clear();
        solver.repair_arithmetic_operations(operations,repaired,&guarded);
        if(solver.arith_active_rows_!=extracted || operations.size()!=1)
            throw std::runtime_error("a satisfied disjunction was treated as an active equality");
    }
    static void run(){
        quotient_case(31,0,31,32,0,31,32);
        quotient_case(0,0,0,-1,0,31,32);
        quotient_case(-1,-1,31,0,0,31,32);
        quotient_case(23,0,23,24,-8,23,32);
        quotient_case(0,0,0,-1,0,31,-32);
        quotient_case(31,0,31,32,0,31,32,false,true);
        quotient_case(31,0,31,32,0,31,32,true,false,"soft");
        linear_closure();active_row_platform();
    }
};
}
int main(){try{ufnia::UfScoringTest::run();std::cout<<"integer auxiliary repairs passed\n";}
catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
