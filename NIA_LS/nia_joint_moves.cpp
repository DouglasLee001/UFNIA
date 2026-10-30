#include "nia_ls.h"
#include <gmpxx.h>
#include <set>
#include <array>
#include <limits>

namespace nia {
namespace {
using Op=ls_solver::JointOperation;
using Kind=ls_solver::JointKind;
struct Stop {};
struct Work {
    unsigned left=24000;
    void tick(){if(!left)throw Stop{};--left;}
};
mpz_class z(__int128_t value){
    const bool negative=value<0;
    __uint128_t u=negative?__uint128_t(0)-__uint128_t(value):__uint128_t(value);
    char buffer[42];char* end=buffer+sizeof(buffer);*--end=0;
    do{*--end='0'+u%10;u/=10;}while(u);
    if(negative)*--end='-';
    return mpz_class(end);
}
mpz_class bounded(mpz_class value){
    if(mpz_sizeinbase(value.get_mpz_t(),2)>512)throw Stop{};
    return value;
}
bool narrow(const mpz_class& value,__int128_t& result){
    static const mpz_class limit=mpz_class(1)<<127;
    if(value < -limit || value>=limit)return false;
    const std::string digits=value.get_str();size_t i=value<0?1:0;__uint128_t u=0;
    for(;i<digits.size();++i)u=u*10+unsigned(digits[i]-'0');
    result=value<0?-__int128_t(u-1)-1:__int128_t(u);
    return true;
}
__int128_t checked(const mpz_class& value){
    __int128_t result;if(!narrow(value,result))throw Stop{};return result;
}
mpz_class power(mpz_class value,int exponent,Work& work){
    if(exponent<0 || exponent>16)throw Stop{};
    mpz_class result=1;
    for(int i=0;i<exponent;++i){work.tick();result=bounded(result*value);}
    return result;
}
bool legal_value(const ls_solver& s,size_t v,__int128_t value){
    if(v>=s._num_vars || !s._vars[v].is_nia || value==s._solution[v])return false;
    const auto& var=s._vars[v];
    const __int128_t limit=s.config.wide_arithmetic?1000000000000LL:100000000;
    if(value < -limit || value>limit || value<var.low_bound || value>var.upper_bound)return false;
    if(s._pre_value_1[v]!=INT32_MAX && value!=s._pre_value_1[v] &&
       (s._pre_value_2[v]==INT32_MAX || value!=s._pre_value_2[v]))return false;
    if(s.core_locked.size()>v && s.core_locked[v])return false;
    __int128_t delta,sum;
    if(__builtin_sub_overflow(value,s._solution[v],&delta))return false;
    if(v==s.last_op_var && !__builtin_add_overflow(delta,s.last_op_value,&sum) && sum==0)return false;
    return s._step>=s.tabulist[2*v+(delta>0?0:1)];
}
bool legal(const ls_solver& s,const Op& op){
    return op.x!=op.y && legal_value(s,op.x,op.x_value) && legal_value(s,op.y,op.y_value);
}
struct Polynomial {
    mpz_class c=0,x=0,y=0,xx=0,xy=0,yy=0;
    mpz_class value(const mpz_class& a,const mpz_class& b)const{
        return bounded(c+x*a+y*b+xx*a*a+xy*a*b+yy*b*b);
    }
};
bool polynomial(const ls_solver& s,int id,size_t x,size_t y,Polynomial& p,Work& work,bool linear=false){
    const auto& l=s._lits[id];p.c=z(l.key);
    for(const auto& ct:l.coff_terms){
        work.tick();const auto& t=s._terms[ct.term_idx];
        if(linear && (t.var_epxs.size()>1 || (!t.var_epxs.empty() && t.var_epxs[0].exponent!=1)))return false;
        int a=0,b=0;mpz_class coefficient=z(ct.coff);
        for(const auto& factor:t.var_epxs){
            work.tick();
            if(size_t(factor.var_index)==x)a+=factor.exponent;
            else if(size_t(factor.var_index)==y)b+=factor.exponent;
            else coefficient=bounded(coefficient*power(z(s._solution[factor.var_index]),factor.exponent,work));
        }
        if(a<0 || b<0 || a+b>2)return false;
        mpz_class* dest=a==2?&p.xx:b==2?&p.yy:a==1&&b==1?&p.xy:a==1?&p.x:b==1?&p.y:&p.c;
        *dest=bounded(*dest+coefficient);
    }
    return true;
}
bool literal_true(const lit& l,int sign,const mpz_class& value){
    return (l.is_equal?value==0:value<=0)==(sign>0);
}
std::vector<mpz_class> nearby(const ls_solver& s,size_t v){
    const auto current=z(s._solution[v]);std::vector<mpz_class> values;
    for(int delta:{-1,1,-2,2,-4,4})values.push_back(current+delta);
    if(s._pre_value_1[v]!=INT32_MAX)values.emplace_back(s._pre_value_1[v]);
    if(s._pre_value_2[v]!=INT32_MAX)values.emplace_back(s._pre_value_2[v]);
    values.push_back(z(s._vars[v].low_bound));values.push_back(z(s._vars[v].upper_bound));
    for(int small:{0,1,-1})values.emplace_back(small);
    return values;
}
// Checked, simultaneous trial. All affected clauses are evaluated once, even
// if both variables occur in the same term/literal/clause. No solver mutation.
struct Trial {
    std::map<size_t,__int128_t> terms,literals;
    struct ClauseState {int index,count,watch;};
    std::vector<ClauseState> clauses;
    int64_t score=0;
};
bool trial(const ls_solver& s,const Op& op,Trial& out,Work& work){
    if(!legal(s,op))return false;
    std::set<size_t> terms,literals;std::set<int> clauses;
    for(size_t v:{op.x,op.y}){
        for(const auto& te:s._vars[v].term_exps){work.tick();terms.insert(te.term_idx);}
        for(size_t l:s._vars[v].literal_idxs){work.tick();literals.insert(l);}
        for(int c:s._vars[v].clause_idxs){work.tick();clauses.insert(c);}
    }
    for(size_t id:terms){
        const auto& t=s._terms[id];mpz_class old=1,next=1;
        for(const auto& factor:t.var_epxs){
            work.tick();const size_t v=factor.var_index;
            const auto value=v==op.x?op.x_value:v==op.y?op.y_value:s._solution[v];
            old*=power(z(s._solution[v]),factor.exponent,work);
            next*=power(z(value),factor.exponent,work);
            checked(old);checked(next);
        }
        if(checked(old)!=t.value)return false;
        const auto value=checked(next);
        if((t.var_epxs.size()>2 || (!t.var_epxs.empty() && t.var_epxs[0].exponent>1)) &&
           (value>max_int || value < -max_int))return false;
        out.terms[id]=value;
    }
    for(size_t id:literals){
        const auto& l=s._lits[id];if(!l.lits_index || !l.is_nia_lit)continue;
        mpz_class old=z(l.key),next=old;
        for(const auto& ct:l.coff_terms){
            work.tick();auto it=out.terms.find(ct.term_idx);
            const mpz_class a=z(ct.coff)*z(s._terms[ct.term_idx].value);
            const mpz_class b=z(ct.coff)*z(it==out.terms.end()?s._terms[ct.term_idx].value:it->second);
            checked(a);checked(b);old+=a;next+=b;checked(old);checked(next);
        }
        if(checked(old)!=l.delta)return false;
        out.literals[id]=checked(next);
    }
    for(int id:clauses){
        const auto& c=s._clauses[id];int old_count=0,count=0,watch=0;
        for(int sign:c.literals){
            work.tick();const size_t id=std::abs(sign);const auto& l=s._lits[id];
            const bool old=l.is_nia_lit?(l.is_equal?l.delta==0:l.delta<=0):s._solution[l.delta]>0;
            old_count+=old==(sign>0);
            const auto it=out.literals.find(id);
            const bool next=it==out.literals.end()?old:(l.is_equal?it->second==0:it->second<=0);
            if(next==(sign>0)){++count;watch=sign;}
        }
        if(old_count!=c.sat_count)return false;
        if(!old_count && count)out.score+=c.weight;
        else if(old_count && !count)out.score-=c.weight;
        out.clauses.push_back({id,count,watch});
    }
    return true;
}
}

std::vector<ls_solver::JointOperation> ls_solver::generate_joint_operations(){
    std::vector<Op> result;
    if(!joint_operators_enabled || config.strategy!=SlsStrategy::corewarm2 || unsat_clauses->empty())return result;
    Work work;unsigned counts[2]={0,0};
    auto add=[&](size_t x,size_t y,const mpz_class& xv,const mpz_class& yv,Kind kind,const Polynomial& target,int sign){
        work.tick();if(counts[kind==Kind::divisibility?0:1]>=8)return;
        Op op{x,y,0,0,kind};
        if(!narrow(xv,op.x_value) || !narrow(yv,op.y_value) || !legal(*this,op))return;
        if(!literal_true(_lits[std::abs(sign)],sign,target.value(xv,yv)))return;
        if(x>y){std::swap(op.x,op.y);std::swap(op.x_value,op.y_value);}
        for(const auto& prev:result)if(prev.x==op.x && prev.y==op.y && prev.x_value==op.x_value && prev.y_value==op.y_value)return;
        ++counts[kind==Kind::divisibility?0:1];result.push_back(op);
    };
    try{
        unsigned targets=0,pairs=0;std::set<int> seen;
        const size_t start=_step%unsat_clauses->size();
        for(size_t ci=0;ci<std::min(size_t(4),size_t(unsat_clauses->size()));++ci){
            const auto& clause=_clauses[unsat_clauses->element_at((start+ci)%unsat_clauses->size())];
            for(int sign:clause.nia_literals){
                work.tick();if(!seen.insert(sign).second)continue;
                if(++targets>4)return result;
                const auto& target=_lits[std::abs(sign)];std::vector<size_t> vars;
                for(const auto& v:target.var_lit_terms){work.tick();if(vars.empty() || vars.back()!=v.var_idx)vars.push_back(v.var_idx);}
                if(vars.size()<2)continue;
                std::rotate(vars.begin(),vars.begin()+_step%vars.size(),vars.end());
                if(vars.size()>6)vars.resize(6);
                for(size_t i=0;i<vars.size();++i)for(size_t j=i+1;j<vars.size();++j){
                    if(++pairs>8)return result;
                    const size_t x=vars[i],y=vars[j];Polynomial p;
                    if(!polynomial(*this,std::abs(sign),x,y,p,work))continue;
                    if(target.is_equal && sign>0 && p.xy!=0 && p.xx==0 && p.yy==0){
                        for(bool reverse:{false,true})for(const auto& v:nearby(*this,reverse?y:x)){
                            work.tick();if(counts[0]>=8)break;
                            const mpz_class denominator=p.xy*v+(reverse?p.x:p.y);
                            const mpz_class numerator=-(p.c+(reverse?p.y:p.x)*v);
                            if(denominator!=0){
                                if(!mpz_divisible_p(numerator.get_mpz_t(),denominator.get_mpz_t()))continue;
                                const mpz_class other=numerator/denominator;
                                add(x,y,reverse?other:v,reverse?v:other,Kind::divisibility,p,sign);
                            }else if(numerator==0){
                                for(int delta:{-1,1}){
                                    const mpz_class other=z(_solution[reverse?x:y])+delta;
                                    add(x,y,reverse?other:v,reverse?v:other,Kind::divisibility,p,sign);
                                }
                            }
                        }
                    }
                    // Preserve one currently true, positively occurring linear
                    // equality. Other variables in that equality stay fixed.
                    unsigned anchors=0;
                    for(size_t id:_vars[x].literal_idxs){
                        work.tick();if(counts[1]>=8)break;
                        const auto& anchor=_lits[id];
                        if(!anchor.is_nia_lit || !anchor.is_equal || anchor.delta!=0)continue;
                        Polynomial eq;if(!polynomial(*this,id,x,y,eq,work,true) || eq.x==0 || eq.y==0)continue;
                        bool positive=false;
                        for(size_t k=_score_literal_offsets[id];k<_score_literal_offsets[id+1];++k){work.tick();if(_score_clause_occurrences[k]>0){positive=true;break;}}
                        if(!positive)continue;
                        if(++anchors>2)break;
                        mpz_class gcd;mpz_gcd(gcd.get_mpz_t(),eq.x.get_mpz_t(),eq.y.get_mpz_t());
                        const mpz_class dx=eq.y/gcd,dy=-eq.x/gcd;
                        const auto a=z(_solution[x]),b=z(_solution[y]);
                        const mpz_class q2=bounded(p.xx*dx*dx+p.xy*dx*dy+p.yy*dy*dy);
                        const mpz_class q1=bounded(2*p.xx*a*dx+p.xy*(a*dy+b*dx)+2*p.yy*b*dy+p.x*dx+p.y*dy);
                        const mpz_class q0=p.value(a,b);
                        std::vector<mpz_class> ks={-1,1,-2,2};
                        auto around=[&](const mpz_class& n,const mpz_class& d){
                            mpz_class base;mpz_fdiv_q(base.get_mpz_t(),n.get_mpz_t(),d.get_mpz_t());
                            for(int offset=-1;offset<=2;++offset)ks.push_back(base+offset);
                        };
                        if(q2!=0){
                            const mpz_class disc=bounded(q1*q1-4*q2*q0);
                            if(disc>=0){mpz_class sq;mpz_sqrt(sq.get_mpz_t(),disc.get_mpz_t());around(-q1-sq,2*q2);around(-q1+sq,2*q2);}
                        }else if(q1!=0)around(-q0,q1);
                        for(const auto& k:ks){
                            work.tick();if(k==0)continue;
                            const mpz_class xv=bounded(a+k*dx),yv=bounded(b+k*dy);
                            if(eq.value(xv,yv)==0)add(x,y,xv,yv,Kind::preserve_equality,p,sign);
                        }
                    }
                }
            }
        }
    }catch(const Stop&){} // Bounded work: keep only fully constructed candidates.
    return result;
}

bool ls_solver::score_joint_operation(const JointOperation& op,int64_t& score)const{
    try{Work work;Trial candidate;if(!trial(*this,op,candidate,work))return false;score=candidate.score;return true;}
    catch(const Stop&){return false;}
}

bool ls_solver::apply_joint_operation(const JointOperation& op){
    Trial candidate;
    try{Work work;if(!trial(*this,op,candidate,work) || candidate.score<=0)return false;}
    catch(const Stop&){return false;}
    const auto dx=op.x_value-_solution[op.x],dy=op.y_value-_solution[op.y];
    // No sequential intermediate assignment is exposed to scoring, restarts,
    // best-solution tracking, or clause maintenance.
    _solution[op.x]=op.x_value;_solution[op.y]=op.y_value;
    for(const auto& entry:candidate.terms){
        _terms[entry.first].value=entry.second;
        for(const auto& v:_terms[entry.first].var_epxs)_vars[v.var_index].scoring_coffs_valid=false;
    }
    for(const auto& entry:candidate.literals){
        auto& l=_lits[entry.first];l.delta=entry.second;l.is_true=(l.is_equal?l.delta==0:l.delta<=0)?1:-1;
        critical_operation_caches[2*entry.first].valid=false;
        critical_operation_caches[2*entry.first+1].valid=false;
    }
    for(const auto& state:candidate.clauses){
        auto& c=_clauses[state.index];
        if(c.sat_count==0)for(int l:c.bool_literals)_vars[_lits[std::abs(l)].delta].score-=c.weight;
        if(c.sat_count==1){const auto& l=_lits[std::abs(c.watch_lit_idx)];if(!l.is_nia_lit)_vars[l.delta].score+=c.weight;}
        if(c.sat_count==0 && state.count>0){_lit_in_unsat_clause_num-=c.literals.size();_bool_lit_in_unsat_clause_num-=c.bool_literals.size();}
        if(c.sat_count>0 && state.count==0){_lit_in_unsat_clause_num+=c.literals.size();_bool_lit_in_unsat_clause_num+=c.bool_literals.size();}
        c.sat_count=state.count;c.watch_lit_idx=state.watch;
        if(c.sat_count>0)sat_a_clause(state.index);else unsat_a_clause(state.index);
        if(c.sat_count>0 && c.sat_count<c.literals.size())sat_clause_with_false_literal->insert_element(state.index);
        else sat_clause_with_false_literal->delete_element(state.index);
        if(c.sat_count==0)for(int l:c.bool_literals)_vars[_lits[std::abs(l)].delta].score+=c.weight;
        if(c.sat_count==1){const auto& l=_lits[std::abs(c.watch_lit_idx)];if(!l.is_nia_lit)_vars[l.delta].score-=c.weight;}
    }
    ++distance_current_generation;
    for(const auto& change:{std::make_pair(op.x,dx),std::make_pair(op.y,dy)}){
        const size_t direction=change.second>0?0:1;
        last_move[2*change.first+direction]=_step;
        tabulist[2*change.first+1-direction]=_step+3+mt()%10;
    }
    last_op_var=op.y;last_op_value=dy;
    if(_record_frequency)modify_frequency_in_unsat_clauses();
    if(op.kind==Kind::divisibility)++joint_divisibility_moves;else ++joint_preserving_moves;
    if(joint_divisibility_moves+joint_preserving_moves<=8)
        std::cerr<<"joint_move kind="<<(op.kind==Kind::divisibility?"divisibility":"preserve_equality")
                 <<" step="<<_step<<" score="<<candidate.score<<"\n";
    return true;
}

bool ls_solver::try_joint_move(){
    if(!joint_operators_enabled || config.strategy!=SlsStrategy::corewarm2)return false;
    // Deterministic throttling of the expensive fallback, independent of time.
    if(joint_probes++%16!=0)return false;
    ++joint_attempts;const auto choices=generate_joint_operations();joint_candidates+=choices.size();
    int64_t best_score=0;size_t best=choices.size();uint64_t best_age=UINT64_MAX;
    for(size_t i=0;i<choices.size();++i){
        ++joint_evaluations;int64_t score;
        if(!score_joint_operation(choices[i],score) || score<=0)continue;
        const auto& op=choices[i];const uint64_t age=std::max({last_move[2*op.x],last_move[2*op.x+1],last_move[2*op.y],last_move[2*op.y+1]});
        if(score>best_score || (score==best_score && age<best_age)){best_score=score;best_age=age;best=i;}
    }
    return best<choices.size() && apply_joint_operation(choices[best]);
}
}
