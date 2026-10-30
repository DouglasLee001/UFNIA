#pragma once
// Adapted from NIA_LS/nia_lia_sls.h; the reference source is unchanged.
// In-process stochastic local search over nonlinear integer/Boolean expressions.
// Z3 is used only for AST storage and model evaluation in this backend.
#include <z3++.h>
#include "mpz_preprocess.h"
#include <gmpxx.h>
#include <chrono>
#include <cmath>
#include <random>
#include <set>
#include <map>
#include <algorithm>
#include <vector>
#include <stdexcept>
#include <unordered_set>
#include <unordered_map>
#ifdef NIA_LIA_SCORING_TEST
#include <cstring>
#endif

namespace ufnia {
inline unsigned sls_seed=0;
class NiaSolver {
    using Int=mpz_class;
    struct Arguments {
        const z3::expr& expression;
        struct Iterator {
            const z3::expr& expression;unsigned position;
            z3::expr operator*()const{return expression.arg(position);}
            Iterator& operator++(){++position;return *this;}
            bool operator!=(const Iterator& b)const{return position!=b.position;}
        };
        Iterator begin()const{return {expression,0};}
        Iterator end()const{return {expression,expression.num_args()};}
    };
    static Arguments args(const z3::expr& e){return {e};}
    struct Score {
        unsigned unsat=0; double distance=0;
        bool operator<(const Score& b)const {
            return unsat<b.unsat || (unsat==b.unsat && distance<b.distance-1e-9);
        }
    };
    struct Polynomial {Int constant=0,linear=0,quadratic=0;bool supported=true;};
    unsigned long long steps_taken_=0;
    mutable unsigned long long higher_degree_fallbacks_=0;
    bool validated_=false;
    std::vector<Score> clause_scores;
    std::vector<std::vector<size_t>> variable_clauses;
    std::vector<uint64_t> last_variable_move;
#ifdef NIA_LIA_SCORING_TEST
    friend struct LiaScoringTest;
    bool deterministic_test=false;
    unsigned test_step_limit=0,test_steps_taken=0;
    mutable uint64_t test_scored_clauses=0;
    uint64_t test_trace=1469598103934665603ull;
#endif
    z3::context& c;
    std::vector<z3::expr> clauses,original,variables;
    std::vector<std::pair<z3::expr,z3::expr>> substitutions;
    std::map<unsigned,size_t> indices;
    std::unordered_set<unsigned> collected;
    std::map<size_t,Int> lower,upper;
    std::vector<Int> assignment,best;
    std::mt19937 rng;
    unsigned timeout_ms=1000;
    bool invalid=false,has_best=false;
    struct BudgetExpired {};
    mutable unsigned evaluations=0;
    bool budget_active=false;
    std::chrono::steady_clock::time_point deadline;
    bool expired()const{
#ifdef NIA_LIA_SCORING_TEST
        if(deterministic_test)return false;
#endif
        return budget_active && std::chrono::steady_clock::now()>=deadline;
    }
    bool variable(const z3::expr& e)const {
        return e.is_const() && e.decl().decl_kind()==Z3_OP_UNINTERPRETED;
    }
    void collect(const z3::expr& expression) {
        // The parser shares let-bindings and common subexpressions. Traverse
        // each AST once rather than expanding its potentially exponential tree.
        std::vector<z3::expr> pending{expression};
        while(!pending.empty()){
            auto e=pending.back();pending.pop_back();
            if(!collected.insert(e.id()).second)continue;
            if(variable(e)){
                if(!e.is_int() && !e.is_bool())throw std::runtime_error("unsupported SLS sort");
                if(!indices.count(e.id())){
                    indices[e.id()]=variables.size();variables.push_back(e);assignment.push_back(0);
                }
            }else{
                if(!e.is_app())throw std::runtime_error("unsupported SLS expression");
                for(unsigned i=e.num_args();i>0;--i)pending.push_back(e.arg(i-1));
            }
        }
    }
    Int eval_cached(const z3::expr& e,std::unordered_map<unsigned,Int>& memo)const {
        if((++evaluations & 1023u)==0 && expired())throw BudgetExpired{};
        auto found=memo.find(e.id());if(found!=memo.end())return found->second;
        auto child=[&](const z3::expr& a){return eval_cached(a,memo);};
        const Int value=[&]() -> Int {
        if(variable(e))return assignment.at(indices.at(e.id()));
        if(e.is_numeral())return Int(Z3_get_numeral_string(c,e));
        auto k=e.decl().decl_kind();
        switch(k){
        case Z3_OP_TRUE:return 1;
        case Z3_OP_FALSE:return 0;
        case Z3_OP_NOT:return child(e.arg(0))==0?1:0;
        case Z3_OP_AND:for(auto a:args(e))if(child(a)==0)return 0;return 1;
        case Z3_OP_OR:for(auto a:args(e))if(child(a)!=0)return 1;return 0;
        case Z3_OP_IMPLIES:return child(e.arg(0))==0 || child(e.arg(1))!=0?1:0;
        case Z3_OP_ITE:return child(e.arg(child(e.arg(0))!=0?1:2));
        case Z3_OP_ADD:{Int v=0;for(auto a:args(e))v+=child(a);return v;}
        case Z3_OP_SUB:{Int v=child(e.arg(0));for(unsigned i=1;i<e.num_args();++i)v-=child(e.arg(i));return v;}
        case Z3_OP_UMINUS:return -child(e.arg(0));
        case Z3_OP_MUL:{Int v=1;for(auto a:args(e))v*=child(a);return v;}
        case Z3_OP_EQ:case Z3_OP_IFF:return child(e.arg(0))==child(e.arg(1))?1:0;
        case Z3_OP_LE:return child(e.arg(0))<=child(e.arg(1))?1:0;
        case Z3_OP_GE:return child(e.arg(0))>=child(e.arg(1))?1:0;
        case Z3_OP_LT:return child(e.arg(0))<child(e.arg(1))?1:0;
        case Z3_OP_GT:return child(e.arg(0))>child(e.arg(1))?1:0;
        default:throw std::runtime_error("unsupported NIA SLS operator");
        }
        }();
        memo.emplace(e.id(),value);return value;
    }
    Int eval(const z3::expr& e)const {
        // A cache belongs to one assignment evaluation. Reusing it across
        // tentative moves would invalidate exact SAT checking.
        std::unordered_map<unsigned,Int> memo;return eval_cached(e,memo);
    }
    static double magnitude(Int v){
        if(v<0)v=-v;
        if(v==0)return 0;
        // Logarithmic distance keeps arbitrary-size coefficients representable.
        unsigned bits=mpz_sizeinbase(v.get_mpz_t(),2)-1;
        if(bits>900)return (bits+1)*0.6931471805599453;
        return std::log1p(v.get_d());
    }
    double distance(const z3::expr& e,bool desired=true)const {
        if((eval(e)!=0)==desired)return 0;
        auto k=e.decl().decl_kind();
        if(k==Z3_OP_NOT)return distance(e.arg(0),!desired);
        if(k==Z3_OP_AND || k==Z3_OP_OR){
            bool sum=(k==Z3_OP_AND)==desired;double d=sum?0:1e100;
            for(auto a:args(e)){double v=distance(a,desired);d=sum?d+v:std::min(d,v);}return d;
        }
        if(k==Z3_OP_ITE)return distance(e.arg(eval(e.arg(0))!=0?1:2),desired);
        if(e.num_args()==2 && e.arg(0).is_int() && e.arg(1).is_int()){
            Int delta=eval(e.arg(0))-eval(e.arg(1));
            if(k==Z3_OP_EQ)return desired?magnitude(delta):magnitude(Int(1));
            if(k==Z3_OP_LE)return magnitude(desired?Int(delta):Int(1-delta));
            if(k==Z3_OP_GE)return magnitude(desired?Int(-delta):Int(delta+1));
            if(k==Z3_OP_LT)return magnitude(desired?Int(delta+1):Int(-delta));
            if(k==Z3_OP_GT)return magnitude(desired?Int(1-delta):Int(delta));
        }
        return 1;
    }
    Score clause_score(size_t j)const {
#ifdef NIA_LIA_SCORING_TEST
        ++test_scored_clauses;
#endif
        const auto& e=clauses[j];
        return eval(e)==0?Score{1,distance(e)}:Score{};
    }
    Score score()const {
        Score s;
        for(size_t j=0;j<clauses.size();++j){auto part=clause_score(j);
            if(part.unsat){++s.unsat;s.distance+=part.distance;}
        }return s;
    }
    Score initialize_scores(){
        clause_scores.clear();clause_scores.reserve(clauses.size());
        Score total;
        for(size_t j=0;j<clauses.size();++j){
            auto part=clause_score(j);clause_scores.push_back(part);
            if(part.unsat){++total.unsat;total.distance+=part.distance;}
        }return total;
    }
    void build_dependencies(){
        variable_clauses.assign(variables.size(),{});
        for(size_t j=0;j<clauses.size();++j){
            if(expired())throw BudgetExpired{};
            // Include every ITE guard and both branches, even when inactive.
            // AST visitation deduplicates repeated leaves and shared subtrees.
            std::vector<z3::expr> pending{clauses[j]};std::unordered_set<unsigned> seen;
            while(!pending.empty()){
                auto e=pending.back();pending.pop_back();
                if(!seen.insert(e.id()).second)continue;
                if(variable(e))variable_clauses[indices.at(e.id())].push_back(j);
                else for(auto a:args(e))pending.push_back(a);
                if((seen.size() & 255u)==0 && expired())throw BudgetExpired{};
            }
        }
    }
    Score trial_score(size_t v,std::vector<Score>* updates=nullptr)const {
        // Re-evaluate only affected clauses. Sum cached contributions in the
        // original order so floating-point ties match full scoring exactly.
        const auto& affected=variable_clauses[v];size_t next=0;Score total;
        if(updates){updates->clear();updates->reserve(affected.size());}
        for(size_t j=0;j<clause_scores.size();++j){
            const bool changed=next<affected.size() && affected[next]==j;
#ifdef NIA_LIA_SCORING_REFERENCE
            Score part=clause_score(j);
#else
            Score part=changed?clause_score(j):clause_scores[j];
#endif
            if(changed){if(updates)updates->push_back(part);++next;}
            if(part.unsat){++total.unsat;total.distance+=part.distance;}
            if((j & 1023u)==0 && expired())throw BudgetExpired{};
        }
#ifdef NIA_LIA_SCORING_VERIFY
        auto reference=score();
        if(total.unsat!=reference.unsat || total.distance!=reference.distance)
            throw std::logic_error("LIA incremental trial score differs from full score");
#endif
        return total;
    }
    void commit_scores(size_t v,const std::vector<Score>* updates=nullptr){
        const auto& affected=variable_clauses[v];
        // Winning trial contributions are already evaluated at this assignment.
        // Reusing them avoids extra AST work and timeout during commit.
        for(size_t k=0;k<affected.size();++k)
            clause_scores[affected[k]]=updates?updates->at(k):clause_score(affected[k]);
#ifdef NIA_LIA_SCORING_VERIFY
        verify_scores();
#endif
    }
    unsigned conflicts(size_t v)const {
        unsigned count=0;for(size_t j:variable_clauses[v])count+=clause_scores[j].unsat;
        return count;
    }
    unsigned breaks(size_t v,const std::vector<Score>& updates)const {
        unsigned count=0;const auto& affected=variable_clauses[v];
        for(size_t k=0;k<affected.size();++k)
            count+=clause_scores[affected[k]].unsat==0 && updates[k].unsat!=0;
        return count;
    }
    void verify_scores()const {
        if(clause_scores.size()!=clauses.size())throw std::logic_error("LIA score cache size mismatch");
        for(size_t j=0;j<clauses.size();++j){
            auto reference=clause_score(j);
            if(clause_scores[j].unsat!=reference.unsat || clause_scores[j].distance!=reference.distance)
                throw std::logic_error("LIA committed clause cache differs from full score");
        }
    }
    void involved(const z3::expr& expression,std::set<size_t>& out)const {
        std::vector<z3::expr> pending{expression};std::unordered_set<unsigned> seen;
        while(!pending.empty()){
            auto e=pending.back();pending.pop_back();
            if(!seen.insert(e.id()).second)continue;
            if((seen.size() & 255u)==0 && expired())throw BudgetExpired{};
            if(variable(e))out.insert(indices.at(e.id()));
            else for(auto a:args(e))pending.push_back(a);
        }
    }
    Polynomial polynomial(const z3::expr& e,size_t v)const {
        if(expired())throw BudgetExpired{};
        if(variable(e))return indices.at(e.id())==v?Polynomial{0,1,0,true}:Polynomial{eval(e),0,0,true};
        if(e.is_numeral())return {eval(e),0,0,true};
        const auto k=e.decl().decl_kind();
        if(k==Z3_OP_ITE)return polynomial(e.arg(eval(e.arg(0))!=0?1:2),v);
        if(k==Z3_OP_UMINUS){auto p=polynomial(e.arg(0),v);return {-p.constant,-p.linear,-p.quadratic,p.supported};}
        if(k==Z3_OP_ADD || k==Z3_OP_SUB){
            auto p=polynomial(e.arg(0),v);
            for(unsigned j=1;j<e.num_args();++j){
                auto q=polynomial(e.arg(j),v);if(!q.supported)return q;
                const int sign=k==Z3_OP_SUB?-1:1;
                p.constant+=sign*q.constant;p.linear+=sign*q.linear;p.quadratic+=sign*q.quadratic;
            }return p;
        }
        if(k==Z3_OP_MUL){
            Polynomial p{1,0,0,true};
            for(auto a:args(e)){
                auto q=polynomial(a,v);if(!q.supported)return q;
                // Never silently truncate a genuine cubic or quartic term.
                const Int cubic=p.linear*q.quadratic+p.quadratic*q.linear;
                const Int quartic=p.quadratic*q.quadratic;
                if(cubic!=0 || quartic!=0)return {0,0,0,false};
                p={Int(p.constant*q.constant),Int(p.linear*q.constant+p.constant*q.linear),
                   Int(p.quadratic*q.constant+p.linear*q.linear+p.constant*q.quadratic),true};
            }return p;
        }
        return {0,0,0,false};
    }
    void polynomial_roots(size_t v,const Polynomial& p,std::map<size_t,std::set<Int>>& moves)const {
        auto around=[&](const Int& numerator,const Int& denominator){
            if(denominator==0)return;
            Int root;mpz_fdiv_q(root.get_mpz_t(),numerator.get_mpz_t(),denominator.get_mpz_t());
            for(int d=-2;d<=2;++d)moves[v].insert(clamp(v,Int(root+d)));
        };
        if(p.quadratic==0){if(p.linear!=0)around(Int(-p.constant),p.linear);return;}
        const Int denominator=2*p.quadratic;
        // The vertex is useful when an inequality has no real boundary roots.
        around(Int(-p.linear),denominator);
        const Int discriminant=p.linear*p.linear-4*p.quadratic*p.constant;
        if(discriminant<0)return;
        Int root;mpz_sqrt(root.get_mpz_t(),discriminant.get_mpz_t());
        // Exact GMP floor square root plus neighbouring integers supplies both
        // boundary sides. Every trial is checked with exact integer evaluation.
        around(Int(-p.linear-root),denominator);
        around(Int(-p.linear+root),denominator);
    }
    Int clamp(size_t v,Int x)const {
        if(variables[v].is_bool())x=x==0?0:1;
        auto l=lower.find(v),u=upper.find(v);
        if(l!=lower.end() && x<l->second)x=l->second;
        if(u!=upper.end() && x>u->second)x=u->second;
        return x;
    }
    void candidates(const z3::expr& expression,std::map<size_t,std::set<Int>>& moves)const {
        std::vector<z3::expr> pending{expression};std::unordered_set<unsigned> seen;
        while(!pending.empty()){
            if(expired())throw BudgetExpired{};
            auto e=pending.back();pending.pop_back();
            if(!seen.insert(e.id()).second)continue;
            if(e.is_app() && e.num_args()==2 && e.arg(0).is_int() && e.arg(1).is_int()){
                const auto k=e.decl().decl_kind();
                if(k==Z3_OP_EQ || k==Z3_OP_LE || k==Z3_OP_GE || k==Z3_OP_LT || k==Z3_OP_GT){
                    std::set<size_t> vs;involved(e,vs);
                    for(auto v:vs)if(variables[v].is_int()){
                        auto a=polynomial(e.arg(0),v),b=polynomial(e.arg(1),v);
                        if(!a.supported || !b.supported){++higher_degree_fallbacks_;continue;}
                        Polynomial difference{Int(a.constant-b.constant),Int(a.linear-b.linear),Int(a.quadratic-b.quadratic),true};
                        polynomial_roots(v,difference,moves);
                    }
                }
            }
            // Include ITE guards and both branches when collecting moves.
            for(unsigned i=e.num_args();i>0;--i)pending.push_back(e.arg(i-1));
        }
    }
    void bounds(const z3::expr& e){
        if(e.is_and()){for(auto a:args(e))bounds(a);return;}
        auto put=[&](size_t i,bool low,const Int& x){
            auto& m=low?lower:upper;auto it=m.find(i);
            if(it==m.end() || (low?x>it->second:x<it->second))m[i]=x;
        };
        if(variable(e) && e.is_bool()){
            size_t i=indices.at(e.id());put(i,true,Int(1));put(i,false,Int(1));return;
        }
        if(e.is_not() && variable(e.arg(0)) && e.arg(0).is_bool()){
            size_t i=indices.at(e.arg(0).id());put(i,true,Int(0));put(i,false,Int(0));return;
        }
        if(e.is_eq() && e.arg(0).is_bool()){
            auto v=e.arg(0),value=e.arg(1);
            if(!variable(v))std::swap(v,value);
            if(variable(v) && (value.is_true() || value.is_false())){
                size_t i=indices.at(v.id());Int fixed=value.is_true()?1:0;
                put(i,true,fixed);put(i,false,fixed);return;
            }
        }
        bool negated=e.is_not();auto atom=negated?e.arg(0):e;
        if(atom.num_args()!=2)return;
        auto k=atom.decl().decl_kind();
        if(negated){
            if(k==Z3_OP_LE)k=Z3_OP_GT;else if(k==Z3_OP_GE)k=Z3_OP_LT;
            else if(k==Z3_OP_LT)k=Z3_OP_GE;else if(k==Z3_OP_GT)k=Z3_OP_LE;
            else return;
        }
        if(k!=Z3_OP_EQ && k!=Z3_OP_LE && k!=Z3_OP_GE && k!=Z3_OP_LT && k!=Z3_OP_GT)return;
        NiaPreprocessor::Linear a;
        if(!NiaPreprocessor::difference(atom,a) || a.coefficients.size()!=1)return;
        auto p=*a.coefficients.begin();size_t i=indices.at(p.first);
        Int coeff=p.second,rhs=-a.constant;
        if(k==Z3_OP_EQ){
            if(rhs%coeff!=0)return;
            Int value=rhs/coeff;put(i,true,value);put(i,false,value);return;
        }
        // Convert strict integer comparisons before exact floor/ceil division.
        bool less=k==Z3_OP_LE || k==Z3_OP_LT;
        if(k==Z3_OP_LT)--rhs;else if(k==Z3_OP_GT)++rhs;
        bool low=less?(coeff<0):(coeff>0);
        if(coeff<0){coeff=-coeff;rhs=-rhs;}
        Int value;
        if(low)mpz_cdiv_q(value.get_mpz_t(),rhs.get_mpz_t(),coeff.get_mpz_t());
        else mpz_fdiv_q(value.get_mpz_t(),rhs.get_mpz_t(),coeff.get_mpz_t());
        put(i,low,value);
    }
    void restore_best(){
        if(!has_best)return;
        // Restoration must finish even when the search budget just expired.
        budget_active=false;assignment=best;
        for(auto it=substitutions.rbegin();it!=substitutions.rend();++it)
            assignment[indices.at(it->first.id())]=eval(it->second);
        best=assignment;
    }

public:
    explicit NiaSolver(z3::context& ctx):c(ctx),rng(sls_seed){}
    void add(const z3::expr& e){
        original.push_back(e);collect(e);has_best=false;
    }
    void set(const char*,unsigned value){timeout_ms=value;}
    void prefer(const z3::expr& v,const z3::expr& value){
        collect(v);assignment[indices.at(v.id())]=Int(value.is_true()?"1":value.is_false()?"0":Z3_get_numeral_string(c,value));
    }
    bool candidate_available()const{return has_best;}
    unsigned long long steps_taken()const{return steps_taken_;}
    unsigned long long higher_degree_fallbacks()const{return higher_degree_fallbacks_;}
    bool validated()const{return validated_;}
    z3::check_result check(){
        struct BudgetGuard {bool& active;~BudgetGuard(){active=false;}} guard{budget_active};
        budget_active=true;
        has_best=false;invalid=false;evaluations=0;
        steps_taken_=0;higher_degree_fallbacks_=0;validated_=false;
        clause_scores.clear();variable_clauses.clear();
        last_variable_move.assign(variables.size(),0);
#ifdef NIA_LIA_SCORING_TEST
        test_steps_taken=0;test_scored_clauses=0;test_trace=1469598103934665603ull;
#endif
        try {
        deadline=std::chrono::steady_clock::now()+std::chrono::milliseconds(timeout_ms);
        substitutions.clear();lower.clear();upper.clear();
        auto preprocessing_deadline=std::chrono::steady_clock::now()+
            std::chrono::milliseconds(std::min(100u,timeout_ms/5));
        NiaPreprocessor pre{c,[this,preprocessing_deadline](){
#ifdef NIA_LIA_SCORING_TEST
            if(deterministic_test)return false;
#endif
            return expired() || std::chrono::steady_clock::now()>=preprocessing_deadline;
        },{},{},false};
        pre.run(original,variables);clauses=std::move(pre.clauses);
        substitutions=std::move(pre.substitutions);
        if(pre.contradiction){
            // An impossible repair can still provide a useful partial candidate.
            // Search the original clauses rather than a reduced false formula.
            clauses=original;substitutions.clear();
        }
        if(expired() || invalid)return z3::unknown;
        for(const auto& e:clauses)bounds(e);
        for(size_t i=0;i<variables.size();++i){
            if(lower.count(i) && upper.count(i) && lower.at(i)>upper.at(i))invalid=true;
            assignment[i]=clamp(i,assignment[i]);
        }
        if(invalid)return z3::unknown;
        Score current=initialize_scores(),best_score=current;best=assignment;has_best=true;
        if(current.unsat)build_dependencies();
        unsigned steps=0,stall=0;
        while(current.unsat && !expired()){
#ifdef NIA_LIA_SCORING_TEST
            if(deterministic_test && steps>=test_step_limit)break;
#endif
            std::vector<size_t> violated;
            for(size_t j=0;j<clauses.size();++j)if(clause_scores[j].unsat)violated.push_back(j);
            auto& clause=clauses[violated[rng()%violated.size()]];
            std::map<size_t,std::set<Int>> moves;candidates(clause,moves);
            std::set<size_t> vs;involved(clause,vs);
            for(auto v:vs){
                if(lower.count(v) && upper.count(v) && lower.at(v)==upper.at(v))continue;
                if(variables[v].is_bool())moves[v].insert(1-assignment[v]);
                else for(int d:{-1,1,-2,2})moves[v].insert(clamp(v,Int(assignment[v]+d)));
            }
            Score chosen;bool found=false;size_t chosen_var=0;Int chosen_value=0;
            std::vector<Score> trial_updates,chosen_updates;
            bool walk=(rng()%100<15 || stall>20);
            unsigned ties=0;
            std::vector<size_t> order;
            for(auto& entry:moves){
                entry.second.erase(assignment[entry.first]);
                if(!entry.second.empty())order.push_back(entry.first);
            }
#ifndef NIA_LIA_VARIABLE_REFERENCE
            std::map<size_t,unsigned> pressure;
            for(auto v:order)pressure[v]=conflicts(v);
            std::stable_sort(order.begin(),order.end(),[&](size_t a,size_t b){
                if(pressure[a]!=pressure[b])return pressure[a]>pressure[b];
                if(variable_clauses[a].size()!=variable_clauses[b].size())return variable_clauses[a].size()<variable_clauses[b].size();
                return last_variable_move[a]<last_variable_move[b];
            });
            // Random walks choose a variable before a value, so variables with
            // many numeric roots no longer dominate Boolean guard flips.
            if(walk && !order.empty()){
                uint64_t total=0;for(auto v:order)total+=1ull+pressure[v];
                uint64_t draw=std::uniform_int_distribution<uint64_t>(0,total-1)(rng);
                size_t selected=order.back();
                for(auto v:order){uint64_t weight=1ull+pressure[v];if(draw<weight){selected=v;break;}draw-=weight;}
                auto it=moves[selected].begin();std::advance(it,rng()%moves[selected].size());
                Int value=*it;moves[selected].clear();moves[selected].insert(value);order.clear();order.push_back(selected);
            }
            unsigned chosen_breaks=0;
#endif
            for(size_t v:order){
                Int old=assignment[v];
                for(const auto& x:moves[v]){
                    if(x==old)continue;
                    if(expired())break;
                    assignment[v]=x;Score s=trial_score(v,&trial_updates);
#ifdef NIA_LIA_VARIABLE_REFERENCE
                    if(!found || (walk?rng()%++ties==0:s<chosen)){
                        found=true;chosen=s;chosen_var=v;chosen_value=x;chosen_updates=trial_updates;if(!walk)ties=1;
                    }else if(!walk && !(chosen<s) && rng()%++ties==0){chosen=s;chosen_var=v;chosen_value=x;chosen_updates=trial_updates;}
#else
                    unsigned broken=breaks(v,trial_updates);
                    bool take=!found || s<chosen;bool reset_ties=take;
                    if(found && !(s<chosen) && !(chosen<s)){
                        bool better=broken<chosen_breaks || (broken==chosen_breaks && last_variable_move[v]<last_variable_move[chosen_var]);
                        bool equal=broken==chosen_breaks && last_variable_move[v]==last_variable_move[chosen_var];
                        take=better || (equal && rng()%++ties==0);reset_ties=better;
                    }
                    if(take){found=true;chosen=s;chosen_var=v;chosen_value=x;chosen_updates=trial_updates;chosen_breaks=broken;if(reset_ties)ties=1;}
#endif
                }
                assignment[v]=old;if(expired())break;
            }
            if(!found)break;
            assignment[chosen_var]=chosen_value;commit_scores(chosen_var,&chosen_updates);current=chosen;++steps;++stall;
            last_variable_move[chosen_var]=steps;steps_taken_=steps;
#ifdef NIA_LIA_SCORING_TEST
            test_steps_taken=steps;
            auto trace_byte=[&](unsigned char byte){test_trace=(test_trace^byte)*1099511628211ull;};
            auto trace_number=[&](uint64_t number){for(unsigned k=0;k<8;++k){trace_byte(number&255u);number>>=8;}};
            trace_number(chosen_var);
            for(unsigned char byte:chosen_value.get_str())trace_byte(byte);
            trace_byte(255);trace_number(current.unsat);
            uint64_t distance_bits;std::memcpy(&distance_bits,&current.distance,sizeof(distance_bits));
            trace_number(distance_bits);
#endif
            if(current<best_score){best_score=current;best=assignment;stall=0;}
            if(steps%256==0){assignment=best;current=initialize_scores();
#ifdef NIA_LIA_SCORING_VERIFY
                if(current.unsat!=best_score.unsat || current.distance!=best_score.distance)
                    throw std::logic_error("LIA best assignment cache rebuild mismatch");
#endif
            }
        }
        restore_best();
        if(best_score.unsat==0){
            // Check the complete reconstructed assignment against every input.
            for(const auto& e:original)if(eval(e)==0)return z3::unknown;
            validated_=true;
            return z3::sat;
        }
        return z3::unknown;
        }catch(const BudgetExpired&){
            restore_best();
            return z3::unknown;
        }
    }
    z3::model get_model(){
        if(!has_best)throw std::runtime_error("no SLS candidate");
        z3::model model(c);
        for(size_t i=0;i<variables.size();++i){
            auto decl=variables[i].decl();
            z3::expr value=variables[i].is_bool()?c.bool_val(best[i]!=0):c.int_val(best[i].get_str().c_str());
            model.add_const_interp(decl,value);
        }return model;
    }
};
}
