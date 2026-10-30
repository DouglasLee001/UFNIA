#include "nia_linear_initialize.h"
#include <z3++.h>
#include <algorithm>
#include <chrono>
#include <iostream>
#include <map>
#include <set>
#include <unordered_map>
#include <vector>

#include "nia_linearize.h"
#include "nia_lia_sls.h"

#include "nia_linear_initialize.h"
#include <fstream>
#include <sstream>
#include <cctype>
#include <cmath>

namespace nia {
// Vertices are signed literals. Equal open neighborhoods form independent
// groups. A complete bipartite set of clauses (a OR b), a in A, b in B,
// equals (AND A) OR (AND B), for arbitrary arithmetic/Boolean atom values.
struct BinaryClauseFactorization {
    std::vector<std::vector<int>> groups;
    std::vector<std::pair<size_t,size_t>> edges;
    std::vector<size_t> other;
    size_t binary_count=0;
    explicit BinaryClauseFactorization(const std::vector<std::vector<int>>& clauses){
        std::map<int,std::vector<int>> neighbors;
        for(size_t i=0;i<clauses.size();++i){
            const auto& clause=clauses[i];
            if(clause.size()!=2 || clause[0]==clause[1]){other.push_back(i);continue;}
            int a=clause[0],b=clause[1];
            if(a==-b)continue; // Propositional tautology.
            ++binary_count;neighbors[a].push_back(b);neighbors[b].push_back(a);
        }
        std::map<std::vector<int>,size_t> signatures;
        std::map<int,size_t> group_for_literal;
        for(auto& entry:neighbors){
            auto& adjacent=entry.second;
            std::sort(adjacent.begin(),adjacent.end());
            adjacent.erase(std::unique(adjacent.begin(),adjacent.end()),adjacent.end());
            auto found=signatures.emplace(adjacent,groups.size());
            if(found.second)groups.emplace_back();
            size_t group=found.first->second;
            groups[group].push_back(entry.first);group_for_literal.emplace(entry.first,group);
        }
        std::set<std::pair<size_t,size_t>> distinct;
        for(const auto& entry:neighbors){
            size_t a=group_for_literal.at(entry.first);
            for(int literal:entry.second){
                size_t b=group_for_literal.at(literal);
                if(a<=b)distinct.emplace(a,b);
            }
        }
        edges.assign(distinct.begin(),distinct.end());
    }
};

// Read the full converted CNF independently of the local-search simplifier.
// This lets the seed model be checked before variable/clause elimination.
struct ConvertedSeedInput {
    z3::context& c;
    bool gate_input=false;
    bool raw_gates=false;
    bool factor_binary=false;
    std::vector<std::vector<int>> raw_cnf;
    std::map<std::string,z3::expr> vars;
    std::map<int,z3::expr> lits, raw_lits;
    std::map<std::string,z3::expr> auxiliary_symbols;
    z3::expr_vector clauses, validation_clauses;
    ConvertedSeedInput(z3::context& ctx,SlsStrategy strategy=SlsStrategy::automatic,bool raw=false,bool compact=false)
        :c(ctx),gate_input(sls_config(strategy).gate_input),raw_gates(raw),factor_binary(compact),clauses(ctx),validation_clauses(ctx){}
    z3::expr var(const std::string& name,bool boolean){
        auto it=vars.find(name);
        if(it!=vars.end()){
            if(it->second.is_bool()!=boolean)throw std::runtime_error("ambiguous variable sort");
            return it->second;
        }
        z3::expr e=boolean?c.bool_const(name.c_str()):c.int_const(name.c_str());
        vars.emplace(name,e);return e;
    }
    std::vector<std::string> tokens(const std::string& text){
        if(!gate_input){
            std::vector<std::string> result;std::string part;
            for(char ch:text){
                if(std::isspace(static_cast<unsigned char>(ch)) || ch=='(' || ch==')'){
                    if(!part.empty()){result.push_back(part);part.clear();}
                    if(ch=='(' || ch==')')result.push_back(std::string(1,ch));
                }else part+=ch;
            }
            if(!part.empty())result.push_back(part);return result;
        }
        std::vector<std::string> result;std::string part;unsigned atom_depth=0;
        auto flush=[&](){if(!part.empty()){result.push_back(part);part.clear();}atom_depth=0;};
        for(char ch:text){
            if(std::isspace(static_cast<unsigned char>(ch)))flush();
            else if(ch=='('){if(part.empty())result.push_back("(");else{part+=ch;++atom_depth;}}
            else if(ch==')'){if(!part.empty() && atom_depth){part+=ch;--atom_depth;}else{flush();result.push_back(")");}}
            else part+=ch;
        }
        flush();return result;
    }
    z3::expr parse(const std::vector<std::string>& ts,size_t& pos,bool boolean=false){
        if(pos>=ts.size())throw std::runtime_error("truncated literal");
        std::string token=ts[pos++];
        if(token!="("){
            if(token=="true" || token=="false")return c.bool_val(token=="true");
            size_t offset=token[0]=='-' || token[0]=='+' ? 1 : 0;
            bool numeral=offset<token.size();
            for(size_t i=offset;i<token.size();++i)numeral&=std::isdigit(static_cast<unsigned char>(token[i]))!=0;
            if(numeral)return c.int_val(token.c_str());
            if(token==")")throw std::runtime_error("unexpected closing parenthesis");
            return var(token,boolean);
        }
        if(pos>=ts.size())throw std::runtime_error("missing operator");
        std::string op=ts[pos++];
        bool bool_args=op=="and" || op=="or" || op=="not" || op=="=>";
        z3::expr_vector args(c);
        while(pos<ts.size() && ts[pos]!=")")args.push_back(parse(ts,pos,bool_args));
        if(pos==ts.size())throw std::runtime_error("unclosed expression");
        ++pos;
        if(op=="+")return z3::sum(args);
        if(op=="*"){z3::expr e=c.int_val(1);for(auto a:args)e=e*a;return e.simplify();}
        if(op=="-"){
            if(args.empty())throw std::runtime_error("empty subtraction");
            z3::expr e=args[0];if(args.size()==1)return -e;
            for(unsigned i=1;i<args.size();++i)e=e-args[i];return e;
        }
        if(op=="and")return z3::mk_and(args);
        if(op=="or")return z3::mk_or(args);
        if(op=="not" && args.size()==1)return !args[0];
        if(args.size()==2){
            if(op=="=")return args[0]==args[1];
            if(op=="<=")return args[0]<=args[1];
            if(op==">=")return args[0]>=args[1];
            if(op=="<")return args[0]<args[1];
            if(op==">")return args[0]>args[1];
            if(op=="=>")return z3::implies(args[0],args[1]);
        }
        throw std::runtime_error("unsupported seed operator: "+op);
    }
    unsigned raw_cost(z3::model& model){
        std::map<int,bool> truth;
        for(const auto& entry:lits){auto value=model.eval(entry.second,true).simplify();if(!value.is_true() && !value.is_false())return UINT32_MAX;truth.emplace(entry.first,value.is_true());}
        unsigned cost=0;
        for(const auto& clause:raw_cnf){bool satisfied=false;for(int literal:clause)if(truth.at(std::abs(literal))==(literal>0)){satisfied=true;break;}if(!satisfied)++cost;}
        return cost;
    }
    bool validate_raw(z3::model& model){return raw_cost(model)==0;}
    void read(const std::string& path){
        std::ifstream in(path);uint64_t count;
        if(!(in>>count))throw std::runtime_error("cannot read seed input");
        std::string line;std::getline(in,line);
        while(std::getline(in,line) && line!="0"){
            auto ts=tokens(line);if(ts.empty())continue;
            int id=std::stoi(ts[0]);size_t pos=1;
            if(pos>=ts.size())throw std::runtime_error("missing literal body");
            if(gate_input && ts.size()==2 && ts[pos]!="("){
                z3::expr atom=ts[pos]=="true" || ts[pos]=="false"?c.bool_val(ts[pos]=="true"):var(ts[pos],true);
                if(raw_gates){lits.emplace(id,atom);raw_lits.emplace(id,atom);}
                else{lits.emplace(id,atom.simplify());raw_lits.emplace(id,atom.simplify());}
                continue;
            }
            if(ts[pos]=="or" || ts[pos]=="if" || (gate_input && (ts[pos]=="ite" || ts[pos]=="iff"))){
                if(ts.size()<3)throw std::runtime_error("unsupported auxiliary literal");
                z3::expr symbol=c.bool_const(ts[2].c_str());
                raw_lits.emplace(id,symbol);
                if(raw_gates || ts.size()==3){lits.emplace(id,var(ts[2],true));}
                else{
                    z3::expr_vector refs(c);
                    for(size_t i=3;i<ts.size();++i){
                        int index=std::stoi(ts[i]);auto it=lits.find(std::abs(index));
                        if(it==lits.end())throw std::runtime_error("forward auxiliary reference");
                        refs.push_back(index>0?it->second:!it->second);
                    }
                    // Recover the defined gate instead of asking the linear
                    // solver to rediscover it from its CNF encoding. The model
                    // exports the gate's evaluated value, so every original
                    // CNF clause is still checked under a complete assignment.
                    z3::expr definition=c.bool_val(false);
                    if(ts[pos]=="or")definition=z3::mk_or(refs);
                    else if(gate_input && ts[pos]=="iff" && refs.size()==2)definition=refs[0]==refs[1];
                    else if(refs.size()==3)definition=z3::ite(refs[0],refs[1],refs[2]);
                    else throw std::runtime_error("invalid auxiliary conditional");
                    if(!vars.emplace(ts[2],definition).second)throw std::runtime_error("duplicate auxiliary name");
                    auxiliary_symbols.emplace(ts[2],symbol);
                    lits.emplace(id,definition);
                }
            }else{
                z3::expr e=parse(ts,pos,ts[pos]!="(");
                if(pos!=ts.size() || !e.is_bool())throw std::runtime_error("invalid literal");
                lits.emplace(id,e.simplify());
                raw_lits.emplace(id,e.simplify());
            }
        }
        if(!(in>>count))throw std::runtime_error("missing clause count");
        if(factor_binary && count<50000)factor_binary=false;
        const bool compact_input=raw_gates || factor_binary;
        if(gate_input)raw_cnf.reserve(count);
        auto expression=[&](const std::vector<int>& refs){
            z3::expr_vector disj(c);
            for(int index:refs){auto atom=lits.at(std::abs(index));disj.push_back(index>0?atom:!atom);}
            return z3::mk_or(disj).simplify();
        };
        for(uint64_t i=0;i<count;++i){
            std::string token;if(!(in>>token) || token!="(")throw std::runtime_error("missing clause");
            std::vector<int> refs;bool closed=false;
            z3::expr_vector disj(c),raw_disj(c);
            while(in>>token){
                if(token==")"){closed=true;break;}
                int index=std::stoi(token);
                if(!lits.count(std::abs(index)))throw std::runtime_error("unknown literal");
                if(gate_input)refs.push_back(index);
                else{
                    auto atom=lits.at(std::abs(index));disj.push_back(index>0?atom:!atom);
                    auto raw=raw_lits.at(std::abs(index));raw_disj.push_back(index>0?raw:!raw);
                }
            }
            if(!closed)throw std::runtime_error("unclosed clause");
            if(gate_input)raw_cnf.push_back(refs);
            if(compact_input)continue;
            // Keep the original AST construction order for the first SLS
            // pass: its preprocessing and seeded trajectory depend on IDs.
            for(int index:refs){
                auto atom=lits.at(std::abs(index));disj.push_back(index>0?atom:!atom);
                auto raw=raw_lits.at(std::abs(index));raw_disj.push_back(index>0?raw:!raw);
            }
            clauses.push_back(z3::mk_or(disj).simplify());
            validation_clauses.push_back(z3::mk_or(raw_disj));
        }
        if(compact_input){
            BinaryClauseFactorization factor(raw_cnf);
            for(size_t i:factor.other)clauses.push_back(expression(raw_cnf[i]));
            std::vector<z3::expr> groups;groups.reserve(factor.groups.size());
            for(const auto& members:factor.groups){
                z3::expr_vector conjuncts(c);
                for(int index:members){auto atom=lits.at(std::abs(index));conjuncts.push_back(index>0?atom:!atom);}
                groups.push_back(z3::mk_and(conjuncts).simplify());
            }
            for(auto edge:factor.edges)clauses.push_back((groups[edge.first] || groups[edge.second]).simplify());
            std::cerr<<"binary_factoring original="<<raw_cnf.size()<<" binary="<<factor.binary_count
                     <<" groups="<<factor.groups.size()<<" edges="<<factor.edges.size()<<" reduced="<<clauses.size()<<"\n";
        }
    }
};


bool make_linear_initial_model(const std::string& path,double budget_seconds,initial_model& result,SlsStrategy strategy){
    auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    try{
        z3::context c;ConvertedSeedInput input(c,strategy);input.read(path);
        Seeder seed(c,1);std::set<unsigned> seen;
        for(auto e:input.clauses){seed.units(e);seed.count(e,seen);}
        LiaSolver solver(c);
        for(auto e:input.clauses)solver.add(seed.visit(e));
        for(auto& d:seed.domains)solver.add(d.second);
        if(elapsed()>=budget_seconds)return false;
        solver.set("timeout",unsigned(1000*(budget_seconds-elapsed())));
        auto status=solver.check();
        std::cerr<<"linear_initialization_status="<<status<<" elapsed="<<elapsed()<<" bounded_variables="<<seed.domains.size()<<"\n";
        if(status!=z3::sat)return false;
        auto model=solver.get_model();
        // Validate against every original CNF clause, not its linearized form
        // or the local-search parser's possibly simplified representation.
        z3::expr_vector gate_symbols(c),gate_values(c);
        for(const auto& entry:input.auxiliary_symbols){
            gate_symbols.push_back(entry.second);
            gate_values.push_back(model.eval(input.vars.at(entry.first),true));
        }
        for(auto e:input.validation_clauses){
            if(!gate_symbols.empty())e=e.substitute(gate_symbols,gate_values);
            if(!model.eval(e,true).simplify().is_true())return false;
        }
        initial_model candidate;
        for(const auto& entry:input.vars)candidate.values.emplace(entry.first,model.eval(entry.second,true).to_string());
        candidate.seconds=elapsed();candidate.bounded_variables=seed.domains.size();
        result=std::move(candidate);
        return true;
    }catch(const std::exception& e){
        std::cerr<<"linear_initialization=skipped reason="<<e.what()<<"\n";
        return false;
    }
}
}

namespace nia {
bool make_progressive_initial_model(const std::string& path,double budget_seconds,initial_model& best,SlsStrategy strategy){
    auto began=std::chrono::steady_clock::now();
    auto elapsed=[&](){return std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();};
    unsigned checks=0,best_cost=UINT32_MAX,best_prefix=0;
    const char* reason="budget";
    auto finish=[&](bool complete){
        best.seconds=elapsed();
        std::cerr<<"progressive_stage status="<<reason<<" checks="<<checks
                 <<" candidate="<<(!best.values.empty())<<" candidate_unsat="<<best_cost
                 <<" best_prefix="<<best_prefix<<" seconds="<<best.seconds<<"\n";
        return complete;
    };
    try{
        z3::context c;ConvertedSeedInput input(c,strategy);input.read(path);
        Seeder seed(c,1);std::set<unsigned> seen;
        for(auto e:input.clauses){seed.units(e);seed.count(e,seen);}
        z3::expr_vector linear(c);
        for(auto e:input.clauses){if(elapsed()>=budget_seconds)return finish(false);linear.push_back(seed.visit(e));}
        LiaSolver solver(c);
        for(auto& d:seed.domains)solver.add(d.second);
        const unsigned total=linear.size();
        unsigned added=0,target=std::min(total,std::max(1u,total/16));
        while(elapsed()<budget_seconds){
            while(added<target)solver.add(linear[added++]);
            double remaining=budget_seconds-elapsed();
            if(remaining<=0)break;
            // Short prefix checks reserve most of the budget for the final check.
            unsigned ms=unsigned(1000*remaining);
            if(target<total)ms=std::min(ms,500u);
            solver.set("timeout",std::max(1u,ms));
            ++checks;auto status=solver.check();
            if(status==z3::sat || solver.candidate_available()){
                auto model=solver.get_model();
                z3::expr_vector symbols(c),values(c);
                for(const auto& entry:input.auxiliary_symbols){
                    symbols.push_back(entry.second);
                    values.push_back(model.eval(input.vars.at(entry.first),true));
                }
                unsigned cost=0;
                for(auto clause:input.validation_clauses){
                    if(!symbols.empty())clause=clause.substitute(symbols,values);
                    if(!model.eval(clause,true).simplify().is_true())++cost;
                }
                if(cost<best_cost){
                    initial_model candidate;
                    for(const auto& entry:input.vars)
                        candidate.values.emplace(entry.first,model.eval(entry.second,true).to_string());
                    candidate.bounded_variables=seed.domains.size();
                    best=std::move(candidate);best_cost=cost;best_prefix=target;
                }
                if(cost==0){reason="sat";return finish(true);}
            }
            // SLS UNKNOWN can expose its best assignment for the handoff.
            if(target==total){reason="unknown";return finish(false);}
            target=std::min(total,std::max(target+1,target*2));
        }
        return finish(false);
    }catch(const std::exception& error){
        std::cerr<<"progressive_error="<<error.what()<<"\n";
        reason="unsupported";return finish(false);
    }
}
}

// Exact model validation and unit elimination share the same input reader.
#include <filesystem>
#include <functional>
#include <iterator>
#include <memory>
#include <regex>

namespace nia_sls_internal {



using Values=std::map<std::string,std::string>;

static Values read_values(const std::string& path){
    std::ifstream input(path);if(!input)throw std::runtime_error("cannot read SLS model");
    Values values;std::string line;
    while(std::getline(input,line)){
        size_t tab=line.find('\t');if(tab==std::string::npos)throw std::runtime_error("invalid SLS model row");
        values[line.substr(0,tab)]=line.substr(tab+1);
    }
    return values;
}

static void write_values(const std::string& path,const Values& values){
    std::ofstream output(path);
    for(const auto& item:values)output<<item.first<<'\t'<<item.second<<'\n';
    if(!output)throw std::runtime_error("cannot write SLS model");
}

static bool validate_values(const std::string& path,const Values& values){
    try{
        z3::context context;nia::ConvertedSeedInput input(context,nia::SlsStrategy::corewarm2);input.read(path);z3::model model(context);
        for(const auto& item:values){
            auto found=input.vars.find(item.first);if(found==input.vars.end())continue;
            auto variable=found->second;
            if(!variable.is_const() || variable.decl().decl_kind()!=Z3_OP_UNINTERPRETED)continue;
            std::string number=item.second;
            if(number.rfind("(- ",0)==0 && number.back()==')')number="-"+number.substr(3,number.size()-4);
            auto value=variable.is_bool()?context.bool_val(number=="true"):context.int_val(number.c_str());
            auto declaration=variable.decl();model.add_const_interp(declaration,value);
        }
        unsigned cost=input.raw_cost(model);std::cerr<<"converted_model_unsat="<<cost<<'\n';return cost==0;
    }catch(const std::exception& error){std::cerr<<"converted_model_error="<<error.what()<<'\n';return false;}
}

bool validate_model(const std::string& input,const std::string& model){
    try{return validate_values(input,read_values(model));}
    catch(const std::exception& error){std::cerr<<error.what()<<'\n';return false;}
}

// Preserve insertion order: elimination choices and the emitted residual CNF
// must stay deterministic when a coefficient cancels and is later reintroduced.
struct UnitPolynomial {
    using Monomial=std::vector<std::string>;
    using Term=std::pair<Monomial,mpz_class>;
    std::vector<Term> terms;
    std::map<Monomial,size_t> positions;
    size_t size()const{return positions.size();}
    void add(const Monomial& monomial,const mpz_class& coefficient,bool keep_zero=false){
        auto found=positions.find(monomial);
        if(found==positions.end()){
            if(coefficient==0 && !keep_zero)return;
            positions.emplace(monomial,terms.size());terms.emplace_back(monomial,coefficient);
        }else{
            auto& value=terms[found->second].second;value+=coefficient;
            if(value==0 && !keep_zero)positions.erase(found);
        }
    }
    std::set<std::string> variables()const{
        std::set<std::string> result;
        for(const auto& term:terms)if(term.second!=0)result.insert(term.first.begin(),term.first.end());
        return result;
    }
};

static UnitPolynomial polynomial_add(UnitPolynomial left,const UnitPolynomial& right,int factor=1){
    for(const auto& term:right.terms)if(term.second!=0)left.add(term.first,mpz_class(factor)*term.second);
    return left;
}

static UnitPolynomial polynomial_multiply(const UnitPolynomial& left,const UnitPolynomial& right){
    if(left.size()*right.size()>4096)throw std::overflow_error("expansion");
    UnitPolynomial temporary;
    const mpz_class limit=mpz_class(1)<<61;
    for(const auto& x:left.terms)if(x.second!=0)for(const auto& y:right.terms)if(y.second!=0){
        auto monomial=x.first;monomial.insert(monomial.end(),y.first.begin(),y.first.end());
        std::sort(monomial.begin(),monomial.end());
        if(monomial.size()>8)throw std::overflow_error("degree");
        temporary.add(monomial,x.second*y.second,true);
        if(abs(temporary.terms[temporary.positions.at(monomial)].second)>limit)throw std::overflow_error("coefficient");
    }
    UnitPolynomial result;
    for(const auto& term:temporary.terms)if(term.second!=0)result.add(term.first,term.second);
    return result;
}

static std::vector<std::string> split_words(const std::string& line){
    std::istringstream input(line);std::vector<std::string> words;std::string word;
    while(input>>word)words.push_back(word);return words;
}

static bool integer_word(const std::string& text){
    size_t i=!text.empty() && text.front()=='-'?1:0;
    if(i==text.size())return false;
    for(;i<text.size();++i)if(text[i]<'0' || text[i]>'9')return false;
    return true;
}

static UnitPolynomial polynomial_expression(const std::vector<std::string>& tokens,size_t& position){
    if(position>=tokens.size())throw std::runtime_error("truncated polynomial");
    std::string token=tokens[position++];
    if(token!="("){
        UnitPolynomial result;
        if(integer_word(token))result.add({},mpz_class(token,10));
        else{
            static const std::regex variable("[A-Za-z_][A-Za-z0-9_!]*");
            if(!std::regex_match(token,variable))throw std::runtime_error("unsupported variable: "+token);
            result.add({token},1);
        }
        return result;
    }
    if(position>=tokens.size())throw std::runtime_error("missing polynomial operator");
    std::string operation=tokens[position++];std::vector<UnitPolynomial> arguments;
    while(position<tokens.size() && tokens[position]!=")")arguments.push_back(polynomial_expression(tokens,position));
    if(position==tokens.size())throw std::runtime_error("unclosed polynomial");++position;
    UnitPolynomial result;
    if(operation=="+")for(const auto& argument:arguments)result=polynomial_add(std::move(result),argument);
    else if(operation=="*"){
        result.add({},1);
        for(const auto& argument:arguments)result=polynomial_multiply(result,argument);
    }else if(operation=="-" && !arguments.empty()){
        if(arguments.size()==1){for(const auto& term:arguments[0].terms)if(term.second!=0)result.add(term.first,-term.second);}
        else{
            result=arguments[0];
            for(size_t i=1;i<arguments.size();++i)result=polynomial_add(std::move(result),arguments[i],-1);
        }
    }else throw std::runtime_error("unsupported polynomial operator: "+operation);
    return result;
}

static UnitPolynomial polynomial_substitute(const UnitPolynomial& polynomial,const std::string& variable,const UnitPolynomial& replacement){
    UnitPolynomial result;
    for(const auto& term:polynomial.terms)if(term.second!=0){
        size_t count=std::count(term.first.begin(),term.first.end(),variable);
        if(!count){result.add(term.first,term.second);continue;}
        UnitPolynomial::Monomial monomial;
        for(const auto& factor:term.first)if(factor!=variable)monomial.push_back(factor);
        UnitPolynomial part;part.add(monomial,term.second);
        for(size_t i=0;i<count;++i)part=polynomial_multiply(part,replacement);
        result=polynomial_add(std::move(result),part);
        if(result.size()>1024)throw std::overflow_error("terms");
    }
    return result;
}

static std::string emit_polynomial(const UnitPolynomial& polynomial){
    std::map<UnitPolynomial::Monomial,mpz_class> sorted;
    for(const auto& term:polynomial.terms)if(term.second!=0)sorted.emplace(term.first,term.second);
    std::vector<std::string> terms;
    for(const auto& term:sorted){
        if(term.first.empty()){terms.push_back(term.second.get_str());continue;}
        std::string body;
        if(term.first.size()==1)body=term.first[0];
        else{body="( *";for(const auto& factor:term.first)body+=' '+factor;body+=" )";}
        terms.push_back(term.second==1?body:"( * "+term.second.get_str()+" "+body+" )");
    }
    if(terms.empty())return "0";
    if(terms.size()==1)return terms[0];
    std::string result="( +";for(const auto& term:terms)result+=' '+term;return result+" )";
}

using UnitHistory=std::vector<std::pair<std::string,UnitPolynomial>>;

static UnitHistory reduce_unit_formula(const std::string& source,const std::string& destination){
    std::ifstream input(source);std::string line;
    if(!std::getline(input,line))throw std::runtime_error("missing literal count");
    unsigned long long count=std::stoull(line);
    std::map<int,std::string> raw,relations;
    std::map<int,UnitPolynomial> polynomials;
    std::vector<int> order;
    while(std::getline(input,line)){
        auto tokens=split_words(line);if(tokens.size()==1 && tokens[0]=="0")break;
        if(tokens.size()<2)throw std::runtime_error("invalid literal");
        int id=std::stoi(tokens[0]);std::string body;
        for(size_t i=1;i<tokens.size();++i){if(i>1)body+=' ';body+=tokens[i];}raw[id]=body;
        if(body.rfind("or ",0)==0 || body.rfind("if ",0)==0 || body.rfind("ite ",0)==0 || body.rfind("iff ",0)==0)throw std::runtime_error("gates");
        if(tokens.size()>2 && tokens[1]=="(" && (tokens[2]=="=" || tokens[2]=="<=" || tokens[2]==">=" || tokens[2]=="<" || tokens[2]==">")){
            size_t position=3;auto left=polynomial_expression(tokens,position);auto right=polynomial_expression(tokens,position);
            auto polynomial=polynomial_add(std::move(left),right,-1);const auto& operation=tokens[2];
            if(operation==">=" || operation==">")for(auto& term:polynomial.terms)term.second=-term.second;
            if(operation=="<" || operation==">")polynomial.add({},1);
            if(!polynomials.count(id))order.push_back(id);
            polynomials[id]=std::move(polynomial);relations[id]=operation=="="?"=":"<=";
        }
    }
    if(!std::getline(input,line))throw std::runtime_error("missing clauses");
    size_t clause_count=std::stoull(line);std::vector<std::vector<int>> clauses;
    for(size_t i=0;i<clause_count;++i){
        if(!std::getline(input,line))throw std::runtime_error("missing clause");
        std::vector<int> clause;
        for(const auto& token:split_words(line))if(token!="(" && token!=")")clause.push_back(std::stoi(token));
        clauses.push_back(std::move(clause));
    }
    std::string restored((std::istreambuf_iterator<char>(input)),std::istreambuf_iterator<char>());
    std::vector<int> equations;UnitHistory history;std::set<std::string> blocked;
    for(const auto& clause:clauses)if(clause.size()==1 && clause[0]>0 && relations[clause[0]]=="=")equations.push_back(clause[0]);
    std::map<std::string,std::set<int>> occurrences;
    int64_t total_terms=0;
    for(int id:order){
        total_terms+=polynomials.at(id).size();
        for(const auto& variable:polynomials.at(id).variables())occurrences[variable].insert(id);
    }
    for(unsigned iteration=0;iteration<10000;++iteration){
        std::string variable;UnitPolynomial replacement;
        for(int id:equations){
            const auto& polynomial=polynomials.at(id);
            for(const auto& term:polynomial.terms){
                if(term.first.size()!=1 || abs(term.second)!=1)continue;
                const auto& candidate=term.first[0];if(blocked.count(candidate))continue;
                bool recursive=false;
                for(const auto& other:polynomial.terms)if(other.second!=0 && other.first!=term.first && std::find(other.first.begin(),other.first.end(),candidate)!=other.first.end()){recursive=true;break;}
                if(recursive)continue;
                variable=candidate;
                for(const auto& other:polynomial.terms)if(other.second!=0 && other.first!=term.first)replacement.add(other.first,-other.second/term.second);
                break;
            }
            if(!variable.empty())break;
        }
        if(variable.empty())break;
        std::map<int,UnitPolynomial> changed;int64_t new_total=total_terms;
        try{
            for(int id:occurrences[variable]){
                auto polynomial=polynomial_substitute(polynomials.at(id),variable,replacement);
                new_total+=int64_t(polynomial.size())-int64_t(polynomials.at(id).size());changed.emplace(id,std::move(polynomial));
            }
            if(new_total>500000)throw std::overflow_error("total");
        }catch(const std::overflow_error&){blocked.insert(variable);continue;}
        for(auto& item:changed){
            auto old_variables=polynomials.at(item.first).variables(),new_variables=item.second.variables();
            for(const auto& name:old_variables)if(!new_variables.count(name))occurrences[name].erase(item.first);
            for(const auto& name:new_variables)if(!old_variables.count(name))occurrences[name].insert(item.first);
            polynomials[item.first]=std::move(item.second);
        }
        total_terms=new_total;history.emplace_back(variable,std::move(replacement));
    }
    std::map<int,bool> truth{{0,true}};
    for(int id:order){
        const auto& polynomial=polynomials.at(id);bool constant=true;mpz_class value=0;
        for(const auto& term:polynomial.terms)if(term.second!=0){if(term.first.empty())value=term.second;else{constant=false;break;}}
        if(constant)truth[id]=relations.at(id)=="="?value==0:value<=0;
    }
    std::vector<std::vector<int>> residual;std::set<int> used;
    for(const auto& clause:clauses){
        bool satisfied=false;std::vector<int> remaining;
        for(int literal:clause){
            auto known=truth.find(std::abs(literal));
            if(known==truth.end())remaining.push_back(literal);
            else if(known->second==(literal>=0)){satisfied=true;break;}
        }
        if(satisfied)continue;
        if(remaining.empty())throw std::runtime_error("inconsistent constant residual");
        for(int literal:remaining)used.insert(std::abs(literal));residual.push_back(std::move(remaining));
    }
    std::ofstream output(destination);output<<count<<"\n0 true\n";
    for(int id:used){
        output<<id<<' ';
        auto polynomial=polynomials.find(id);
        if(polynomial==polynomials.end())output<<raw.at(id);
        else output<<"( "<<relations.at(id)<<' '<<emit_polynomial(polynomial->second)<<" 0 )";
        output<<'\n';
    }
    output<<"0\n"<<residual.size()<<'\n';
    for(const auto& clause:residual){output<<"( ";for(int literal:clause)output<<literal<<' ';output<<")\n";}
    output<<restored;if(!output)throw std::runtime_error("cannot write reduced formula");
    return history;
}

static int unit_sls(const std::vector<std::string>& arguments){
    if(arguments.size()<2)return 2;
    char directory[]="/tmp/nia-unitreduce-XXXXXX";if(!mkdtemp(directory))return 2;
    struct Cleanup {std::string path;~Cleanup(){std::error_code error;std::filesystem::remove_all(path,error);}} cleanup{directory};
    std::string reduced=std::string(directory)+"/input",model=std::string(directory)+"/model.tsv";
    const auto began=std::chrono::steady_clock::now();UnitHistory history;
    try{history=reduce_unit_formula(arguments[1],reduced);}
    catch(const std::exception& error){
        std::cerr<<"unit_reduce_fallback="<<error.what()<<'\n';auto fallback=arguments;fallback[0]="fastplain-v3";return run_stage(fallback);
    }
    std::cerr<<"unit_eliminated="<<history.size()<<" seconds="<<std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count()<<'\n';
    std::vector<std::string> command{"corebip",reduced};std::string output;
    for(size_t i=2;i<arguments.size();++i){
        if(arguments[i].rfind("--model-output=",0)==0)output=arguments[i].substr(15);else command.push_back(arguments[i]);
    }
    command.push_back("--model-output="+model);command.push_back("--initialize=off");command.push_back("--core-jitter=4");
    std::ostringstream answer;int status;
    {
        struct Capture {std::streambuf* previous;explicit Capture(std::ostream& target):previous(std::cout.rdbuf(target.rdbuf())){}~Capture(){std::cout.rdbuf(previous);}} capture(answer);
        status=run_stage(command);
    }
    auto words=split_words(answer.str());
    if(status!=0 || words!=std::vector<std::string>{"sat"}){std::cout<<"unknown\n";return 0;}
    try{
        auto values=read_values(model);std::map<std::string,mpz_class> numbers;
        for(const auto& item:values)if(integer_word(item.second))numbers.emplace(item.first,mpz_class(item.second,10));
        for(auto item=history.rbegin();item!=history.rend();++item){
            mpz_class value=0;
            for(const auto& term:item->second.terms)if(term.second!=0){
                mpz_class product=term.second;
                for(const auto& factor:term.first){auto known=numbers.find(factor);if(known==numbers.end()){product=0;break;}product*=known->second;}
                value+=product;
            }
            numbers[item->first]=value;values[item->first]=value.get_str();
        }
        if(!validate_values(arguments[1],values)){std::cout<<"unknown\n";return 0;}
        if(!output.empty())write_values(output,values);
        std::cout<<"sat\n";return 0;
    }catch(const std::exception& error){std::cerr<<"unit_model_error="<<error.what()<<'\n';std::cout<<"unknown\n";return 0;}
}

int run_stage(const std::vector<std::string>& arguments){
    if(arguments.size()<2)return 2;
    if(arguments[0]=="unitreduce-v5")return unit_sls(arguments);
    if(arguments[0]=="validate-converted")return arguments.size()==3 && validate_model(arguments[1],arguments[2])?0:1;
    nia::SlsStrategy strategy;
    const std::string name=arguments[0]=="fastplain-v3"?"plain":arguments[0];
    if(!nia::parse_strategy(name,strategy))return 2;
    auto values=arguments;values.push_back("--strategy="+name);
    std::vector<char*> argv;
    for(auto& value:values)argv.push_back(value.data());argv.push_back(nullptr);
    return call::run_sls_entry(int(values.size()),argv.data());
}
}
