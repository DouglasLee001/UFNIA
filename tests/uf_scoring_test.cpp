#define UFNIA_UF_TEST
#include "mpz_sls.h"
#include <iostream>

namespace ufnia {
struct UfScoringTest {
    static unsigned reference(const NiaSolver& solver){
        std::map<NiaSolver::UfKey,std::map<mpz_class,unsigned>> groups;
        for(const auto& application:solver.applications){
            NiaSolver::UfKey key;key.function=application.function;
            for(const auto& argument:application.arguments)key.arguments.push_back(solver.eval(argument));
            ++groups[key][solver.eval(application.result)];
        }
        unsigned result=0;
        for(const auto& group:groups){unsigned total=0,maximum=0;
            for(const auto& count:group.second){total+=count.second;maximum=std::max(maximum,count.second);}
            result+=total-maximum;
        }
        return result;
    }
    static NiaSolver::NativeScore reference_score(const NiaSolver& solver){
        NiaSolver::NativeScore result;result.arithmetic=solver.score();result.uf=reference(solver);
        for(size_t id=0;id<solver.clauses.size();++id){
            auto score=solver.clause_score(id);
            result.weighted_unsat+=solver.clause_weights[id]*score.unsat;
            result.weighted_distance+=solver.clause_weights[id]*score.distance;
            if(!solver.clause_kinds.empty() && solver.clause_kinds[id]==1){
                result.boolean_unsat+=score.unsat;result.boolean_weighted_unsat+=solver.clause_weights[id]*score.unsat;
                result.boolean_distance+=solver.clause_weights[id]*score.distance;
            }
        }
        return result;
    }
    static void compare(const NiaSolver::NativeScore& actual,const NiaSolver::NativeScore& expected){
        if(actual.arithmetic.unsat!=expected.arithmetic.unsat || actual.uf!=expected.uf ||
           actual.weighted_unsat!=expected.weighted_unsat ||
           std::abs(actual.arithmetic.distance-expected.arithmetic.distance)>1e-7 ||
           std::abs(actual.weighted_distance-expected.weighted_distance)>1e-7 ||
           actual.boolean_unsat!=expected.boolean_unsat || actual.boolean_weighted_unsat!=expected.boolean_weighted_unsat ||
           std::abs(actual.boolean_distance-expected.boolean_distance)>1e-7)
            throw std::runtime_error("weighted incremental score differs from exact evaluation");
    }
    static void run(const std::string& mode,bool alternating){
        z3::context c;auto x=c.int_const("x"),y=c.int_const("y"),z=c.int_const("z");
        auto u=c.int_const("u"),v=c.int_const("v"),b=c.bool_const("b");
        NiaSolver solver(c);solver.set_distance(mode);solver.set_search_policy("greedy");
        if(alternating)solver.set_search_phase("alternating");
        auto gate=c.bool_const("gate");solver.add(b || gate);solver.add(!b || !gate);solver.add(x*x+y>=3);solver.add(u+v==x*y);solver.add(z3::ite(b,u,v)<z+2);
        solver.add(c.bool_val(true)==(b && (x>2)));solver.add(z3::ite(b,x>1,y< -1));
        for(unsigned i=0;i<45;++i){
            z3::expr argument=i%4==0?x:i%4==1?y:i%4==2?x-y:z3::ite(b,x,y);
            z3::expr result=i%3==0?u:i%3==1?v:u+v;
            solver.add_uf_application(i%3,{argument,i%2?z:x+z},result+c.int_val(int(i%5)));
        }
        solver.clauses=solver.original;solver.prepare_applications();solver.build_dependencies();
        solver.initialize_scores();solver.initialize_applications();solver.clause_weights.assign(solver.clauses.size(),1);std::mt19937 random(7);
        for(unsigned iteration=0;iteration<1200;++iteration){
            NiaSolver::Operation operation;
            for(unsigned count=0;count<1+random()%3;++count){
                size_t variable=random()%solver.variables.size();mpz_class value=int(random()%9)-4;
                if(solver.variables[variable].is_bool())value=random()%2;
                else if(iteration%17==0)value=mpz_class(1)<<260;
                operation[variable]=value;
            }
            if(iteration%17==0)for(auto& weight:solver.clause_weights)weight=1+random()%11;
            auto original=solver.assignment;auto current=solver.native_score(solver.score());
            auto trial=solver.trial_operation(operation,current);
            if(solver.assignment!=original || solver.uf_conflicts_!=reference(solver))
                throw std::runtime_error("trial did not restore assignment or UF groups");
            for(const auto& change:operation)solver.assignment[change.first]=change.second;
            auto exact=reference_score(solver);solver.assignment=original;
            compare(trial.score,exact);
            if(iteration%3){
                solver.commit_operation(operation,trial);solver.verify_scores();
                if(solver.uf_conflicts_!=reference(solver))throw std::runtime_error("committed UF groups differ");
                for(size_t id=0;id<solver.applications.size();++id){
                    const auto& application=solver.applications[id];const auto& cached=solver.uf_values[id];
                    if(solver.eval(application.result)!=cached.result)throw std::runtime_error("cached result is stale");
                    for(size_t ai=0;ai<application.arguments.size();++ai)
                        if(solver.eval(application.arguments[ai])!=cached.key.arguments[ai])
                            throw std::runtime_error("cached argument is stale");
                }
            }
            if(iteration%41==0){
                solver.scoring="paws";solver.best=original;current=solver.native_score(solver.score());
                auto saved=solver.assignment;solver.assignment=solver.best;
                auto best_score=reference_score(solver);solver.assignment=saved;
                solver.paws_reweight(current,best_score);compare(current,reference_score(solver));
                solver.verify_scores();
                if(solver.uf_conflicts_!=reference(solver))throw std::runtime_error("reweighting corrupted UF caches");
                saved=solver.assignment;solver.assignment=solver.best;
                compare(best_score,reference_score(solver));solver.assignment=saved;
            }
        }
        NiaSolver::NativeScore many_low,one_high;
        many_low.arithmetic.unsat=2;many_low.weighted_unsat=2;
        one_high.arithmetic.unsat=1;one_high.weighted_unsat=4;
        if(!(many_low<one_high))throw std::runtime_error("raw counts incorrectly dominate dynamic weights");
    }
};
}
int main(){try{ufnia::UfScoringTest::run("classic",false);ufnia::UfScoringTest::run("structural",false);ufnia::UfScoringTest::run("classic",true);ufnia::UfScoringTest::run("structural",true);std::cout<<"1200 exact joint trials passed\n";}
    catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}}
