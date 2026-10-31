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
#ifdef UFNIA_UF_TEST
    friend struct UfScoringTest;
#endif
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
    std::vector<unsigned char> clause_kinds;
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
    double distance_cached(const z3::expr& e,bool desired,std::unordered_map<unsigned,Int>& values,
                           std::unordered_map<uint64_t,double>& distances)const {
        uint64_t key=(uint64_t(e.id())<<1)|unsigned(desired);
        auto found=distances.find(key);if(found!=distances.end())return found->second;
        auto value=[&](const z3::expr& expression){return eval_cached(expression,values);};
        auto child=[&](const z3::expr& expression,bool target){return distance_cached(expression,target,values,distances);};
        const double result=[&]() -> double {
            if((value(e)!=0)==desired)return 0;
            auto k=e.decl().decl_kind();
            if(k==Z3_OP_NOT)return child(e.arg(0),!desired);
            if(k==Z3_OP_AND || k==Z3_OP_OR){
                bool sum=(k==Z3_OP_AND)==desired;double d=sum?0:1e100;
                for(auto a:args(e)){double v=child(a,desired);d=sum?d+v:std::min(d,v);}return d;
            }
            if(distance_mode=="structural"){
                if(e.is_true() || e.is_false())return 1e100;
                if((k==Z3_OP_EQ || k==Z3_OP_IFF) && e.arg(0).is_bool()){
                    double tt=child(e.arg(0),true),tf=child(e.arg(0),false);
                    double ft=child(e.arg(1),true),ff=child(e.arg(1),false);
                    return desired?std::min(tt+ft,tf+ff):std::min(tt+ff,tf+ft);
                }
                if(k==Z3_OP_IMPLIES)return desired?
                    std::min(child(e.arg(0),false),child(e.arg(1),true)):
                    child(e.arg(0),true)+child(e.arg(1),false);
                if(k==Z3_OP_ITE)return std::min(child(e.arg(0),true)+child(e.arg(1),desired),
                                               child(e.arg(0),false)+child(e.arg(2),desired));
            }
            if(k==Z3_OP_ITE)return child(e.arg(value(e.arg(0))!=0?1:2),desired);
            if(e.num_args()==2 && e.arg(0).is_int() && e.arg(1).is_int()){
                Int delta=value(e.arg(0))-value(e.arg(1));
                if(k==Z3_OP_EQ)return desired?magnitude(delta):magnitude(Int(1));
                if(k==Z3_OP_LE)return magnitude(desired?Int(delta):Int(1-delta));
                if(k==Z3_OP_GE)return magnitude(desired?Int(-delta):Int(delta+1));
                if(k==Z3_OP_LT)return magnitude(desired?Int(delta+1):Int(-delta));
                if(k==Z3_OP_GT)return magnitude(desired?Int(1-delta):Int(delta));
            }
            return 1;
        }();
        distances.emplace(key,result);return result;
    }
    double distance(const z3::expr& e,bool desired=true)const {
        // Both caches belong to this assignment and this root call. Boolean
        // DAG sharing must not cause exponential distance expansion, and no
        // tentative assignment may observe another assignment's cached values.
        std::unordered_map<unsigned,Int> values;
        std::unordered_map<uint64_t,double> distances;
        return distance_cached(e,desired,values,distances);
    }
    Score clause_score(size_t j)const {
#ifdef NIA_LIA_SCORING_TEST
        ++test_scored_clauses;
#endif
        const auto& e=clauses[j];
        std::unordered_map<unsigned,Int> values;
        if(eval_cached(e,values)!=0)return {};
        std::unordered_map<uint64_t,double> distances;
        return {1,distance_cached(e,true,values,distances)};
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
        if(search_phase=="alternating")clause_kinds.assign(clauses.size(),0);else clause_kinds.clear();
        for(size_t j=0;j<clauses.size();++j){
            if(expired())throw BudgetExpired{};
            // Include every ITE guard and both branches, even when inactive.
            // AST visitation deduplicates repeated leaves and shared subtrees.
            std::vector<z3::expr> pending{clauses[j]};std::unordered_set<unsigned> seen;
            while(!pending.empty()){
                auto e=pending.back();pending.pop_back();
                if(!seen.insert(e.id()).second)continue;
                if(variable(e)){
                    variable_clauses[indices.at(e.id())].push_back(j);
                    if(!clause_kinds.empty())clause_kinds[j]|=e.is_bool()?1:2;
                }else for(auto a:args(e))pending.push_back(a);
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
    void candidates(const z3::expr& expression,std::map<size_t,std::set<Int>>& moves,
                    const std::set<size_t>* allowed=nullptr,unsigned probe_limit=0)const {
        unsigned probes=0;
        std::vector<z3::expr> pending{expression};std::unordered_set<unsigned> seen;
        while(!pending.empty()){
            if(expired())throw BudgetExpired{};
            auto e=pending.back();pending.pop_back();
            if(!seen.insert(e.id()).second)continue;
            if(e.is_app() && e.num_args()==2 && e.arg(0).is_int() && e.arg(1).is_int()){
                const auto k=e.decl().decl_kind();
                if(k==Z3_OP_EQ || k==Z3_OP_LE || k==Z3_OP_GE || k==Z3_OP_LT || k==Z3_OP_GT){
                    std::set<size_t> vs;involved(e,vs);
                    for(auto v:vs)if(variables[v].is_int() && (!allowed || allowed->count(v))){
                        if(probe_limit && probes++>=probe_limit)return;
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

    struct UfApplication {
        unsigned function;
        std::vector<z3::expr> arguments;
        z3::expr result;
    };
    struct UfKey {
        unsigned function=0;
        std::vector<Int> arguments;
        bool operator<(const UfKey& b)const{
            if(function!=b.function)return function<b.function;
            return arguments<b.arguments;
        }
    };
    struct UfValue {UfKey key;Int result;};
    struct UfBucket {
        std::map<Int,unsigned> frequencies;
        std::set<size_t> members;
        std::map<unsigned,unsigned> frequency_counts;
        void change_frequency(const Int& result,int delta){
            auto found=frequencies.find(result);unsigned old=found==frequencies.end()?0:found->second;
            if(old){auto count=frequency_counts.find(old);if(!--count->second)frequency_counts.erase(count);}
            unsigned next=unsigned(int64_t(old)+delta);
            if(next){frequencies[result]=next;++frequency_counts[next];}
            else if(found!=frequencies.end())frequencies.erase(found);
        }
        unsigned conflicts()const{
            return unsigned(members.size())-(frequency_counts.empty()?0:frequency_counts.rbegin()->first);
        }
    };
    using Operation=std::map<size_t,Int>;
    struct NativeScore {
        Score arithmetic;
        unsigned uf=0;
        uint64_t weighted_unsat=0;
        double weighted_distance=0;
        unsigned boolean_unsat=0;
        uint64_t boolean_weighted_unsat=0;
        double boolean_distance=0;
        bool solved()const{return arithmetic.unsat==0 && uf==0;}
        bool operator<(const NativeScore& b)const{
            uint64_t a_cost=weighted_unsat+uf,b_cost=b.weighted_unsat+b.uf;
            return a_cost<b_cost || (a_cost==b_cost && weighted_distance<b.weighted_distance-1e-9);
        }
    };
    struct NativeTrial {
        NativeScore score;
        std::vector<std::pair<size_t,Score>> clauses;
        std::vector<std::pair<size_t,UfValue>> applications;
    };
    std::vector<UfApplication> source_uf,applications;
    std::vector<UfValue> uf_values;
    std::map<UfKey,UfBucket> uf_buckets;
    std::set<UfKey> uf_conflicting_keys;
    std::vector<std::vector<size_t>> variable_applications;
    std::string interaction="joint",scoring="count",distance_mode="classic",search_policy="legacy",search_phase="mixed",candidate_policy="broad";
    uint64_t candidate_probes_=0,critical_candidates_=0,fallback_candidates_=0;
    uint64_t bool_phase_steps_=0,int_phase_steps_=0,phase_switches_=0,boolean_joint_moves_=0,phase_cross_escapes_=0;
    uint64_t greedy_rounds_=0,greedy_evaluations_=0,random_escape_moves_=0,paws_reselections_=0;
    std::vector<uint64_t> clause_weights;
    uint64_t weight_updates_=0;
    unsigned uf_conflicts_=0;
    uint64_t uf_joint_moves_=0;

    UfValue evaluate_application(size_t id,std::unordered_map<unsigned,Int>& memo)const{
        const auto& application=applications[id];UfValue value;value.key.function=application.function;
        for(const auto& argument:application.arguments)value.key.arguments.push_back(eval_cached(argument,memo));
        value.result=eval_cached(application.result,memo);return value;
    }
    void insert_application(size_t id,const UfValue& value){
        auto& bucket=uf_buckets[value.key];uf_conflicts_-=bucket.conflicts();
        bucket.members.insert(id);bucket.change_frequency(value.result,1);uf_conflicts_+=bucket.conflicts();
        if(bucket.conflicts())uf_conflicting_keys.insert(value.key);else uf_conflicting_keys.erase(value.key);
    }
    void remove_application(size_t id,const UfValue& value){
        auto it=uf_buckets.find(value.key);auto& bucket=it->second;uf_conflicts_-=bucket.conflicts();
        bucket.members.erase(id);bucket.change_frequency(value.result,-1);
        uf_conflicts_+=bucket.conflicts();
        if(bucket.conflicts())uf_conflicting_keys.insert(value.key);else uf_conflicting_keys.erase(value.key);
        if(bucket.members.empty())uf_buckets.erase(it);
    }
    void initialize_applications(){
        uf_values.clear();uf_buckets.clear();uf_conflicting_keys.clear();uf_conflicts_=0;
        std::unordered_map<unsigned,Int> memo;
        for(size_t id=0;id<applications.size();++id){
            auto value=evaluate_application(id,memo);uf_values.push_back(value);insert_application(id,value);
        }
    }
    void prepare_applications(){
        applications=source_uf;
        // Elimination stores x -> rhs in forward order. Observations must use
        // the same residual variables as arithmetic during every tentative move.
        z3::expr_vector from(c),to(c);
        for(auto it=substitutions.rbegin();it!=substitutions.rend();++it){
            auto replacement=it->second;
            if(!from.empty())replacement=replacement.substitute(from,to).simplify();
            from.push_back(it->first);to.push_back(replacement);
        }
        if(!from.empty())for(auto& application:applications){
            for(auto& argument:application.arguments)argument=argument.substitute(from,to).simplify();
            application.result=application.result.substitute(from,to).simplify();
        }
        variable_applications.assign(variables.size(),{});
        for(size_t id=0;id<applications.size();++id){
            std::set<size_t> dependencies;involved(applications[id].result,dependencies);
            for(const auto& argument:applications[id].arguments)involved(argument,dependencies);
            for(size_t v:dependencies)variable_applications[v].push_back(id);
        }
    }
    bool validate_applications(const std::vector<UfApplication>& table)const{
        std::map<UfKey,Int> representatives;std::unordered_map<unsigned,Int> memo;
        for(const auto& application:table){
            UfKey key;key.function=application.function;
            for(const auto& argument:application.arguments)key.arguments.push_back(eval_cached(argument,memo));
            auto result=eval_cached(application.result,memo);auto added=representatives.emplace(std::move(key),result);
            if(!added.second && added.first->second!=result)return false;
        }
        return true;
    }
    NativeScore native_score(const Score& arithmetic)const{
        NativeScore result;result.arithmetic=arithmetic;result.uf=interaction=="none"?0:uf_conflicts_;
        for(size_t id=0;id<clause_scores.size();++id){
            result.weighted_unsat+=clause_weights[id]*clause_scores[id].unsat;
            result.weighted_distance+=clause_weights[id]*clause_scores[id].distance;
            if(!clause_kinds.empty() && clause_kinds[id]==1){
                result.boolean_unsat+=clause_scores[id].unsat;
                result.boolean_weighted_unsat+=clause_weights[id]*clause_scores[id].unsat;
                result.boolean_distance+=clause_weights[id]*clause_scores[id].distance;
            }
        }
        return result;
    }
    NativeTrial trial_operation(const Operation& operation,const NativeScore& current){
        NativeTrial trial;trial.score=current;
        std::set<size_t> changed_clauses,changed_applications;
        Operation old;
        for(const auto& change:operation){
            size_t v=change.first;old[v]=assignment[v];assignment[v]=change.second;
            changed_clauses.insert(variable_clauses[v].begin(),variable_clauses[v].end());
            changed_applications.insert(variable_applications[v].begin(),variable_applications[v].end());
        }
        std::vector<std::pair<size_t,UfValue>> previous;
        auto restore=[&](){
            for(auto it=previous.rbegin();it!=previous.rend();++it){
                remove_application(it->first,uf_values[it->first]);
                uf_values[it->first]=it->second;insert_application(it->first,it->second);
            }
            for(const auto& change:old)assignment[change.first]=change.second;
        };
        try{
            for(size_t id:changed_clauses){
                auto value=clause_score(id);trial.clauses.emplace_back(id,value);
                trial.score.arithmetic.unsat-=clause_scores[id].unsat;
                trial.score.arithmetic.unsat+=value.unsat;
                trial.score.arithmetic.distance+=value.distance-clause_scores[id].distance;
                trial.score.weighted_unsat-=clause_weights[id]*clause_scores[id].unsat;
                trial.score.weighted_unsat+=clause_weights[id]*value.unsat;
                trial.score.weighted_distance+=clause_weights[id]*(value.distance-clause_scores[id].distance);
                if(!clause_kinds.empty() && clause_kinds[id]==1){
                    trial.score.boolean_unsat-=clause_scores[id].unsat;trial.score.boolean_unsat+=value.unsat;
                    trial.score.boolean_weighted_unsat-=clause_weights[id]*clause_scores[id].unsat;
                    trial.score.boolean_weighted_unsat+=clause_weights[id]*value.unsat;
                    trial.score.boolean_distance+=clause_weights[id]*(value.distance-clause_scores[id].distance);
                }
            }
            if(trial.score.boolean_unsat==0)trial.score.boolean_distance=0;
            if(trial.score.arithmetic.unsat==0){trial.score.arithmetic.distance=0;trial.score.weighted_distance=0;}
            std::unordered_map<unsigned,Int> memo;
            for(size_t id:changed_applications){
                auto value=evaluate_application(id,memo);previous.emplace_back(id,uf_values[id]);
                remove_application(id,uf_values[id]);uf_values[id]=value;insert_application(id,value);
                trial.applications.emplace_back(id,std::move(value));
            }
            trial.score.uf=interaction=="none"?0:uf_conflicts_;restore();return trial;
        }catch(...){restore();throw;}
    }
    void commit_operation(const Operation& operation,const NativeTrial& trial,bool random_escape=false){
        if(random_escape)++random_escape_moves_;
        for(const auto& change:operation)assignment[change.first]=change.second;
        for(const auto& change:trial.clauses)clause_scores[change.first]=change.second;
        for(const auto& change:trial.applications){
            remove_application(change.first,uf_values[change.first]);uf_values[change.first]=change.second;
            insert_application(change.first,change.second);
        }
    }
    // Candidate construction mutates only a short patch, then atomically rolls
    // it back. It can invert residual expressions after either preprocessing
    // stage; an observed result need not remain a free UF result variable.
    struct Repair {
        NiaSolver& solver;Operation old;
        explicit Repair(NiaSolver& s):solver(s){}
        void put(size_t v,const Int& value){
            if(!old.count(v))old.emplace(v,solver.assignment[v]);
            solver.assignment[v]=value;
        }
        Operation operation()const{
            Operation result;for(const auto& entry:old)
                if(solver.assignment[entry.first]!=entry.second)result.emplace(entry.first,solver.assignment[entry.first]);
            return result;
        }
        ~Repair(){for(const auto& entry:old)solver.assignment[entry.first]=entry.second;}
    };
    bool repair_expression(const z3::expr& e,const Int& target,Repair& patch,unsigned& fuel,unsigned depth=0){
        if(!fuel || depth>12 || patch.old.size()>16)return false;
        --fuel;
        if(eval(e)==target)return true;
        if(variable(e)){
            size_t v=indices.at(e.id());auto value=clamp(v,target);
            if((patch.old.size()>=16 && !patch.old.count(v)) || value!=target)return false;
            patch.put(v,value);return true;
        }
        if(e.is_numeral() || e.is_true() || e.is_false())return false;
        auto kind=e.decl().decl_kind();
        if(kind==Z3_OP_ITE)return repair_expression(e.arg(eval(e.arg(0))!=0?1:2),target,patch,fuel,depth+1);
        if(kind==Z3_OP_NOT)return repair_expression(e.arg(0),target==0?Int(1):Int(0),patch,fuel,depth+1);
        std::set<size_t> involved_variables;involved(e,involved_variables);
        std::map<size_t,std::set<Int>> proposals;
        if(e.is_bool())candidates(e,proposals);
        for(size_t v:involved_variables){
            if(!fuel)break;
            --fuel;
            if(variables[v].is_bool()){proposals[v].insert(1-assignment[v]);continue;}
            if(!e.is_int())continue;
            auto p=polynomial(e,v);if(!p.supported)continue;p.constant-=target;polynomial_roots(v,p,proposals);
        }
        for(const auto& entry:proposals){
            size_t v=entry.first;Int old=assignment[v];
            for(const auto& value:entry.second){
                assignment[v]=value;bool repaired=eval(e)==target;assignment[v]=old;
                if(repaired){patch.put(v,value);return true;}
            }
        }
        if(interaction=="joint" && (kind==Z3_OP_AND || kind==Z3_OP_OR)){
            bool all=(kind==Z3_OP_AND)==(target!=0);
            for(auto child:args(e)){
                bool repaired=repair_expression(child,target,patch,fuel,depth+1);
                if(all && !repaired)return false;
                if(!all && repaired && eval(e)==target)return true;
            }
            return eval(e)==target;
        }
        if(kind==Z3_OP_UMINUS)return repair_expression(e.arg(0),Int(-target),patch,fuel,depth+1);
        if(interaction=="joint" && (kind==Z3_OP_ADD || kind==Z3_OP_SUB)){
            for(unsigned i=0;i<e.num_args();++i){
                Int desired=eval(e.arg(i));
                desired+=(kind==Z3_OP_SUB && i>0)?Int(eval(e)-target):Int(target-eval(e));
                if(repair_expression(e.arg(i),desired,patch,fuel,depth+1) && eval(e)==target)return true;
            }
        }
        if(kind==Z3_OP_MUL && interaction=="joint"){
            if(target==0){
                for(auto child:args(e))if(repair_expression(child,Int(0),patch,fuel,depth+1) && eval(e)==0)return true;
            }else{
                // Activating every zero factor is a genuine multivariable
                // move, including multilinear products of total degree > 2.
                for(auto child:args(e))if(eval(child)==0 && !repair_expression(child,Int(1),patch,fuel,depth+1))return false;
                for(unsigned i=0;i<e.num_args();++i){
                    Int other=1;for(unsigned j=0;j<e.num_args();++j)if(j!=i)other*=eval(e.arg(j));
                    if(other!=0 && target%other==0 && repair_expression(e.arg(i),Int(target/other),patch,fuel,depth+1) && eval(e)==target)return true;
                }
            }
        }
        return eval(e)==target;
    }
    void add_operation(std::vector<Operation>& operations,Operation operation)const{
        if(operation.empty() || operation.size()>16 || operations.size()>=64)return;
        if(interaction!="joint" && operation.size()>1 && arithmetic_repair=="off" && search_phase!="alternating")return;
        for(const auto& previous:operations)if(previous==operation)return;
        operations.push_back(std::move(operation));
    }
    void uf_operations(std::vector<Operation>& operations){
        if(interaction=="none")return;
        unsigned groups=0;
        // Rotate over conflicts so large tables do not repeatedly repair only
        // the first declaration/signature in AST order.
        std::vector<const UfBucket*> conflicts;
        for(const auto& key:uf_conflicting_keys)conflicts.push_back(&uf_buckets.at(key));
        if(conflicts.empty())return;
        size_t start=rng()%conflicts.size();
        for(size_t gi=0;gi<conflicts.size() && groups<3;++gi,++groups){
            const auto& bucket=*conflicts[(start+gi)%conflicts.size()];
            std::vector<std::pair<unsigned,Int>> targets;
            for(const auto& frequency:bucket.frequencies)targets.emplace_back(frequency.second,frequency.first);
            std::stable_sort(targets.begin(),targets.end(),[](const auto& a,const auto& b){return a.first>b.first;});
            if(targets.size()>3)targets.resize(3);
            for(const auto& target:targets){
                Repair patch(*this);unsigned fuel=512,members=0;
                for(size_t id:bucket.members){
                    if(++members>32 || patch.old.size()>16)break;
                    if(eval(applications[id].result)!=target.second){
                        repair_expression(applications[id].result,target.second,patch,fuel);
                        auto operation=patch.operation();
                        if(operation.size()==1)add_operation(operations,std::move(operation));
                    }
                }
                if(interaction=="joint")add_operation(operations,patch.operation());
            }
            // Separate one argument of applications with disagreeing results.
            unsigned members=0;
            for(size_t id:bucket.members){
                if(++members>4)break;
                const auto& application=applications[id];
                for(unsigned ai=0;ai<application.arguments.size() && ai<4;++ai){
                    const auto& argument=application.arguments[ai];Int value=eval(argument);
                    for(int delta:{-1,1}){
                        Repair patch(*this);unsigned fuel=128;
                        Int target=argument.is_bool()?Int(1-value):Int(value+delta);
                        if(repair_expression(argument,target,patch,fuel))add_operation(operations,patch.operation());
                    }
                }
            }
        }
    }
    void compensate_operations(std::vector<Operation>& operations){
        if(interaction!="joint")return;
        const size_t count=std::min(size_t(12),operations.size());
        for(size_t oi=0;oi<count && operations.size()<64;++oi){
            Operation operation=operations[oi];if(operation.size()>4)continue;
            Repair patch(*this);std::set<size_t> touched;
            for(const auto& change:operation){patch.put(change.first,change.second);
                touched.insert(variable_clauses[change.first].begin(),variable_clauses[change.first].end());}
            unsigned attempts=0;
            for(size_t id:touched){
                if(++attempts>4)break;
                const auto& equality=clauses[id];
                if(!equality.is_eq() || !equality.arg(0).is_int() || eval(equality)!=0)continue;
                std::map<size_t,std::set<Int>> moves;candidates(equality,moves);
                for(const auto& entry:moves){
                    if(operation.count(entry.first))continue;
                    unsigned values=0;
                    for(const auto& value:entry.second){
                        if(++values>6)break;
                        Int old=assignment[entry.first];assignment[entry.first]=value;
                        bool repaired=eval(equality)!=0;assignment[entry.first]=old;
                        if(repaired){auto combined=operation;combined[entry.first]=value;add_operation(operations,std::move(combined));break;}
                    }
                    if(operations.size()>=64)break;
                }
            }
        }
    }
    void arithmetic_operations(const z3::expr& clause,std::vector<Operation>& operations,bool include_boolean=true,size_t operation_budget=32){
        std::set<size_t> involved_variables;involved(clause,involved_variables);
        std::vector<size_t> order(involved_variables.begin(),involved_variables.end());
        if(!include_boolean)order.erase(std::remove_if(order.begin(),order.end(),[&](size_t v){return variables[v].is_bool();}),order.end());
        std::shuffle(order.begin(),order.end(),rng);
        std::map<size_t,uint64_t> pressure;
        for(size_t v:order)for(size_t id:variable_clauses[v])
            if(clause_scores[id].unsat)pressure[v]+=clause_weights[id];
        std::stable_sort(order.begin(),order.end(),[&](size_t a,size_t b){
            if(pressure[a]!=pressure[b])return pressure[a]>pressure[b];
            return last_variable_move[a]<last_variable_move[b];
        });
        // Bound polynomial extraction as well as operation scoring. A rotated
        // pressure/age order prevents a wide clause's first variables from
        // consuming every root probe and every candidate slot indefinitely.
        if(order.size()>32)order.resize(32);
        std::set<size_t> selected(order.begin(),order.end());
        std::map<size_t,std::set<Int>> roots;candidates(clause,roots,&selected,96);
        std::vector<std::vector<Int>> values(order.size());
        std::vector<std::vector<unsigned char>> critical_flags;
        if(candidate_policy=="critical")critical_flags.resize(order.size());
        for(size_t i=0;i<order.size();++i){
            size_t v=order[i];auto& choices=values[i];
            if(variables[v].is_bool())choices.push_back(clamp(v,Int(1-assignment[v])));
            else{
                for(const auto& value:roots[v])if(value!=assignment[v])choices.push_back(value);
                std::stable_sort(choices.begin(),choices.end(),[&](const Int& a,const Int& b){
                    Int da=a-assignment[v],db=b-assignment[v];if(da<0)da=-da;if(db<0)db=-db;return da<db;
                });
                for(int delta:{-1,1,-2,2}){
                    Int value=clamp(v,Int(assignment[v]+delta));
                    if(value!=assignment[v] && std::find(choices.begin(),choices.end(),value)==choices.end())choices.push_back(value);
                }
            }
            choices.erase(std::remove(choices.begin(),choices.end(),assignment[v]),choices.end());
            if(candidate_policy=="critical"){
                struct RankedValue {Int value,step;bool satisfies;double distance;};
                std::vector<RankedValue> ranked;ranked.reserve(choices.size());
                for(const auto& value:choices){
                    Int step=value-assignment[v];if(step<0)step=-step;
                    Repair patch(*this);patch.put(v,value);++candidate_probes_;
                    std::unordered_map<unsigned,Int> memo;
                    bool satisfies=eval_cached(clause,memo)!=0;double d=0;
                    if(!satisfies){std::unordered_map<uint64_t,double> distances;d=distance_cached(clause,true,memo,distances);}
                    ranked.push_back({value,step,satisfies,d});
                }
                std::stable_sort(ranked.begin(),ranked.end(),[](const auto& a,const auto& b){
                    if(a.satisfies!=b.satisfies)return a.satisfies;
                    if(a.distance!=b.distance)return a.distance<b.distance;
                    return a.step<b.step;
                });
                choices.clear();
                for(const auto& value:ranked){choices.push_back(value.value);critical_flags[i].push_back(value.satisfies);}
            }
        }
        size_t limit=std::min(size_t(64),operations.size()+operation_budget);
        for(size_t round=0;round<20 && operations.size()<limit;++round){
            bool any=false;
            for(size_t i=0;i<order.size() && operations.size()<limit;++i)if(round<values[i].size()){
                size_t before=operations.size();add_operation(operations,Operation{{order[i],values[i][round]}});any=true;
                if(operations.size()>before && candidate_policy=="critical"){
                    if(critical_flags[i][round])++critical_candidates_;else ++fallback_candidates_;
                }
            }
            if(!any)break;
        }
    }
    struct ArithmeticRow {
        Int constant;
        std::vector<std::pair<size_t,Int>> terms;
    };
    struct QuotientRelation {size_t row,q,r;Int q_coefficient,r_coefficient;};
    std::vector<ArithmeticRow> arithmetic_rows;
    std::vector<QuotientRelation> quotient_relations;
    std::vector<std::vector<size_t>> variable_arithmetic_rows,row_quotients;
    std::string arithmetic_repair="off";
    bool active_arithmetic_rows=false;
    uint64_t arith_active_rows_=0;
    uint64_t arith_joint_moves_=0,arith_repair_proposals_=0,arith_repair_attempts_=0;
    unsigned initial_arithmetic_unsat_=0,best_arithmetic_unsat_=0;

    bool append_arithmetic_row(const z3::expr& clause){
        if(!clause.is_eq() || !clause.arg(0).is_int())return false;
        std::set<size_t> involved_variables;involved(clause,involved_variables);
        if(involved_variables.empty() || involved_variables.size()>16)return false;
        NiaPreprocessor::Linear linear;if(!NiaPreprocessor::difference(clause,linear))return false;
        ArithmeticRow row{linear.constant,{}};
        for(const auto& term:linear.coefficients)row.terms.emplace_back(indices.at(term.first),term.second);
        if(row.terms.empty())return false;
        size_t id=arithmetic_rows.size();arithmetic_rows.push_back(std::move(row));row_quotients.emplace_back();
        for(const auto& term:arithmetic_rows.back().terms)variable_arithmetic_rows[term.first].push_back(id);
        for(const auto& quotient:arithmetic_rows.back().terms){
            Int modulus=quotient.second;if(modulus<0)modulus=-modulus;
            if(modulus<=1)continue;
            for(const auto& remainder:arithmetic_rows.back().terms){
                size_t r=remainder.first;
                if((remainder.second!=1 && remainder.second!=-1) || !lower.count(r) || !upper.count(r))continue;
                if(upper.at(r)-lower.at(r)>=modulus || upper.at(r)<lower.at(r))continue;
                row_quotients[id].push_back(quotient_relations.size());
                quotient_relations.push_back({id,quotient.first,r,quotient.second,remainder.second});
                if(row_quotients[id].size()>=8)break;
            }
            if(row_quotients[id].size()>=8)break;
        }
        return true;
    }
    void build_arithmetic_repairs(){
        arithmetic_rows.clear();quotient_relations.clear();row_quotients.clear();
        variable_arithmetic_rows.assign(variables.size(),{});
        if(arithmetic_repair=="off")return;
        for(const auto& clause:clauses){if(expired())throw BudgetExpired{};append_arithmetic_row(clause);}
    }
    struct ActiveArithmeticRows {
        NiaSolver& solver;size_t rows,relations;
        std::map<size_t,size_t> dependency_sizes;
        explicit ActiveArithmeticRows(NiaSolver& s):solver(s),rows(s.arithmetic_rows.size()),relations(s.quotient_relations.size()){}
        void remember_last(){
            for(const auto& term:solver.arithmetic_rows.back().terms)
                dependency_sizes.emplace(term.first,solver.variable_arithmetic_rows[term.first].size()-1);
        }
        ~ActiveArithmeticRows(){
            for(const auto& entry:dependency_sizes)solver.variable_arithmetic_rows[entry.first].resize(entry.second);
            solver.arithmetic_rows.resize(rows);solver.row_quotients.resize(rows);solver.quotient_relations.resize(relations);
        }
    };
    Int arithmetic_residual(size_t id)const{
        const auto& row=arithmetic_rows[id];Int result=row.constant;
        for(const auto& term:row.terms)result+=term.second*assignment[term.first];
        return result;
    }
    bool reconstruct_quotient(const QuotientRelation& relation,const Operation& protected_values,Repair& patch){
        const auto& row=arithmetic_rows[relation.row];Int rest=row.constant;
        for(const auto& term:row.terms)if(term.first!=relation.q && term.first!=relation.r)
            rest+=term.second*assignment[term.first];
        // r = s-k*q. The remainder interval may be offset or shorter than a
        // complete residue system. Derive the exact feasible q interval first.
        Int s=-rest/relation.r_coefficient,k=relation.q_coefficient/relation.r_coefficient;
        Int qlow,qhigh;
        if(k>0){qlow=NiaPreprocessor::ceil_div(Int(s-upper.at(relation.r)),k);
                qhigh=NiaPreprocessor::floor_div(Int(s-lower.at(relation.r)),k);}
        else {Int d=-k;qlow=NiaPreprocessor::ceil_div(Int(lower.at(relation.r)-s),d);
                      qhigh=NiaPreprocessor::floor_div(Int(upper.at(relation.r)-s),d);}
        if(lower.count(relation.q))qlow=std::max(qlow,lower.at(relation.q));
        if(upper.count(relation.q))qhigh=std::min(qhigh,upper.at(relation.q));
        if(protected_values.count(relation.q)){
            qlow=std::max(qlow,assignment[relation.q]);qhigh=std::min(qhigh,assignment[relation.q]);
        }
        if(qlow>qhigh)return false;
        Int q=assignment[relation.q];q=std::max(qlow,std::min(qhigh,q));
        if(protected_values.count(relation.r)){
            Int numerator=s-assignment[relation.r];if(numerator%k!=0)return false;q=numerator/k;
            if(q<qlow || q>qhigh)return false;
        }
        Int r=s-k*q;
        if(r<lower.at(relation.r) || r>upper.at(relation.r))return false;
        size_t extra=(!patch.old.count(relation.q))+(!patch.old.count(relation.r));
        if(patch.old.size()+extra>16)return false;
        patch.put(relation.q,q);patch.put(relation.r,r);
        return arithmetic_residual(relation.row)==0;
    }
    bool reconstruct_linear(size_t id,const Operation& protected_values,Repair& patch){
        Int residual=arithmetic_residual(id);if(residual==0)return false;
        auto pivots=arithmetic_rows[id].terms;
        std::stable_sort(pivots.begin(),pivots.end(),[&](const auto& a,const auto& b){
            bool a_used=patch.old.count(a.first),b_used=patch.old.count(b.first);
            if(a_used!=b_used)return !a_used;
            bool a_unit=a.second==1 || a.second==-1,b_unit=b.second==1 || b.second==-1;
            if(a_unit!=b_unit)return a_unit;
            return variable_arithmetic_rows[a.first].size()<variable_arithmetic_rows[b.first].size();
        });
        for(const auto& pivot:pivots){
            size_t v=pivot.first;if(protected_values.count(v) || residual%pivot.second!=0)continue;
            Int value=assignment[v]-residual/pivot.second;
            if(clamp(v,value)!=value || (patch.old.size()>=16 && !patch.old.count(v)))continue;
            patch.put(v,value);return true;
        }
        return false;
    }
    void repair_arithmetic_operations(std::vector<Operation>& operations,std::set<size_t>& repaired_indices,
                                      const z3::expr* active_clause=nullptr){
        if(arithmetic_repair=="off")return;
        ActiveArithmeticRows temporary(*this);
        if(active_arithmetic_rows && active_clause && eval(*active_clause)==0){
            std::vector<z3::expr> equalities;
            if(active_clause->is_or())for(auto literal:args(*active_clause))
                if(literal.is_eq() && literal.arg(0).is_int())equalities.push_back(literal);
            if(active_clause->decl().decl_kind()==Z3_OP_IMPLIES && active_clause->arg(1).is_eq() && active_clause->arg(1).arg(0).is_int())
                equalities.push_back(active_clause->arg(1));
            unsigned appended=0;
            for(const auto& equality:equalities){
                if(appended>=2)break;
                if(append_arithmetic_row(equality)){temporary.remember_last();++appended;++arith_active_rows_;}
            }
        }
        if(arithmetic_rows.empty())return;
        std::vector<size_t> bases;
        for(size_t i=0;i<operations.size();++i)if(operations[i].size()<=4){
            bool useful=false;for(const auto& change:operations[i])useful|=!variable_arithmetic_rows[change.first].empty();
            if(useful)bases.push_back(i);
        }
        std::shuffle(bases.begin(),bases.end(),rng);
        std::stable_sort(bases.begin(),bases.end(),[&](size_t a,size_t b){return operations[a].size()<operations[b].size();});
        if(bases.size()>12)bases.resize(12);
        for(size_t base:bases){
            if(operations.size()>=64)break;
            ++arith_repair_attempts_;
            Operation protected_values=operations[base];Repair patch(*this);
            std::vector<size_t> pending;std::map<size_t,unsigned> visits;
            for(const auto& change:protected_values){
                patch.put(change.first,change.second);
                pending.insert(pending.end(),variable_arithmetic_rows[change.first].begin(),variable_arithmetic_rows[change.first].end());
            }
            unsigned attempts=0;
            for(size_t next=0;next<pending.size() && attempts<12 && operations.size()<64;++next){
                size_t id=pending[next];if(visits[id]++>=2 || arithmetic_residual(id)==0)continue;++attempts;
                auto old_operation=patch.operation();bool changed=false;
                if(arithmetic_repair=="quotient" || arithmetic_repair=="joint")
                    for(size_t qi:row_quotients[id])if(reconstruct_quotient(quotient_relations[qi],protected_values,patch)){changed=true;break;}
                if(!changed && (arithmetic_repair=="linear" || arithmetic_repair=="joint"))changed=reconstruct_linear(id,protected_values,patch);
                if(!changed)continue;
                auto operation=patch.operation();
                if(operation.size()>protected_values.size()){
                    size_t before=operations.size();add_operation(operations,operation);
                    if(operations.size()>before){repaired_indices.insert(before);++arith_repair_proposals_;}
                }
                for(const auto& change:operation){
                    auto old=old_operation.find(change.first);
                    if(old==old_operation.end() || old->second!=change.second)
                        pending.insert(pending.end(),variable_arithmetic_rows[change.first].begin(),variable_arithmetic_rows[change.first].end());
                }
            }
        }
    }

    bool paws_reweight(NativeScore& current,NativeScore& best_score){
        if(scoring!="paws" || clauses.empty())return false;
        bool smoothing=rng()%100<10,changed=false;
        for(size_t id=0;id<clause_weights.size();++id){
            auto& weight=clause_weights[id];
            if(smoothing){if(!clause_scores[id].unsat && weight>1){--weight;changed=true;}}
            else if(clause_scores[id].unsat && weight<65535){++weight;changed=true;}
        }
        if(!changed)return false;
        ++weight_updates_;
        // Both incumbents are re-evaluated under the same weight version.
        // Rebuild the arithmetic and UF caches after restoring the current
        // assignment; no cached trial can survive a PAWS update.
        auto saved=assignment;assignment=best;
        auto best_arithmetic=initialize_scores();initialize_applications();best_score=native_score(best_arithmetic);
        assignment=std::move(saved);
        auto current_arithmetic=initialize_scores();initialize_applications();current=native_score(current_arithmetic);
        if(current<best_score){best_score=current;best=assignment;}
        best_arithmetic_unsat_=best_score.arithmetic.unsat;
        return true;
    }
    struct NativeChoice {
        bool found=false,escape=false;
        size_t index=0;
        NativeTrial trial;
    };
    NativeChoice greedy_native_operation(const std::vector<Operation>& operations,const NativeScore& current){
        NativeChoice choice;unsigned ties=0;++greedy_rounds_;
        for(size_t oi=0;oi<operations.size() && !expired();++oi){
            ++greedy_evaluations_;
            auto trial=trial_operation(operations[oi],current);
            bool take=!choice.found || trial.score<choice.trial.score;
            if(!take && !(choice.trial.score<trial.score) && rng()%++ties==0)take=true;
            if(take){
                if(!choice.found || trial.score<choice.trial.score)ties=1;
                choice.found=true;choice.index=oi;choice.trial=std::move(trial);
            }
        }
        return choice;
    }
    NativeChoice random_native_operation(const std::vector<Operation>& operations,const NativeScore& current){
        NativeChoice choice;if(operations.empty() || expired())return choice;
        choice.index=rng()%operations.size();choice.trial=trial_operation(operations[choice.index],current);
        choice.found=true;choice.escape=true;return choice;
    }
    NativeChoice choose_native_operation(const std::vector<Operation>& operations,NativeScore& current,
                                         NativeScore& best_score,unsigned steps,unsigned stall){
        if(operations.empty())return {};
        if(search_policy=="legacy"){
            bool walk=rng()%100<10 || stall>64;
            return walk?random_native_operation(operations,current):greedy_native_operation(operations,current);
        }
        // A plateau changes the escape decision for one move. It never turns
        // off hill climbing in subsequent rounds, even after a long stall.
        auto choice=greedy_native_operation(operations,current);
        if(!choice.found || choice.trial.score<current)return choice;
        if(stall+1>=8 && (steps+1)%16==0 && paws_reweight(current,best_score)){
            // All trials above used the preceding weight version. Rebuild both
            // incumbents in paws_reweight, then evaluate candidates afresh.
            ++paws_reselections_;choice=greedy_native_operation(operations,current);
            if(!choice.found || choice.trial.score<current)return choice;
        }
        return random_native_operation(operations,current);
    }
    struct PhaseState {
        bool boolean=false,initialized=false;
        uint64_t best_raw=0;
        unsigned stalled=0;
    };
    uint64_t phase_raw(const NativeScore& score,bool boolean)const{
        return boolean?score.boolean_unsat:uint64_t(score.arithmetic.unsat)+score.uf;
    }
    void enter_phase(PhaseState& phase,bool boolean,const NativeScore& current){
        if(phase.initialized && phase.boolean!=boolean)++phase_switches_;
        phase.boolean=boolean;phase.initialized=true;phase.stalled=0;phase.best_raw=phase_raw(current,boolean);
    }
    void observe_phase(PhaseState& phase,const NativeScore& current){
        uint64_t raw=phase_raw(current,phase.boolean);
        if(raw<phase.best_raw){phase.best_raw=raw;phase.stalled=0;}else ++phase.stalled;
    }
    bool boolean_less(const NativeScore& a,const NativeScore& b)const{
        if(a.boolean_weighted_unsat!=b.boolean_weighted_unsat)return a.boolean_weighted_unsat<b.boolean_weighted_unsat;
        if(a.boolean_distance<b.boolean_distance-1e-9)return true;
        if(b.boolean_distance<a.boolean_distance-1e-9)return false;
        return a<b;
    }
    bool operation_has_type(const Operation& operation,bool boolean)const{
        if(operation.empty())return false;
        for(const auto& change:operation)if(variables[change.first].is_bool()!=boolean)return false;
        return true;
    }
    void boolean_closure_candidates(const Operation& operation,std::vector<Operation>& operations,unsigned& work){
        if(operation.empty() || operation.size()>4 || operations.size()>=32)return;
        add_operation(operations,operation);
        if(operation.size()==4 || !work)return;
        Repair patch(*this);std::set<size_t> touched;
        for(const auto& change:operation){patch.put(change.first,change.second);
            touched.insert(variable_clauses[change.first].begin(),variable_clauses[change.first].end());}
        size_t broken=clauses.size();
        for(size_t id:touched)if(clause_kinds[id]==1){
            if(!work)return;
            --work;
            if(eval(clauses[id])==0){broken=id;break;}
        }
        if(broken==clauses.size())return;
        std::set<size_t> involved_variables;involved(clauses[broken],involved_variables);
        unsigned branches=0;
        for(size_t v:involved_variables){
            if(operation.count(v) || !variables[v].is_bool())continue;
            Int value=clamp(v,Int(1-assignment[v]));if(value==assignment[v])continue;
            auto next=operation;next[v]=value;boolean_closure_candidates(next,operations,work);
            if(++branches>=2 || !work || operations.size()>=32)break;
        }
    }
    std::vector<size_t> phase_clauses(bool boolean,const NativeScore& current,const std::vector<size_t>& violated){
        std::vector<size_t> eligible;
        for(size_t id:violated){
            bool use=boolean?(current.boolean_unsat?clause_kinds[id]==1:(clause_kinds[id]&1)!=0):(clause_kinds[id]&2)!=0;
            if(use)eligible.push_back(id);
        }
        if(eligible.size()>3){
            size_t start=rng()%eligible.size();std::rotate(eligible.begin(),eligible.begin()+start,eligible.end());eligible.resize(3);
        }
        return eligible;
    }
    void phase_operations(bool boolean,const NativeScore& current,const std::vector<size_t>& violated,
                          const std::vector<Operation>& uf,std::vector<Operation>& operations,std::set<size_t>& repaired){
        for(const auto& operation:uf)if(operation_has_type(operation,boolean)){
            add_operation(operations,operation);if(operations.size()>=8)break;
        }
        auto selected=phase_clauses(boolean,current,violated);
        if(boolean){
            std::set<size_t> seeds;
            for(size_t id:selected)involved(clauses[id],seeds);
            std::vector<size_t> order;
            for(size_t v:seeds)if(variables[v].is_bool())order.push_back(v);
            std::stable_sort(order.begin(),order.end(),[&](size_t a,size_t b){return last_variable_move[a]<last_variable_move[b];});
            if(order.size()>8)order.resize(8);
            unsigned work=256;
            for(size_t v:order){
                Int value=clamp(v,Int(1-assignment[v]));
                if(value!=assignment[v])boolean_closure_candidates(Operation{{v,value}},operations,work);
                if(operations.size()>=32)break;
            }
        }else{
            for(size_t id:selected)arithmetic_operations(clauses[id],operations,false,10);
            const z3::expr* active=selected.empty()?nullptr:&clauses[selected.front()];
            repair_arithmetic_operations(operations,repaired,active);compensate_operations(operations);
        }
    }
    NativeChoice greedy_boolean_operation(const std::vector<Operation>& operations,const NativeScore& current){
        NativeChoice choice;unsigned ties=0;++greedy_rounds_;
        for(size_t oi=0;oi<operations.size() && !expired();++oi){
            ++greedy_evaluations_;auto trial=trial_operation(operations[oi],current);
            bool take=!choice.found || boolean_less(trial.score,choice.trial.score);
            if(!take && !boolean_less(choice.trial.score,trial.score) && rng()%++ties==0)take=true;
            if(take){
                if(!choice.found || boolean_less(trial.score,choice.trial.score))ties=1;
                choice.found=true;choice.index=oi;choice.trial=std::move(trial);
            }
        }
        return choice;
    }
    NativeChoice choose_boolean_operation(const std::vector<Operation>& operations,NativeScore& current,
                                          NativeScore& best_score,unsigned steps,unsigned stall){
        auto choice=greedy_boolean_operation(operations,current);
        if(!choice.found || boolean_less(choice.trial.score,current))return choice;
        if(stall+1>=8 && (steps+1)%16==0 && paws_reweight(current,best_score)){
            ++paws_reselections_;choice=greedy_boolean_operation(operations,current);
            if(!choice.found || boolean_less(choice.trial.score,current))return choice;
        }
        return random_native_operation(operations,current);
    }
    z3::check_result check_with_phases(){
        prepare_applications();build_dependencies();initialize_applications();build_arithmetic_repairs();
        clause_weights.assign(clauses.size(),1);
        NativeScore current=native_score(initialize_scores()),best_score=current;
        best=assignment;has_best=true;initial_arithmetic_unsat_=best_arithmetic_unsat_=current.arithmetic.unsat;
        PhaseState phase;enter_phase(phase,current.boolean_unsat!=0,current);
        unsigned steps=0,stall=0;
        while(!current.solved() && !expired()){
            std::vector<size_t> violated;
            for(size_t id=0;id<clauses.size();++id)if(clause_scores[id].unsat)violated.push_back(id);
            std::vector<Operation> uf;uf_operations(uf);
            if(!phase.boolean && phase.stalled>=20){
                bool available=false;
                for(size_t id:violated)available|=(clause_kinds[id]&1)!=0;
                for(const auto& operation:uf)available|=operation_has_type(operation,true);
                if(available)enter_phase(phase,true,current);
            }
            std::vector<Operation> operations;std::set<size_t> repaired;bool cross=false;
            phase_operations(phase.boolean,current,violated,uf,operations,repaired);
            if(operations.empty()){
                phase_operations(!phase.boolean,current,violated,uf,operations,repaired);
                if(!operations.empty())enter_phase(phase,!phase.boolean,current);
            }
            if(operations.empty()){
                for(const auto& operation:uf){add_operation(operations,operation);if(operations.size()>=8)break;}
                cross=!operations.empty();
            }
            if(operations.empty())break;
            auto choice=(!cross && phase.boolean)?choose_boolean_operation(operations,current,best_score,steps,stall):
                                                  choose_native_operation(operations,current,best_score,steps,stall);
            if(!choice.found)break;
            const auto& operation=operations[choice.index];
            commit_operation(operation,choice.trial,choice.escape);current=choice.trial.score;
            ++steps;++stall;steps_taken_=steps;
            if(operation.size()>1)++uf_joint_moves_;
            if(repaired.count(choice.index))++arith_joint_moves_;
            for(const auto& change:operation)last_variable_move[change.first]=steps;
            if(current<best_score){best_score=current;best=assignment;stall=0;best_arithmetic_unsat_=current.arithmetic.unsat;}
            if(cross){++phase_cross_escapes_;enter_phase(phase,current.boolean_unsat!=0,current);}
            else if(phase.boolean){
                ++bool_phase_steps_;if(operation.size()>1)++boolean_joint_moves_;observe_phase(phase,current);
                if(current.boolean_unsat==0 || phase.stalled>=5)enter_phase(phase,false,current);
            }else{
                ++int_phase_steps_;observe_phase(phase,current);
            }
        }
        restore_best();initialize_applications();
        if(best_score.solved()){
            for(const auto& expression:original)if(eval(expression)==0)return z3::unknown;
            if(!validate_applications(source_uf))return z3::unknown;
            validated_=true;return z3::sat;
        }
        return z3::unknown;
    }
    z3::check_result check_with_applications(){
        if(search_phase=="alternating")return check_with_phases();
        prepare_applications();build_dependencies();initialize_applications();build_arithmetic_repairs();
        clause_weights.assign(clauses.size(),1);
        NativeScore current=native_score(initialize_scores()),best_score=current;
        best=assignment;has_best=true;unsigned stall=0,steps=0;
        initial_arithmetic_unsat_=best_arithmetic_unsat_=current.arithmetic.unsat;
        while(!current.solved() && !expired()){
            std::vector<Operation> operations;uf_operations(operations);
            size_t uf_limit=arithmetic_repair=="off"?24:16;
            if(operations.size()>uf_limit)operations.resize(uf_limit);
            std::vector<size_t> violated;const z3::expr* active_clause=nullptr;
            for(size_t id=0;id<clauses.size();++id)if(clause_scores[id].unsat)violated.push_back(id);
            if(!violated.empty()){
                const auto& clause=clauses[violated[rng()%violated.size()]];active_clause=&clause;
                arithmetic_operations(clause,operations);
            }
            std::set<size_t> arithmetic_repaired;
            repair_arithmetic_operations(operations,arithmetic_repaired,active_clause);
            compensate_operations(operations);
            if(operations.empty())break;
            auto choice=choose_native_operation(operations,current,best_score,steps,stall);
            if(!choice.found)break;
            size_t chosen=choice.index;auto& winner=choice.trial;
            bool improvement=winner.score<current;
            commit_operation(operations[chosen],winner,choice.escape);current=winner.score;++steps;++stall;steps_taken_=steps;
            if(operations[chosen].size()>1)++uf_joint_moves_;
            if(arithmetic_repaired.count(chosen))++arith_joint_moves_;
            for(const auto& change:operations[chosen])last_variable_move[change.first]=steps;
            if(current<best_score){best_score=current;best=assignment;stall=0;best_arithmetic_unsat_=best_score.arithmetic.unsat;}
            if(search_policy=="legacy" && !improvement && stall>=8 && steps%16==0)paws_reweight(current,best_score);
            if(steps%512==0 && stall>128){
                assignment=best;auto arithmetic=initialize_scores();initialize_applications();
                current=native_score(arithmetic);
            }
        }
        restore_best();initialize_applications();
        if(best_score.solved()){
            for(const auto& expression:original)if(eval(expression)==0)return z3::unknown;
            if(!validate_applications(source_uf))return z3::unknown;
            validated_=true;return z3::sat;
        }
        return z3::unknown;
    }

public:
    struct DiagnosticViolation {
        std::string scope;
        z3::expr expression;
        std::vector<std::pair<z3::expr,z3::expr>> assignments;
    };
    struct Diagnostics {
        unsigned input_unsat_count=0,working_unsat_count=0,uf_conflicts=0;
        bool truncated=false;
        std::vector<DiagnosticViolation> violations;
    };
    Diagnostics candidate_diagnostics(size_t limit=8){
        if(!has_best)throw std::runtime_error("no SLS candidate for diagnostics");
        // Explicit debugging only: search has ended, so reconstruct and count
        // the saved assignment exactly without charging the expired budget.
        budget_active=false;assignment=best;
        Diagnostics result;std::unordered_map<unsigned,Int> memo;
        std::vector<z3::expr> input_violations,working_violations;
        auto count=[&](const std::vector<z3::expr>& expressions,std::vector<z3::expr>& samples,unsigned& total){
            for(const auto& expression:expressions)if(eval_cached(expression,memo)==0){
                ++total;if(samples.size()<limit)samples.push_back(expression);
            }
        };
        count(original,input_violations,result.input_unsat_count);
        count(clauses,working_violations,result.working_unsat_count);
        unsigned shown_input=0,shown_working=0;
        auto append=[&](const z3::expr& expression,const std::string& scope){
            std::set<size_t> dependencies;involved(expression,dependencies);
            DiagnosticViolation violation{scope,expression,{}};
            for(size_t v:dependencies){
                auto value=variables[v].is_bool()?c.bool_val(assignment[v]!=0):c.int_val(assignment[v].get_str().c_str());
                violation.assignments.emplace_back(variables[v],value);
            }
            result.violations.push_back(std::move(violation));
        };
        for(size_t i=0;result.violations.size()<limit && (i<input_violations.size() || i<working_violations.size());++i){
            if(i<input_violations.size()){append(input_violations[i],"input");++shown_input;}
            if(result.violations.size()<limit && i<working_violations.size()){append(working_violations[i],"working");++shown_working;}
        }
        result.truncated=result.input_unsat_count>shown_input || result.working_unsat_count>shown_working;
        std::map<UfKey,std::map<Int,unsigned>> groups;
        for(const auto& application:source_uf){
            UfKey key;key.function=application.function;
            for(const auto& argument:application.arguments)key.arguments.push_back(eval_cached(argument,memo));
            ++groups[key][eval_cached(application.result,memo)];
        }
        for(const auto& group:groups){
            unsigned total=0,maximum=0;
            for(const auto& frequency:group.second){total+=frequency.second;maximum=std::max(maximum,frequency.second);}
            result.uf_conflicts+=total-maximum;
        }
        return result;
    }
    explicit NiaSolver(z3::context& ctx):c(ctx),rng(sls_seed){}
    void add(const z3::expr& e){
        original.push_back(e);collect(e);has_best=false;
    }
    void set(const char*,unsigned value){timeout_ms=value;}
    void set_interaction(const std::string& mode){interaction=mode;}
    void set_scoring(const std::string& mode){scoring=mode;}
    void set_search_policy(const std::string& mode){search_policy=mode;}
    void set_search_phase(const std::string& mode){search_phase=mode;}
    void set_candidate_policy(const std::string& mode){candidate_policy=mode;}
    uint64_t candidate_probes()const{return candidate_probes_;}
    uint64_t critical_candidates()const{return critical_candidates_;}
    uint64_t fallback_candidates()const{return fallback_candidates_;}
    uint64_t bool_phase_steps()const{return bool_phase_steps_;}
    uint64_t int_phase_steps()const{return int_phase_steps_;}
    uint64_t phase_switches()const{return phase_switches_;}
    uint64_t boolean_joint_moves()const{return boolean_joint_moves_;}
    uint64_t phase_cross_escapes()const{return phase_cross_escapes_;}
    uint64_t greedy_rounds()const{return greedy_rounds_;}
    uint64_t greedy_evaluations()const{return greedy_evaluations_;}
    uint64_t random_escape_moves()const{return random_escape_moves_;}
    uint64_t paws_reselections()const{return paws_reselections_;}
    void set_distance(const std::string& mode){distance_mode=mode;}
    void set_arithmetic_repair(const std::string& mode){arithmetic_repair=mode;}
    void set_active_arithmetic_rows(bool enabled){active_arithmetic_rows=enabled;}
    uint64_t arith_active_rows()const{return arith_active_rows_;}
    uint64_t arith_joint_moves()const{return arith_joint_moves_;}
    uint64_t arith_repair_proposals()const{return arith_repair_proposals_;}
    uint64_t arith_repair_attempts()const{return arith_repair_attempts_;}
    size_t arith_linear_rows()const{return arithmetic_rows.size();}
    size_t arith_quotient_relations()const{return quotient_relations.size();}
    unsigned initial_arithmetic_unsat()const{return initial_arithmetic_unsat_;}
    unsigned best_arithmetic_unsat()const{return best_arithmetic_unsat_;}
    uint64_t weight_updates()const{return weight_updates_;}
    void add_uf_application(unsigned function,const std::vector<z3::expr>& arguments,const z3::expr& result){
        for(const auto& argument:arguments)collect(argument);
        collect(result);
        source_uf.push_back({function,arguments,result});has_best=false;
    }
    uint64_t uf_joint_moves()const{return uf_joint_moves_;}
    unsigned uf_conflicts()const{return uf_conflicts_;}
    void prefer(const z3::expr& v,const z3::expr& value){
        collect(v);assignment[indices.at(v.id())]=Int(value.is_true()?"1":value.is_false()?"0":Z3_get_numeral_string(c,value));
    }
    bool candidate_available()const{return has_best;}
    unsigned long long steps_taken()const{return steps_taken_;}
    unsigned long long higher_degree_fallbacks()const{return higher_degree_fallbacks_;}
    bool validated()const{return validated_;}
    z3::check_result check(){
        if(search_phase=="alternating" && search_policy!="greedy")throw std::runtime_error("alternating search-phase requires greedy search-policy");
        struct BudgetGuard {bool& active;~BudgetGuard(){active=false;}} guard{budget_active};
        budget_active=true;
        has_best=false;invalid=false;evaluations=0;
        steps_taken_=0;higher_degree_fallbacks_=0;validated_=false;uf_joint_moves_=0;uf_conflicts_=0;weight_updates_=0;
        arith_joint_moves_=arith_repair_proposals_=arith_repair_attempts_=arith_active_rows_=0;
        initial_arithmetic_unsat_=best_arithmetic_unsat_=0;
        greedy_rounds_=greedy_evaluations_=random_escape_moves_=paws_reselections_=0;
        bool_phase_steps_=int_phase_steps_=phase_switches_=boolean_joint_moves_=phase_cross_escapes_=0;
        candidate_probes_=critical_candidates_=fallback_candidates_=0;
        clause_kinds.clear();
        arithmetic_rows.clear();quotient_relations.clear();
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
        if(!source_uf.empty() || scoring=="paws" || arithmetic_repair!="off" || search_policy=="greedy" || candidate_policy=="critical")return check_with_applications();
        Score current=initialize_scores(),best_score=current;best=assignment;has_best=true;
        initial_arithmetic_unsat_=best_arithmetic_unsat_=current.unsat;
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
            if(current<best_score){best_score=current;best=assignment;stall=0;best_arithmetic_unsat_=best_score.unsat;}
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
